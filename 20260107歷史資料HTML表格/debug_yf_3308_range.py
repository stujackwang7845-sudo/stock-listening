
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def search_price(stock_id, start_date, end_date, target_price):
    print(f"--- Searching {stock_id} in {start_date} to {end_date} for {target_price} ---")
    
    ticker = f"{stock_id}.TW"
    try:
        stock = yf.Ticker(ticker)
        # Fetch daily data for range
        df = stock.history(interval="1d", start=start_date, end=end_date)
        print(df)
        
        # Check if target_price matches any OHLC
        print(f"\nChecking vs {target_price}...")
        for date, row in df.iterrows():
            print(f"{date.date()}: Open={row['Open']}, High={row['High']}, Low={row['Low']}, Close={row['Close']}")
            
        # Fetch minute data for Jan 5
        print("\n--- Minute Data Jan 5 ---")
        df_min = stock.history(interval="1m", start="2026-01-05", end="2026-01-06")
        if not df_min.empty:
            print(f"Jan 5 Close: {df_min.iloc[-1]['Close']}")
        
        # Fetch minute data for Jan 6
        print("\n--- Minute Data Jan 6 (Live) ---")
        df_min_today = stock.history(interval="1m", period="1d")
        if not df_min_today.empty:
            print(f"Jan 6 Latest: {df_min_today.iloc[-1]['Close']}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    search_price("3308", "2025-12-30", "2026-01-07", 21.45)
