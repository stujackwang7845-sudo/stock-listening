
from core.fetcher import StockFetcher
import json

def check_2408():
    fetcher = StockFetcher()
    
    # Check 2026-01-02 (Friday)
    date_str = "20260102"
    
    print(f"Fetching TWSE Attention for {date_str}...")
    twse_data = fetcher.fetch_twse_attention(date_str)
    
    found = False
    if twse_data:
        # TWSE format: Check 'title' and 'fields'. Data is usually in 'data' list.
        # Typically looks like: {"data": [["1", "2408", "南亞科", ...], ...]}
        # Or distinct JSON structure.
        
        # Dump full structure to inspect
        # print("TWSE Data Keys:", twse_data.keys())
        
        if "data" in twse_data:
            for row in twse_data["data"]:
                # Row format varies, but code is usually 2nd (index 1) or 1st (index 0) if no index.
                # Usually: [Index, Code, Name, ...]
                # Let's simple string search the row
                if "2408" in str(row):
                    print(f"FOUND 2408 in TWSE for {date_str}: {row}")
                    found = True
                    # Print Clause info which is usually the last few columns
        else:
            print("TWSE 'data' key missing or structure unknown.")
            print(str(twse_data)[:500])
            
    if not found:
        print(f"2408 NOT found in TWSE for {date_str}")
        
    # Just in case, check TPEX (unlikely for 2408)
    # print(f"Fetching TPEX Attention for {date_str}...")
    # tpex_data = fetcher.fetch_tpex_attention(date_str)
    # ...

if __name__ == "__main__":
    check_2408()
