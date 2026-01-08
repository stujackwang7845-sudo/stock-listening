
import asyncio
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils

def debug_3092():
    print("Initializing Fetcher and Parser...")
    fetcher = StockFetcher()
    parser = StockParser()
    
    # Check last 10 days
    dates_info = DateUtils.get_market_calendar(past_days=10)
    dates = dates_info["past"] + [dates_info["current"]]
    
    print(f"Checking Attention Data for 3092 in {dates}...")
    
    found_any = False
    for d_str in dates: # e.g. "01/02"
        # Convert "MM/DD" to "YYYYMMDD"
        current_year = "2026"
        if "12/" in d_str: current_year = "2025" 
        
        full_date = f"{current_year}{d_str.replace('/', '')}"
        
        print(f"Fetching {full_date} (TWSE & TPEX)...")
        
        # Determine source? 3092 is TPEX (Stars Technology)
        # But we check both just in case
        
        # TWSE
        twse_raw = fetcher.fetch_twse_attention(full_date)
        twse_items = parser.parse_twse_attention(twse_raw)
        
        # TPEX
        tpex_raw = fetcher.fetch_tpex_attention(full_date)
        tpex_items = parser.parse_tpex_attention(tpex_raw)
        
        all_items = twse_items + tpex_items
        
        # Check if 3092 in results
        hits = [x for x in all_items if x['code'] == '3092']
        if hits:
            print(f"  [FOUND] 3092 in {full_date}: {hits[0].get('reason', '')[:20]}...")
            found_any = True
        else:
            # print(f"  3092 NOT found in {full_date}")
            pass
            
    if not found_any:
        print("3092 NOT found in any attention lists (TWSE/TPEX) for the past 10 days.")
    else:
        print("Done. 3092 was found in some lists.")

if __name__ == "__main__":
    debug_3092()
