"""
清理歷史紀錄中的自動生成註解
移除包含 GitHub Actions 相關文字的註解
"""
import json

FILE_PATH = "listening_history.json"

# 不應該出現的註解關鍵字
INVALID_COMMENT_KEYWORDS = [
    "GitHub Actions",
    "自動偵測",
    "自動每日擷取"
]

def clean_comments():
    # Load
    try:
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        print("無法載入歷史紀錄")
        return
    
    cleaned_count = 0
    
    for record in history:
        comment = record.get("comment", "")
        
        # Check if comment contains any invalid keywords
        should_clean = any(keyword in comment for keyword in INVALID_COMMENT_KEYWORDS)
        
        if should_clean:
            record["comment"] = ""  # Clear the comment
            cleaned_count += 1
    
    # Save
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    # Report
    print(f"清理完成！")
    print(f"總紀錄數: {len(history)}")
    print(f"清除的註解數: {cleaned_count}")

if __name__ == "__main__":
    clean_comments()
