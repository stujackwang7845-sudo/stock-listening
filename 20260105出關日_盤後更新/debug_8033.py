
from core.fetcher import StockFetcher
import pandas as pd

def debug_8033():
    code = "8033"
    fetcher = StockFetcher()
    
    print(f"Fetching data for {code}...")
    df, shares = fetcher.fetch_stock_history(code, "上市", period="5d")
    
    if df is not None and not df.empty:
        last = df.iloc[-1]
        close = last['Close']
        per = last.get('PER', 0)
        pbr = last.get('PBR', 0)
        
        print("-" * 30)
        print(f"Stock: {code} (Date: {last.name})")
        print(f"Close: {close}")
        print(f"PER: {per}")
        print(f"PBR: {pbr}")
        
    else:
        print("No data found for 8033.")

if __name__ == "__main__":
    debug_8033()
