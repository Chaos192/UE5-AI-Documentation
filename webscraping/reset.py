# reset_start_url.py
import sqlite3
import os

DB_FILE = "crawled_data.db"
START_URL = "https://dev.epicgames.com/documentation/en-us/unreal-engine"

if not os.path.exists(DB_FILE):
    print(f"Database file '{DB_FILE}' not found. Nothing to reset.")
else:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # This query surgically resets the main page back to 'new'
        cursor.execute("UPDATE pages SET status = 'new', attempts = 0 WHERE url = ?", (START_URL,))
        if cursor.rowcount > 0:
            print(f"✅ Successfully reset status for the start URL.")
            print("You can now run the main crawler.py script.")
        else:
            print(f"Start URL not found in the database. No changes made.")