
import sqlite3

def clean():
    print("Force cleaning 3691 from DB...")
    conn = sqlite3.connect("data/disposal_history.db")
    c = conn.cursor()
    c.execute("DELETE FROM disposal_records WHERE code LIKE '%3691%'")
    print(f"Deleted {c.rowcount} records for 3691.")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    clean()
