"""
修正 disposal_records 的公告日錯誤與重複紀錄(2026-10-09，使用者要求)。

背景：dashboard_engine 同步處置公告時讀錯欄位(parser 給 ann_date，程式讀 date)，公告日一律
被記成處置開始日；同一次處置常常另有一筆公告日正確的紀錄(網頁更新寫的)，形成重複。
公告日錯，會讓「公告當天看不到新處置、處置開始日又被誤列成當天公告」。

規則(證交所/櫃買：公告後「次一營業日」起處置；以公告日正確的 2,035 筆驗證 98% 吻合，
其餘為 2024 年連假不在程式內建日曆)：
  1. 公告日 = 處置開始日，且同一段處置(同股號、同起訖)另有更早公告日的紀錄 → 刪除這筆
  2. 公告日 = 處置開始日，沒有可對照的紀錄 → 公告日改成「處置開始日的前一個真實交易日」
     (交易日用 DB 專案官方日 K 的實際開盤日＋market_data.db，不用程式內建休市表)
  3. 同一段處置、公告日也相同(格式不同或股號 8080.0/8080)的完全重複 → 留一筆
     保留順序：有 custom_stats(使用者自訂值) > measure 有內容(16 組重複只有一筆有撮合頻率文字，
     刪錯會改變統計圖表的頻率分類) > 有 calculated_stats > 股號已是乾淨格式 > id 小
     同組有兩筆不同的 custom_stats → 整組不動，列出來給使用者看
預設只試算；--apply 先完整備份資料庫再寫入。

  uv run python scripts/fix_disposal_announce_dates.py [--apply]
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PARQUET = r"E:\Vibe Coding\Stock\DB\parquet_data\price_daily_raw"


def trading_days(market_db):
    import pyarrow.parquet as pq
    days = set()
    for p in glob.glob(os.path.join(PARQUET, "market=*", "year=*", "month=*", "*.parquet")):
        days.update(d.isoformat() for d in pq.ParquetFile(p).read(columns=["date"]).column("date").to_pylist())
    conn = sqlite3.connect(market_db)
    days.update(r[0] for r in conn.execute("SELECT DISTINCT date FROM price_history"))
    conn.close()
    return sorted(days)


def clean_code(code):
    c = str(code or "").strip().upper().replace(".TWO", "").replace(".TW", "")
    return c[:-2] if c.endswith(".0") else c


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)
    from core.runtime import get_paths
    from core.utils import DateUtils
    import bisect
    paths = get_paths()

    days = trading_days(paths.market_db)
    first_day = days[0]

    def prev_trading_day(iso):
        i = bisect.bisect_left(days, iso)
        return days[i - 1] if i > 0 and iso > first_day else None

    conn = sqlite3.connect(paths.disposal_db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, code, announce_date, period_start, period_end, measure, calculated_stats, custom_stats "
        "FROM disposal_records WHERE period_start IS NOT NULL AND period_start != ''")]

    groups = defaultdict(list)
    for r in rows:
        r["ann"] = DateUtils.to_iso_date_str(r["announce_date"] or "")
        groups[(clean_code(r["code"]), r["period_start"], r["period_end"])].append(r)

    delete, update, conflicts, no_prev = [], [], [], []
    for key, rs in groups.items():
        ps = key[1]
        # 規則 1
        has_earlier = any(x["ann"] and x["ann"] < ps for x in rs)
        keep = []
        for r in rs:
            if r["ann"] == ps and has_earlier:
                if r["custom_stats"] and not any(x["custom_stats"] for x in rs if x["ann"] < ps):
                    conflicts.append((key, "錯誤副本帶有自訂值，未刪", r["id"]))
                    keep.append(r)
                    continue
                delete.append((r["id"], "公告日=開始日的錯誤副本", key))
            else:
                keep.append(r)
        # 規則 2
        for r in keep:
            if r["ann"] == ps:
                prev = prev_trading_day(ps)
                if prev:
                    update.append((r["id"], r["announce_date"], prev, key))
                    r["ann"] = prev
                else:
                    no_prev.append((r["id"], key))
        # 規則 3
        by_ann = defaultdict(list)
        for r in keep:
            by_ann[r["ann"]].append(r)
        for ann, same in by_ann.items():
            if len(same) < 2:
                continue
            customs = {x["custom_stats"] for x in same if x["custom_stats"]}
            if len(customs) > 1:
                conflicts.append((key, "完全重複但自訂值不同，整組不動", [x["id"] for x in same]))
                continue
            same.sort(key=lambda x: (not x["custom_stats"], not (x["measure"] or "").strip(), not x["calculated_stats"],
                                     clean_code(x["code"]) != str(x["code"]).strip(), x["id"]))
            for x in same[1:]:
                delete.append((x["id"], f"完全重複(保留 id {same[0]['id']})", key))

    print(f"處置紀錄 {len(rows)} 筆、{len(groups)} 段處置；交易日曆 {days[0]}～{days[-1]} 共 {len(days)} 天")
    # 刪除前把同一段處置裡最完整的漲跌幅快取(calculated_stats)留給保留的紀錄，不因刪重複而流失
    def stats_score(js):
        try:
            ch = json.loads(js).get("changes", {}) if js else None
        except Exception:
            ch = None
        if not isinstance(ch, dict):
            return (-1, -1)
        return (sum(v is not None for v in ch.values()), len(ch))

    deleted_ids = {d[0] for d in delete}
    stats_fix = []
    for key, rs in groups.items():
        survivors = [r for r in rs if r["id"] not in deleted_ids]
        if len(survivors) == len(rs) or not survivors:
            continue
        best = max(rs, key=lambda r: stats_score(r["calculated_stats"]))
        for r in survivors:
            if stats_score(r["calculated_stats"]) < stats_score(best["calculated_stats"]):
                stats_fix.append((best["calculated_stats"], r["id"]))
    print(f"保留紀錄補上同組較完整的漲跌幅快取 {len(stats_fix)} 筆")

    print(f"刪除 {len(delete)} 筆：", dict(sorted(
        {k: sum(1 for d in delete if d[1].startswith(k)) for k in ("公告日=開始日", "完全重複")}.items())))
    print(f"修正公告日 {len(update)} 筆；無法推前一交易日 {len(no_prev)} 筆；保留未動(衝突) {len(conflicts)} 組")
    for x in [d for d in delete if d[2][0] in ("6672", "8021")][:6]:
        print("  例 刪", x)
    for x in update[-5:]:
        print("  例 改", x)
    for x in conflicts[:10]:
        print("  衝突", x)

    if not a.apply:
        print("試算模式，未寫入。加 --apply 才會備份並寫入。")
        return
    bak = f"{paths.disposal_db}.bak_{datetime.now():%Y%m%d_%H%M%S}_announce_fix"
    dst = sqlite3.connect(bak)
    conn.backup(dst)
    dst.close()
    print(f"已備份 {bak}")
    with conn:
        conn.executemany("DELETE FROM disposal_records WHERE id = ?", [(d[0],) for d in delete])
        conn.executemany("UPDATE disposal_records SET announce_date = ? WHERE id = ?",
                         [(u[2], u[0]) for u in update])
        conn.executemany("UPDATE disposal_records SET calculated_stats = ? WHERE id = ?", stats_fix)
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
