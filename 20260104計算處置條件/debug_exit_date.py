
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils
from datetime import datetime

def test_fetch_and_parse():
    fetcher = StockFetcher()
    parser = StockParser()
    
    # 1. Fetch Disposal Data
    # Try fetching TWSE and TPEX
    print("Fetching TWSE Disposition...")
    twse_disp = fetcher.fetch_twse_disposition(None)
    parsed_twse = parser.parse_twse_disposition(twse_disp)
    
    print("Fetching TPEX Disposition...")
    # Need a date for TPEX, usually today or recent trading day
    last_trading_day = DateUtils.get_last_trading_day().strftime("%Y%m%d")
    tpex_disp = fetcher.fetch_tpex_disposition(last_trading_day)
    parsed_tpex = parser.parse_tpex_disposition(tpex_disp)
    
    all_disp = parsed_twse + parsed_tpex
    
    # Find 4971 or any stock with "Period"
    target_code = "4971" 
    found = False
    
    for item in all_disp:
        code = item.get("code")
        period = item.get("period")
        
        if code == target_code:
            found = True
            print(f"\n[FOUND TARGET {target_code}]")
            print(f"Raw Item: {item}")
            print(f"Period String: '{period}'")
            
            end_dt = DateUtils.parse_period_end(period)
            print(f"Parsed End Date: {end_dt}")
            
            if end_dt:
                print(f"End Date Object: {end_dt}")
            else:
                print("FAILED TO PARSE END DATE")
                
    if not found:
        print(f"\nTarget {target_code} not found in disposal list. Listing first 5 items with period:")
        for i, item in enumerate(all_disp[:5]):
            print(f"{item['code']}: {item.get('period')}")

if __name__ == "__main__":
    test_fetch_and_parse()
