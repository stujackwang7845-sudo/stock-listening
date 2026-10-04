import sqlite3
import os
import sys

def merge_databases(target_db, source_db):
    """
    Merges data from source_db INTO target_db.
    Only inserts records that do NOT exist in target_db.
    """
    if not os.path.exists(source_db):
        print(f"Error: Source DB not found: {source_db}")
        return
    if not os.path.exists(target_db):
        print(f"Error: Target DB not found: {target_db}")
        return

    print(f"Merging from [{source_db}] -> [{target_db}]")
    
    conn_target = sqlite3.connect(target_db)
    cursor_target = conn_target.cursor()
    
    conn_source = sqlite3.connect(source_db)
    cursor_source = conn_source.cursor()
    
    tables = [
        ("daily_cache", "date_str"),
        ("dashboard_summary", "date_str"),
        ("agg_cache", "date_str"),
        ("chart_cache", "cache_key")
    ]
    
    total_copied = 0
    
    for table, pk_col in tables:
        print(f"Processing table: {table}...")
        
        # Check if table exists in source
        try:
            cursor_source.execute(f"SELECT * FROM {table}")
            rows = cursor_source.fetchall()
        except sqlite3.OperationalError:
            print(f"  - Table {table} missing in source, skipping.")
            continue
            
        # Get column names
        col_names = [description[0] for description in cursor_source.description]
        placeholders = ",".join("?" * len(col_names))
        cols_str = ",".join(col_names)
        
        pk_idx = -1
        for i, name in enumerate(col_names):
            if name == pk_col:
                pk_idx = i
                break
        
        if pk_idx == -1:
            print(f"  - PK {pk_col} not found in {table}, skipping.")
            continue

        copied_count = 0
        for row in rows:
            pk_val = row[pk_idx]
            
            # Check existence in target
            try:
                cursor_target.execute(f"SELECT 1 FROM {table} WHERE {pk_col} = ?", (pk_val,))
                exists = cursor_target.fetchone()
                
                if not exists:
                    # Insert
                    cursor_target.execute(f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})", row)
                    copied_count += 1
            except sqlite3.OperationalError:
                # Table might not exist in target? Create it?
                # For simplicity, assume target schema implies tables exist or use a generic CREATE later.
                # But CacheManager creates tables on init.
                print(f"  - Target table {table} error (missing?).")
                break
                
        print(f"  - Copied {copied_count} new records.")
        total_copied += copied_count
        
    conn_target.commit()
    conn_target.close()
    conn_source.close()
    
    print(f"Merge Complete. Total records copied: {total_copied}")

if __name__ == "__main__":
    # Usage: python scripts/merge_cache_db.py old.db new.db
    # But for safety, let's hardcode or prompt if no args
    if len(sys.argv) < 3:
        print("Usage: python scripts/merge_cache_db.py <TARGET_DB_PATH> <SOURCE_DB_PATH>")
        print("Example: python scripts/merge_cache_db.py data/cache.db data/new_cache_backup.db")
    else:
        target = sys.argv[1]
        source = sys.argv[2]
        merge_databases(target, source)
