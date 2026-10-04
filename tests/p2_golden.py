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


def run_stage(stage, label):
    import subprocess
    for d in DATES:
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
    ap.add_argument('cmd', choices=['forecast', 'compare', '_forecast_one'])
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
    elif args.cmd == '_forecast_one':
        run_forecast_one(args.label, args.date)


if __name__ == '__main__':
    main()
