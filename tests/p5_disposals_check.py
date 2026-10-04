"""
P5 處置統計表匯出驗證：export_disposal_table 的 JSON，照網頁端規則(預設篩選：只顯示4碼、
公告日 2000-01-01～今天、處置開始日新→舊)重建整張表，必須與桌面版 DisposalStatsPage
(offscreen)同一份資料畫出來的表格逐格相同，含底部 10 列統計。
render_from_export 是網頁版 JS 的 Python 對照實作，結果另存成 fixtures 給 P7 的 JS 測試用。

執行(專案根目錄)：uv run python tests/p5_disposals_check.py
資料：P2 凍結快照的副本(temp/p5/stats_work)，離線模式 compute_stats_rows。
"""
import copy
import json
import os
import shutil
import sys
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WORK = os.path.join(ROOT, 'temp', 'p5', 'stats_work')
SNAP = os.path.join(ROOT, 'temp', 'p2_golden', 'snapshot')
OUT = os.path.join(ROOT, 'temp', 'p5')


def setup():
    if not os.path.isdir(WORK):
        shutil.copytree(SNAP, WORK)
    os.environ.update({"DISPO_DATA_DIR": WORK, "DISPO_NO_FINMIND": "1", "DISPO_NO_SHIOAJI": "1",
                       "DISPO_CB_CSV_PATH": os.path.join(WORK, "cb_data.csv"),
                       "QT_QPA_PLATFORM": "offscreen"})
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))


def fmt_change(v):
    """populate_table 的漲跌幅欄文字。"""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):+.2f}%"
    except (ValueError, TypeError):
        return str(v)


def render_from_export(export, today):
    """網頁端預設篩選與排序後的表格文字(對照 DisposalStatsPage.apply_filters + populate_table)。"""
    from core.disposal_stats_engine import summary_stat_texts
    F = {f: i for i, f in enumerate(export["fields"])}
    rows = [r for r in export["rows"] if len(str(r[F["code"]]).strip()) == 4]
    lo, hi = "2000-01-01", today.isoformat()
    rows = [r for r in rows if not r[F["announce_date"]] or lo <= r[F["announce_date"]] <= hi]

    def start_iso(r):
        s = r[F["start_date"]]
        return s.replace("/", "-") if s else ""
    rows.sort(key=start_iso, reverse=True)

    cols = export["columns"]
    shown = sorted({i for r in rows for i in export["header_sets"][r[F["h"]]]})
    headers = ["公告日", "股號", "股名", "股本(億)", "頻率", "處置天數", "初犯/累犯", "處置開始", "處置結束"] + \
        [cols[i] for i in shown]
    grid = []
    for r in rows:
        cap = r[F["capital"]]
        grid.append([r[F["announce_date"]], r[F["code"]], r[F["name"]], f"{cap:.2f}" if cap else "N/A",
                     r[F["freq"]], r[F["duration"]], r[F["offense"]], r[F["start_date"]], r[F["end_date"]]]
                    + [fmt_change(r[F["v"]][i]) for i in shown])
    labels = ["📊 總計", "📊 平均", "📊 上漲%", "📊 下跌%", "📊 >9% 佔比", "📊 <-9% 佔比",
              "📊 期望值", "📊 獲利因子", "📊 風險報酬比", "📊 凱利倉位"]
    col_texts = []
    for i in shown:
        values = []
        for r in rows:
            v = r[F["v"]][i]
            if isinstance(v, (int, float)):
                values.append(v)
            elif isinstance(v, str):
                try:
                    values.append(float(v))
                except ValueError:
                    pass
        col_texts.append(summary_stat_texts(values))
    if shown and rows:
        for k, label in enumerate(labels):
            grid.append([label] + [""] * 8 + [(t[k] if t else "N/A") for t in col_texts])
    return headers, grid


def render_desktop(processed):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    import ui.disposal_stats_page as sp
    sp.DisposalStatsPage.load_data_from_db = lambda self, *a, **k: None   # 不啟動背景 worker
    page = sp.DisposalStatsPage()
    page.on_data_ready(copy.deepcopy(processed))
    t = page.table
    headers = [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())]
    grid = [[(t.item(r, c).text() if t.item(r, c) else "") for c in range(t.columnCount())]
            for r in range(t.rowCount())]
    return page, headers, grid, app


def main():
    setup()
    import pickle
    from core.disposal_database import DisposalDatabase
    from core.disposal_stats_engine import compute_stats_rows, export_disposal_table
    pkl = os.path.join(OUT, 'processed_offline.pkl')
    if os.path.exists(pkl):
        with open(pkl, 'rb') as f:
            processed = pickle.load(f)
    else:
        processed = compute_stats_rows(DisposalDatabase().get_all_records(), allow_download=False)
        with open(pkl, 'wb') as f:
            pickle.dump(processed, f)

    page, d_headers, d_grid, _app = render_desktop(processed)
    today = date.today()
    export = export_disposal_table(processed, cb_db=page.cb_db, mf_db=page.mf_db, as_of=today)
    path = os.path.join(OUT, 'disposals.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, separators=(",", ":"))
    print(f"匯出 {len(export['rows'])} 列、{len(export['columns'])} 個漲跌幅欄，{os.path.getsize(path) / 1024:.0f} KB")

    w_headers, w_grid = render_from_export(export, today)
    bad = 0
    if d_headers != w_headers:
        bad += 1
        print(f"表頭不同\n 桌面 {d_headers}\n 網頁 {w_headers}")
    if len(d_grid) != len(w_grid):
        bad += 1
        print(f"列數不同：桌面 {len(d_grid)} 網頁 {len(w_grid)}")
    for i, (x, y) in enumerate(zip(d_grid, w_grid)):
        if x != y:
            bad += 1
            if bad <= 10:
                print(f"第 {i} 列不同\n 桌面 {x}\n 網頁 {y}")
    print(f"桌面 {len(d_grid)} 列(含 10 列統計) × {len(d_headers)} 欄；結果：", "全部相同" if bad == 0 else f"{bad} 處不同")
    with open(os.path.join(OUT, 'disposals_fixture.json'), 'w', encoding='utf-8') as f:
        json.dump({"as_of": today.isoformat(), "headers": d_headers, "grid": d_grid}, f, ensure_ascii=False)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
