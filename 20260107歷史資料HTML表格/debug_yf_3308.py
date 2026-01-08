
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def test_fetch(stock_id, target_date):
    print(f"--- Fetching {stock_id} for {target_date} ---")
    
    # Calculate End Date
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    dt_next = dt + timedelta(days=1)
    end_date = dt_next.strftime("%Y-%m-%d")
    
    tickers = [f"{stock_id}.TW", f"{stock_id}.TWO"]
    
    for ticker in tickers:
        print(f"\nTrying {ticker}...")
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(interval="1m", start=target_date, end=end_date)
            
            if df.empty:
                print("Result: Empty DataFrame")
                continue
                
            print(f"Result: {len(df)} rows")
            print("Timezone:", df.index.dtype)
            if df.index.tz is not None:
                 print("TZ Info:", df.index.tz)
                 # Convert to TW
                 df_tw = df.copy()
                 df_tw.index = df_tw.index.tz_convert('Asia/Taipei')
                 print("First Row (TW):", df_tw.index[0], df_tw.iloc[0]['Close'])
                 print("Last Row (TW):", df_tw.index[-1], df_tw.iloc[-1]['Close'])
            else:
                 print("First Row (Naive):", df.index[0], df.iloc[0]['Close'])
                 print("Last Row (Naive):", df.index[-1], df.iloc[-1]['Close'])
                 
            # Check price range
            print(f"High: {df['High'].max()}, Low: {df['Low'].min()}")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    # Test 3308 for 2026-01-05 (Monday)
    test_fetch("3308", "2026-01-05")
    
    # Test 4971 for 2025-12-31 (Just to compare)
    # test_fetch("4971", "2025-12-31")
