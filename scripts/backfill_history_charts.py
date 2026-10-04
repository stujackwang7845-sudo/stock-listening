import sys
import os
import json
import sqlite3
import datetime
import pandas as pd
import requests
from dotenv import load_dotenv
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import CacheManager
from core.utils import DateUtils

# Fugle 行情金鑰由 .env 集中管理（2026-07-07，取代原本的 yfinance）
# [Fix 2026-10-01] 筆電外出版沒有 E 槽那個共用設定資料夾，找不到就退回專案根目錄的 .env
_ENV_PATH = r"E:\Vibe Coding\ANTIGRAVITY SETTINGS\.env"
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)
else:
    _local_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_local_env):
        load_dotenv(_local_env)


def _fetch_fugle_minute(code, d_str):
    """用 Fugle 抓單日 1 分 K，回傳與 UI 快取相容的 DataFrame（欄位 Open/High/Low/Close/Volume，
    index 為 datetime）。查無資料回傳 None。取代原本的 yfinance（台股禁用 yfinance 規則）。"""
    api_key = os.getenv("FUGLE_API_KEY", "")
    if not api_key:
        print("    [警告] 未設定 FUGLE_API_KEY，略過")
        return None
    url = (f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/"
           f"{code}?from={d_str}&to={d_str}&timeframe=1")
    try:
        resp = requests.get(url, headers={"X-API-KEY": api_key}, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                           'close': 'Close', 'volume': 'Volume'}, inplace=True)
        df = df.sort_index()
        # 只保留目標日、且至少 2 筆才算有效（單筆多為零股/異常）
        target = df[df.index.date == datetime.datetime.strptime(d_str, "%Y-%m-%d").date()]
        if len(target) > 1:
            return target
        return None
    except Exception as e:
        print(f"    Fugle 報錯: {e}")
        return None

def main():
    print("=== 歷史圖表快取在地化補齊計畫 ===")
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(base_dir, "data", "cache.db")
    history_file = os.path.join(base_dir, "listening_history.json")
    
    cache_mgr = CacheManager(db_path)
    
    if not os.path.exists(history_file):
        print(f"找不到 {history_file}，無法掃描歷史股")
        return
        
    with open(history_file, "r", encoding="utf-8") as f:
        try:
            hist_data = json.load(f)
        except Exception as e:
            print(f"讀取 JSON 錯誤: {e}")
            return
            
    # 先蒐集所有的 (stock_code, date_str) 需求
    # 每個歷史項目需要 T, T+1, T+2 三天的圖
    needed_pairs = set()
    total_items = 0
    
    if isinstance(hist_data, list):
        for rec in hist_data:
            date_str = rec.get('date', '')
            code = str(rec.get('code', ''))
            if not date_str or not code: continue
            total_items += 1
            
            try:
                base_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                d1 = base_dt.strftime("%Y-%m-%d")
                
                dt2 = DateUtils.get_next_trading_day(base_dt)
                d2 = dt2.strftime("%Y-%m-%d")
                
                dt3 = DateUtils.get_next_trading_day(dt2)
                d3 = dt3.strftime("%Y-%m-%d")
                
                needed_pairs.add((code, d1))
                needed_pairs.add((code, d2))
                needed_pairs.add((code, d3))
            except Exception as e:
                pass
    else:
        # Fallback if it behaves like a dict (old format)
        for date_str, records in hist_data.items():
            for rec in records:
                code = str(rec.get('code', ''))
                if not code: continue
                total_items += 1
                
                try:
                    base_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    d1 = base_dt.strftime("%Y-%m-%d")
                    
                    dt2 = DateUtils.get_next_trading_day(base_dt)
                    d2 = dt2.strftime("%Y-%m-%d")
                    
                    dt3 = DateUtils.get_next_trading_day(dt2)
                    d3 = dt3.strftime("%Y-%m-%d")
                    
                    needed_pairs.add((code, d1))
                    needed_pairs.add((code, d2))
                    needed_pairs.add((code, d3))
                except Exception as e:
                    pass

    print(f"共掃描 {total_items} 筆歷史紀錄，需要 {len(needed_pairs)} 張走勢圖快取。")
    
    # Check what we already have
    conn = sqlite3.connect(cache_mgr.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT cache_key FROM chart_cache")
    existing_keys = {row[0] for row in cursor.fetchall()}
    conn.close()
    
    missing_pairs = []
    for code, date_str in needed_pairs:
        k = f"{code}_{date_str}"
        if k not in existing_keys:
            missing_pairs.append((code, date_str))
            
    print(f"已快取: {len(needed_pairs) - len(missing_pairs)}，待補齊: {len(missing_pairs)}")
    
    if not missing_pairs:
        print("所有圖表均已在地化，無需補齊。")
        return
        
    # Process
    import time
    success_count = 0
    fail_count = 0
    
    for i, (code, d_str) in enumerate(missing_pairs, 1):
        print(f"[{i}/{len(missing_pairs)}] 下載 {code} 在 {d_str} 的走勢圖...")

        try:
            df = _fetch_fugle_minute(code, d_str)

            if df is not None and not df.empty:
                # Reset timezone info to string format before JSON serialization
                df.index = df.index.astype(str)
                json_str = df.to_json(orient="index", date_format="iso")
                cache_mgr.save_chart_data(code, d_str, json_str)
                success_count += 1
                time.sleep(0.1)  # 對 Fugle 禮貌性延遲
            else:
                print(f"    無法取得 {code} 於 {d_str} 的資料 (可能未開盤或已下市)")
                # Write an empty dict just to mark it as verified missing
                cache_mgr.save_chart_data(code, d_str, "{}")
                fail_count += 1

        except Exception as e:
             print(f"    報錯: {e}")
             fail_count += 1

    print(f"\n完成！成功寫入 {success_count} 筆，失敗/查無資料 {fail_count} 筆。")

if __name__ == '__main__':
    main()
