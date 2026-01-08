from core.fetcher import StockFetcher
import pandas as pd

def debug_shares():
    fetcher = StockFetcher()
    # List of various stocks: 3006 (Known Good), 2330 (TSMC), 3293 (IGS - OTC), 8069 (OTC), 2603, 3037
    stocks = ['3006', '2330', '3293', '8069', '2603', '3037', '3529', '6187']
    
    print(f"{'Code':<6} | {'Shares (est)':<15} | {'Source'}")
    print("-" * 40)
    
    for code in stocks:
        try:
            shares = fetcher.fm_client.fetch_stock_info(code)
            print(f"{code:<6} | {shares:,.0f} | FinMind")
        except Exception as e:
             print(f"{code:<6} | Error: {e}")

if __name__ == "__main__":
    debug_shares()
