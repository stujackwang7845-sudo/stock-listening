
from core.fetcher import StockFetcher
from core.tick_utils import TickUtils
import math

def debug_4967():
    code = "4967"
    fetcher = StockFetcher()
    
    print(f"Fetching data for {code}...")
    df, shares = fetcher.fetch_stock_history(code, "上市", period="10d")
    
    if df is None or df.empty:
        print("No data.")
        return

    print("Columns:", df.columns)
    last_row = df.iloc[-1]
    last_close = last_row['Close']
    per = last_row.get('PER', 0)
    pbr = last_row.get('PBR', 0)
    
    print(f"Last Close: {last_close}")
    print(f"PER: {per}")
    print(f"PBR: {pbr}")
    
    # Logic Re-simulation
    thresh_per = 60
    thresh_pbr = 6
    
    # 1. PER Target
    target_price_per = 0
    if per > 0:
        # EPS = Price / PER
        eps = last_close / per
        print(f"Implied EPS: {eps:.4f}")
        # Target = 60 * EPS
        target_price_per = last_close * (thresh_per / per)
        print(f"Target Price (PER >= 60): {target_price_per:.4f}")
        
    # 2. PBR Target
    target_price_pbr = 0
    if pbr > 0:
        # BVPS = Price / PBR
        bvps = last_close / pbr
        print(f"Implied BVPS: {bvps:.4f}")
        target_price_pbr = last_close * (thresh_pbr / pbr)
        print(f"Target Price (PBR >= 6): {target_price_pbr:.4f}")
        
    target_6 = max(target_price_per, target_price_pbr)
    print(f"Raw Max Target: {target_6:.4f}")
    
    # Round logic
    tick = TickUtils.get_tick_size(target_6)
    steps = math.ceil((target_6 - 0.0000001) / tick)
    final_target = round(steps * tick, 2)
    print(f"Rounded Target: {final_target}")
    
    # Volume Calc
    req_vol_zhang = 0
    print(f"Shares Outstanding: {shares}")
    if shares > 0:
        req_vol_shares = math.ceil(shares * (5.0 / 100.0))
        req_vol_zhang = math.ceil(req_vol_shares / 1000)
    print("-" * 30)
    print(f"Ref Date: {last_row.name}")
    print(f"Close: {last_close}")
    print(f"PER: {per}")
    print(f"PBR: {pbr}")
    print(f"Target PER (60*EPS): {target_price_per:.4f}")
    print(f"Target PBR (6*BVPS): {target_price_pbr:.4f}")
    print(f"Raw Max: {target_6:.4f}")
    print(f"Final Target: {final_target}")
    print(f"Vol: {max(3000, req_vol_zhang if shares > 0 else 0)}")

if __name__ == "__main__":
    debug_4967()
