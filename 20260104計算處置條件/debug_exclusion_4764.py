
import sys
import os
import pandas as pd
from datetime import datetime, timedelta
sys.path.append(os.getcwd())

from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils

def check_exclusion_4764():
    fetcher = StockFetcher()
    parser = StockParser()
    
    stock_id = "4764"
    target_dates = ["2025-12-23", "2025-12-24"]
    
    print(f"=== Verification for Exclusion Logic: {stock_id} ===")
    
    # 1. Fetch Price History (for Percentage Calculation)
    # We need data covering T, T-1, T-5. Approx 3 months back for safe measure
    print("Fetching Price History...")
    df, shares = fetcher.fetch_stock_history(stock_id, period="180d") # Fetch ample history
    if df is None or df.empty:
        print("Error: No price data found.")
        return

    # 2. Fetch Attention/Disposition History (The "Past 30/60 days" check)
    # We need to scan past attention notices to see if Clause 1 happened.
    # This acts as a mock "Database" of attention events.
    # For efficiency in this debug, we'll fetch a range around the target.
    # But usually we rely on cache. Let's just mock the capability or fetch specific dates if needed.
    # Actually, to verify strictly, we need to know if "Clause 1" happened in [T-30, T].
    
    # For this debug, I will assume we can fetch daily attention for the relevant window.
    # Let's verify what dates we need.
    # T=12/23. T-30 approx Nov 10.
    
    # Let's fetch Top 50 attention list for Nov-Dec to build a mini-db
    attention_db = {} # date -> code -> clauses
    
    print("Fetching Attention Data (Simulating History)...")
    # Fetching a few key dates or range? Fetching range is slow with current fetcher (one day at a time).
    # I'll implement a 'smart check' or just manual check for 4764's attention history if possible.
    # OR, I will trust the price logic first and assume "Clause 1 Exists" to see the price calculation.
    # User said: "Check past 30 days... IF YES, then check price."
    
    # Let's fetch 4764's attention history *from FinMind* or just Iterate days?
    # Our fetcher uses TWSE/TPEX Daily Notice.
    # Let's iterate T-30 to T.
    
    # optimization: check if 4764 is in cache for those days?
    # For now, let's focus on the PRICE calculation part of exclusion, 
    # and "simulate" the Clause 1 detection (or force it True to test the price exclusion).
    
    # Wait, the user wants me to VERIFY using 2025/12/23 data. 
    # This implies I should actually check if 4764 had Clause 1.
    
    scan_start = datetime(2025, 11, 10)
    scan_end = datetime(2025, 12, 25)

    # Search for ~108.8
    print("Searching for Close ~ 108.8...")
    match = df[(df['Close'] >= 108.5) & (df['Close'] <= 109.5)]
    print(match)
    
    # Check Price Logic First
    target_date_str = "2025-12-23" # The "Future" day (T)
    # We base calc on P_T-1 (12/22) and previous 4 days (Total 5 days history)
    
    # Need index of 12/23 to get Previous Data
    t_dt = pd.Timestamp(target_date_str)
    
    if t_dt in df.index:
        pos_t = df.index.get_loc(t_dt)
        print(f"\n=== User Formula Verification ({target_date_str}) ===")
        
        # T-1
        idx_t_1 = pos_t - 1
        date_t_1 = df.index[idx_t_1]
        price_t_1 = df.iloc[idx_t_1]['Close']
        print(f"基準昨收 (T-1) [{date_t_1.strftime('%Y-%m-%d')}]: {price_t_1}")
        
        # Calculate Sum ROC 5 (Last 5 days returns)
        # Days: T-5, T-4, T-3, T-2, T-1.
        # ROC_i = (P_i / P_i-1 - 1) * 100
        # We need Price History for T-1, T-2, T-3, T-4, T-5, T-6 (base for T-5)
        
        sum_roc = 0
        print("\n--- Sum ROC 5 Calculation ---")
        for i in range(1, 6):
            # Day i counting back from T-1? No, T-1 is the 5th day.
            # Sequence: T-5, T-4, T-3, T-2, T-1.
            # So iter current from T-5 to T-1.
            
            # Loop backwards: i=1 (T-1), i=2 (T-2)... i=5 (T-5)
            curr_idx = pos_t - i
            prev_idx = curr_idx - 1
            
            d_curr = df.index[curr_idx]
            p_curr = df.iloc[curr_idx]['Close']
            p_prev = df.iloc[prev_idx]['Close']
            
            roc = (p_curr / p_prev - 1) * 100
            sum_roc += roc
            print(f"Day T-{i} ({d_curr.strftime('%m/%d')}): {p_curr} / {p_prev} - 1 = {roc:.2f}%")
            
        print(f"Sum ROC 5: {sum_roc:.2f}% (User Target: 7.23%)")
        
        # Limit Calculation
        limit_rate = 25.0 # Listed
        remaining_room = limit_rate - sum_roc
        target_price = price_t_1 * (1 + remaining_room / 100)
        
        print(f"\n--- Target Price Calculation ---")
        print(f"Limit Rate: {limit_rate}%")
        print(f"Remaining Room: {remaining_room:.2f}% ({limit_rate} - {sum_roc:.2f})")
        print(f"Target Price: {price_t_1} * (1 + {remaining_room:.2f}%) = {target_price:.2f}")
        print(f"User Target: 136.02")
        
        # Current Price T Check
        price_t = df.iloc[pos_t]['Close']
        print(f"\n--- Result Check ---")
        print(f"Actual Close (T) [{target_date_str}]: {price_t}")
        print(f"Is Excluded (< {target_price:.2f})? {price_t < target_price}")

    else:
        print("12/23 not in data.")

if __name__ == "__main__":
    check_exclusion_4764()
