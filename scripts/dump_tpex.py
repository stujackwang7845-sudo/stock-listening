import json
from core.fetcher import StockFetcher
from core.parser import StockParser

fetcher = StockFetcher()
parser = StockParser()
raw_data = fetcher.fetch_tpex_disposition("20260527") # Try 0527
if not raw_data:
    raw_data = fetcher.fetch_tpex_disposition() # Try all

parsed = parser.parse_tpex_disposition(raw_data)
with open("../temp/tpex_parsed.json", "w", encoding="utf-8") as f:
    json.dump(parsed, f, ensure_ascii=False, indent=2)

with open("../temp/tpex_raw.json", "w", encoding="utf-8") as f:
    json.dump(raw_data, f, ensure_ascii=False, indent=2)
