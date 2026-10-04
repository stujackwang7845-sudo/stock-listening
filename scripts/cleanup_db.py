
import sqlite3

def cleanup_db():
    db_path = "data/disposal_history.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Beginning DB Cleanup...")
    
    # 1. Identify and Delete Duplicates (Keep row with Max ID - likely newest)
    # Actually, we want to keep the one with Non-Null Announce Date if possible.
    # Simple logic: Delete all but the one with Max ID for each (code, period_raw).
    
    cursor.execute("""
        DELETE FROM disposal_records
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM disposal_records
            GROUP BY code, period_raw
        )
    """)
    print(f"Deleted duplicates. Rows affected: {cursor.rowcount}")
    
    # 2. Add Unique Index to prevent future duplicates
    try:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_code_period 
            ON disposal_records (code, period_raw)
        """)
        print("Successfully created UNIQUE INDEX on (code, period_raw).")
    except Exception as e:
        print(f"Index creation failed: {e}")

    conn.commit()
    conn.close()
    print("DB Cleanup operations finished.")

if __name__ == "__main__":
    cleanup_db()
