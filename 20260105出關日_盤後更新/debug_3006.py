
from core.cache import CacheManager
from core.parser import StockParser
from core.predictor import DispositionPredictor
from core.utils import ClauseParser, DateUtils
import datetime as dt

def debug_3006():
    cm = CacheManager()
    today = dt.datetime(2026, 1, 3) # Force anchor if needed, or use now
    today = dt.datetime.now()
    
    code = "3006"
    history_items = []
    
    print(f"\n=== Debugging 3006 (Today: {today.strftime('%Y-%m-%d')}) ===")
    print(f"{'Date':<10} | {'Found':<5} | {'C1':<5} | {'Any':<5} | {'Raw Reason'}")
    print("-" * 80)
    
    # Check last 10 trading days strictly to see streak
    days_to_check = []
    curr = today
    while len(days_to_check) < 10:
         curr -= dt.timedelta(days=1)
         if DateUtils.is_trading_day(curr):
             days_to_check.insert(0, curr)
             
    H = []
    
    for d in days_to_check:
        d_str = d.strftime("%Y%m%d")
        data = cm.get_daily_data(d_str)
        
        found = False
        is_c1 = False
        is_any = False
        reason = ""
        
        if data:
            target = next((x for x in data if x.get("code") == code), None)
            if target:
                found = True
                reason = str(target.get("reason", ""))
                c_str = ClauseParser.parse_clauses(reason)
                is_c1 = "一" in c_str
                is_any = len(c_str) > 0
                
        print(f"{d_str:<10} | {str(found):<5} | {str(is_c1):<5} | {str(is_any):<5} | {reason[:40]}")
        
        h_item = {"is_clause1": is_c1, "is_any": is_any}
        history_items.append(h_item)
        H.append({"any": is_any})

    needed_c1, needed_any = DispositionPredictor.get_status_counts(history_items)
    
    # Calculate Streak manually
    streak = 0
    for x in reversed(H):
        if x["any"]: streak += 1
        else: break
        
    print("-" * 80)
    print(f"Needed C1: {needed_c1}")
    print(f"Needed Any: {needed_any}")
    print(f"Calculated Any Streak: {streak}")
    
    # Calculate Accum
    last_9 = H[-9:]
    hits = sum(1 for x in last_9 if x["any"])
    print(f"Accum Hits (Last 9): {hits}")
    print(f"Needed Accum: {max(1, 6 - hits)}")

if __name__ == "__main__":
    debug_3006()
