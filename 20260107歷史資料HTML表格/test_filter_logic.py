"""
測試腳本 - 驗證可轉債過濾邏輯
"""

# 測試資料
test_data = [
    ("3260", "威剛"),
    ("4304", "勝昱"),
    ("5351", "鈺創"),
    ("8299", "群聯"),
    ("82992", "群聯二"),
    ("30061", "晶豪科一"),
]

markers = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']

print("=" * 60)
print("可轉債過濾測試")
print("=" * 60)

for code, name in test_data:
    is_5_digit = len(code) == 5
    has_marker = any(marker in name for marker in markers)
    is_convertible_bond = is_5_digit or has_marker
    
    status = "❌ 過濾" if is_convertible_bond else "✅ 保留"
    reason = []
    if is_5_digit:
        reason.append("5位數")
    if has_marker:
        reason.append("有中文數字")
    
    reason_str = f"({', '.join(reason)})" if reason else ""
    
    print(f"{status} {code} {name:10s} {reason_str}")

print("=" * 60)
print("預期結果: 保留 3260, 4304, 5351, 8299")
print("         過濾 82992, 30061")
print("=" * 60)
