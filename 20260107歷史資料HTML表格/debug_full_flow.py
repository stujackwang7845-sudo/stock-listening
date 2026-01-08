
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import numpy as np

def test_logic(stock_id, target_date):
    print(f"--- Testing Logic for {stock_id} on {target_date} ---")
    
    ticker = f"{stock_id}.TWO" # Try TWO
    start_lookback = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    end_date = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"Fetching Daily from {start_lookback} to {end_date}")
    
    stock = yf.Ticker(ticker)
    try:
        # 1. Fetch Daily
        d_daily = stock.history(interval="1d", start=start_lookback, end=end_date)
        print("Daily Data:\n", d_daily.tail())
        
        target_date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
        d_daily_dates = d_daily.index.date
        
        official_close = None
        p_close = None
        
        if target_date_obj in d_daily_dates:
            print("Target date found in Daily.")
            locs = np.where(d_daily_dates == target_date_obj)[0]
            pos = locs[0]
            
            official_close = d_daily.iloc[pos]['Close']
            print(f"Official Close: {official_close}")
            
            if pos > 0:
                p_close = d_daily.iloc[pos-1]['Close']
                print(f"Previous Close: {p_close}")
            else:
                print("No previous row for PrevClose.")
        else:
            print("Target date NOT found in Daily.")

        # 2. Fetch Minute
        d = stock.history(interval="1m", start=target_date, end=end_date)
        print(f"Minute Data Last: {d.iloc[-1]['Close']} at {d.index[-1]}")
        
        # 3. Patch Logic
        if official_close and not d.empty:
            last_min = d['Close'].iloc[-1]
            if abs(official_close - last_min) > 0.01:
                print(f"Discrepancy: {last_min} vs {official_close}. Patching...")
            else:
                print("No discrepancy.")
                
        # 4. Calc Change
        if p_close:
            change = (official_close - p_close) / p_close * 100
            print(f"Calculated Change: {change:.2f}%")
        else:
            print("Cannot calc change without PrevClose")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_logic("8299", "2026-01-05")
