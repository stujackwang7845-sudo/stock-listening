from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils
from datetime import datetime

def check_stocks():
    fetcher = StockFetcher()
    parser = StockParser()
    
    targets = ['3701', '5498', '2413', '3354', '3444']
    
    print("Fetching Disposition Lists...")
    twse_disp = fetcher.fetch_twse_disposition(None)
    parsed_twse = parser.parse_twse_disposition(twse_disp)
    tpex_disp = fetcher.fetch_tpex_disposition(None)
    parsed_tpex = parser.parse_tpex_disposition(tpex_disp)
    
    disp_map = {}
    for item in parsed_twse: disp_map[item['code']] = item
    for item in parsed_tpex: disp_map[item['code']] = item
    
    today_dt = datetime(2026, 1, 3).date() # Simulating Today
    print(f"Simulation Today: {today_dt}")

    test_dates = [
        datetime(2025, 12, 30).date(),
        datetime(2025, 12, 31).date(),
        datetime(2026, 1, 1).date(),
        datetime(2026, 1, 2).date()
    ]
    
    for code in targets:
        print(f"\nChecking {code}...")
        if code not in disp_map:
            print("  NOT FOUND in Disposition List.")
            continue
            
        data = disp_map[code]
        period = data.get('period', '')
        print(f"  Raw Period: '{period}'")
        
        start_dt = DateUtils.parse_period_start(period)
        print(f"  Parsed Start: {start_dt}")
        
        if not start_dt:
            print("  FAILED TO PARSE START DATE.")
            continue
            
        start_date = start_dt.date()
        
        # Test Reset Logic
        for d in test_dates:
            should_reset = False
            if start_date <= today_dt:
                if d < start_date:
                    should_reset = True
            
            print(f"    Hist Date {d} vs Start {start_date}: Reset? {should_reset}")

if __name__ == "__main__":
    check_stocks()
