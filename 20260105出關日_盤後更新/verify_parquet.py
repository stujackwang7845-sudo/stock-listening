import pandas as pd
import os

parquet_path = r"E:\Vibe Coding\Stock\雙刀戰法\data\market_data.parquet"

def check_parquet():
    if not os.path.exists(parquet_path):
        print(f"File not found: {parquet_path}")
        return

    try:
        print(f"Reading: {parquet_path}")
        df = pd.read_parquet(parquet_path)
        
        print("\n--- DataFrame Info ---")
        print(f"Shape: {df.shape}")
        print(f"Index: {df.index.name} (Type: {type(df.index)})")
        
        print("\n--- Column Analysis ---")
        cols = df.columns
        print(f"Column Type: {type(cols)}")
        if isinstance(cols, pd.MultiIndex):
            print("Detected MultiIndex Columns!")
            print("Levels:", cols.names)
            print("Level 0:", cols.get_level_values(0).unique()[:10])
            print("Level 1:", cols.get_level_values(1).unique()[:10])
        else:
            print("Flat Columns. First 10:", cols[:10].tolist())
            
        print("\n--- Sample Data ---")
        print(df.iloc[-5:, :5])  # Last 5 rows, first 5 cols

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_parquet()
