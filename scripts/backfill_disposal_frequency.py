"""
回填歷史處置股「撮合頻率」(measure)
=====================================
問題：DB 內約 74% 的處置紀錄 measure 欄為空或只存款券/次別，撮合頻率(5分/20分…)遺失，
      導致處置統計頁的「頻率」欄與統計不正確。
方法：重抓 TWSE/TPEX 官方處置公告「全文」，用 MeasureParser 解析出撮合間隔，
      以 (代號, 處置起日, 處置迄日) 精確配對，回填 DB 中『目前無有效頻率』的紀錄。
      已有正確頻率者不動；配對不到者（多為 2008 年前，源頭亦無）維持原狀並列入殘留報告。

用法：
    uv run python scripts/backfill_disposal_frequency.py           # dry-run，只報告不寫入
    uv run python scripts/backfill_disposal_frequency.py --apply   # 實際寫入 DB
來源：
    TWSE punish 端點支援 startDate/endDate，一次可抓 2008~今全部。
    TPEX bulletin/disposal 端點支援日期區間，需逐年抓。
"""
from __future__ import annotations

import os
import re
import sys
import time
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

import requests
import urllib3

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from core.measure_parser import MeasureParser
from core.disposal_database import DisposalDatabase

urllib3.disable_warnings()

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJ, "data", "disposal_history.db")
HEADERS = {"User-Agent": "Mozilla/5.0"}
_db = DisposalDatabase(DB_PATH)  # 借用其 parse_period


def _clean_code(code: str) -> str:
    """取代號前段英數，去 HTML 雜訊與 .0 尾巴。"""
    c = str(code or "").strip()
    m = re.match(r"([0-9A-Za-z]+)", c)
    c = m.group(1) if m else c
    if c.endswith(".0"):
        c = c[:-2]
    return c


def _period_to_dates(period_raw: str):
    try:
        s, e = _db.parse_period(period_raw or "")
        return s, e
    except Exception:
        return None, None


# ------------------------------------------------------------------
# 抓取來源全文並解析頻率
# ------------------------------------------------------------------
def fetch_twse() -> list[dict]:
    """TWSE 一次抓全部歷史。回傳 [{code, start, end, announce, freq, full}]"""
    url = ("https://www.twse.com.tw/rwd/zh/announcement/punish"
           "?response=json&startDate=20010101&endDate="
           + datetime.now().strftime("%Y%m%d"))
    out = []
    try:
        data = requests.get(url, headers=HEADERS, timeout=60).json().get("data", [])
    except Exception as e:
        print(f"  [TWSE] 抓取失敗: {e}")
        return out
    # fields: 編號,公布日期,證券代號,證券名稱,累計,處置條件,處置起迄時間,處置措施,處置內容,備註
    for r in data:
        if len(r) < 9:
            continue
        code = _clean_code(r[2])
        period_raw = str(r[6]).strip()
        full = str(r[8]).strip()
        freq = MeasureParser.parse_frequency(full)
        s, e = _period_to_dates(period_raw)
        out.append({"code": code, "start": s, "end": e,
                    "announce": str(r[1]).strip(), "freq": freq})
    return out


def fetch_tpex() -> list[dict]:
    """TPEX 逐年抓 bulletin/disposal。"""
    out = []
    this_year = datetime.now().year
    for y in range(2007, this_year + 1):
        url = (f"https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"
               f"?startDate={y}/01/01&endDate={y}/12/31&response=json")
        try:
            j = requests.get(url, headers=HEADERS, timeout=60, verify=False).json()
            rows = j.get("tables", [{}])[0].get("data", []) or []
        except Exception as e:
            print(f"  [TPEX {y}] 抓取失敗: {e}")
            continue
        # fields: 編號,公布日期,證券代號,證券名稱,累計,處置起訖時間,處置原因,處置內容,收盤價,...
        for r in rows:
            if len(r) < 8:
                continue
            code = _clean_code(r[2])
            period_raw = str(r[5]).strip()
            full = str(r[7]).strip()
            freq = MeasureParser.parse_frequency(full)
            s, e = _period_to_dates(period_raw)
            out.append({"code": code, "start": s, "end": e,
                        "announce": str(r[1]).strip(), "freq": freq})
        print(f"  [TPEX {y}] {len(rows)} 筆")
        time.sleep(0.5)
    return out


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main():
    apply = "--apply" in sys.argv
    print("=" * 60)
    print("回填處置撮合頻率  模式:", "APPLY (寫入)" if apply else "DRY-RUN (僅報告)")
    print("=" * 60)

    print("[1/4] 抓取 TWSE 官方全文 ...")
    src = fetch_twse()
    print(f"    TWSE: {len(src)} 筆")
    print("[2/4] 抓取 TPEX 官方全文（逐年）...")
    tpex = fetch_tpex()
    src += tpex
    print(f"    來源合計: {len(src)} 筆")

    # 建立 (code,start,end) -> freq 對照（僅保留可解析頻率者）
    lookup: dict[tuple, str] = {}
    for x in src:
        if x["freq"] and x["start"] and x["end"]:
            lookup[(x["code"], x["start"], x["end"])] = x["freq"]
    print(f"    可用頻率對照（去重後）: {len(lookup)} 組")

    print("[3/4] 比對 DB 待補紀錄 ...")
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, code, period_start, period_end, measure FROM disposal_records")
    records = cur.fetchall()

    to_update = []      # (id, new_freq)
    stat = Counter()
    by_year_fix = Counter()
    mismatch = []       # 已有頻率但與來源不符（僅報告，不改）
    for rid, code, ps, pe, measure in records:
        c = _clean_code(code)
        cur_freq = MeasureParser.parse_frequency(measure) if measure else ""
        key = (c, ps, pe)
        src_freq = lookup.get(key)
        if cur_freq:
            stat["已有頻率"] += 1
            if src_freq and src_freq != cur_freq:
                mismatch.append((c, ps, cur_freq, src_freq))
            continue
        # 目前無有效頻率
        if src_freq:
            to_update.append((rid, src_freq))
            stat["可回填"] += 1
            y = (ps or pe or "")[:4]
            by_year_fix[y] += 1
        else:
            stat["仍無法回填"] += 1

    print(f"\n--- 診斷 ---")
    print(f"  總紀錄: {len(records)}")
    for k in ["已有頻率", "可回填", "仍無法回填"]:
        print(f"  {k}: {stat[k]}")
    print(f"  （已有頻率但與官方全文不符，僅列不改）: {len(mismatch)} 筆")

    print(f"\n--- 可回填 分年度 ---")
    for y in sorted(by_year_fix):
        print(f"  {y}: {by_year_fix[y]}")

    print(f"\n--- 回填樣本(前10) ---")
    id2 = {rid: f for rid, f in to_update}
    shown = 0
    for rid, code, ps, pe, measure in records:
        if rid in id2 and shown < 10:
            print(f"  {code} {ps}~{pe}  '{measure}' -> '{id2[rid]}'")
            shown += 1

    if mismatch[:5]:
        print(f"\n--- 不符樣本(前5，供人工檢視) ---")
        for c, ps, a, b in mismatch[:5]:
            print(f"  {c} {ps}  DB={a}  官方={b}")

    if not apply:
        print(f"\n[DRY-RUN] 未寫入。確認無誤後加 --apply 執行。")
        con.close()
        return

    print(f"\n[4/4] 寫入 {len(to_update)} 筆 ...")
    cur.executemany(
        "UPDATE disposal_records SET measure=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        [(f, rid) for rid, f in to_update],
    )
    con.commit()
    con.close()
    print(f"完成，已回填 {len(to_update)} 筆頻率。")


if __name__ == "__main__":
    main()
