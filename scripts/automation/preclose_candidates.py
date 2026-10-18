"""
盤前候選(2026-10-12)：方案 D「5 天初犯」要在處置公告前一天(也就是公告當天)收盤前買進，
所以要在盤中列出「今天收盤可能進處置」的聽牌股與觸發條件。

在「資料副本」上跑完整預測(不動桌面版資料庫)：以最近一個已收盤交易日 D 為錨點，
build_agg_data → 三個合併 → build_forecast_inputs → run_forecast，與桌面版「處置預測總覽」同一套核心；
挑出官方聽牌(listening)中：非處置中、再 1 次注意就進處置(min_needed ≤ 1)、進處置後是初犯的股票，
再用 disposal_detail.predict_next_days(第十三款規則)判斷 5 天或 7 天。
輸出 JSON 給 notify_disposal_golden.py --pre-close 使用。需要網路(官方注意股/處置公告；FinMind 補缺的股價)。

  uv run python scripts/automation/preclose_candidates.py --anchor 20261009 --out temp/preclose.json
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
SCRIPTS = PROJ / "scripts"


def prepare_copy():
    tmp = tempfile.mkdtemp(prefix="dispo_preclose_")
    state = os.environ.get("DISPO_DATA_DIR")       # 雲端：從狀態資料夾複製(檔名都在同一層)
    for src, name in (("data/cache.db", "cache.db"), ("data/market_data.db", "market_data.db"),
                      ("data/disposal_history.db", "disposal_history.db"),
                      ("data/margin_futures.db", "margin_futures.db"), ("stock_prices.db", "stock_prices.db")):
        p = Path(state) / name if state else PROJ / src
        if p.exists():
            s, d = sqlite3.connect(str(p)), sqlite3.connect(os.path.join(tmp, name))
            s.backup(d)
            d.close()
            s.close()
    for name in ("listening_history.json", "tags_config.json", "twse_holidays.json"):
        p = (Path(state) if state else PROJ / "data") / name
        if p.exists():
            shutil.copy2(p, os.path.join(tmp, name))
    return tmp


def strip_html(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def trigger_lines(calc_results):
    """calc_results 中「明日達以下任一 → 進處置:」到「變聽牌」之前的條件文字。"""
    out, on = [], False
    for line in calc_results or []:
        plain = strip_html(line)
        # 「變聽牌（還不會進處置）」的標題也含「進處置」三個字，必須先判斷它
        if "變聽牌" in plain and "明日達以下任一" in plain:
            break
        if "進處置" in plain and "明日達以下任一" in plain:
            on = True
            continue
        if on and plain:
            out.append(plain)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", required=True, help="最近一個已收盤交易日 YYYYMMDD")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    tmp = prepare_copy()
    os.environ["DISPO_DATA_DIR"] = tmp
    os.environ["DISPO_NO_SHIOAJI"] = "1"        # 不連本機 StockGateway
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.path.insert(0, str(SCRIPTS))
    os.chdir(PROJ)
    try:
        from core.cache import CacheManager
        from core.history_manager import HistoryManager
        from core.margin_futures_db import MarginFuturesDatabase
        from core.cb_data import CBDatabase
        from core.dashboard_engine import (
            build_agg_data, fill_attention_clauses_gap, merge_clauses_from_db,
            merge_clauses_from_listening_history, merge_disposal_status_from_db)
        from core.forecast_engine import build_forecast_inputs, run_forecast
        from core.disposal_detail import predict_next_days
        from core.runtime import get_paths

        D = datetime.strptime(a.anchor, "%Y%m%d")
        paths = get_paths()
        try:
            fill_attention_clauses_gap(D, paths.disposal_db)
        except Exception as e:
            print(f"[preclose] 注意條款回補失敗(略過): {e}")
        hm, cm = HistoryManager(), CacheManager()
        mf, cb = MarginFuturesDatabase(), CBDatabase(paths.cb_csv)
        agg = build_agg_data(hm, target_date=D, cache_mgr=cm)
        merge_clauses_from_db(agg, D)
        merge_clauses_from_listening_history(agg, D, hm)
        merge_disposal_status_from_db(agg, D, hm)
        cm.save_agg_data(a.anchor, agg)
        agg_f, attention = build_forecast_inputs(D)
        res = run_forecast(agg_f, a.anchor, attention, mf, cb, punish_df=None)

        iso = D.strftime("%Y-%m-%d")
        cands = []
        for x in res["listening"]:
            tp = x.get("trigger_progress") or {}
            need = tp.get("min_needed", x.get("min_needed"))
            if x.get("current_status") not in (None, "非處置"):
                continue                              # 處置中的再進處置不是初犯
            if not (isinstance(need, int) and need <= 1):
                continue
            offense = "累犯" if "累犯" in (x.get("enter_freq") or "") else "初犯"
            days, why = predict_next_days(x["code"], iso, tp)
            cands.append({"code": x["code"], "name": x["name"], "market": x.get("source", ""),
                          "close": x.get("close"), "enter_freq": x.get("enter_freq"), "offense": offense,
                          "days": days, "days_note": why, "min_needed": need,
                          "progress": {k: tp.get(k) for k in ("rule1", "rule2", "rule3", "rule4")},
                          "conditions": trigger_lines(x.get("calc_results")),
                          # 資料不完整警告(股價缺日/注意條款缺日，2026-10-18)：推播要照實標出，不可默默給錯的目標價
                          "data_warning": [strip_html(l) for l in x.get("calc_results") or []
                                           if "股價資料不完整" in l or "注意條款資料缺" in l or "本益比資料缺" in l]})
        out = {"anchor": iso, "n_listening": len(res["listening"]), "candidates": cands}
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[preclose] 官方聽牌 {len(res['listening'])} 檔，候選 {len(cands)} 檔 → {a.out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
