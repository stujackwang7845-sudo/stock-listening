"""
加強 fetcher.py 的 debug 訊息
"""

# 讀取檔案
with open("core/fetcher.py", "r", encoding="utf-8") as f:
    content = f.read()

# 替換 fetch_margin_eligible_stocks 方法
old_code = """            # [Fix] Handle both DataFrame and dict response
            if result is not None:
                # Case 1: Direct DataFrame
                if hasattr(result, 'empty') and not result.empty:
                    return set(result['stock_id'].astype(str).tolist())
                # Case 2: Dict with 'data' key
                elif isinstance(result, dict) and 'data' in result:
                    df = pd.DataFrame(result['data'])
                    if not df.empty:
                        return set(df['stock_id'].astype(str).tolist())
            return set()"""

new_code = """            # [Fix] Handle both DataFrame and dict response
            if result is not None:
                print(f"[DEBUG] Margin API result type: {type(result)}")
                if isinstance(result, dict):
                    print(f"[DEBUG] Dict keys: {result.keys()}")
                # Case 1: Direct DataFrame
                if hasattr(result, 'empty') and not result.empty:
                    return set(result['stock_id'].astype(str).tolist())
                # Case 2: Dict with 'data' key
                elif isinstance(result, dict) and 'data' in result:
                    df = pd.DataFrame(result['data'])
                    if not df.empty:
                        print(f"[DEBUG] Margin data loaded: {len(df)} records")
                        return set(df['stock_id'].astype(str).tolist())
            print(f"[DEBUG] No margin data available")
            return set()"""

content = content.replace(old_code, new_code)

# 寫回檔案
with open("core/fetcher.py", "w", encoding="utf-8") as f:
    f.write(content)

print("✓ 已加強 fetcher.py 的 debug 訊息")
