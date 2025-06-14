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
from queue import Queue
from contextlib import contextmanager

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from tqdm import tqdm

# --- Configuration ---
# --- MODIFIED: Set to True to see the browser windows (GUI) ---
DEBUG_MODE = True

START_URL = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
URL_PREFIX = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
ALLOWED_DOMAIN = "dev.epicgames.com"
DB_FILE = "crawled_data.db"
LOG_FILE = "crawler.log"

# Use fewer workers in debug mode so it's easier to watch
MAX_WORKERS = 4 if DEBUG_MODE else min(10, multiprocessing.cpu_count() + 4)
DRIVER_RECYCLE_INTERVAL_SECONDS = 3600
WRITE_BUFFER_SIZE = 200
MAX_RETRIES = 3 
WEBDRIVER_TIMEOUT_SECONDS = 90

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)
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
    """Creates a WebDriver instance, visible or headless based on DEBUG_MODE."""
    options = uc.ChromeOptions()
    options.headless = not DEBUG_MODE
    options.add_argument("--disable-blink-features=AutomationControlled")
    if not DEBUG_MODE:
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
    else:
        # Set a visible window size for debug mode
        options.add_argument("window-size=1280,720")
        
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
    for attempt in range(MAX_RETRIES):
        try:
            with get_driver_from_pool(driver_pool) as driver:
                if driver is None: continue
                driver.get(url)
                WebDriverWait(driver, WEBDRIVER_TIMEOUT_SECONDS).until(EC.title_contains("Unreal Engine"))
                time.sleep(3) 
                soup = BeautifulSoup(driver.page_source, "html.parser")
                title = soup.title.string.strip() if soup.title else "Untitled"
                new_links = {
                    urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                    if urljoin(url, a["href"]).startswith(URL_PREFIX) and urlparse(urljoin(url, a["href"])).netloc == ALLOWED_DOMAIN and "#" not in urljoin(url, a["href"])
                }
                return {"status": "success", "title": title, "new_links": new_links}
        except Exception:
            logging.error(f"Worker failed on url {url} (attempt {attempt+1}/{MAX_RETRIES}):\n{traceback.format_exc()}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(5)
    return {"status": "failed", "new_links": set()}

# --- Main Orchestrator ---
def main():
    init_db(DB_FILE)
    
    db_conn = sqlite3.connect(DB_FILE, timeout=10)
    db_lock = threading.Lock()
    
    with db_lock:
        cursor = db_conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO pages (url) VALUES (?)", (START_URL,))
        cursor.execute("SELECT url FROM pages WHERE status != 'success' AND attempts < ?", (MAX_RETRIES,))
        urls_to_process = deque([row[0] for row in cursor.fetchall()])
        cursor.execute("SELECT count(*) FROM pages WHERE status = 'success'")
        completed_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM pages")
        total_known_urls = cursor.fetchone()[0]
        db_conn.commit()

    if not urls_to_process:
        print("All known URLs have been processed. Nothing to do.")
        print("If this is incorrect, run the `reset.py` script again.")
        return
        
    print(f"Resuming crawl. To-Do: {len(urls_to_process)}, Completed: {completed_count}, Total Known: {total_known_urls}")

    driver_pool = Queue(maxsize=MAX_WORKERS)
    for _ in range(MAX_WORKERS):
        driver = create_driver()
        if driver: driver_pool.put((driver, time.time()))

    if driver_pool.empty():
        print("Could not create any WebDriver instances. Exiting.")
        return

    pbar = tqdm(total=total_known_urls, initial=completed_count, desc="Crawling")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Crawler") as executor:
        futures = {executor.submit(worker, urls_to_process.popleft(), driver_pool): None for _ in range(min(len(urls_to_process), MAX_WORKERS*2))}
        for i in range(len(futures)):
             # Associate the future with its URL
             future = list(futures.keys())[i]
             futures[future] = list(set(urls_to_process) | {START_URL})[i] if i < len(list(set(urls_to_process) | {START_URL})) else START_URL

        try:
            while futures:
                for future in as_completed(futures):
                    original_url = futures.pop(future)
                    result = future.result()
                    
                    with db_lock:
                        cursor = db_conn.cursor()
                        if result["status"] == "success":
                            cursor.execute(
                                "UPDATE pages SET title = ?, scraped_at = ?, status = 'success', attempts = attempts + 1 WHERE url = ?",
                                (result['title'], time.time(), original_url)
                            )
                            pbar.update(1)
                        else:
                            cursor.execute(
                                "UPDATE pages SET status = 'failed', attempts = attempts + 1 WHERE url = ?",
                                (original_url,)
                            )
                        
                        newly_added_links = []
                        for link in result.get("new_links", set()):
                            try:
                                cursor.execute("INSERT OR IGNORE INTO pages (url) VALUES (?)", (link,))
                                if cursor.rowcount > 0:
                                    newly_added_links.append(link)
                            except sqlite3.IntegrityError:
                                continue
                        db_conn.commit()

                        if newly_added_links:
                            urls_to_process.extend(newly_added_links)
                            pbar.total += len(newly_added_links)

                    if urls_to_process:
                        next_url = urls_to_process.popleft()
                        futures[executor.submit(worker, next_url, driver_pool)] = next_url

        except KeyboardInterrupt:
            print("\nShutdown signal received...")
        finally:
            print("\nCleaning up...")
            pbar.close()
            db_conn.close()
            for future in futures: future.cancel()
            while not driver_pool.empty():
                try:
                    driver, _ = driver_pool.get_nowait()
                    if driver: driver.quit()
                except Exception: pass
            print("Crawler finished gracefully.")

if __name__ == "__main__":
    main()