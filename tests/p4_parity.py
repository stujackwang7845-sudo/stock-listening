"""
P4 parity：雲端每日總控 scripts/cloud/run_daily.py 與桌面版(P2 golden 輸出)逐欄比對。

  A 輪：資料快照(含桌面版的 market_data.db)+ P2 錄製重播的網路資料 → 應與 golden 一致，
        有差異必須是可解釋的流程差異。
  B 輪：同 A，但 market_data.db 換成官方回補版(temp/p3_official/d90) → 列出「股價來源」造成的差異。

基準：temp/p2_golden/forecast/after/20261002_result.json、temp/p2_golden/dashboard/final/worker_ui.json
(golden 的 dashboard worker 情境就是 start_worker(force_refresh=True)，最新交易日 2026-10-02)。

執行(專案根目錄)：uv run python tests/p4_parity.py
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import p2_golden as g  # noqa: E402

DATE = "20261002"
OUT = os.path.join(ROOT, 'temp', 'p4_parity')
OFFICIAL_DB = os.path.join(ROOT, 'temp', 'p3_official', 'd90', 'market_data.db')
GOLD_FORECAST = os.path.join(g.OUT, 'forecast', 'after', f'{DATE}_result.json')
GOLD_DASH = os.path.join(g.OUT, 'dashboard', 'final', 'worker_ui.json')


def run_one(mode):
    """子程序內執行：建資料副本 → 錄製重播 → run_daily → 只留輸出 JSON。"""
    data_dir = g.fresh_data_dir(f"p4_{mode}")
    if mode == "B":
        dst = os.path.join(data_dir, "market_data.db")
        os.remove(dst)
        g._sqlite_copy(OFFICIAL_DB, dst)
    g.setup_env(data_dir)
    tape = g.Tape()
    g.patch_fetcher(tape)
    g.patch_dashboard_net(tape, [])
    # 股價不重播，直接讀本輪的 market_data.db(B 輪要吃到官方股價)；
    # 雲端旗標下 fetch_stock_history 不會連網，A 輪讀快照等同 golden 錄製當時
    import core.fetcher as fetcher_mod
    if "fetch_stock_history" in fetcher_mod.StockFetcher.__dict__:
        delattr(fetcher_mod.StockFetcher, "fetch_stock_history")
    sys.path.insert(0, os.path.join(g.SCRIPTS, 'cloud'))
    import run_daily
    run_daily.run_daily(DATE, data_dir, os.path.join(OUT, mode), skip_quotes=True)
    print(f"[p4] {mode} 新增外部呼叫 {tape.new_calls} 次")


def load_day(mode):
    with open(os.path.join(OUT, mode, 'data', 'day', f'{DATE}.json'), encoding='utf-8') as f:
        return json.load(f)


F_KEYS = {"listening": ["current_status", "enter_freq", "trigger_progress", "min_needed", "calc_results"],
          "one_step": ["current_status", "enter_freq", "trigger_progress", "min_needed", "calc_results"],
          "disposed": ["freq", "start", "end", "exit", "remaining", "days_elapsed", "days_total", "enter_freq"]}


def norm(v):
    return json.loads(json.dumps(v, ensure_ascii=False, default=str))


def diff_forecast(a, b, label):
    n = 0
    for k, fields in F_KEYS.items():
        ca, cb = [x["code"] for x in a[k]], [x["code"] for x in b[k]]
        if ca != cb:
            n += 1
            print(f"  [{label}] {k} 名單不同：只在左 {sorted(set(ca) - set(cb))} 只在右 {sorted(set(cb) - set(ca))}"
                  f"{'(順序不同)' if set(ca) == set(cb) else ''}")
        ma, mb = {x["code"]: x for x in a[k]}, {x["code"]: x for x in b[k]}
        for code in sorted(set(ma) & set(mb)):
            for f in fields:
                if norm(ma[code].get(f)) != norm(mb[code].get(f)):
                    n += 1
                    print(f"  [{label}] {k} {code} {f}:\n     左 {norm(ma[code].get(f))}\n     右 {norm(mb[code].get(f))}")
    return n


def golden_dash_rows():
    with open(GOLD_DASH, encoding='utf-8') as f:
        u = json.load(f)
    t = u["table"]
    rows = {}
    order = []
    for r in t["cells"]:
        texts = [(c.get("item") or c.get("w") or {}).get("t") for c in r["c"]]
        rec = dict(zip(t["headers"], texts))
        order.append(rec["股票"])
        rows[rec["股票"]] = rec
    boxes = {}
    for name, items in u["boxes"]:
        boxes[name] = items   # 取最後一次更新
    return t["headers"], order, rows, boxes


def diff_dashboard(day, label):
    headers, g_order, g_rows, g_boxes = golden_dash_rows()
    table = day["dashboard"]["table"]
    order = [r["code"] for r in table["rows"]]
    n = 0
    if headers[:-1] != table["headers"][:-1] and headers != table["headers"]:
        n += 1
        print(f"  [{label}] 表頭不同\n     golden {headers}\n     雲端   {table['headers']}")
    if order != g_order:
        n += 1
        print(f"  [{label}] 表格列不同：只在 golden {sorted(set(g_order) - set(order))}，"
              f"只在雲端 {sorted(set(order) - set(g_order))}{'(順序不同)' if set(order) == set(g_order) else ''}")
    for r in table["rows"]:
        g_r = g_rows.get(r["code"])
        if not g_r:
            continue
        mine = {"處置頻率": r["freq_text"] or None, "處置天數": r["days_text"] or None,
                "機率": str(r["prob"]), "處置預測": r["final_msg"]}
        for col, v in mine.items():
            gv = g_r.get(col)
            if (gv or None) != (v or None):
                n += 1
                print(f"  [{label}] 表格 {r['code']} {col}: golden {gv!r} 雲端 {v!r}")
    boxes = day["dashboard"]["info_boxes"]
    for g_name, key in [("observer_box", "obs_list"), ("disposition_box", "notice_list"),
                        ("disp_active_box", "active_list"), ("disp_exit_box", "exit_list")]:
        if norm(g_boxes.get(g_name)) != norm(boxes.get(key)):
            n += 1
            print(f"  [{label}] 資訊框 {key} 不同\n     golden {norm(g_boxes.get(g_name))}\n     雲端   {norm(boxes.get(key))}")
    return n


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "_run":
        g.ensure_snapshot()
        run_one(sys.argv[2])
        return 0
    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    for mode in ("A", "B"):
        r = subprocess.run([sys.executable, __file__, "_run", mode], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        # 子程序結束、SQLite 連線都關了才刪資料副本(約 220MB／份)
        shutil.rmtree(os.path.join(g.OUT, "work", f"p4_{mode}"), ignore_errors=True)
        with open(os.path.join(OUT, f"run_{mode}.log"), "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n--- stderr ---\n" + r.stderr)
        print(f"[p4] {mode} 輪結束 exit={r.returncode}，log: temp/p4_parity/run_{mode}.log")
        if r.returncode != 0:
            print(r.stderr[-3000:])
            return 1
    with open(GOLD_FORECAST, encoding='utf-8') as f:
        gold_f = json.load(f)["result"]
    A, B = load_day("A"), load_day("B")
    for mode, d in (("A", A), ("B", B)):
        print(f"[p4] {mode} 輪狀態 {d['status']}：", [(s['step'], s['status']) for s in d['steps']])
    print("=== A 輪 vs 桌面版 golden(處置預測) ===")
    na = diff_forecast(gold_f, A["forecast"], "golden→A")
    print("=== A 輪 vs 桌面版 golden(儀表板) ===")
    na += diff_dashboard(A, "golden→A")
    print(f"A 輪差異 {na} 項")
    print("=== B 輪(官方股價) vs A 輪(桌面股價) ===")
    nb = diff_forecast(A["forecast"], B["forecast"], "A→B")
    nb += diff_dashboard(B, "golden→B")
    print(f"B 輪差異 {nb} 項(股價來源)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
