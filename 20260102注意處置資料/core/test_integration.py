
from core.fetcher import StockFetcher
from core.parser import StockParser
import json

def test():
    fetcher = StockFetcher()
    parser = StockParser()
    
    # 1. TWSE Attention
    print("--- TWSE Attention ---")
    data = fetcher.fetch_twse_attention()
    if data:
        parsed = parser.parse_twse_attention(data)
        print(f"Fetch count: {len(parsed)}")
        if parsed:
            print(parsed[0])
    
    # 2. TPEX Attention
    print("\n--- TPEX Attention ---")
    data = fetcher.fetch_tpex_attention()
    if data:
        # Debugging JSON keys
        if len(data) > 0:
            print("Row keys:", data[0].keys())
        parsed = parser.parse_tpex_attention(data)
        print(f"Fetch count: {len(parsed)}")
        if parsed:
            print(parsed[0])

if __name__ == "__main__":
    test()
