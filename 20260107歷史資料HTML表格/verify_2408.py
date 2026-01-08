
import sys
import os
import pandas as pd
from datetime import datetime, timedelta
sys.path.append(os.getcwd())

from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import DateUtils, ClauseParser
from core.cache import CacheManager

def verify_2408():
    stock_id = "2408"
    print(f"=== Verification for {stock_id} ===")
    
    # 1. Fetch Price History
    fetcher = StockFetcher()
    df, shares = fetcher.fetch_stock_history(stock_id, period="180d")
    
    if df is None or df.empty:
        print("No price data.")
        return

    # User says "Current check" implies specific date? 
    # Usually "Today" or specific date.
    # User User's previous context was 12/23. Let's assume standard "Latest" or check 12/23 if implied.
    # "2408 前5日漲幅應該是9.4%". Let's check the latest date available in DF first.
    # t_dt = df.index[-1]
    
    # Update: User likely refers to 12/23 context
    # t_dt = pd.Timestamp("2025-12-23")
    
    # User requested "Latest Data"
    t_dt = df.index[-1]
    
    print(f"Target Date (Latest in DB): {t_dt.strftime('%Y-%m-%d')}")
    
    # Calculate Sum ROC 5
    # T is last row. T-1 is last-1.
    # Sum ROC 5 = Sum of returns from T-5 to T-1.
    if t_dt not in df.index:
        print("Date not in data")
        return
        
    pos_t = df.index.get_loc(t_dt)
    
    sum_roc = 0.0
    print("\n--- Sum ROC 5 Calculation (Including T) ---")
    for i in range(0, 5): # 0,1,2,3,4. (T to T-4)
        # Logic: T (pos_t) vs T-1.
        
        curr_idx = pos_t - i
        prev_idx = curr_idx - 1
        
        if prev_idx < 0:
            print("Not enough data")
            break
            
        d_curr = df.index[curr_idx]
        p_curr = df.iloc[curr_idx]['Close']
        p_prev = df.iloc[prev_idx]['Close']
        
        roc = (p_curr / p_prev - 1) * 100
        sum_roc += roc
        print(f"Day T-{i} ({d_curr.strftime('%Y-%m-%d')}): {p_curr} / {p_prev} - 1 = {roc:.2f}%")
        
    print(f"Sum ROC 5: {sum_roc:.2f}% (User expects 9.4%)")
    
    # 2. Check History (Live Check via Website)
    print("\n--- History Check (Live from Website) ---")
    today = datetime.now()
    d90_ago = today - timedelta(days=90)
    s_date = d90_ago.strftime("%Y%m%d")
    e_date = today.strftime("%Y%m%d")
    
    # 30d Window
    cutoff_30 = (today - timedelta(days=30)).strftime("%Y%m%d")
    hist_att = fetcher.fetch_stock_attention_history(stock_id, s_date, e_date, "上市")
    
    has_c1_30 = False
    if hist_att:
        rows = get_rows(hist_att)
        for r in rows:
            ad_date = parse_date(r)
            if ad_date and ad_date >= cutoff_30:
                if check_reason(r, ["第一款", "第1款", "一"]):
                    has_c1_30 = True
                    print(f"Found Clause 1 on {ad_date}: {r}")
                    
    # 60d Window (Clause 2 Disp)
    cutoff_60 = (today - timedelta(days=60)).strftime("%Y%m%d")
    hist_disp = fetcher.fetch_stock_disposition_history(stock_id, s_date, e_date, "上市")
    
    has_c2_60 = False
    if hist_disp:
        rows = get_rows(hist_disp)
        for r in rows:
            ad_date = parse_date(r)
            if ad_date and ad_date >= cutoff_60:
                # Check Reason: Clause 2
                if check_reason(r, ["第二款", "第2款", "二"]):
                    has_c2_60 = True
                    print(f"Found Clause 2 Disp on {ad_date}: {r}")

    print(f"Has Clause 1 (30d): {has_c1_30}")
    print(f"Has Clause 2 Disp (60d): {has_c2_60}")

def get_rows(data):
    if isinstance(data, dict):
        if 'data' in data: return data['data']
        elif 'tables' in data and len(data['tables']) > 0: return data['tables'][0].get('data', [])
    elif isinstance(data, list): return data
    return []

def parse_date(row):
    found = ""
    if isinstance(row, list) and len(row) > 1: found = str(row[1])
    elif isinstance(row, dict): found = str(row.get("Date", ""))
    
    if "/" in found:
        ps = found.split('/')
        if len(ps) == 3:
            return f"{int(ps[0])+1911}{ps[1]}{ps[2]}"
    return ""

def check_reason(row, kw_list):
    r_str = str(row)
    for k in kw_list:
        if k in r_str: return True
    if "款" in r_str and ("一" in kw_list and "一" in r_str): return True
    if "款" in r_str and ("二" in kw_list and "二" in r_str): return True
    return False

if __name__ == "__main__":
    verify_2408()
