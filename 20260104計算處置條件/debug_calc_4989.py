
from core.fetcher import StockFetcher
from core.tick_utils import TickUtils
import math

def debug_4989():
    code = "4989"
    fetcher = StockFetcher()
    
    # Try fetching to determine source and get data
    # Try TWSE first, then TPEX
    print(f"Fetching data for {code}...")
    source = "上市"
    df, shares = fetcher.fetch_stock_history(code, "上市", period="10d")
    
    if df is None or df.empty:
        source = "上櫃"
        df, shares = fetcher.fetch_stock_history(code, "上櫃", period="10d")
        
    if df is None or df.empty:
        print("Failed to fetch data for 4989.")
        return

    print(f"Source: {source}")
    closes = df['Close'].tolist()
    last_close = closes[-1]
    print(f"Last Close: {last_close}")
    print(f"History Closes: {closes}")
    
    # 6-day Accumulation
    # Need last 5 days ROC + Next Day ROC
    # We need to simulate 'tomorrow' being the 6th day.
    # Formula: Sum(ROC_1..5) + ROC_6 >= Threshold
    
    # Get last 5 ROCs
    # history: [d-5, d-4, d-3, d-2, d-1, d-0(Last)]
    # ROCs are: (d-4 vs d-5), (d-3 vs d-4), ... (d-0 vs d-1)
    # Total 5 ROCs already occurred?
    # Wait, "6日累積" means checking a window of 6 days.
    # Does it mean Today is Day 6? Or Tomorrow is Day 6?
    # Usually for prediction: "Tomorrow triggers if..." means Tomorrow is Day 6.
    # So we sum ROCs from Day 1 to Day 5 (which is Today).
    # Then calculate needed ROC for Tomorrow (Day 6).
    
    # Let's count ROCs available
    # We need 5 past ROCs.
    if len(closes) < 6:
        print("Not enough data.")
        return
        
    current_sum_roc = 0.0
    print("\nCalculating ROCs (Day 1 to 5):")
    for i in range(5):
        # i=0 -> Today vs Yesterday (-1 vs -2)
        # i=1 -> Yesterday vs DayBefore (-2 vs -3)
        curr_p = closes[-(i+1)]
        prev_p = closes[-(i+2)]
        roc = ((curr_p - prev_p) / prev_p) * 100
        print(f"  Day -{i}: {curr_p} vs {prev_p} -> {roc:.2f}%")
        current_sum_roc += roc
        
    print(f"Current Sum ROC (5 days): {current_sum_roc:.4f}%")
    
    # Thresholds
    thresholds = [25, 27]
    
    for th in thresholds:
        print(f"\n--- Threshold {th}% ---")
        required_next_roc = th - current_sum_roc
        print(f"Required Next ROC: {required_next_roc:.4f}%")
        
        raw_target = last_close * (1 + (required_next_roc / 100.0))
        print(f"Raw Target Price: {raw_target:.6f}")
        
        tick = TickUtils.get_tick_size(raw_target)
        print(f"Tick Size at {raw_target:.2f}: {tick}")
        
        # Ceiling calculation
        steps = math.ceil((raw_target - 0.0000001) / tick)
        target = round(steps * tick, 2)
        
        print(f"Initial Target (Ceiling): {target}")

        # Mimic Safety Loop
        for _ in range(10): 
            new_roc = ((target - last_close) / last_close) * 100
            total_roc = current_sum_roc + new_roc
            rounded_total = round(total_roc, 2)
            
            print(f"  Check {target}: Total={total_roc:.4f}% -> Round={rounded_total:.2f}% > {th}%? {rounded_total > th}")
            
            if rounded_total > th:
                break
            
            # Bump
            tick = TickUtils.get_tick_size(target)
            target = round(target + tick, 2)
            
        print(f"Final Safe Target: {target}")

if __name__ == "__main__":
    debug_4989()
