
from core.fetcher import StockFetcher
import math

def check_shares():
    codes = ["8054", "3030", "3552", "4967"]
    fetcher = StockFetcher()
    
    for c in codes:
        try:
            # fetch info
            # Force fetch info from API if cache might be empty
            # fetch_stock_history calls get_stock_info logic
            _, shares = fetcher.fetch_stock_history(c, "上市", period="1d")
            
            req_vol_shares = math.ceil(shares * (5.0 / 100.0))
            req_vol_zhang = math.ceil(req_vol_shares / 1000)
            
            print(f"{c}: Shares={shares}, 5% Vol={req_vol_zhang}")
        except:
            print(f"{c}: Error")

if __name__ == "__main__":
    check_shares()
