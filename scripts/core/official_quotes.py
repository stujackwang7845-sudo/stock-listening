"""
官方全市場日行情(雲端版股價來源，取代 FinMind / Shioaji 逐檔抓取)。

每個交易日只打 4 個請求：
  上市日行情  TWSE MI_INDEX(type=ALLBUT0999，不含權證)
  上櫃日行情  TPEx dailyQuotes(含「管理股票」表)
  上市本益比  TWSE BWIBBU_d
  上櫃本益比  TPEx peQryDate
股本另由 StockFetcher.fetch_all_shares_outstanding(官方 openapi，最新一份)提供。

寫進 market_data.db 既有的表(MarketDataCache 的結構)，核心計算不用改就讀得到：
  price_history(stock_id, date YYYY-MM-DD, open, high, low, close, volume)
    volume 一律是「股數」(官方欄位「成交股數」)，calculator.py 60均量就是以股數計算。
  ratios(stock_id, date, per, pbr)   本益比/淨值比，「-」(虧損或無資料)存 NULL
  stock_info(stock_id, shares_outstanding, updated_at)

防呆(2026-07-08 TPEx 快照污染事故的教訓，見 agent_ops.md)：
  回應內容自帶的日期與請求日期不符就整天拒收，不會把別天的資料標成請求日期寫進去。
休市判定：上市、上櫃兩邊都確認「該日無資料」且日期已經過去，才記為休市
  (寫入 twse_holidays.json，DateUtils 會讀)；只有一邊沒資料視為異常，不記休市。

執行(專案根目錄)：
  uv run python scripts/core/official_quotes.py --end 20261002 --days 90 --data-dir temp/x
"""
import json
import os
import random
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import requests

TWSE_QUOTES = ("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date={ymd}&type=ALLBUT0999",
               "https://www.twse.com.tw/zh/trading/historical/mi-index.html")
TPEX_QUOTES = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={slash}&response=json",
               "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html")
TWSE_RATIOS = ("https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={ymd}&selectType=ALL&response=json",
               "https://www.twse.com.tw/zh/trading/historical/bwibbu-day.html")
TPEX_RATIOS = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date={slash}&response=json",
               "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pe.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

# 上市：證券代號 4~6 碼(股票、ETF、ETN)；上櫃 dailyQuotes 另含可轉債(5碼)、權證(6碼)等上萬列，
# 只留 4 碼股票與 00 開頭 ETF(與 DB 專案 tpex_parser 相同規則)
_TWSE_CODE = re.compile(r"^[0-9A-Z]{4,6}$")


class DateMismatchError(Exception):
    """回應內容的日期與請求日期不符(拒收整天)。"""


def _num(value):
    """官方數字欄位 → float；空值、'--'、'-'、'X' 之類回傳 None。"""
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _roc_to_date(text):
    """'115/10/02' 或 '115年10月02日' → date；找不到回傳 None。"""
    m = re.search(r"(\d{2,3})\D(\d{1,2})\D(\d{1,2})", text or "")
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y + 1911, mo, d)
    except ValueError:
        return None


def _check_payload_date(payload, target, market):
    """回應頂層 date(YYYYMMDD) 必須等於請求日期。"""
    got = str(payload.get("date") or "")
    if got != target.strftime("%Y%m%d"):
        raise DateMismatchError(f"{market} 請求 {target} 但回應 date={got!r}")


def _find_tables(payload, required):
    return [t for t in payload.get("tables") or []
            if all(f in (t.get("fields") or []) for f in required)]


def parse_twse_quotes(payload, target):
    """MI_INDEX JSON → {code: row}；該日無資料回傳 None。"""
    if payload.get("stat") != "OK":
        return None
    _check_payload_date(payload, target, "TWSE")
    tables = _find_tables(payload, ("證券代號", "收盤價", "成交股數"))
    if not tables:
        return None
    t = tables[0]
    title_date = _roc_to_date(t.get("title"))
    if title_date is not None and title_date != target:
        raise DateMismatchError(f"TWSE 表頭日期 {title_date} ≠ 請求 {target}")
    idx = {f: i for i, f in enumerate(t["fields"])}
    rows = {}
    for r in t.get("data") or []:
        code = str(r[idx["證券代號"]]).replace("=", "").replace('"', "").strip()
        if not _TWSE_CODE.match(code):
            continue
        rows[code] = {
            "name": str(r[idx["證券名稱"]]).strip(),
            "open": _num(r[idx["開盤價"]]), "high": _num(r[idx["最高價"]]),
            "low": _num(r[idx["最低價"]]), "close": _num(r[idx["收盤價"]]),
            "volume": _num(r[idx["成交股數"]]), "market": "上市",
        }
    return rows or None


def parse_tpex_quotes(payload, target):
    """dailyQuotes JSON(上櫃股票行情 + 管理股票兩張表) → {code: row}；無資料回傳 None。"""
    if str(payload.get("stat", "")).lower() != "ok":
        return None
    _check_payload_date(payload, target, "TPEx")
    rows = {}
    for t in _find_tables(payload, ("代號", "收盤", "成交股數")):
        title_date = _roc_to_date(t.get("date"))
        if title_date is not None and title_date != target:
            raise DateMismatchError(f"TPEx 表頭日期 {title_date} ≠ 請求 {target}")
        idx = {f: i for i, f in enumerate(t["fields"])}
        for r in t.get("data") or []:
            code = str(r[idx["代號"]]).strip()
            if not (len(code) == 4 or code.startswith("00")):
                continue
            rows[code] = {
                "name": str(r[idx["名稱"]]).strip(),
                "open": _num(r[idx["開盤"]]), "high": _num(r[idx["最高"]]),
                "low": _num(r[idx["最低"]]), "close": _num(r[idx["收盤"]]),
                "volume": _num(r[idx["成交股數"]]), "market": "上櫃",
            }
    return rows or None


def parse_twse_ratios(payload, target):
    """BWIBBU_d JSON → {code: (per, pbr)}；無資料回傳 None。"""
    if payload.get("stat") != "OK":
        return None
    _check_payload_date(payload, target, "TWSE 本益比")
    fields = payload.get("fields") or []
    if "證券代號" not in fields:
        return None
    i_code, i_per, i_pbr = fields.index("證券代號"), fields.index("本益比"), fields.index("股價淨值比")
    out = {str(r[i_code]).strip(): (_num(r[i_per]), _num(r[i_pbr])) for r in payload.get("data") or []}
    return out or None


def parse_tpex_ratios(payload, target):
    """peQryDate JSON → {code: (per, pbr)}；無資料回傳 None。"""
    if str(payload.get("stat", "")).lower() != "ok":
        return None
    _check_payload_date(payload, target, "TPEx 本益比")
    out = {}
    for t in _find_tables(payload, ("股票代號", "本益比", "股價淨值比")):
        title_date = _roc_to_date(t.get("date"))
        if title_date is not None and title_date != target:
            raise DateMismatchError(f"TPEx 本益比表頭日期 {title_date} ≠ 請求 {target}")
        f = t["fields"]
        i_code, i_per, i_pbr = f.index("股票代號"), f.index("本益比"), f.index("股價淨值比")
        for r in t.get("data") or []:
            out[str(r[i_code]).strip()] = (_num(r[i_per]), _num(r[i_pbr]))
    return out or None


class OfficialClient:
    """官方網站請求：完整瀏覽器 headers + Referer、請求間隔 2~3 秒、指數退避重試。"""

    def __init__(self, min_interval=2.0, jitter=1.0, retries=5, sleep=time.sleep):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.min_interval = min_interval
        self.jitter = jitter
        self.retries = retries
        self._sleep = sleep
        self._last = 0.0

    def _throttle(self):
        wait = self._last + self.min_interval + random.uniform(0, self.jitter) - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def get_json(self, url, referer):
        last_err = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                res = self.session.get(url, headers={"Referer": referer}, timeout=30)
                if res.status_code in (403, 429) or res.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {res.status_code}")
                res.raise_for_status()
                return res.json()
            except (requests.RequestException, ValueError) as e:
                last_err = e
                if attempt < self.retries - 1:
                    delay = min(120, 5 * 2 ** attempt) + random.uniform(0, 3)
                    print(f"[official_quotes] {e}；{delay:.0f} 秒後重試({attempt + 1}/{self.retries}): {url}")
                    self._sleep(delay)
        raise RuntimeError(f"官方 API 重試 {self.retries} 次仍失敗: {url} ({last_err})")


@dataclass
class DayQuotes:
    day: date
    status: str                      # ok / holiday / pending(還沒公布) / partial(只有一邊有資料)
    quotes: dict = field(default_factory=dict)   # {code: row}
    ratios: dict = field(default_factory=dict)   # {code: (per, pbr)}
    note: str = ""


def _fmt(target):
    return {"ymd": target.strftime("%Y%m%d"), "slash": target.strftime("%Y/%m/%d")}


def fetch_day(client, target, with_ratios=False, today=None):
    """抓一天的上市＋上櫃行情(可選本益比)。"""
    today = today or date.today()
    twse = parse_twse_quotes(client.get_json(TWSE_QUOTES[0].format(**_fmt(target)), TWSE_QUOTES[1]), target)
    tpex = parse_tpex_quotes(client.get_json(TPEX_QUOTES[0].format(**_fmt(target)), TPEX_QUOTES[1]), target)

    if twse is None and tpex is None:
        # 今天(或未來)查無資料只代表還沒公布，不能記成休市
        status = "holiday" if target < today else "pending"
        return DayQuotes(target, status)
    if twse is None or tpex is None:
        missing = "上市" if twse is None else "上櫃"
        return DayQuotes(target, "partial", {**(twse or {}), **(tpex or {})}, note=f"{missing}無資料")

    day = DayQuotes(target, "ok", {**twse, **tpex})
    if with_ratios:
        r1 = parse_twse_ratios(client.get_json(TWSE_RATIOS[0].format(**_fmt(target)), TWSE_RATIOS[1]), target)
        r2 = parse_tpex_ratios(client.get_json(TPEX_RATIOS[0].format(**_fmt(target)), TPEX_RATIOS[1]), target)
        day.ratios = {**(r1 or {}), **(r2 or {})}
        if r1 is None or r2 is None:
            day.note = "本益比缺" + ("上市" if r1 is None else "") + ("上櫃" if r2 is None else "")
    return day


def save_day(day, market_db):
    """寫入 price_history(及 ratios)。沒有收盤價(當天無成交)的不寫，回傳寫入列數。"""
    from core.market_cache import MarketDataCache
    MarketDataCache(market_db)  # 確保資料表存在
    d = day.day.strftime("%Y-%m-%d")
    price_rows = [(code, d, q["open"], q["high"], q["low"], q["close"], q["volume"])
                  for code, q in day.quotes.items() if q["close"] is not None]
    ratio_rows = [(code, d, per, pbr) for code, (per, pbr) in day.ratios.items()]
    conn = sqlite3.connect(market_db)
    try:
        with conn:
            conn.executemany("INSERT OR REPLACE INTO price_history (stock_id, date, open, high, low, close, volume) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", price_rows)
            if ratio_rows:
                conn.executemany("INSERT OR REPLACE INTO ratios (stock_id, date, per, pbr) VALUES (?, ?, ?, ?)",
                                 ratio_rows)
    finally:
        conn.close()
    return len(price_rows)


def record_holiday(target, holidays_json):
    """休市日併入 twse_holidays.json({"holidays": [YYYY-MM-DD...]}，DateUtils 讀的格式)。"""
    data = {"holidays": []}
    if os.path.exists(holidays_json):
        with open(holidays_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    days = set(data.get("holidays", []))
    iso = target.strftime("%Y-%m-%d")
    if iso in days:
        return False
    days.add(iso)
    data["holidays"] = sorted(days)
    tmp = holidays_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, holidays_json)
    return True


def stored_days(market_db, min_rows=1000):
    """已完整寫入的日期(全市場列數 ≥ min_rows)，回補時跳過，可中斷續跑。"""
    if not os.path.exists(market_db):
        return set()
    conn = sqlite3.connect(market_db)
    try:
        rows = conn.execute("SELECT date FROM price_history GROUP BY date HAVING COUNT(*) >= ?", (min_rows,))
        return {datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def update_shares(market_db):
    """全市場已發行股數(官方 openapi，最新一份)寫入 stock_info，回傳檔數。"""
    from core.fetcher import StockFetcher
    from core.market_cache import MarketDataCache
    shares = StockFetcher(db_path=market_db).fetch_all_shares_outstanding()
    MarketDataCache(market_db).save_stock_info_bulk(shares)
    return len(shares)


def backfill(end, n_days, market_db, holidays_json, client=None, today=None, log=print):
    """
    從 end(含)往回補到湊滿 n_days 個交易日；end 當天另抓本益比。
    已在資料庫的日期跳過。回傳 {date: status}。
    """
    from core.utils import DateUtils
    client = client or OfficialClient()
    have = stored_days(market_db)
    result = {}
    trading = 0
    cur = end
    while trading < n_days:
        if cur.weekday() >= 5:
            cur -= timedelta(days=1)
            continue
        if cur in have and cur != end:
            result[cur] = "stored"
            trading += 1
            cur -= timedelta(days=1)
            continue
        if cur != end and not DateUtils.is_trading_day(cur):
            result[cur] = "holiday(known)"
            cur -= timedelta(days=1)
            continue
        day = fetch_day(client, cur, with_ratios=(cur == end), today=today)
        result[cur] = day.status
        if day.status == "ok":
            n = save_day(day, market_db)
            trading += 1
            log(f"[official_quotes] {cur} 寫入 {n} 檔{('，本益比 ' + str(len(day.ratios)) + ' 檔') if day.ratios else ''}"
                f"{('，' + day.note) if day.note else ''}")
        elif day.status == "holiday":
            record_holiday(cur, holidays_json)
            log(f"[official_quotes] {cur} 上市上櫃皆無資料，記為休市")
        else:
            # partial / pending：不寫入，仍算一個交易日位置，避免往回多抓
            trading += 1
            log(f"[official_quotes] {cur} {day.status} {day.note}")
        cur -= timedelta(days=1)
    return result


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="官方全市場日行情回補")
    p.add_argument("--end", required=True, help="YYYYMMDD(含)")
    p.add_argument("--days", type=int, default=90, help="交易日數")
    p.add_argument("--data-dir", help="設定 DISPO_DATA_DIR(不給就用桌面版路徑)")
    p.add_argument("--no-shares", action="store_true", help="不更新股本")
    a = p.parse_args(argv)
    if a.data_dir:
        os.makedirs(a.data_dir, exist_ok=True)
        os.environ["DISPO_DATA_DIR"] = a.data_dir
    from core.runtime import get_paths
    paths = get_paths()
    end = datetime.strptime(a.end, "%Y%m%d").date()
    res = backfill(end, a.days, paths.market_db, paths.twse_holidays_json)
    print(f"[official_quotes] 完成：{sum(1 for s in res.values() if s == 'ok')} 天新寫入，"
          f"{sum(1 for s in res.values() if s == 'stored')} 天已存在，"
          f"異常 {[str(d) for d, s in res.items() if s in ('partial', 'pending')]}")
    if not a.no_shares:
        print(f"[official_quotes] 股本 {update_shares(paths.market_db)} 檔")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
