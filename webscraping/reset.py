# cleanup_db.py
import sqlite3
from urllib.parse import urlparse
from collections import defaultdict
from tqdm import tqdm
import os

DB_FILE = "crawled_data.db"

def main():
    """
    Cleans the database by normalizing URLs (removing query parameters)
    and merging duplicate entries, preserving scraped progress.
    """
    if not os.path.exists(DB_FILE):
        print(f"Database file '{DB_FILE}' not found. No cleanup needed.")
        return

    print(f"Connecting to database '{DB_FILE}' to begin cleanup...")
    
    url_map = defaultdict(list)
    scraped_data = {}
    conn = None

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        print("Step 1: Reading all URLs from the database...")
        cursor.execute("SELECT url, title, scraped_at, attempts, status FROM pages")
        all_rows = cursor.fetchall()
        
        if not all_rows:
            print("Database is empty. No cleanup needed.")
            return

        print(f"Step 2: Analyzing {len(all_rows)} URLs for duplicates...")
        for row in tqdm(all_rows, desc="Analyzing URLs"):
            original_url, title, scraped_at, attempts, status = row
            normalized_url = urlparse(original_url)._replace(query="", fragment="").geturl()
            
            url_map[normalized_url].append(original_url)
            
            if status == 'success' and title is not None:
                if normalized_url not in scraped_data:
                    scraped_data[normalized_url] = (title, scraped_at, attempts, status)

        print(f"\nAnalysis complete. Found {len(url_map)} unique pages.")
        
        print("Step 3: Cleaning the database. This may take a moment...")
        
        cursor.execute("DELETE FROM pages")

        rows_to_insert = []
        for normalized_url in url_map.keys():
            if normalized_url in scraped_data:
                title, scraped_at, attempts, status = scraped_data[normalized_url]
                rows_to_insert.append((normalized_url, title, scraped_at, attempts, status))
            else:
                rows_to_insert.append((normalized_url, None, None, 0, 'new'))

        cursor.executemany(
            "INSERT OR IGNORE INTO pages (url, title, scraped_at, attempts, status) VALUES (?, ?, ?, ?, ?)",
            rows_to_insert
        )
        
        conn.commit()
        
        print(f"\n✅ Cleanup complete! Your database now contains {len(rows_to_insert)} clean, unique URLs.")
        print("You can now run the main crawler.py script.")

    except sqlite3.Error as e:
        print(f"\nAn error occurred during database cleanup: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    main()
