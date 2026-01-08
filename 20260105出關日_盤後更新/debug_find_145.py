
from core.fetcher import StockFetcher

def find_stock():
    codes = ["8054", "3030", "3552", "4967"]
    fetcher = StockFetcher()
    
    for c in codes:
        df, _ = fetcher.fetch_stock_history(c, "上市", period="5d") # Try Listed
        if df is None:
             df, _ = fetcher.fetch_stock_history(c, "上櫃", period="5d") # Try OTC
        
        if df is not None and not df.empty:
            last = df.iloc[-1]
            print(f"{c}: Close={last['Close']} Date={last.name}")
        else:
            print(f"{c}: No Data")

if __name__ == "__main__":
    find_stock()
