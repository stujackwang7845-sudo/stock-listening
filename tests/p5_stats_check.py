"""
P5 統計圖表匯出驗證：export_event_stages 的逐事件資料重新彙總，必須與桌面版
build_disposal_stats_dataset(全部 / 60 日 / 一年)的每個分桶逐值相同。
aggregate_exported 是網頁版 JS 彙總的 Python 對照實作，結果另存成 fixtures 給 P7 的 JS 測試用。

執行(專案根目錄)：uv run python tests/p5_stats_check.py
資料庫用 P2 凍結快照(temp/p2_golden/snapshot)，日 K 用 DB 專案官方 parquet；
另用 P3 官方回補(temp/p3_official/d90)驗證雲端價格來源算出的近期事件與 parquet 相同。
"""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from core import disposal_stats_analytics as a  # noqa: E402

DB = os.path.join(ROOT, 'temp', 'p2_golden', 'snapshot', 'disposal_history.db')
OFFICIAL_DB = os.path.join(ROOT, 'temp', 'p3_official', 'd90', 'market_data.db')
OUT = os.path.join(ROOT, 'temp', 'p5')
WINDOWS = (None, 60, 365)


def aggregate_exported(export, bucket, window_days=None, as_of=None, post_days=a.DEFAULT_POST_DAYS):
    """網頁版 JS 彙總的對照實作(與 aggregate_interval 同規則，輸入改成匯出的逐事件價格)。"""
    as_of = as_of or date.fromisoformat(export["as_of"])
    cutoff = (as_of - timedelta(days=window_days)).isoformat() if window_days is not None else None
    min_sample, bucket_max_t = a.bucket_rules(bucket)
    allowed = set(a._build_stage_labels(bucket_max_t, post_days))

    buckets = {}
    used = total = 0
    for row in export["events"]:
        if isinstance(bucket, str):
            if row["b"] != bucket:
                continue
        elif row["i"] != bucket:
            continue
        if cutoff is not None and row["s"] < cutoff:
            continue
        total += 1
        metrics = {}
        for label, (prev_close, open_price, close_price) in row["p"].items():
            if label in allowed:
                m = a._record_metrics(pd.Series({"open": open_price, "close": close_price, "prev_close": prev_close}))
                if m is not None:
                    metrics[label] = m
        if not metrics:
            continue
        used += 1
        for label, m in metrics.items():
            buckets.setdefault(label, []).append(m)

    rows = []
    for label in a._build_stage_labels(bucket_max_t, post_days):
        records = buckets.get(label, [])
        if len(records) < min_sample:
            continue
        if not records:
            rows.append([label, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            continue
        returns = [float(r["return_pct"]) for r in records]
        bodies = [float(r["body_pct"]) for r in records]
        rows.append([
            label, len(records),
            round(sum(returns) / len(returns), 2),
            round(sum(bodies) / len(bodies), 2),
            round(sum(1 for v in returns if v > 0) / len(returns) * 100.0, 1),
            round(sum(1 for r in records if bool(r["red"])) / len(records) * 100.0, 1),
            round(sum(1 for r in records if bool(r["black"])) / len(records) * 100.0, 1),
            round(sum(1 for r in records if bool(r["flat"])) / len(records) * 100.0, 1),
        ])
    return rows, used, total


def main():
    os.makedirs(OUT, exist_ok=True)
    today = date.today()   # build_disposal_stats_dataset 的窗口以「今天」為基準
    export = a.export_event_stages(db_path=DB, prices_loader=a.load_prices, as_of=today)
    path = os.path.join(OUT, 'event_stages.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, separators=(",", ":"))
    print(f"匯出 {len(export['events'])} 個事件，{os.path.getsize(path) / 1024:.0f} KB")

    bad = 0
    fixtures = []
    for w in WINDOWS:
        ds = a.build_disposal_stats_dataset(db_path=DB, force_refresh=True,
                                            report_dir=os.path.join(OUT, 'report_check'), window_days=w)
        for key, frame in ds.frames.items():
            rows, used, total = aggregate_exported(export, key, w, today)
            want = frame.values.tolist()
            if rows != want or (used, total) != ds.event_counts[key]:
                bad += 1
                print(f"  ✘ 窗口 {w} 分桶 {key}: 事件數 {(used, total)} vs {ds.event_counts[key]}")
                for x, y in zip(rows, want):
                    if x != y:
                        print(f"     對照 {x}\n     桌面 {y}")
            fixtures.append({"window_days": w, "bucket": key, "rows": rows, "used": used, "total": total})
        print(f"窗口 {w}: {len(ds.frames)} 個分桶比對完成")
    with open(os.path.join(OUT, 'event_stage_fixtures.json'), 'w', encoding='utf-8') as f:
        json.dump({"as_of": today.isoformat(), "cases": fixtures}, f, ensure_ascii=False)

    # 雲端價格來源：用官方回補(約 90 交易日)重算，涵蓋期間內的事件必須與 parquet 版相同
    cloud = a.export_event_stages(
        db_path=DB, prices_loader=lambda syms: a.load_prices_from_market_db(syms, OFFICIAL_DB),
        as_of=today, keep=export, recompute_since=today - timedelta(days=60))
    base = {(r["i"], r["c"], r["s"], r["e"]): r["p"] for r in export["events"]}
    diff = [(r["c"], r["s"]) for r in cloud["events"] if r["p"] != base[(r["i"], r["c"], r["s"], r["e"])]]
    recomputed = sum(1 for r in cloud["events"] if r["e"] >= (today - timedelta(days=60)).isoformat())
    print(f"雲端來源：重算 {recomputed} 個近期事件，與 parquet 版不同 {len(diff)} 個 {diff[:10]}")
    bad += len(diff)

    print("結果：", "全部相同" if bad == 0 else f"{bad} 項不同")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
