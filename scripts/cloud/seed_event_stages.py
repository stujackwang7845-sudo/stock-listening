"""
統計圖表的種子資料(在家裡電腦一次性執行)：用本機 DB 專案的官方日 K parquet 算出全部處置事件
各階段價格，存成 event_stages.json 放進雲端狀態資料夾。之後雲端每天只用官方行情重算近期事件，
舊事件沿用這份(雲端只保留約 90 個交易日的行情)。

  uv run python scripts/cloud/seed_event_stages.py --out <狀態資料夾>/event_stages.json
  (--db 預設 data/disposal_history.db；--parquet 預設 DB 專案 price_daily_raw)
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.disposal_stats_analytics import DEFAULT_DB_PATH, DEFAULT_PRICE_ROOT, export_event_stages, load_prices  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description="匯出統計圖表種子 event_stages.json")
    p.add_argument("--out", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--parquet", default=str(DEFAULT_PRICE_ROOT))
    a = p.parse_args(argv)
    export = export_event_stages(db_path=a.db, prices_loader=lambda symbols: load_prices(symbols, a.parquet),
                                 as_of=date.today())
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, a.out)
    with_prices = sum(1 for e in export["events"] if e["p"])
    print(f"[seed] {len(export['events'])} 個事件(有價格 {with_prices})，{os.path.getsize(a.out) / 1024:.0f} KB → {a.out}")


if __name__ == "__main__":
    main()
