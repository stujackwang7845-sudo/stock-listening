from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils

def check_1528():
    fetcher = StockFetcher()
    parser = StockParser()
    
    print("Fetching TWSE Disposition...")
    twse = fetcher.fetch_twse_disposition(None)
    parsed_twse = parser.parse_twse_disposition(twse)
    
    print(f"Found {len(parsed_twse)} TWSE items.")
    for item in parsed_twse:
        if item['code'] == '1528':
            print(f"FOUND 1528 in TWSE: {item}")
            period = item.get('period', '')
            start = DateUtils.parse_period_start(period)
            print(f"  Parsed Start: {start}")
            
    print("Fetching TPEX Disposition...")
    tpex = fetcher.fetch_tpex_disposition(None)
    parsed_tpex = parser.parse_tpex_disposition(tpex)
    for item in parsed_tpex:
        if item['code'] == '1528':
            print(f"FOUND 1528 in TPEX: {item}")
            period = item.get('period', '')
            start = DateUtils.parse_period_start(period)
            print(f"  Parsed Start: {start}")

if __name__ == "__main__":
    check_1528()
