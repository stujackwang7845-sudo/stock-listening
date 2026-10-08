"""
桌面版補建指定交易日的「監控儀表板 / 處置預測總覽」快照(2026-10-08)。

桌面版只在開軟體時算「最新交易日」，沒開軟體的日子不會有 agg_cache / dashboard_summary，
之後切到那天只能用不完整的資料重建。這支用跟桌面版同一套核心(dashboard_engine 的
build_agg_data 以 target_date 為錨點、on_data_ready 的合併、compute_info_boxes、
forecast_engine)把指定日期補算存進快取。

不連 FinMind / 永豐(DISPO_NO_FINMIND、DISPO_NO_SHIOAJI)：股價與本益比改抓官方全市場日行情
(core/official_quotes)，而且只補 market_data.db 裡本來就有的股票(不新增股票，避免新股票
只有幾天資料、之後桌面版不再抓兩年歷史)。寫入前先備份 cache.db、market_data.db、
disposal_history.db、data/listening_history.json。

執行(專案根目錄，桌面程式先關掉)：
  uv run python scripts/backfill_desktop_days.py --dates 20261005 20261006
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

os.environ["DISPO_NO_FINMIND"] = "1"
os.environ["DISPO_NO_SHIOAJI"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def backup(paths, stamp):
    out = []
    for p in (paths.cache_db, paths.market_db, paths.disposal_db):
        dst = f"{p}.bak_{stamp}_backfill"
        s, d = sqlite3.connect(p), sqlite3.connect(dst)
        s.backup(d)
        d.close()
        s.close()
        out.append(dst)
    lj = paths.listening_json
    if os.path.exists(lj):
        dst = f"{lj}.bak_{stamp}_backfill"
        shutil.copy2(lj, dst)
        out.append(dst)
    return out


def fill_official_quotes(dates, market_db):
    """官方日行情＋本益比，只寫 market_data.db 已有的股票。"""
    from core import official_quotes as oq
    conn = sqlite3.connect(market_db)
    known = {r[0] for r in conn.execute("SELECT DISTINCT stock_id FROM price_history")}
    conn.close()
    client = oq.OfficialClient()
    for d in dates:
        day = oq.fetch_day(client, d.date(), with_ratios=True)
        if day.status != "ok":
            print(f"[backfill] {d:%Y-%m-%d} 官方行情 {day.status} {day.note}，略過")
            continue
        day.quotes = {k: v for k, v in day.quotes.items() if k in known}
        day.ratios = {k: v for k, v in day.ratios.items() if k in known}
        n = oq.save_day(day, market_db)
        print(f"[backfill] {d:%Y-%m-%d} 股價 {n} 檔、本益比 {len(day.ratios)} 檔(只補既有股票)")


def build_day(D, hm, cache_mgr, mf_db, cb_db):
    """對照 Dashboard.start_worker(force_refresh) → on_data_ready，再加 ForecastPage.load_data。"""
    from core.dashboard_engine import (
        build_agg_data, merge_clauses_from_db, merge_clauses_from_listening_history,
        merge_disposal_status_from_db, compute_info_boxes,
    )
    from core.forecast_engine import build_forecast_inputs, run_forecast
    date_str = D.strftime("%Y%m%d")
    agg = build_agg_data(hm, target_date=D, cache_mgr=cache_mgr)
    merge_clauses_from_db(agg, D)
    merge_clauses_from_listening_history(agg, D, hm)
    merge_disposal_status_from_db(agg, D, hm)
    boxes = compute_info_boxes(agg, D, hm, mf_db, cb_db)
    cache_mgr.save_dashboard_summary(date_str, {
        "obs": boxes["obs_list"], "notice": boxes["notice_list"],
        "exit": boxes["exit_list"] or [], "active": boxes["active_list"],
    })
    cache_mgr.save_agg_data(date_str, agg)
    agg_f, attention_list = build_forecast_inputs(D)
    result = run_forecast(agg_f, date_str, attention_list, mf_db, cb_db, punish_df=None)
    return len(agg), {k: len(result[k]) for k in ("listening", "one_step", "disposed")}


def main(argv=None):
    p = argparse.ArgumentParser(description="桌面版補建指定交易日快照(不連 FinMind/永豐)")
    p.add_argument("--dates", nargs="+", required=True, help="YYYYMMDD ...")
    p.add_argument("--skip-quotes", action="store_true", help="不抓官方行情")
    a = p.parse_args(argv)
    from core.runtime import get_paths, data_dir_override
    if data_dir_override():
        raise SystemExit("這支是給桌面版資料用的，不要設 DISPO_DATA_DIR")
    from core.cache import CacheManager
    from core.history_manager import HistoryManager
    from core.margin_futures_db import MarginFuturesDatabase
    from core.cb_data import CBDatabase
    from core.utils import DateUtils

    paths = get_paths()
    dates = sorted(datetime.strptime(x, "%Y%m%d") for x in a.dates)
    for d in dates:
        if not DateUtils.is_trading_day(d):
            raise SystemExit(f"{d:%Y-%m-%d} 不是交易日")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in backup(paths, stamp):
        print(f"[backfill] 已備份 {f}")
    if not a.skip_quotes:
        fill_official_quotes(dates, paths.market_db)

    hm = HistoryManager()
    cache_mgr = CacheManager()
    mf_db = MarginFuturesDatabase()
    cb_db = CBDatabase()
    for D in dates:
        n, counts = build_day(D, hm, cache_mgr, mf_db, cb_db)
        print(f"[backfill] {D:%Y-%m-%d} 儀表板 {n} 檔；處置預測 聽牌 {counts['listening']}、"
              f"一進聽 {counts['one_step']}、處置中 {counts['disposed']}")


if __name__ == "__main__":
    main()
