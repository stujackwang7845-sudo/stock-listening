"""
處置統計空白漲跌幅，用 DB 專案還原股價(adjusted_price_daily 的 adj_close)補上(2026-10-14 使用者要求)。

規則：
  - 只補 calculated_stats 裡「已經有日期、值是空的」欄位；已有數值的不動。
  - 漲跌幅 = 當天還原收盤 / 該股前一個有成交日的還原收盤 - 1(%，兩位小數)。
  - 驗證：台股漲跌幅上限 ±10%，|漲跌幅| > 10.1%(容許升降單位捨入誤差)視為股價有問題，不補、列出來。
  - 前一個有成交日隔超過 12 天(停牌、減資換發)不補：那不是正常的單日漲跌。
  - 另外用「已有數值」的格子抽驗還原股價：同一天兩邊都有值時差多少(除權息日會不同，其餘應幾乎一樣)。
寫入前先備份 disposal_history.db。

  uv run python scripts/fill_stats_from_adjusted.py            # 只試算，不寫入
  uv run python scripts/fill_stats_from_adjusted.py --apply    # 備份後寫入
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "data", "disposal_history.db")
ADJ = r"E:\Vibe Coding\Stock\DB\parquet_data\adjusted_price_daily"
LIMIT = 10.1


def norm_code(c):
    c = str(c).upper().replace(".TW", "").replace(".TWO", "").strip()
    return c[:-2] if c.endswith(".0") else c


def load_adj(codes):
    """{code: [(iso日期, adj_close)] 依日期排序}"""
    import pyarrow.parquet as pq
    out = defaultdict(dict)
    files = glob.glob(os.path.join(ADJ, "market=*", "year=*", "month=*", "*.parquet"))
    for f in files:
        t = pq.ParquetFile(f).read(columns=["date", "symbol", "adj_close"]).to_pydict()
        for d, s, c in zip(t["date"], t["symbol"], t["adj_close"]):
            s = str(s).strip()
            if s in codes and c is not None and c == c and c > 0:
                out[s][d.isoformat()] = float(c)
    return {k: sorted(v.items()) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id, code, calculated_stats FROM disposal_records "
                        "WHERE calculated_stats IS NOT NULL AND calculated_stats <> ''").fetchall()
    recs, codes = [], set()
    for rid, code, js in rows:
        try:
            d = json.loads(js)
        except Exception:
            continue
        if d.get("_unresolvable") or not isinstance(d.get("changes"), dict):
            continue
        recs.append((rid, norm_code(code), d))
        codes.add(norm_code(code))
    print(f"處置紀錄(有快取) {len(recs)} 筆、股票 {len(codes)} 檔；讀還原股價中…", flush=True)
    adj = load_adj(codes)
    last_day = max((s[-1][0] for s in adj.values() if s), default="")
    print(f"還原股價涵蓋 {len(adj)} 檔，最後日期 {last_day}", flush=True)

    idx = {c: {d: i for i, (d, _) in enumerate(s)} for c, s in adj.items()}

    def adj_change(code, day):
        s = adj.get(code)
        if not s or day not in idx[code]:
            return None, "還原股價沒有這天"
        i = idx[code][day]
        if i == 0:
            return None, "沒有前一天"
        gap = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(s[i - 1][0], "%Y-%m-%d")).days
        if gap > 12:                                  # 停牌/減資換發：不是正常的單日漲跌(春節長假約 9~10 天)
            return None, "前一個成交日隔太久(>12 天)"
        return round((s[i][1] / s[i - 1][1] - 1) * 100, 2), None

    filled, reasons, rejected, updates = 0, Counter(), [], {}
    check_diff = []                                           # 抽驗：已有值 vs 還原股價
    by_year = Counter()
    for rid, code, d in recs:
        ch, dates = d["changes"], d.get("dates") or {}
        new = dict(ch)
        got = []
        for k, v in ch.items():
            day = dates.get(k)
            if not day:
                continue
            if v is None:
                if day > last_day:
                    reasons["日期晚於還原股價最後一天"] += 1
                    continue
                val, why = adj_change(code, day)
                if val is None:
                    reasons[why] += 1
                    continue
                if abs(val) > LIMIT:
                    rejected.append((code, day, k, val))
                    reasons["超過 ±10%(不補)"] += 1
                    continue
                new[k] = val
                got.append(k)
                by_year[day[:4]] += 1
            elif len(check_diff) < 200000:
                val, _ = adj_change(code, day)
                if val is not None:
                    check_diff.append(abs(val - v))
        if got:
            filled += len(got)
            d2 = dict(d)
            d2["changes"] = new
            d2["_adj_filled"] = sorted(set(d.get("_adj_filled", [])) | set(got))
            updates[rid] = d2

    print(f"\n可補上 {filled} 格（{len(updates)} 筆紀錄）；各年份：{dict(sorted(by_year.items()))}")
    print(f"補不了的原因：{dict(reasons)}")
    if rejected:
        print(f"超過 ±10% 不補 {len(rejected)} 格，前 20 筆：{rejected[:20]}")
    if check_diff:
        n = len(check_diff)
        same = sum(x <= 0.02 for x in check_diff)
        print(f"抽驗(已有值 vs 還原股價，同一天)：{n} 格，相差 ≤0.02 個百分點 {same / n * 100:.1f}%；"
              f"相差 >1 個百分點 {sum(x > 1 for x in check_diff)} 格(多為除權息日，已有值用原始價)")

    if not a.apply:
        print("\n[試算] 未寫入。確認後加 --apply")
        return
    bak = DB + f".bak_{datetime.now():%Y%m%d_%H%M%S}_before_adjfill"
    b = sqlite3.connect(bak)
    conn.backup(b)
    b.close()
    print(f"已備份 → {bak}")
    with conn:
        conn.executemany("UPDATE disposal_records SET calculated_stats=? WHERE id=?",
                         [(json.dumps(v), k) for k, v in updates.items()])
    print(f"已寫入 {len(updates)} 筆紀錄")
    r = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"integrity_check：{r}")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
