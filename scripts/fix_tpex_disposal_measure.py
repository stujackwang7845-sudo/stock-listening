"""
補上櫃處置紀錄缺的撮合頻率(2026-10-09)。

parser.parse_tpex_disposition 原本讀「處置措施」欄(只有「第一次處置/再次處置」)，撮合頻率在
「處置內容」全文裡，所以改用 Web Portal 之後(2026-08-31 起)寫進資料庫的上櫃紀錄 measure 是空的：
總覽顯示「處置中」、統計圖表漏算上櫃事件。parser 已修正，這支用修正後的 parser 重抓櫃買
處置公告，只補 measure 為空的上櫃紀錄(同股號、同處置起訖)，reason 是短字串時一併換成公告全文。
預設只試算；--apply 先備份 disposal_history.db 再寫入。

  uv run python scripts/fix_tpex_disposal_measure.py --since 20260831 [--apply]
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="20260831", help="YYYYMMDD，處置期間涵蓋這天之後的公告都重抓")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)
    from core.runtime import get_paths
    from core.fetcher import StockFetcher
    from core.parser import StockParser
    from core.disposal_database import DisposalDatabase

    since = datetime.strptime(a.since, "%Y%m%d")
    # fetch_tpex_disposition 會查 [date, date+10天]；處置期間涵蓋查詢區間才會回傳，所以逐 7 天往後查
    fetcher, parser = StockFetcher(), StockParser()
    items = {}
    d = since
    while d <= datetime.now() + timedelta(days=7):
        for it in parser.parse_tpex_disposition(fetcher.fetch_tpex_disposition(d.strftime("%Y%m%d"))):
            items[(it["code"], it["period"])] = it
        d += timedelta(days=7)
    print(f"櫃買處置公告 {len(items)} 筆(處置期間涵蓋 {since:%Y-%m-%d} 之後)")

    db = DisposalDatabase(get_paths().disposal_db)
    conn = sqlite3.connect(get_paths().disposal_db)
    rows = conn.execute("SELECT id, code, period_start, period_end, reason FROM disposal_records "
                        "WHERE source='上櫃' AND (measure IS NULL OR measure='') AND period_end >= ?",
                        (since.strftime("%Y-%m-%d"),)).fetchall()
    official = {}
    for (code, period), it in items.items():
        ps, pe = db.parse_period(period)
        if ps and it["measure"]:
            official[(code, ps, pe)] = it
    updates, missing = [], []
    for rid, code, ps, pe, reason in rows:
        it = official.get((str(code).strip(), ps, pe))
        if not it:
            missing.append((rid, code, ps, pe))
            continue
        new_reason = it["reason"] if len(reason or "") < 20 else reason
        updates.append((it["measure"], new_reason, rid))
    print(f"measure 為空的上櫃紀錄 {len(rows)} 筆：可補 {len(updates)} 筆、官方查無 {len(missing)} 筆 {missing[:10]}")
    from collections import Counter
    print("補上的頻率分布", Counter(u[0] for u in updates))
    db.close()

    if not a.apply:
        print("試算模式，未寫入。加 --apply 才會備份並寫入。")
        return
    bak = f"{get_paths().disposal_db}.bak_{datetime.now():%Y%m%d_%H%M%S}_tpex_measure"
    dst = sqlite3.connect(bak)
    conn.backup(dst)
    dst.close()
    print(f"已備份 {bak}")
    with conn:
        conn.executemany("UPDATE disposal_records SET measure = ?, reason = ? WHERE id = ?", updates)
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
