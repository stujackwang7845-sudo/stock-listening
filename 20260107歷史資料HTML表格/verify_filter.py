"""
簡易驗證腳本 - 測試處置股抓取是否正確過濾
"""
from core.fetcher import StockFetcher
from core.parser import StockParser

fetcher = StockFetcher()
parser = StockParser()

print("=" * 60)
print("測試上櫃處置股過濾")
print("=" * 60)

# 抓取和解析
tpex_disp = fetcher.fetch_tpex_disposition()
if tpex_disp:
    parsed = parser.parse_tpex_disposition(tpex_disp)
    print(f"\n✓ 成功解析 {len(parsed)} 個項目\n")
    
    for item in parsed:
        print(f"  - {item['code']} {item['name']}")
    
    # 檢查是否包含可轉債
    codes = [item['code'] for item in parsed]
    
    print("\n" + "-" * 60)
    if '82992' in codes or '30061' in codes:
        print("✗ 錯誤: 仍包含可轉債!")
    else:
        print("✓ 正確: 可轉債已被過濾")
    
    if len(codes) == 4:
        print(f"✓ 數量正確: {len(codes)} 個項目")
    else:
        print(f"⚠ 注意: {len(codes)} 個項目 (預期4個,但可能因日期不同)")
        
    print("=" * 60)
else:
    print("✗ 無法抓取資料")
