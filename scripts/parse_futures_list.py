"""
解析台灣期交所的期貨標的清單
從 ODS 檔案中提取股票代碼
"""

import pandas as pd

# 讀取 ODS 檔案
try:
    # 嘗試使用 pandas 讀取（可能需要 odfpy 套件）
    df = pd.read_excel("temp/futures_stocks.ods", engine='odf')
    
    print(f"檔案欄位: {df.columns.tolist()}")
    print(f"\n前 10 筆資料:")
    print(df.head(10))
    print(f"\n第2欄資料:")
    print(df.iloc[:, 2].head(20))
    
    # 提取股票代碼（根據輸出，代碼在第2欄 Unnamed: 2）
    if 'Unnamed: 2' in df.columns:
        futures_codes = df['Unnamed: 2'].astype(str).tolist()
    else:
        # Fallback: 使用第2欄（index 2）
        futures_codes = df.iloc[:, 2].astype(str).tolist()
    
    # 清理代碼（移除空值、NaN 和非數字）
    futures_codes = [
        c.strip() for c in futures_codes 
        if c and str(c).strip() and str(c).strip() != 'nan' and str(c).strip().isdigit() and len(str(c).strip()) == 4
    ]
    
    print(f"\n\n總共 {len(futures_codes)} 支有期貨的股票:")
    print(futures_codes)  # 顯示全部
    
    # 儲存為 Python 集合格式
    output = f"# 有發行股票期貨的標的清單（來源：台灣期交所）\n"
    output += f"# 更新日期：2026-02-04\n"
    output += f"# 來源：https://www.taifex.com.tw/cht/2/stockLists\n\n"
    output += f"FUTURES_STOCKS = {{\n"
    for i, code in enumerate(futures_codes):
        if i % 10 == 0 and i > 0:
            output += "\n"
        output += f"    '{code}',"
    output += "\n}\n"
    output += f"\n\ndef has_futures(stock_code):\n"
    output += f"    \"\"\"檢查股票是否有發行期貨\"\"\"\n"
    output += f"    return str(stock_code).strip() in FUTURES_STOCKS\n"
    
    with open("core/futures_stocks.py", "w", encoding="utf-8") as f:
        f.write(output)
    
    print(f"\n\n✓ 已儲存到 core/futures_stocks.py")
    
except Exception as e:
    print(f"讀取失敗: {e}")
    import traceback
    traceback.print_exc()
