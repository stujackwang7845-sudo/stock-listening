"""
股價快取(market_data.db price_history)補洞(2026-10-18)。

問題：fetch_stock_history 只比對「快取最後一天」，中間某天沒抓到(官網/券商臨時失敗)之後就永遠不會回補；
calculator 用「往前數第幾筆」算 6 日/30 日漲幅，缺一天就會算到更早的日期，條件整個錯掉。
實測 22 檔有 17 檔中間有洞(例 2059 缺 8/20~8/31)。

做法(只補缺的日子、INSERT OR IGNORE，不覆蓋既有資料)：
  1. 交易日曆：DB 專案官方行情 parquet 出現過的日期(颱風假等臨時休市也正確)；沒有 parquet 才用 DateUtils。
  2. 缺的日子先從 parquet 補(本機、官方、完整)。
  3. 還缺且允許連網 → FinMind 日K 補。
  4. 還缺 → 回傳缺的日期，由呼叫端在畫面上標「資料不完整」，不默默算錯。
"""
import os
import sqlite3
from datetime import date, datetime, timedelta

PARQUET_ROOT = os.environ.get("DISPO_PRICE_PARQUET",
                              r"E:\Vibe Coding\Stock\DB\parquet_data\price_daily_raw")
_month_cache = {}      # (market, year, month) -> {symbol: {date: (o, h, l, c, v)}}，None = 檔案不存在
_fm_tried = set()      # 本次執行已上網補過的股票


def parquet_available():
    if os.environ.get("DISPO_NO_PARQUET"):
        return False
    return os.path.isdir(PARQUET_ROOT)


def _load_month(market, y, m):
    key = (market, y, m)
    if key in _month_cache:
        return _month_cache[key]
    f = os.path.join(PARQUET_ROOT, f"market={market}", f"year={y}", f"month={m}", "data.parquet")
    out = None
    if os.path.exists(f):
        import pyarrow.parquet as pq
        # read_table 會套 hive 分區欄位而型別衝突，用 ParquetFile.read
        t = pq.ParquetFile(f).read(columns=["date", "symbol", "open", "high", "low", "close", "volume"]).to_pydict()
        out = {}
        for d, s, o, h, l, c, v in zip(t["date"], t["symbol"], t["open"], t["high"], t["low"], t["close"], t["volume"]):
            if c is None or c != c or c <= 0:
                continue
            out.setdefault(str(s).strip(), {})[d] = (o, h, l, c, v)
    _month_cache[key] = out
    return out


def _months(lo, hi):
    y, m = lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def parquet_days(lo, hi):
    """parquet 有資料的交易日(市場層級)與涵蓋的月份。"""
    days, covered = set(), set()
    if parquet_available():
        for y, m in _months(lo, hi):
            mon = _load_month("TWSE", y, m) or _load_month("TPEX", y, m)
            if mon:
                covered.add((y, m))
                for rows in mon.values():
                    days.update(rows.keys())
    return {d for d in days if lo <= d <= hi}, covered


def trading_days(lo, hi):
    """lo~hi(含)的交易日。parquet 有涵蓋的月份以 parquet 為準，其餘用 DateUtils。"""
    days, covered = parquet_days(lo, hi)
    # parquet 最後一天之後(例如今天還沒進 DB)與沒涵蓋的月份 → DateUtils
    last_pq = max(days) if days else None
    from core.utils import DateUtils
    d = lo
    while d <= hi:
        if ((d.year, d.month) not in covered or (last_pq and d > last_pq)) and \
                DateUtils.is_trading_day(datetime.combine(d, datetime.min.time())):
            days.add(d)
        d += timedelta(days=1)
    return sorted(days)


def parquet_rows(code, days):
    """{date: (o, h, l, c, v)}，v 為成交股數(跟快取同單位)。"""
    out = {}
    if not parquet_available() or not days:
        return out
    want = set(days)
    for y, m in _months(min(days), max(days)):
        for mk in ("TWSE", "TPEX"):
            mon = _load_month(mk, y, m)
            if mon and code in mon:
                for d, row in mon[code].items():
                    if d in want:
                        out[d] = row
    return out


def insert_missing(db_path, code, rows):
    """只新增，不覆蓋既有資料。回傳實際新增筆數。"""
    if not rows:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO price_history (stock_id, date, open, high, low, close, volume) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(code, d.isoformat(), *r) for d, r in sorted(rows.items())])
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()


def find_missing(have_dates, lo, hi):
    have = {d.date() if isinstance(d, datetime) else d for d in have_dates}
    return [d for d in trading_days(lo, hi) if d not in have]


def fill_code(db_path, code, have_dates, lo, hi, fm_client=None, allow_fetch=False):
    """
    補 code 在 lo~hi 缺的交易日。回傳 (新增筆數, 仍缺的日期)。
    have_dates：快取現有日期(date 或 datetime)。
    """
    missing = find_missing(have_dates, lo, hi)
    if not missing:
        return 0, []
    rows = parquet_rows(code, missing)
    added = insert_missing(db_path, code, rows)
    # parquet 有那天的全市場行情、卻沒有這檔 → 這檔當天沒交易(停牌/暫停交易)，不算缺
    pq_days, _ = parquet_days(min(missing), max(missing))
    still = [d for d in missing if d not in rows and d not in pq_days]
    if still and allow_fetch and fm_client is not None and code not in _fm_tried:
        _fm_tried.add(code)          # 同一個程式執行期間每檔只上網補一次，補不到就標示，不要每次切日期都重打
        try:
            fm = fm_client.fetch_daily_price(code, min(still).isoformat(), max(still).isoformat())
            if fm is not None and not fm.empty:
                got = {}
                want = set(still)
                for r in fm.itertuples():
                    d = r.date if isinstance(r.date, date) else datetime.strptime(str(r.date)[:10], "%Y-%m-%d").date()
                    if d in want and r.close and r.close > 0:
                        got[d] = (r.open, r.max, r.min, r.close, getattr(r, "Trading_Volume", None))
                added += insert_missing(db_path, code, got)
                still = [d for d in still if d not in got]
        except Exception as e:
            print(f"[price_gapfill] {code} FinMind 補洞失敗：{e}")
    if added:
        print(f"[price_gapfill] {code} 補了 {added} 天；仍缺 {len(still)} 天")
    return added, still


def missing_text(days, limit=5):
    s = "、".join(f"{d:%m/%d}" for d in days[:limit])
    return s + (f" 等 {len(days)} 天" if len(days) > limit else "")


# ---------------------------------------------------------------- 本益比/淨值比(ratios 表)
PER_ROOT = os.environ.get("DISPO_PER_PARQUET", r"E:\Vibe Coding\Stock\DB\parquet_data\per_daily_raw")
_per_year = {}         # year -> {(symbol, date): (per, pbr)}


def _load_per_year(y):
    if y in _per_year:
        return _per_year[y]
    out = {}
    if not os.environ.get("DISPO_NO_PARQUET"):
        import pyarrow.parquet as pq
        for mk in ("TWSE", "TPEx"):
            f = os.path.join(PER_ROOT, f"market={mk}", f"year={y}", "data.parquet")
            if not os.path.exists(f):
                continue
            t = pq.ParquetFile(f).read(columns=["date", "stock_id", "per", "pbr"]).to_pydict()
            for d, s, a, b in zip(t["date"], t["stock_id"], t["per"], t["pbr"]):
                # 官方「-」(虧損/無資料)在 parquet 是 0，快取慣例存 NULL(official_quotes 同)
                out[(str(s).strip(), d)] = (a if a else None, b if b else None)
    _per_year[y] = out
    return out


def fill_ratios(db_path, code, price_dates, lo, hi):
    """有股價、沒本益比的日子用 DB 專案 per_daily_raw 補(只新增)。回傳新增筆數。"""
    want = sorted({d.date() if isinstance(d, datetime) else d for d in price_dates})
    want = [d for d in want if lo <= d <= hi]
    if not want:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        have = {date.fromisoformat(r[0][:10]) for r in conn.execute(
            "SELECT date FROM ratios WHERE stock_id = ? AND date BETWEEN ? AND ?", (code, lo.isoformat(), hi.isoformat()))}
        rows = []
        for d in want:
            if d in have:
                continue
            v = _load_per_year(d.year).get((code, d))
            if v is not None:
                rows.append((code, d.isoformat(), v[0], v[1]))
        if not rows:
            return 0
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO ratios (stock_id, date, per, pbr) VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()
