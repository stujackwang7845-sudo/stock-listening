"""
雲端每日總控(P4)：抓官方資料 → 更新資料庫 → 跑與桌面版同一套核心計算 → 輸出網頁 JSON。

  uv run python scripts/cloud/run_daily.py --date 20261002 --data-dir <狀態資料夾> --out site

流程對照桌面版開軟體(main_window.preload_data → Dashboard.on_data_ready → ForecastPage.load_data)：
  1. 官方行情：股本 + 回補到 90 個交易日(core/official_quotes)
  2. attention_clauses 回補(dashboard_engine.fill_attention_clauses_gap)
     桌面版是在儀表板算完之後才補；雲端先補，表格與總覽用的是補齊後的條款
  3. build_agg_data(target_date=D)：注意股/聽牌/處置公告/融券/期貨
     (桌面版的 ListeningFetchWorker 抓的是同一支 AttentionScraper，已含在這一步)
  4. on_data_ready 的三個合併 → compute_info_boxes → compute_dashboard_rows
  5. build_forecast_inputs + run_forecast(punish_df=None：雲端不連 Shioaji，用本地處置紀錄)
輸出 <out>/data/day/YYYYMMDD.json(總覽與儀表板共用同一份) 與 <out>/data/index.json(最近 30 天)。

雲端不呼叫 FinMind/Shioaji：本程式一啟動就設 DISPO_NO_FINMIND=1、DISPO_NO_SHIOAJI=1。
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DAYS = 30


def _setup_env(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    os.environ["DISPO_DATA_DIR"] = os.path.abspath(data_dir)
    os.environ["DISPO_NO_FINMIND"] = "1"
    os.environ["DISPO_NO_SHIOAJI"] = "1"
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=_json_default, separators=(",", ":"))
    os.replace(tmp, path)


class StepLog:
    """每一步的結果；必要步驟失敗整天標 partial，不中斷後續能做的部分。"""

    def __init__(self):
        self.steps = []
        self.ok = True

    def run(self, name, fn, required=True):
        t0 = time.time()
        try:
            result = fn()
            self.steps.append({"step": name, "status": "ok", "sec": round(time.time() - t0, 1)})
            print(f"[run_daily] ✔ {name} ({time.time() - t0:.1f}s)", flush=True)
            return result
        except Exception as e:
            traceback.print_exc()
            self.steps.append({"step": name, "status": "error", "error": f"{type(e).__name__}: {e}"})
            print(f"[run_daily] ✘ {name}: {e}", flush=True)
            if required:
                self.ok = False
            return None


def run_daily(date_str, data_dir, out_dir, quote_days=90, skip_quotes=False):
    _setup_env(data_dir)
    from core.runtime import get_paths
    from core import official_quotes
    from core.cache import CacheManager
    from core.history_manager import HistoryManager
    from core.margin_futures_db import MarginFuturesDatabase
    from core.cb_data import CBDatabase
    from core.utils import DateUtils
    from core.fetcher import StockFetcher
    from core.parser import StockParser
    from core.dashboard_engine import (
        build_agg_data, fill_attention_clauses_gap, fetch_market_flags,
        merge_clauses_from_db, merge_clauses_from_listening_history, merge_disposal_status_from_db,
        compute_info_boxes, compute_dashboard_rows,
    )
    from core.forecast_engine import build_forecast_inputs, run_forecast

    paths = get_paths()
    D = datetime.strptime(date_str, "%Y%m%d")
    log = StepLog()
    quotes_status = None

    # 1. 官方行情
    if not skip_quotes:
        log.run("股本", lambda: official_quotes.update_shares(paths.market_db), required=False)
        res = log.run("官方行情", lambda: official_quotes.backfill(
            D.date(), quote_days, paths.market_db, paths.twse_holidays_json))
        quotes_status = (res or {}).get(D.date())
        if quotes_status != "ok":
            log.ok = False
            print(f"[run_daily] {date_str} 官方行情狀態 {quotes_status}，整天標 partial", flush=True)

    # 2. 注意條款回補
    log.run("注意條款回補", lambda: fill_attention_clauses_gap(D, paths.disposal_db))

    # 3. 融券/期貨清單寫進 margin_futures.db(桌面版是手動按鈕，雲端每天更新)
    mf_db = MarginFuturesDatabase()

    def _update_mf():
        margin, futures = fetch_market_flags(StockFetcher(), StockParser(), date_str, lambda m: None)
        if margin:
            mf_db.save_margin_stocks(margin)
        if futures:
            mf_db.save_futures_stocks(futures)
        return len(margin), len(futures)
    log.run("融券期貨清單", _update_mf, required=False)
    cb_db = CBDatabase(paths.cb_csv)

    # 4. 儀表板(對照 Dashboard.start_worker(force_refresh=True) → on_data_ready)
    hm = HistoryManager()
    cache_mgr = CacheManager()
    agg = log.run("build_agg_data", lambda: build_agg_data(hm, target_date=D, cache_mgr=cache_mgr))
    dashboard = None
    if agg is not None:
        def _dashboard():
            merge_clauses_from_db(agg, D)
            merge_clauses_from_listening_history(agg, D, hm)
            merge_disposal_status_from_db(agg, D, hm)
            boxes = compute_info_boxes(agg, D, hm, mf_db, cb_db)
            cache_mgr.save_agg_data(date_str, agg)
            calendar = DateUtils.get_market_calendar(D, past_days=9, future_days=9)
            table = compute_dashboard_rows(agg, D, calendar, hm, boxes["today_attention_map"],
                                           boxes["today_attention_names"], mf_db, cb_db)
            for r in table["rows"]:
                r.pop("data", None)   # 整份 agg 資料，網頁用不到
            return {"calendar": calendar, "info_boxes": boxes, "table": table}
        dashboard = log.run("儀表板", _dashboard)

    # 5. 處置預測總覽(對照 ForecastPage.load_data)
    def _forecast():
        agg_f, attention_list = build_forecast_inputs(D)
        if not agg_f:
            raise RuntimeError("build_forecast_inputs 沒有資料")
        return run_forecast(agg_f, date_str, attention_list, mf_db, cb_db, punish_df=None)
    forecast = log.run("處置預測", _forecast)

    status = "ok" if log.ok else "partial"
    day = {
        "date": date_str,
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "quotes_status": quotes_status,
        "steps": log.steps,
        "forecast": forecast,
        "dashboard": dashboard,
    }
    _write_json(os.path.join(out_dir, "data", "day", f"{date_str}.json"), day)
    _update_index(out_dir, date_str, status)
    print(f"[run_daily] {date_str} 完成，狀態 {status}", flush=True)
    return day


def _update_index(out_dir, date_str, status):
    path = os.path.join(out_dir, "data", "index.json")
    days = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            days = {d["date"]: d for d in json.load(f).get("days", [])}
    days[date_str] = {"date": date_str, "status": status}
    keep = sorted(days, reverse=True)[:INDEX_DAYS]
    _write_json(path, {"updated_at": datetime.now().isoformat(timespec="seconds"),
                       "days": [days[d] for d in keep]})


def main(argv=None):
    p = argparse.ArgumentParser(description="處置股雲端每日總控")
    p.add_argument("--date", required=True, help="YYYYMMDD")
    p.add_argument("--data-dir", required=True, help="狀態資料夾(DISPO_DATA_DIR)")
    p.add_argument("--out", default="site", help="網頁輸出根目錄")
    p.add_argument("--quote-days", type=int, default=90)
    p.add_argument("--skip-quotes", action="store_true", help="不抓官方行情(測試用)")
    a = p.parse_args(argv)
    day = run_daily(a.date, a.data_dir, a.out, a.quote_days, a.skip_quotes)
    return 0 if day["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
