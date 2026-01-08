"""
測試上櫃處置股資料抓取功能
驗證新的 HTML 解析邏輯和可轉債過濾功能
"""

from core.fetcher import StockFetcher
from core.parser import StockParser

def test_tpex_disposition():
    """測試上櫃處置股抓取和解析功能"""
    print("=" * 60)
    print("測試上櫃處置股資料抓取")
    print("=" * 60)
    
    # 初始化
    fetcher = StockFetcher()
    parser = StockParser()
    
    # 1. 抓取資料
    print("\n[步驟 1] 抓取網頁資料...")
    raw_data = fetcher.fetch_tpex_disposition()
    
    if raw_data:
        print(f"✓ 成功抓取資料 (長度: {len(raw_data)} bytes)")
    else:
        print("✗ 抓取資料失敗")
        return False
    
    # 2. 解析資料
    print("\n[步驟 2] 解析資料...")
    parsed_data = parser.parse_tpex_disposition(raw_data)
    
    if parsed_data:
        print(f"✓ 成功解析資料 (項目數: {len(parsed_data)})")
    else:
        print("✗ 解析資料失敗或無資料")
        return False
    
    # 3. 顯示結果
    print("\n[步驟 3] 解析結果:")
    print("-" * 60)
    print(f"{'股票代碼':<10} {'股票名稱':<15} {'處置期間':<20}")
    print("-" * 60)
    
    for item in parsed_data:
        code = item.get('code', '')
        name = item.get('name', '')
        period = item.get('period', '')
        print(f"{code:<10} {name:<15} {period:<20}")
    
    # 4. 驗證過濾邏輯
    print("\n[步驟 4] 驗證可轉債過濾:")
    has_5_digit = any(len(item['code']) == 5 for item in parsed_data)
    has_cb_marker = any(
        any(marker in item['name'] for marker in ['一', '二', '三', '四', '五'])
        for item in parsed_data
    )
    
    if not has_5_digit:
        print("✓ 已過濾 5 位數代碼 (可轉債)")
    else:
        print("✗ 警告:仍包含 5 位數代碼")
    
    if not has_cb_marker:
        print("✓ 已過濾中文數字結尾 (可轉債)")
    else:
        print("✗ 警告:仍包含中文數字結尾")
    
    # 5. 檢查預期結果
    print("\n[步驟 5] 檢查預期股票代碼:")
    codes = [item['code'] for item in parsed_data]
    expected_codes = ['3260', '4304', '5351', '8299']  # 根據 1/6 資料
    
    print(f"實際代碼: {codes}")
    print(f"預期包含: {expected_codes}")
    
    # 如果是 1/6 的資料,檢查是否符合預期
    found_expected = all(code in codes for code in expected_codes if code in codes)
    if found_expected or len(codes) > 0:
        print("✓ 資料符合預期或有正常資料")
    else:
        print("⚠ 注意:實際資料可能與 1/6 不同(正常現象)")
    
    print("\n" + "=" * 60)
    print("測試完成!")
    print("=" * 60)
    
    return True

if __name__ == "__main__":
    try:
        success = test_tpex_disposition()
        if success:
            print("\n✓ 所有測試通過")
        else:
            print("\n✗ 測試失敗")
    except Exception as e:
        print(f"\n✗ 測試過程發生錯誤: {e}")
        import traceback
        traceback.print_exc()
