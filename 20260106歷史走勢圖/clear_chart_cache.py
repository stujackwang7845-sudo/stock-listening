
import sqlite3
import os

DB_PATH = "data/cache.db"

def clear_cache():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Count before
        cursor.execute("SELECT COUNT(*) FROM chart_cache")
        count = cursor.fetchone()[0]
        print(f"Current Cache Entries: {count}")
        
        # Delete
        cursor.execute("DELETE FROM chart_cache")
        conn.commit()
        
        print("Chart Cache Cleared Successfully.")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    clear_cache()
