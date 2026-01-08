
import sys
import logging
import requests
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils

# Setup logging
logging.basicConfig(level=logging.DEBUG)

def test_fetch():
    fetcher = StockFetcher()
    parser = StockParser()
    
    # 1. Test Specific Date: 2025/12/31 (Year 114)
    test_date = "20251231"
    
    # Define Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json"
    }

    # Futures (TAIFEX) Probe

    print("\n[Futures Probe]")
    
    # TAIFEX OpenAPI
    # Try underlying securities of stock futures
    # Endpoint might be /Daily... or something.
    # Ref: https://openapi.taifex.com.tw/v1/SSFLists (Hypothetical)
    
    # Official list of endpoints is at https://openapi.taifex.com.tw/
    # Let's try to hit the daily market report for equity futures
    # /DailyForeignExchangeRates is known good.
    
    try:
        url = "https://openapi.taifex.com.tw/v1/SSFLists"
        print(f"Testing SSFLists: {url}")
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
             js = res.json()
             print(f"  Count: {len(js)}")
             if len(js) > 0:
                 print(f"  Sample: {js[0]}")
        else:
             print(f"  Status: {res.status_code}")
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    test_fetch()
