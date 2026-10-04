"""
清理歷史紀錄中的無效資料
1. 移除非 4 碼股票
2. 移除未來日期的資料
"""
import json
from datetime import datetime

FILE_PATH = "listening_history.json"

def clean_history():
    # Load
    try:
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        print("無法載入歷史紀錄")
        return
    
    original_count = len(history)
    now = datetime.now()
    today = now.date()
    market_close_hour = 17  # 5 PM (After 下午 5 點才會有當天資料)
    
    # Determine cutoff date: if before 5PM today, use yesterday; otherwise use today
    if now.hour < market_close_hour:
        # Before market close: only keep yesterday or earlier
        from datetime import timedelta
        cutoff_date = today - timedelta(days=1)
    else:
        # After market close: can keep today's data
        cutoff_date = today
    
    # Filter
    cleaned = []
    removed_codes = set()
    removed_dates = set()
    
    for record in history:
        code = str(record.get("code", ""))
        date_str = record.get("date", "")
        
        # Check 1: Only 4-digit codes
        if len(code) != 4:
            removed_codes.add(code)
            continue
        
        # Check 2: No future dates (including today if before market close)
        try:
            record_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if record_date > cutoff_date:
                removed_dates.add(date_str)
                continue
        except:
            continue
        
        # Valid record
        cleaned.append(record)
    
    # Save
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    
    # Report
    removed_count = original_count - len(cleaned)
    print(f"清理完成！")
    print(f"原始紀錄數: {original_count}")
    print(f"清理後紀錄數: {len(cleaned)}")
    print(f"移除紀錄數: {removed_count}")
    
    if removed_codes:
        print(f"\n移除的非 4 碼股票: {', '.join(sorted(removed_codes))}")
    
    if removed_dates:
        print(f"\n移除的未來日期: {', '.join(sorted(removed_dates))}")

if __name__ == "__main__":
    clean_history()
