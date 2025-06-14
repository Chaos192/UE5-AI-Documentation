import time
import logging
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
from selenium.common.exceptions import WebDriverException, TimeoutException
from tqdm import tqdm

# --- Configuration ---
START_URL = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
URL_PREFIX = "https://dev.epicgames.com/documentation/en-us/unreal-engine"
ALLOWED_DOMAIN = "dev.epicgames.com"
DB_FILE = "crawled_data.db"
# A good balance for performance in visual mode
MAX_WORKERS = 4
DRIVER_RECYCLE_INTERVAL_SECONDS = 3600
WRITE_BUFFER_SIZE = 100
MAX_RETRIES = 3 
WEBDRIVER_TIMEOUT_SECONDS = 90

# --- Logging ---
logging.basicConfig(
    filename="crawler.log", level=logging.INFO, filemode="w",
    format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s"
)

# --- Database & State Management ---
def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, title TEXT, scraped_at REAL)
    """)
    conn.commit()
    return conn

def load_visited_urls(conn) -> set:
    print("Loading previously visited URLs from the database...")
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM pages")
    return {row[0] for row in cursor.fetchall()}

def flush_buffer_to_db(buffer, conn, lock):
    if not buffer: return
    with lock:
        try:
            cursor = conn.cursor()
            cursor.executemany("INSERT OR IGNORE INTO pages (url, title, scraped_at) VALUES (?, ?, ?)", buffer)
            conn.commit()
            logging.info(f"Flushed {len(buffer)} records to the database.")
            buffer.clear()
        except sqlite3.Error as e:
            logging.error(f"Database error while flushing buffer: {e}")

# --- WebDriver function ---
def create_driver():
    """Creates a new undetected Chrome WebDriver instance in VISUAL mode."""
    options = uc.ChromeOptions()
    
    # Running in visual mode is the stable solution for this site
    options.headless = False
    
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("window-size=1920,1080")

    try:
        # Using your specific Chrome version
        chrome_major_version = 137
        driver = uc.Chrome(version_main=chrome_major_version, options=options)
    except Exception as e:
        logging.error(f"Failed to create undetected_chromedriver: {e}")
        logging.error("Please ensure Google Chrome is installed and the version is set correctly.")
        return None
    return driver


@contextmanager
def get_driver_from_pool(driver_pool: Queue):
    driver, creation_time = driver_pool.get()
    try:
        if time.time() - creation_time > DRIVER_RECYCLE_INTERVAL_SECONDS:
            logging.info("Recycling old WebDriver instance.")
            if driver: driver.quit()
            driver = create_driver()
            creation_time = time.time()
        yield driver
    finally:
        driver_pool.put((driver, creation_time))

def exponential_backoff(retry_attempt: int, base_delay: int = 5, max_delay: int = 45) -> int:
    return min(base_delay * (2 ** retry_attempt), max_delay)

def worker(url: str, driver_pool: Queue) -> dict:
    page_data = {"url": url, "title": None, "new_links": set()}
    for attempt in range(MAX_RETRIES):
        try:
            with get_driver_from_pool(driver_pool) as driver:
                if driver is None: break
                driver.get(url)

                WebDriverWait(driver, WEBDRIVER_TIMEOUT_SECONDS).until(
                    EC.title_contains("Unreal Engine")
                )
                time.sleep(5) 

                soup = BeautifulSoup(driver.page_source, "html.parser")
                page_data["title"] = soup.title.string.strip() if soup.title else ""

                for a_tag in soup.find_all("a", href=True):
                    full_url = urljoin(url, a_tag["href"])
                    if (full_url.startswith(URL_PREFIX) and urlparse(full_url).netloc == ALLOWED_DOMAIN and "#" not in full_url):
                        page_data["new_links"].add(full_url)
                
                logging.info(f"Successfully scraped {url}")
                return page_data

        except TimeoutException:
            logging.warning(f"Timeout waiting for title on {url} on attempt {attempt + 1}/{MAX_RETRIES}.")
            if attempt < MAX_RETRIES - 1:
                wait_time = exponential_backoff(attempt)
                logging.info(f"Waiting {wait_time}s before next retry...")
                time.sleep(wait_time)
            else:
                logging.error(f"Failed to load {url} after {MAX_RETRIES} attempts.")
        
        except Exception as e:
            logging.error(f"An unexpected error occurred in worker for {url} on attempt {attempt+1}")
            logging.error(f"Exception Type: {type(e)}")
            logging.error(f"Exception Message: {e}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(5)
            else:
                logging.error(f"Final attempt failed for {url}")
            
    return page_data

# --- Main Execution ---
def main():
    db_conn = init_db(DB_FILE)
    visited_urls = load_visited_urls(db_conn)
    queue = deque([START_URL] if START_URL not in visited_urls else [])
    db_lock = threading.Lock()
    write_buffer = []

    print(f"Initializing with {MAX_WORKERS} workers...")
    driver_pool = Queue(maxsize=MAX_WORKERS)
    for _ in range(MAX_WORKERS):
        driver = create_driver()
        if driver:
            driver_pool.put((driver, time.time()))

    if driver_pool.empty():
        print("Could not create any WebDriver instances. Exiting.")
        return

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="Crawler") as executor:
            futures = {executor.submit(worker, url, driver_pool): url for url in queue}
            pbar = tqdm(total=len(queue) + len(visited_urls), initial=len(visited_urls), desc="Crawling")

            while futures:
                for future in as_completed(futures):
                    result_data = future.result()
                    futures.pop(future)
                    pbar.update(1)

                    if result_data.get("title"):
                        pbar.write(f"[SCRAPED] {result_data['title']} | {result_data['url']}")
                        with db_lock:
                            write_buffer.append((result_data["url"], result_data["title"], time.time()))
                    
                    new_links_to_add = []
                    with db_lock:
                        for link in result_data.get("new_links", set()):
                            if link not in visited_urls:
                                visited_urls.add(link)
                                new_links_to_add.append(link)
                    
                    if new_links_to_add:
                        for link in new_links_to_add:
                            if len(futures) < MAX_WORKERS * 2:
                                futures[executor.submit(worker, link, driver_pool)] = link
                        pbar.total = len(visited_urls)
                        pbar.set_postfix({"Discovered": len(visited_urls)})
                    
                    if len(write_buffer) >= WRITE_BUFFER_SIZE:
                        flush_buffer_to_db(write_buffer, db_conn, db_lock)
    
    except KeyboardInterrupt:
        print("\nShutdown signal received...")
    finally:
        print("Cleaning up: flushing buffer and closing drivers...")
        flush_buffer_to_db(write_buffer, db_conn, db_lock)
        db_conn.close()
        
        while not driver_pool.empty():
            try:
                driver, _ = driver_pool.get_nowait()
                if driver: driver.quit()
            except Exception: pass
        pbar.close()
        print("Crawler finished gracefully.")

if __name__ == "__main__":
    main()