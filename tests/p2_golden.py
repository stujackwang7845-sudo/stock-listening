"""
P2 golden test：把 QThread 內計算搬到 scripts/core 前後，輸出必須逐位元組相同。

做法：
- 每個日期都在「全新的資料副本」上跑(worker 會寫 cache.db，不能污染正式資料，也不能讓
  前一次執行的寫入影響下一次)。
- 外部網路呼叫(StockFetcher 的逐檔處置歷史、股價)第一次照常打並錄進 tape，之後重播，
  搬移前後拿到完全相同的輸入。DISPO_NO_SHIOAJI / DISPO_NO_FINMIND 一律開啟。
- 輸出用 json.dumps(不排序 key)存檔，連 dict 順序一起比。

執行(專案根目錄)：
  uv run python tests/p2_golden.py forecast --label before
  uv run python tests/p2_golden.py forecast --label after
  uv run python tests/p2_golden.py compare --stage forecast before after
  (stats 同理：stats --label before / compare --stage stats before after)
"""
import argparse
import copy
import filecmp
import json
import os
import pickle
import shutil
import sqlite3
import sys
import time
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPTS = os.path.join(ROOT, 'scripts')
OUT = os.path.join(ROOT, 'temp', 'p2_golden')
TAPE = os.path.join(OUT, 'fetch_tape.pkl')
SNAP = os.path.join(OUT, 'snapshot')   # 第一次執行時凍結的資料快照，之後每次都從這裡複製
DATES = ["20261002", "20261001", "20260930"]

SQLITE_FILES = ["disposal_history.db", "cache.db", "market_data.db", "margin_futures.db"]
PLAIN_FILES = ["listening_history.json", "tags_config.json"]
STOP_SHORT_SRC = r"e:\Vibe Coding\Stock\股期套利\data\stop_short.json"
CB_CSV_SRC = r"E:\Vibe Coding\CB\SummaryList\cb_data.csv"


def _sqlite_copy(src, dst):
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()


def ensure_snapshot():
    """第一次執行時凍結一份資料快照(桌面版可能還在跑、會寫入正式資料)。"""
    if os.path.isdir(SNAP):
        return
    os.makedirs(SNAP)
    for f in SQLITE_FILES:
        _sqlite_copy(os.path.join(ROOT, 'data', f), os.path.join(SNAP, f))
    _sqlite_copy(os.path.join(ROOT, 'stock_prices.db'), os.path.join(SNAP, 'stock_prices.db'))
    for f in PLAIN_FILES:
        shutil.copy2(os.path.join(ROOT, 'data', f), os.path.join(SNAP, f))
    if os.path.exists(STOP_SHORT_SRC):
        shutil.copy2(STOP_SHORT_SRC, os.path.join(SNAP, 'stop_short.json'))
    if os.path.exists(CB_CSV_SRC):
        shutil.copy2(CB_CSV_SRC, os.path.join(SNAP, 'cb_data.csv'))
    print(f"[golden] 已建立資料快照 {SNAP}")


def fresh_data_dir(tag):
    d = os.path.join(OUT, 'work', tag)
    if os.path.isdir(d):
        shutil.rmtree(d)
    shutil.copytree(SNAP, d)
    return d


def setup_env(data_dir):
    os.environ["DISPO_DATA_DIR"] = data_dir
    os.environ["DISPO_NO_SHIOAJI"] = "1"
    os.environ["DISPO_NO_FINMIND"] = "1"
    os.environ["DISPO_STOP_SHORT_PATH"] = os.path.join(data_dir, 'stop_short.json')
    os.environ["DISPO_CB_CSV_PATH"] = os.path.join(data_dir, 'cb_data.csv')
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)


class Tape:
    """StockFetcher 對外呼叫的錄製/重播。"""
    def __init__(self):
        self.data = {}
        if os.path.exists(TAPE):
            with open(TAPE, 'rb') as f:
                self.data = pickle.load(f)
        self.new_calls = 0

    def save(self):
        with open(TAPE, 'wb') as f:
            pickle.dump(self.data, f)

    def call(self, key, fn, sleep=0.0):
        if key in self.data:
            return copy.deepcopy(self.data[key])
        self.new_calls += 1
        res = fn()
        if sleep:
            time.sleep(sleep)
        self.data[key] = copy.deepcopy(res)
        return res


def patch_fetcher(tape):
    import core.fetcher as fetcher_mod
    Base = fetcher_mod.StockFetcher
    if getattr(Base, '_golden_patched', False):
        return

    class TapedFetcher(Base):
        _golden_patched = True

        def fetch_stock_disposition_history(self, code, start_date, end_date, source="上市"):
            key = ("disp_hist", str(code), start_date, end_date, source)
            return tape.call(key, lambda: Base.fetch_stock_disposition_history(
                self, code, start_date, end_date, source), sleep=0.6)

        def fetch_stock_attention_history(self, code, start_date, end_date, source="上市"):
            key = ("att_hist", str(code), start_date, end_date, source)
            return tape.call(key, lambda: Base.fetch_stock_attention_history(
                self, code, start_date, end_date, source), sleep=0.6)

        def fetch_stock_history(self, code, source=None, period="180d", allow_fetch=True):
            key = ("price_hist", str(code), source, period, allow_fetch)
            return tape.call(key, lambda: Base.fetch_stock_history(
                self, code, source, period, allow_fetch))

    fetcher_mod.StockFetcher = TapedFetcher


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


# ---------------------------------------------------------------- forecast
def run_forecast_one(label, date_str):
    """單一日期，在獨立子程序內執行(模組層級的路徑與快取不會跨日期殘留)。"""
    data_dir = fresh_data_dir(f"forecast_{label}_{date_str}")
    setup_env(data_dir)

    from unittest.mock import MagicMock
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    tape = Tape()
    patch_fetcher(tape)
    import ui.forecast_page as fp
    from core.cache import CacheManager

    out_dir = os.path.join(OUT, 'forecast', label)
    os.makedirs(out_dir, exist_ok=True)

    RealWorker = fp.ForecastWorker
    captured = {}

    class SyncWorker(RealWorker):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            captured['inputs'] = {
                "date_str": self.date_str,
                "attention_list": copy.deepcopy(self.attention_list),
                "agg_data": copy.deepcopy(self.agg_data),
                "punish_df_is_none": self.punish_df is None,
                "is_history": self.is_history,
            }
            captured['progress'] = []
            self.progress.connect(lambda c, t: captured['progress'].append([c, t]))

        def start(self):
            self.run()

    fp.ForecastWorker = SyncWorker
    page = MagicMock()
    page.current_display_date = datetime.strptime(date_str, "%Y%m%d")
    from core.margin_futures_db import MarginFuturesDatabase
    from core.cb_data import CBDatabase
    page.mf_db = MarginFuturesDatabase()
    page.cb_db = CBDatabase()
    result_box = {}
    page._on_finished = lambda r: result_box.setdefault('result', r)
    page._on_progress = lambda c, t: None

    fp.ForecastPage.load_data(page)
    fp.ForecastWorker = RealWorker

    saved = CacheManager().get_agg_data(date_str)
    with open(os.path.join(out_dir, f"{date_str}_inputs.json"), 'w', encoding='utf-8') as f:
        f.write(dump(captured.get('inputs')))
    with open(os.path.join(out_dir, f"{date_str}_result.json"), 'w', encoding='utf-8') as f:
        f.write(dump({"result": result_box.get('result'), "progress": captured.get('progress')}))
    with open(os.path.join(out_dir, f"{date_str}_saved_agg.json"), 'w', encoding='utf-8') as f:
        f.write(dump(saved))
    r = result_box.get('result') or {}
    tape.save()
    print(f"[golden] forecast {label} {date_str}: 聽牌 {len(r.get('listening', []))}、"
          f"一進聽 {len(r.get('one_step', []))}、處置中 {len(r.get('disposed', []))}；"
          f"新增外部呼叫 {tape.new_calls} 次(重播時應為 0)")


# ---------------------------------------------------------------- stats
STATS_SCENARIOS = ["offline", "online", "force_subset"]


def patch_stats_manager(tape):
    """DisposalStatsManager.calculate_price_changes 是搬移範圍的邊界，錄製/重播它的回傳值。"""
    import core.disposal_stats_manager as dsm
    Base = dsm.DisposalStatsManager
    if getattr(Base, '_golden_patched', False):
        return
    orig = Base.calculate_price_changes

    def taped(self, code, start_date, end_date, days_after=5, allow_download=True, *a, **kw):
        key = ("price_changes", str(code), str(start_date), str(end_date), days_after, allow_download,
               repr(a), repr(sorted(kw.items())))
        return tape.call(key, lambda: orig(self, code, start_date, end_date, days_after=days_after,
                                           allow_download=allow_download, *a, **kw))
    Base.calculate_price_changes = taped
    Base._golden_patched = True


def run_stats_one(label, scenario):
    data_dir = fresh_data_dir(f"stats_{label}_{scenario}")
    setup_env(data_dir)

    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    tape = Tape()
    patch_fetcher(tape)
    patch_stats_manager(tape)
    import ui.disposal_stats_page as sp
    from core.disposal_database import DisposalDatabase
    from core.runtime import get_paths

    db = DisposalDatabase(get_paths().disposal_db)
    records = db.get_all_records()
    db.close()
    if scenario == "offline":
        w = sp.StatsWorker(records, allow_download=False, api_token=None, target_year="全部")
    elif scenario == "online":
        w = sp.StatsWorker(records, allow_download=True, api_token=None, target_year="2026")
    else:
        # 「更新選取資料」：最近公告的 40 筆，強制重算
        w = sp.StatsWorker(records[:40], allow_download=True, api_token=None, force_refresh=True)

    box = {"progress": []}
    w.progress_update.connect(lambda m: box["progress"].append(m))
    w.data_ready.connect(lambda rows: box.setdefault("rows", rows))
    w.run()

    con = sqlite3.connect(get_paths().disposal_db)
    db_after = con.execute("SELECT id, calculated_stats FROM disposal_records ORDER BY id").fetchall()
    con.close()

    out_dir = os.path.join(OUT, 'stats', label)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{scenario}_rows.json"), 'w', encoding='utf-8') as f:
        f.write(json.dumps(box.get("rows"), ensure_ascii=False, default=str))
    with open(os.path.join(out_dir, f"{scenario}_progress.json"), 'w', encoding='utf-8') as f:
        f.write(json.dumps(box["progress"], ensure_ascii=False))
    with open(os.path.join(out_dir, f"{scenario}_db_after.json"), 'w', encoding='utf-8') as f:
        f.write(json.dumps(db_after, ensure_ascii=False))
    tape.save()
    rows = box.get("rows") or []
    filled = sum(1 for r in rows if any(v is not None for v in (r.get("changes") or {}).values()))
    print(f"[golden] stats {label} {scenario}: 輸入 {len(w.disposal_records)} 筆、輸出 {len(rows)} 筆、"
          f"有漲跌幅 {filled} 筆；新增外部呼叫 {tape.new_calls} 次(重播時應為 0)")


# ---------------------------------------------------------------- dashboard
DASH_SCENARIOS = ([f"cached_{d}" for d in DATES] + [f"fallback_{d}" for d in DATES] + ["worker"])

FETCHER_NET_METHODS = ["fetch_twse_margin_list", "fetch_tpex_margin_list", "fetch_taifex_futures_list",
                       "fetch_twse_disposition", "fetch_tpex_disposition", "fetch_twse_attention",
                       "fetch_tpex_attention", "check_market_type"]


def patch_dashboard_net(tape, calls):
    """儀表板 HistoryWorker 的對外呼叫：逐日注意股/融券/期貨/處置名單錄製重播；GitHub 同步改 no-op。"""
    import core.fetcher as fetcher_mod
    Base = fetcher_mod.StockFetcher
    for m in FETCHER_NET_METHODS:
        orig = getattr(Base, m)
        if getattr(orig, '_golden', False):
            continue

        def make(orig, m):
            def taped(self, *a, **kw):
                key = ("fetcher", m, repr(a), repr(sorted(kw.items())))
                return tape.call(key, lambda: orig(self, *a, **kw), sleep=0.6)
            taped._golden = True
            return taped
        setattr(Base, m, make(orig, m))

    from core.scraper_attention import AttentionScraper
    orig_fd = AttentionScraper.fetch_data

    def fetch_data(date_obj=None):
        key = ("attention_scraper", date_obj.strftime("%Y-%m-%d") if date_obj else None)  # get_last_trading_day() 帶時分秒，只取日期
        return tape.call(key, lambda: orig_fd(date_obj), sleep=0.6)
    AttentionScraper.fetch_data = staticmethod(fetch_data)

    from core.history_manager import HistoryManager

    def no_sync(self, *a, **kw):
        calls.append("sync_from_github")
        return None
    HistoryManager.sync_from_github = no_sync


def _flag(x):
    return getattr(x, 'value', x)


def dump_table(t):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    fmt = QColor.NameFormat.HexArgb
    out = {
        "cols": t.columnCount(), "rows": t.rowCount(),
        "headers": [(t.horizontalHeaderItem(i).text() if t.horizontalHeaderItem(i) else None)
                    for i in range(t.columnCount())],
        "hidden": [t.isColumnHidden(i) for i in range(t.columnCount())],
        "sorting": t.isSortingEnabled(),
        "cells": [],
    }
    for r in range(t.rowCount()):
        row = {"h": t.rowHeight(r), "c": []}
        for c in range(t.columnCount()):
            it = t.item(r, c)
            w = t.cellWidget(r, c)
            cell = {}
            if it is not None:
                cell["item"] = {
                    "cls": type(it).__name__, "t": it.text(), "tip": it.toolTip(),
                    "bg": [it.background().style().name, it.background().color().name(fmt)],
                    "fg": [it.foreground().style().name, it.foreground().color().name(fmt)],
                    "u": repr(it.data(Qt.ItemDataRole.UserRole)),
                    "d": repr(it.data(Qt.ItemDataRole.DisplayRole)),
                    "al": _flag(it.textAlignment()),
                }
            if w is not None:
                cell["w"] = {"cls": type(w).__name__, "t": w.text(), "ss": w.styleSheet(),
                             "al": _flag(w.alignment()), "ww": w.wordWrap(),
                             "tif": _flag(w.textInteractionFlags())}
            row["c"].append(cell)
        out["cells"].append(row)
    return out


def run_dashboard_one(label, scenario):
    data_dir = fresh_data_dir(f"dash_{label}_{scenario}")
    setup_env(data_dir)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    if scenario.startswith("fallback_"):
        d = scenario.split("_", 1)[1]
        con = sqlite3.connect(os.path.join(data_dir, "cache.db"))
        con.execute("DELETE FROM agg_cache WHERE date_str=?", (d,))
        con.execute("DELETE FROM dashboard_summary WHERE date_str=?", (d,))
        con.commit()
        con.close()

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    tape = Tape()
    calls = []
    patch_fetcher(tape)
    patch_dashboard_net(tape, calls)
    import ui.dashboard as dashmod

    box_log = []
    orig_update = dashmod.InfoBox.update_items

    def rec_update(self, items):
        box_log.append((id(self), copy.deepcopy(list(items.items()) if isinstance(items, dict) else list(items))))
        return orig_update(self, items)
    dashmod.InfoBox.update_items = rec_update
    dashmod.HistoryWorker.start = lambda self: self.run()

    dash = dashmod.Dashboard(auto_start=False)
    status = []
    dash.status_message_updated.connect(lambda m: status.append(m))
    box_log.clear()

    if scenario == "worker":
        target = dash.current_display_date
        dash.start_worker(force_refresh=True)
        d = target.strftime("%Y%m%d")
    else:
        d = scenario.split("_", 1)[1]
        dash.load_data_for_date(datetime.strptime(d, "%Y%m%d"))

    names = {}
    for attr in ("observer_box", "disposition_box", "disp_active_box", "disp_exit_box"):
        if hasattr(dash, attr):
            names[id(getattr(dash, attr))] = attr
    from core.cache import CacheManager
    cm = CacheManager()
    con = sqlite3.connect(os.path.join(data_dir, "disposal_history.db"))
    cols = [r[1] for r in con.execute("PRAGMA table_info(disposal_records)") if not r[1].endswith("_at")]  # created_at/updated_at 是寫入當下時間
    disp_rows = con.execute(f"SELECT {','.join(cols)} FROM disposal_records ORDER BY id").fetchall()
    con.close()
    con = sqlite3.connect(os.path.join(data_dir, "cache.db"))
    daily = con.execute("SELECT date_str, data_json FROM daily_cache ORDER BY date_str").fetchall()
    con.close()
    with open(os.path.join(data_dir, "listening_history.json"), encoding="utf-8") as f:
        listening_txt = f.read()

    out = {
        "table": dump_table(dash.grid_table),
        "boxes": [[names.get(i, "?"), items] for i, items in box_log],
        "status": status,
        "calls": calls,
        "today_attention_list": getattr(dash, "today_attention_list", None),
        "today_attention_map": getattr(dash, "today_attention_map", None),
        "today_attention_names": getattr(dash, "today_attention_names", None),
        "self_agg_data": dash.agg_data,
        "calendar": {k: (v.strftime("%Y-%m-%d") if k == "anchor_obj" else v)  # anchor_obj 帶當下時分秒
                     for k, v in (getattr(dash, "calendar", None) or {}).items()},
    }
    out_dir = os.path.join(OUT, 'dashboard', label)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{scenario}_ui.json"), 'w', encoding='utf-8') as f:
        f.write(dump(out))
    with open(os.path.join(out_dir, f"{scenario}_store.json"), 'w', encoding='utf-8') as f:
        f.write(dump({"agg_cache": cm.get_agg_data(d), "summary": cm.get_dashboard_summary(d),
                      "daily_cache": daily, "disposal_records": disp_rows}))
    with open(os.path.join(out_dir, f"{scenario}_listening.json"), 'w', encoding='utf-8') as f:
        f.write(listening_txt)
    tape.save()
    print(f"[golden] dashboard {label} {scenario}: 表格 {dash.grid_table.rowCount()} 列、資訊框更新 "
          f"{len(box_log)} 次；新增外部呼叫 {tape.new_calls} 次(重播時應為 0)")


def run_stage(stage, label):
    import subprocess
    for d in {'stats': STATS_SCENARIOS, 'dashboard': DASH_SCENARIOS}.get(stage, DATES):
        r = subprocess.run([sys.executable, os.path.abspath(__file__), f"_{stage}_one",
                            "--label", label, "--date", d], cwd=ROOT,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        log = os.path.join(OUT, f"{stage}_{label}_{d}.log")
        with open(log, 'w', encoding='utf-8') as f:
            f.write(r.stdout + "\n----- stderr -----\n" + r.stderr)
        tail = [l for l in r.stdout.splitlines() if l.startswith("[golden]")]
        print("\n".join(tail) or f"(無輸出，見 {log})")
        if r.returncode != 0:
            print(f"子程序失敗 rc={r.returncode}，見 {log}")
            print(r.stderr[-3000:])
            sys.exit(1)
        # 這個情境的資料副本(每份約 190MB，從 snapshot 複製來的)跑完就用不到了；失敗時保留供除錯
        prefix = {'dashboard': 'dash'}.get(stage, stage)
        shutil.rmtree(os.path.join(OUT, 'work', f"{prefix}_{label}_{d}"), ignore_errors=True)


def compare(stage, a, b):
    da = os.path.join(OUT, stage, a)
    db = os.path.join(OUT, stage, b)
    files = sorted(set(os.listdir(da)) | set(os.listdir(db)))
    bad = 0
    for f in files:
        pa, pb = os.path.join(da, f), os.path.join(db, f)
        if not (os.path.exists(pa) and os.path.exists(pb)):
            print(f"  缺檔 {f}")
            bad += 1
        elif not filecmp.cmp(pa, pb, shallow=False):
            print(f"  不同 {f}")
            bad += 1
        else:
            print(f"  相同 {f} ({os.path.getsize(pa)} bytes)")
    print("結果：", "全部逐位元組相同" if bad == 0 else f"{bad} 個檔案不同")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['forecast', 'stats', 'dashboard', 'compare', '_forecast_one', '_stats_one', '_dashboard_one'])
    ap.add_argument('--label')
    ap.add_argument('--stage')
    ap.add_argument('--date')
    ap.add_argument('pair', nargs='*')
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if args.cmd == 'compare':
        sys.exit(1 if compare(args.stage, *args.pair) else 0)
    ensure_snapshot()
    if args.cmd == 'forecast':
        run_stage('forecast', args.label)
    elif args.cmd == 'stats':
        run_stage('stats', args.label)
    elif args.cmd == '_forecast_one':
        run_forecast_one(args.label, args.date)
    elif args.cmd == 'dashboard':
        run_stage('dashboard', args.label)
    elif args.cmd == '_dashboard_one':
        run_dashboard_one(args.label, args.date)
    elif args.cmd == '_stats_one':
        run_stats_one(args.label, args.date)


if __name__ == '__main__':
    main()
