import time
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
import threading
import multiprocessing
import traceback
from collections import deque
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from contextlib import contextmanager

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from tqdm import tqdm

# --- Configuration ---
# --- IMPORTANT: Visual mode is required for this site to work reliably. ---
RUN_IN_VISUAL_MODE = True

START_URL = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
URL_PREFIX = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
ALLOWED_DOMAIN = "dev.epicgames.com"
DB_FILE = "crawled_data.db"
LOG_FILE = "crawler.log"

MAX_WORKERS = 4 if RUN_IN_VISUAL_MODE else min(10, multiprocessing.cpu_count() + 4)
DRIVER_RECYCLE_INTERVAL_SECONDS = 3600
MAX_RETRIES = 3 
WEBDRIVER_TIMEOUT_SECONDS = 120

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)
# --- MODIFIED: Console handler is removed to disable terminal logging ---
logger = logging.getLogger()
logger.setLevel(logging.INFO) 
logger.addHandler(log_handler)


# --- Database & State Management ---
def init_db(db_path: str):
    """Ensures the required tables and columns exist."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY, title TEXT, scraped_at REAL,
                attempts INTEGER DEFAULT 0, status TEXT DEFAULT 'new'
            )
        """)
        try: cursor.execute("ALTER TABLE pages ADD COLUMN status TEXT DEFAULT 'new'")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE pages ADD COLUMN attempts INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        conn.commit()

# --- WebDriver Pool & Worker ---
def create_driver():
    options = uc.ChromeOptions()
    options.headless = not RUN_IN_VISUAL_MODE
    options.add_argument("--disable-blink-features=AutomationControlled")
    if RUN_IN_VISUAL_MODE:
        options.add_argument("window-size=1280,720")
    else:
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
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

def worker(url: str, driver_pool: Queue) -> dict:
    """Processes a single URL and returns its result."""
    logging.info(f"Worker starting for url: {url}")
    try:
        with get_driver_from_pool(driver_pool) as driver:
            if driver is None: return {"status": "failed_driver", "new_links": set()}
            
            driver.get(url)

            start_time = time.time()
            title_found = False
            while time.time() - start_time < WEBDRIVER_TIMEOUT_SECONDS:
                if "Unreal Engine" in driver.title:
                    title_found = True
                    break
                time.sleep(1) 
            
            if not title_found:
                logging.warning(f"Timeout waiting for title on {url}. Attempting to scrape partial content.")

            time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            title = soup.title.string.strip() if soup.title else "Untitled"
            
            new_links = {
                urljoin(url, a["href"]).split("?")[0]
                for a in soup.find_all("a", href=True)
                if urljoin(url, a["href"]).startswith(URL_PREFIX)
            }
            logging.info(f"Worker success for {url}. Found {len(new_links)} new links.")
            return {"status": "success", "title": title, "new_links": new_links}
            
    except Exception:
        logging.error(f"Worker failed on url {url}:\n{traceback.format_exc()}")
        return {"status": "failed", "new_links": set()}

# --- Dedicated Database Writer Thread ---
def db_writer(db_path: str, write_queue: Queue, stop_event: threading.Event, pbar: tqdm):
    """A dedicated thread to handle all database writes, preventing lock contention."""
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    
    while not stop_event.is_set() or not write_queue.empty():
        try:
            item = write_queue.get(timeout=1)
            if item is None: continue

            job_type, data = item
            if job_type == "update_status":
                url, status = data
                cursor.execute("UPDATE pages SET status = ?, attempts = attempts + 1 WHERE url = ?", (status, url))
            elif job_type == "add_content":
                url, title = data
                cursor.execute("UPDATE pages SET title = ?, scraped_at = ?, status = 'success', attempts = attempts + 1 WHERE url = ?", (title, time.time(), url))
                pbar.update(1)
            elif job_type == "add_new_links":
                cursor.execute("SELECT count(*) FROM pages")
                count_before = cursor.fetchone()[0]
                cursor.executemany("INSERT OR IGNORE INTO pages (url) VALUES (?)", data)
                cursor.execute("SELECT count(*) FROM pages")
                count_after = cursor.fetchone()[0]
                pbar.total += (count_after - count_before)

            conn.commit()
            write_queue.task_done()
        except Empty:
            continue
        except Exception as e:
            logging.error(f"[DBWriter] Error processing job: {e}")
    conn.close()
    logging.info("DBWriter thread finished.")


# --- Main Orchestrator ---
def main():
    init_db(DB_FILE)
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO pages (url) VALUES (?)", (START_URL.split("?")[0],))
        cursor.execute("SELECT url FROM pages WHERE status != 'success' AND attempts < ?", (MAX_RETRIES,))
        urls_to_process = deque([row[0] for row in cursor.fetchall()])
        cursor.execute("SELECT count(*) FROM pages WHERE status = 'success'")
        completed_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM pages")
        total_known_urls = cursor.fetchone()[0]
        conn.commit()

    if not urls_to_process:
        print("All known URLs have been processed. Nothing to do.")
        return
        
    print(f"Starting crawl. To-Do: {len(urls_to_process)}, Completed: {completed_count}, Total Known: {total_known_urls}")

    driver_pool = Queue(maxsize=MAX_WORKERS)
    for _ in range(MAX_WORKERS):
        driver = create_driver()
        if driver: driver_pool.put((driver, time.time()))

    if driver_pool.empty():
        print("Could not create any WebDriver instances. Exiting.")
        return

    db_write_queue = Queue()
    stop_event = threading.Event()
    
    pbar = tqdm(total=total_known_urls, initial=completed_count, desc="Crawling")
    
    writer_thread = threading.Thread(target=db_writer, args=(DB_FILE, db_write_queue, stop_event, pbar), daemon=True, name="DBWriter")
    writer_thread.start()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Crawler") as executor:
        futures = {executor.submit(worker, urls_to_process.popleft(), driver_pool): url for url in [urls_to_process.popleft() for _ in range(min(len(urls_to_process), MAX_WORKERS))]}
        
        try:
            while futures:
                # Wait for the next future to complete
                for future in as_completed(futures):
                    original_url = futures.pop(future)
                    result = future.result()
                    
                    if result["status"] == "success":
                        db_write_queue.put(("add_content", (original_url, result['title'])))
                        if result["new_links"]:
                            # The DB Writer handles duplicates with INSERT OR IGNORE
                            db_write_queue.put(("add_new_links", [(link,) for link in result["new_links"]]))
                    else:
                        db_write_queue.put(("update_status", (original_url, result['status'])))
                    
                    # --- FIXED: Immediately dispatch a new task to replace the one that just finished ---
                    if urls_to_process:
                        next_url = urls_to_process.popleft()
                        futures[executor.submit(worker, next_url, driver_pool)] = next_url
                    else:
                        logging.info("URL queue is empty. Waiting for active workers to finish.")
                        # The loop will naturally exit when the `futures` dict becomes empty

        except KeyboardInterrupt:
            print("\nShutdown signal received...")
        finally:
            print("\nCleaning up... Waiting for DB writes to finish.")
            stop_event.set()
            # Wait for the writer thread to process everything in the queue before exiting
            db_write_queue.join() 
            writer_thread.join()
            
            pbar.close()
            # Cancel any remaining in-flight tasks
            for future in futures: future.cancel()
            while not driver_pool.empty():
                try:
                    driver, _ = driver_pool.get_nowait()
                    if driver: driver.quit()
                except Exception: pass
            print("Crawler finished gracefully.")

if __name__ == "__main__":
    main()
