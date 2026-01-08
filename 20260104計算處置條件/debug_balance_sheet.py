from core.fetcher import StockFetcher
import pandas as pd

def try_generic_balance_sheet():
    fetcher = StockFetcher()
    client = fetcher.fm_client
    
    print("Attempting to fetch TaiwanStockBalanceSheet directly...")
    try:
        df = client.api.get_data(
            dataset="TaiwanStockBalanceSheet",
            data_id="3006",
            start_date="2024-01-01"
        )
        if df is not None and not df.empty:
            print("Success! Columns:", df.columns)
            print("Origin Names:", df['origin_name'].unique()[:20])
            
            # Look for Capital
            mask = df['origin_name'].str.contains('股本', na=False)
            if mask.any():
                print("Found Capital keys:", df[mask]['origin_name'].unique())
                print(df[mask].tail())
        else:
            print("TaiwanStockBalanceSheet returned empty.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    try_generic_balance_sheet()
