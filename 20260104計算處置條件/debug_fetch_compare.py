
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils

def check_fetch():
    fetcher = StockFetcher()
    parser = StockParser()
    date_str = "20260102"
    
    print(f"Fetching TWSE for {date_str}...")
    twse_data = fetcher.fetch_twse_attention(date_str)
    parsed_twse = parser.parse_twse_attention(twse_data)
    print(f"Parsed TWSE Count: {len(parsed_twse)}")
    
    found_codes = [x['code'] for x in parsed_twse]
    print(f"TWSE Codes: {found_codes}")
    
    if '2408' in found_codes:
        print("SUCCESS: 2408 found in TWSE list.")
        item = next(x for x in parsed_twse if x['code'] == '2408')
        print(f"Item: {item}")
    else:
        print("FAILURE: 2408 NOT found in TWSE list.")

if __name__ == "__main__":
    check_fetch()
