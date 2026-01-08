
from core.fetcher import StockFetcher
from core.tick_utils import TickUtils
import math

def debug_3030():
    code = "3030"
    fetcher = StockFetcher()
    
    print(f"Fetching data for {code}...")
    df, shares = fetcher.fetch_stock_history(code, "上市", period="5d")
    
    if df is not None:
        last = df.iloc[-1]
        close = last['Close']
        per = last.get('PER', 1)
        pbr = last.get('PBR', 1)
        
        print("-" * 30)
        print(f"Stock: {code}")
        print(f"Close: {close}")
        print(f"PER: {per}")
        print(f"PBR: {pbr}")
        
        # Calc Target
        # PER >= 60
        t_per = 0
        if per > 0:
            t_per = close * (60.0 / per)
            
        # PBR >= 6
        t_pbr = 0
        if pbr > 0:
            t_pbr = close * (6.0 / pbr)
            
        print(f"Target PER: {t_per}")
        print(f"Target PBR: {t_pbr}")
        
        raw_t = max(t_per, t_pbr)
        
        tick = TickUtils.get_tick_size(raw_t)
        steps = math.ceil((raw_t - 0.0000001) / tick)
        final_t = round(steps * tick, 2)
        
        print(f"Tick Size: {tick}")
        print(f"Final Target: {final_t}")
        
        # Volume
        print(f"Shares: {shares}")
        req_shares = math.ceil(shares * 0.05)
        req_zhang = math.ceil(req_shares / 1000)
        print(f"Req Vol: {req_zhang}")
        
if __name__ == "__main__":
    debug_3030()
