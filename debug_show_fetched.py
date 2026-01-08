
import sys
import os
import json
from datetime import datetime

# Add Valid Path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from core.scraper_attention import AttentionScraper

def main():
    target_date = datetime.now()
    print(f"--- 正在擷取 {target_date.strftime('%Y-%m-%d')} 的原始資料 ---")
    
    try:
        items = AttentionScraper.fetch_data(target_date)
        print(f"共擷取到 {len(items)} 筆資料:\n")
        
        for i, item in enumerate(items, 1):
            code = item.get("code", "")
            name = item.get("name", "")
            reason = item.get("reason", "")
            print(f"[{i}] {code} {name}")
            print(f"    原因: {reason}")
            print("-" * 40)
            
    except Exception as e:
        print(f"擷取錯誤: {e}")

if __name__ == "__main__":
    main()
