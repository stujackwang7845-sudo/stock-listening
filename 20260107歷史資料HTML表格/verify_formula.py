
import pandas as pd
from core.fetcher import StockFetcher

def verify():
    fetcher = StockFetcher()
    # Fetch 2408 history
    # User focus: "2408"
    print("Fetching 2408 history...")
    df, shares = fetcher.fetch_stock_history("2408", "上市", period="180d")
    
    if df is None or df.empty:
        print("No data.")
        return

    # Debug Last 10 rows
    print("\nLast 7 Days Data:")
    print(df.tail(7)[['Close', 'Open']])
    
    # Identify T (Last Row)
    t_row = df.iloc[-1]
    t_date = df.index[-1]
    price_t = t_row['Close']
    
    # Identify T-1
    t_1_row = df.iloc[-2]
    t_1_date = df.index[-2]
    price_t_1 = t_1_row['Close']
    
    print(f"\nT Date: {t_date.strftime('%Y-%m-%d')} Price: {price_t}")
    print(f"T-1 Date: {t_1_date.strftime('%Y-%m-%d')} Price: {price_t_1}")
    
    # Scenario A: Limit for T (Based on T-1)
    # Sum ROC 5 should be T-1 to T-5
    sum_roc_a = 0.0
    print("\n--- Scenario A: Check Exclusion for T (Verify current status) ---")
    print(f"Base Price (T-1): {price_t_1}")
    for i in range(1, 6): # T-1 to T-5
        curr = df.iloc[-(i)]['Close']
        prev = df.iloc[-(i+1)]['Close']
        roc = (curr/prev - 1)*100
        sum_roc_a += roc
        d = df.index[-(i)]
        print(f"Date {d.strftime('%m/%d')}: {roc:.2f}%")
        
    print(f"Sum ROC 5 (T-1..T-5): {sum_roc_a:.2f}%")
    limit_rate_a = 25.0
    rem_a = limit_rate_a - sum_roc_a
    limit_price_a = price_t_1 * (1 + rem_a/100)
    print(f"Limit Formula: {price_t_1} * (1 + (25 - {sum_roc_a:.2f})/100)")
    print(f"Derived Limit Price for T: {limit_price_a:.2f}")
    print(f"Actual Price T: {price_t}. Excluded? {price_t < limit_price_a}")
    
    # Scenario B: Limit for T+1 (Next Day, using T data)
    # Sum ROC 5 includes T
    sum_roc_b = 0.0
    print("\n--- Scenario B: Check Exclusion for Next Day (T+1) ---")
    print(f"Base Price (T): {price_t}")
    for i in range(0, 5): # T to T-4
        curr = df.iloc[-(i+1)]['Close']
        prev = df.iloc[-(i+2)]['Close']
        roc = (curr/prev - 1)*100
        sum_roc_b += roc
        d = df.index[-(i+1)]
        print(f"Date {d.strftime('%m/%d')}: {roc:.2f}%")
        
    print(f"Sum ROC 5 (T..T-4): {sum_roc_b:.2f}%")
    rem_b = limit_rate_a - sum_roc_b
    limit_price_b = price_t * (1 + rem_b/100)
    print(f"Limit Formula: {price_t} * (1 + (25 - {sum_roc_b:.2f})/100)")
    print(f"Derived Limit Price for T+1: {limit_price_b:.2f}")

if __name__ == "__main__":
    verify()
