import time
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
import threading
import multiprocessing
import traceback
import json
import os
from collections import deque
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from contextlib import contextmanager

import undetected_chromedriver as uc
import spacy
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from tqdm import tqdm
from colorama import Fore, Style, init

# --- Initialization & Configuration ---
init(autoreset=True)

# --- NEW: Debug Mode Flag ---
# Set to True to run in visual mode with fewer workers for easy debugging
DEBUG_MODE = False

# --- State and Output Files ---
DB_FILE = "crawler_state.db"
LOG_FILE = "unified_scraper.log"

START_URL = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
URL_PREFIX = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
ALLOWED_DOMAIN = "dev.epicgames.com"

# Configuration
MAX_WORKERS = 2 if DEBUG_MODE else min(8, (multiprocessing.cpu_count() or 1) + 4)
DRIVER_RECYCLE_INTERVAL_SECONDS = 3600
MAX_RETRIES = 3
WEBDRIVER_TIMEOUT_SECONDS = 90
NEW_LINK_BUFFER_SIZE = 200

# --- AI Model Loading ---
try:
    print(f"{Style.DIM}Loading spaCy NLP model 'en_core_web_sm'...")
    nlp = spacy.load("en_core_web_sm")
    print(f"{Fore.GREEN}NLP model loaded successfully.")
except IOError:
    print(f"{Fore.RED}SpaCy model 'en_core_web_sm' not found. Please run: python -m spacy download en_core_web_sm")
    exit()

# --- Rotating Log File Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
log_handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO if not DEBUG_MODE else logging.DEBUG)
logger.addHandler(log_handler)

# --- Database Schema & Initialization ---
def init_db(db_path: str):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS urls (
                url TEXT PRIMARY KEY, status TEXT DEFAULT 'new',
                attempts INTEGER DEFAULT 0, last_attempt_at REAL
            )""")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyzed_content (
                url TEXT PRIMARY KEY, title TEXT, content_raw TEXT,
                entities_json TEXT, scraped_at REAL
            )""")
        conn.commit()

# --- WebDriver Pool & Worker ---
def create_driver():
    options = uc.ChromeOptions()
    options.headless = not DEBUG_MODE
    options.add_argument("--disable-blink-features=AutomationControlled")
    if not DEBUG_MODE:
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
    else:
        options.add_argument("window-size=1920,1080")
        
    try:
        chrome_major_version = 137
        driver = uc.Chrome(version_main=chrome_major_version, options=options)
    except Exception as e:
        logging.error(f"Failed to create undetected_chromedriver: {e}")
        return None
    return driver

@contextmanager
def get_driver_from_pool(driver_pool: Queue):
    driver, creation_time = driver_pool.get()
    try:
        if time.time() - creation_time > DRIVER_RECYCLE_INTERVAL_SECONDS:
            if driver: driver.quit()
            driver = create_driver()
            creation_time = time.time()
        yield driver
    finally:
        driver_pool.put((driver, creation_time))

def worker(url: str, driver_pool: Queue, nlp_model) -> dict:
    try:
        with get_driver_from_pool(driver_pool) as driver:
            if driver is None: return {"status": "failed_driver_error", "new_links": set()}
            driver.get(url)
            WebDriverWait(driver, WEBDRIVER_TIMEOUT_SECONDS).until(EC.title_contains("Unreal Engine"))
            time.sleep(3)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            title = soup.title.string.strip() if soup.title else ""
            body_div = soup.find("div", {"id": "main-content"})
            if not body_div: return {"status": "failed_no_content", "new_links": set()}
            content_raw = body_div.get_text(separator=' ', strip=True)
            new_links = {
                urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                if urljoin(url, a["href"]).startswith(URL_PREFIX) and urlparse(urljoin(url, a["href"])).netloc == ALLOWED_DOMAIN and "#" not in urljoin(url, a["href"])
            }
            doc = nlp_model(content_raw[:nlp_model.max_length])
            entities = {"PERSON": list({e.text for e in doc.ents if e.label_ == "PERSON"}), "ORG": list({e.text for e in doc.ents if e.label_ == "ORG"}), "PRODUCT": list({e.text for e in doc.ents if e.label_ == "PRODUCT"})}
            return {"status": "success", "title": title, "new_links": new_links, "content_raw": content_raw, "entities_json": json.dumps(entities), "scraped_at": time.time()}
    except Exception:
        logging.error(f"An unexpected error occurred in worker for {url}:\n{traceback.format_exc()}")
        return {"status": "failed_exception", "new_links": set()}

# --- IMPROVEMENT: Dedicated Database Writer Thread ---
def db_writer(db_path: str, write_queue: Queue, stop_event: threading.Event):
    """A dedicated thread to handle all database writes, preventing lock contention."""
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    
    while not stop_event.is_set() or not write_queue.empty():
        try:
            item = write_queue.get(timeout=1)
            if item is None: continue

            job_type, data = item
            if job_type == "update_status":
                cursor.execute("UPDATE urls SET status = ?, attempts = attempts + 1, last_attempt_at = ? WHERE url = ?", data)
            elif job_type == "add_content":
                cursor.execute("INSERT OR REPLACE INTO analyzed_content (url, title, content_raw, entities_json, scraped_at) VALUES (?, ?, ?, ?, ?)", data)
                cursor.execute("UPDATE urls SET status = 'success' WHERE url = ?", (data[0],))
            elif job_type == "add_new_links":
                cursor.executemany("INSERT OR IGNORE INTO urls (url) VALUES (?)", data)
            
            conn.commit()
            write_queue.task_done()
        except Queue.empty:
            continue
        except Exception as e:
            logging.error(f"[DBWriter] Error processing job: {e}")
    conn.close()

# --- Main Orchestrator ---
def main():
    init_db(DB_FILE)
    
    db_write_queue = Queue()
    stop_event = threading.Event()
    
    # Start the dedicated DB writer thread
    writer_thread = threading.Thread(target=db_writer, args=(DB_FILE, db_write_queue, stop_event), daemon=True, name="DBWriter")
    writer_thread.start()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO urls (url) VALUES (?)", (START_URL,))
        conn.commit()

    driver_pool = Queue(maxsize=MAX_WORKERS)
    for _ in range(MAX_WORKERS):
        driver = create_driver()
        if driver: driver_pool.put((driver, time.time()))

    if driver_pool.empty():
        print(f"{Fore.RED}Could not create any WebDriver instances. Exiting.")
        stop_event.set()
        writer_thread.join()
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Scraper") as executor:
        futures = {}
        new_links_buffer = set()
        
        try:
            while True:
                with sqlite3.connect(DB_FILE) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT url FROM urls WHERE status != 'success' AND attempts < ?", (MAX_RETRIES,))
                    urls_to_process = [row[0] for row in cursor.fetchall() if row[0] not in {f.result().get('url', '') for f in futures if f.done() and f.result()}][:MAX_WORKERS*2-len(futures)]

                if urls_to_process:
                    for url in urls_to_process:
                        if url not in [futures[f] for f in futures]:
                            futures[executor.submit(worker, url, driver_pool, nlp)] = url
                elif not futures:
                    print(f"{Fore.GREEN}All tasks complete. Waiting for DB writer to finish...")
                    break

                for future in as_completed(futures):
                    original_url = futures.pop(future)
                    result = future.result()

                    if result["status"] == "success":
                        db_write_queue.put(("add_content", (original_url, result['title'], result['content_raw'], result['entities_json'], result['scraped_at'])))
                    else:
                        db_write_queue.put(("update_status", (result['status'], time.time(), original_url)))
                    
                    new_links_buffer.update(result.get("new_links", set()))

                    if len(new_links_buffer) >= NEW_LINK_BUFFER_SIZE:
                        db_write_queue.put(("add_new_links", [(link,) for link in new_links_buffer]))
                        new_links_buffer.clear()
                
                if not futures: time.sleep(2)

        except KeyboardInterrupt:
            print("\nShutdown signal received...")
        finally:
            print(f"\n{Style.BRIGHT}Processing Complete.{Style.RESET_ALL}")
            if new_links_buffer:
                db_write_queue.put(("add_new_links", [(link,) for link in new_links_buffer]))
            
            stop_event.set()
            db_write_queue.join()
            writer_thread.join()

            for future in futures: future.cancel()
            while not driver_pool.empty():
                try:
                    driver, _ = driver_pool.get_nowait()
                    if driver: driver.quit()
                except Exception: pass

if __name__ == '__main__':
    main()