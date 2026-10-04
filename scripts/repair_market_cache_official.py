"""
用官方日行情修正 data/market_data.db 的 price_history(2026-10-05)。

背景：fetch_stock_history 舊版把 Shioaji K棒(成交量單位「張」)直接存進快取，盤中呼叫時
還會存入當天未收盤的半根 K 棒且之後不再重抓。與官方比對發現約 1.19 萬筆量是張、
約 2.4 萬筆價格不符。程式已修(fetcher.py)，這支修既有資料。

官方來源：DB 專案 parquet_data/price_daily_raw(TWSE MI_INDEX / TPEx 每日收盤行情)，
與 core/official_quotes.py 重抓的 90 個交易日逐筆比對 211,421 筆完全相同。
只改「官方有同一檔同一天」的列；官方沒有的(興櫃、官方缺檔)一律不動，不刪任何列。

執行(專案根目錄)：
  uv run python scripts/repair_market_cache_official.py            # 試算，不寫入
  uv run python scripts/repair_market_cache_official.py --apply    # 先備份再寫入
"""
import argparse
import os
import sqlite3
from collections import Counter
from datetime import datetime

DEFAULT_PARQUET = r"E:\Vibe Coding\Stock\DB\parquet_data\price_daily_raw"


def load_official(parquet_dir, start):
    # 各檔欄位型別不一致(string / large_string)，無法當成一個 dataset 合併讀，改逐檔讀
    import glob
    import pyarrow.parquet as pq
    cols = ["symbol", "date", "open", "high", "low", "close", "volume"]
    start_d = datetime.strptime(start, "%Y-%m-%d").date()
    out = {}
    for path in glob.glob(os.path.join(parquet_dir, "market=*", "year=*", "month=*", "*.parquet")):
        year = int(path.split("year=")[1].split(os.sep)[0])
        if year < start_d.year:
            continue
        d = pq.ParquetFile(path).read(columns=cols).to_pydict()
        for s, dt, o, h, l, c, v in zip(*(d[k] for k in cols)):
            if dt >= start_d:
                out[(s, dt.isoformat())] = (o, h, l, c, v)
    return out


def same(a, b):
    return all((x is None and y is None) or (x is not None and y is not None and abs(x - y) < 1e-6)
               for x, y in zip(a, b))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/market_data.db")
    p.add_argument("--parquet", default=DEFAULT_PARQUET)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()

    conn = sqlite3.connect(a.db, timeout=30)
    start = conn.execute("SELECT MIN(date) FROM price_history").fetchone()[0]
    official = load_official(a.parquet, start)
    print(f"官方資料 {len(official)} 筆(自 {start})")

    updates, stat = [], Counter()
    for row in conn.execute("SELECT stock_id, date, open, high, low, close, volume FROM price_history"):
        off = official.get((row[0], row[1]))
        if off is None:
            stat["官方無對照(不動)"] += 1
            continue
        off = tuple(None if v is None else float(v) for v in off)
        if same(row[2:], off):
            stat["相同"] += 1
            continue
        v_loc, v_off = row[6], off[4]
        if v_loc and v_off and abs(v_loc * 1000 - v_off) < 1000 and same(row[2:6], off[:4]):
            stat["量是張"] += 1
        else:
            stat["價或量不符"] += 1
        updates.append((*off, row[0], row[1]))
    print(dict(stat), f"→ 要更新 {len(updates)} 筆")

    if not a.apply:
        print("試算模式，未寫入。加 --apply 才會備份並寫入。")
        return

    bak = f"{a.db}.bak_{datetime.now():%Y%m%d_%H%M%S}_official_repair"
    dst = sqlite3.connect(bak)
    conn.backup(dst)
    dst.close()
    print(f"已備份 {bak}")
    with conn:
        conn.executemany("UPDATE price_history SET open=?, high=?, low=?, close=?, volume=? "
                         "WHERE stock_id=? AND date=?", updates)
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
