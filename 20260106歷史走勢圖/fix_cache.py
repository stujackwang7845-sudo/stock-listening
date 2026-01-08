
from core.cache import CacheManager
from core.utils import DateUtils
import datetime

def fix_2408_cache():
    try:
        last_day = DateUtils.get_last_trading_day()
        print(f"Last Trading Day: {last_day}")
    except Exception as e:
        print(f"DateUtils Error: {e}")
        # Manual fallback
        last_day = datetime.datetime(2026, 1, 2)
        print(f"Fallback Last Trading Day: {last_day}")
    
    target_date = "20260102"
    
    # Init CacheManager
    cache_mgr = CacheManager() # Defaults to data/cache.db
    print(f"Cache DB Path: {cache_mgr.db_path}")
    
    # Check
    data = cache_mgr.get_daily_data(target_date)
    
    if data:
        print(f"Cache for {target_date} FOUND with {len(data)} items.")
        print("Clearing cache for target date...")
        
        try:
            import sqlite3
            conn = sqlite3.connect(cache_mgr.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM daily_cache WHERE date_str = ?", (target_date,))
            rows = cursor.rowcount
            conn.commit()
            conn.close()
            print(f"Deleted {rows} rows from daily_cache.")
        except Exception as e:
            print(f"Error deleting cache: {e}")
    else:
        print(f"Cache for {target_date} NOT found (Empty).")
        
    # Double Check
    data_after = cache_mgr.get_daily_data(target_date)
    if not data_after:
        print("Verification: Cache is EMPTY/GONE.")
    else:
        print(f"Verification FAILED: Still has {len(data_after)} items.")

if __name__ == "__main__":
    fix_2408_cache()
