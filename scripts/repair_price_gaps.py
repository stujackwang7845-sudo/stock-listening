"""
一次性修復：股價快取(market_data.db price_history)中間缺的交易日，用 DB 專案官方行情 parquet 補(2026-10-18)。
只新增缺的日子(INSERT OR IGNORE)，不覆蓋既有資料；順便比對重疊日的收盤價，列出不一致(只報告、不改)。

uv run python scripts/repair_price_gaps.py               # 只檢查、不寫入
uv run python scripts/repair_price_gaps.py --apply       # 寫入(先自己備份 market_data.db)
"""
import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import price_gapfill as pg  # noqa: E402
from core.runtime import get_paths  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=400, help="檢查最近幾個日曆天(預設 400)")
    a = ap.parse_args()
    if not pg.parquet_available():
        sys.exit("找不到 DB 專案 parquet，無法修復")
    db = get_paths().market_db
    conn = sqlite3.connect(db)
    lo_all = date.today() - timedelta(days=a.days)
    have = defaultdict(set)
    closes = {}
    for code, d, c in conn.execute("SELECT stock_id, date, close FROM price_history WHERE date >= ?", (lo_all.isoformat(),)):
        dd = date.fromisoformat(d[:10])
        have[code].add(dd)
        closes[(code, dd)] = c
    conn.close()
    total_missing = total_fill = 0
    still_by_code = {}
    mismatch = []
    for code in sorted(have):
        ds = have[code]
        lo, hi = min(ds), max(ds)
        missing = pg.find_missing(ds, lo, hi)
        rows = pg.parquet_rows(code, missing) if missing else {}
        pq_days, _ = pg.parquet_days(lo, hi) if missing else (set(), None)
        still = [d for d in missing if d not in rows and d not in pq_days]
        total_missing += len(missing)
        total_fill += len(rows)
        if still:
            still_by_code[code] = still
        if a.apply and rows:
            pg.insert_missing(db, code, rows)
        # 重疊日收盤比對(抽最近 60 天)
        recent = sorted(ds)[-60:]
        for d, row in pg.parquet_rows(code, recent).items():
            c = closes.get((code, d))
            if c is not None and abs(c - row[3]) > 1e-6:
                mismatch.append((code, d, c, row[3]))
    ratio_added = 0
    if a.apply:
        conn = sqlite3.connect(db)
        price_days = defaultdict(list)
        for code, d in conn.execute("SELECT stock_id, date FROM price_history WHERE date >= ?", (lo_all.isoformat(),)):
            price_days[code].append(date.fromisoformat(d[:10]))
        conn.close()
        for code, ds in price_days.items():
            ratio_added += pg.fill_ratios(db, code, ds, min(ds), max(ds))
        print(f"本益比：用 per_daily_raw 補了 {ratio_added} 筆(只新增)")
    print(f"檢查 {len(have)} 檔；中間缺 {total_missing} 個交易日，parquet 可補 {total_fill} 筆"
          f"{'(已寫入)' if a.apply else '(未寫入，加 --apply 才寫)'}")
    print(f"parquet 也沒有、仍缺的：{sum(len(v) for v in still_by_code.values())} 筆 / {len(still_by_code)} 檔")
    for code, v in list(still_by_code.items())[:15]:
        print(f"  {code}: {pg.missing_text(v)}")
    print(f"最近 60 天收盤價跟官方不一致：{len(mismatch)} 筆(只報告，不覆蓋)")
    for x in mismatch[:15]:
        print(f"  {x[0]} {x[1]} 快取 {x[2]} / 官方 {x[3]}")


if __name__ == "__main__":
    main()
