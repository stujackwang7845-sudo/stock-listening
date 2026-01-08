from core.fetcher import StockFetcher
import pandas as pd

def verify():
    fetcher = StockFetcher()
    code = "3006"
    print(f"Fetching {code} using FinMind...")
    
    # 1. First Fetch (Should hit API)
    df, shares = fetcher.fetch_stock_history(code)
    
    msg = []
    if df is not None:
        msg.append("\n--- Data Frame ---")
        msg.append(str(df.tail()))
        msg.append(f"\nShares Outstanding: {shares}")
        
        if 'Volume' in df.columns:
            vols = df['Volume'].tolist()
            if len(vols) >= 59:
                 sum_59 = sum(vols[-59:])
                 msg.append(f"Sum Vol 59: {sum_59:,.0f}")
                 
    # 2. Second Fetch (Cache Test)
    start = pd.Timestamp.now()
    df2, shares2 = fetcher.fetch_stock_history(code)
    end = pd.Timestamp.now()
    msg.append(f"Time taken: {(end-start).total_seconds():.4f}s")
    
    if df2 is not None:
        msg.append(f"Df1 Shape: {df.shape}, Df2 Shape: {df2.shape}")
        try:
            pd.testing.assert_frame_equal(df, df2, check_dtype=False)
            msg.append("Cache Diff: True (Frames Validated)")
        except Exception as e:
            msg.append(f"Cache Diff: False (Mismatch): {e}")

    with open("verification_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(msg))
    print("Verification complete. Results written to verification_result.txt")

if __name__ == "__main__":
    verify()
