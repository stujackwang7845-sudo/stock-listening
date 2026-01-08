
from core.fetcher import StockFetcher
import math

def check_8054():
    fetcher = StockFetcher()
    c = "8054"
    try:
        _, shares = fetcher.fetch_stock_history(c, "上市", period="1d")
        req_vol_shares = math.ceil(shares * 0.05)
        req_zhang = math.ceil(req_vol_shares / 1000)
        print(f"{c}: Shares={shares}, 5% Vol={req_zhang}")
    except: pass
    
    # Check close history for 4967, 3030 to find 145.0
    print("\nChecking History for 145.0:")
    for code in ["4967", "3030", "8054", "3552"]:
        df, _ = fetcher.fetch_stock_history(code, "上市", period="10d")
        if df is not None:
            closes = df['Close'].tolist()
            dates = df.index.astype(str).tolist()
            print(f"{code} Closes: {list(zip(dates, closes))}")

if __name__ == "__main__":
    check_8054()
