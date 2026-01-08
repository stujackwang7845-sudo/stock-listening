import yfinance as yf
import math

def verify_3006():
    code = "3006.TW"
    print(f"Fetching data for {code}...")
    
    ticker = yf.Ticker(code)
    
    # 1. Shares Outstanding
    try:
        info = ticker.info
        shares = info.get('sharesOutstanding', 0)
        print(f"Shares Outstanding (YF): {shares:,} ({shares/1000:,.0f} 張)")
        
        # Calculate Clause 4 target manually
        rate = 10.0 # Listing
        req_vol_shares = math.ceil(shares * (rate / 100.0))
        req_vol_zhang = math.ceil(req_vol_shares / 1000)
        print(f"Clause 4 Target (10%): {req_vol_zhang:,} 張")
        
    except Exception as e:
        print(f"Error fetching info: {e}")
        
    # 2. Volume History
    try:
        df = ticker.history(period="180d")
        if df.empty:
            print("History is empty!")
            return
            
        print("\nLast 5 Records:")
        print(df.tail(5)[['Close', 'Volume']])
        
        volumes = df['Volume'].tolist()
        if len(volumes) >= 59:
            sum_59 = sum(volumes[-59:])
            print(f"\nSum of last 59 days volume: {sum_59:,}")
            
            # Clause 3 Check
            target_vol_shares = math.ceil(sum_59 / 11)
            target_vol_zhang = math.ceil(target_vol_shares / 1000)
            print(f"Clause 3 Target (Sum/11): {target_vol_zhang:,} 張")
        else:
            print(f"Not enough data for 59 days sum. Len: {len(volumes)}")
            
    except Exception as e:
        print(f"Error fetching history: {e}")

if __name__ == "__main__":
    verify_3006()
