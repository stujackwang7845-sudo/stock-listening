"""
用 DB 專案還原股價(adjusted_price_daily 的 adj_close)補處置統計的空白漲跌幅(2026-10-14 使用者要求)。

  - 單日漲跌幅 = 當天還原收盤 / 該股前一個有成交日的還原收盤 - 1(%，兩位小數)
  - 驗證：|漲跌幅| > 10.1%(台股漲跌幅上限 10%，多留 0.1% 給升降單位捨入)視為股價有問題，不補
  - 前一個有成交日隔超過 12 天(停牌、減資換發)不補：那不是正常的單日漲跌
  - 處置期間漲跌幅(多日)= 還原收盤比，不套 ±10%
只讀本機 parquet，不連網；找不到資料夾(例如雲端)就什麼都不補。
"""
import os
import time
from datetime import date, datetime

ADJ_ROOT = os.environ.get("DISPO_ADJ_ROOT", r"E:\Vibe Coding\Stock\DB\parquet_data\adjusted_price_daily")
LIMIT_PCT = 10.1
MAX_GAP_DAYS = 12

_month_cache = {}
_month_sig = {}        # (y, m) → (檔案修改時間簽名, 上次檢查的時間)
RECHECK_SEC = 30


def _sig(y, m):
    out = []
    for mk in ("TWSE", "TPEX"):
        f = os.path.join(ADJ_ROOT, f"market={mk}", f"year={y}", f"month={m}", "data.parquet")
        try:
            out.append(os.path.getmtime(f))
        except OSError:
            out.append(None)
    return tuple(out)


def _month(y, m):
    """{code: {iso日期: adj_close}}，上市＋上櫃同一個月。
    [2026-10-29] 常駐的桌面版開著時 DB 專案每天更新 parquet，舊快取會一直缺最新一天 → 檔案修改時間變了就重讀
    (最多每 RECHECK_SEC 秒才檢查一次)。"""
    key = (y, m)
    now = time.time()
    if key in _month_cache:
        sig, checked = _month_sig.get(key, (None, 0))
        if now - checked < RECHECK_SEC:
            return _month_cache[key]
        cur = _sig(y, m)
        if cur == sig:
            _month_sig[key] = (sig, now)
            return _month_cache[key]
    out = {}
    try:
        import pyarrow.parquet as pq
        for mk in ("TWSE", "TPEX"):
            f = os.path.join(ADJ_ROOT, f"market={mk}", f"year={y}", f"month={m}", "data.parquet")
            if not os.path.exists(f):
                continue
            # 整批轉換(逐列 isoformat 太慢：統計頁第一次載入會讀上百個月份檔)
            df = pq.ParquetFile(f).read(columns=["date", "symbol", "adj_close"]).to_pandas()
            df = df[df["adj_close"] > 0]
            df["d"] = df["date"].astype(str).str[:10]
            df["symbol"] = df["symbol"].astype(str).str.strip()
            for sym, g in df.groupby("symbol", sort=False):
                out.setdefault(sym, {}).update(zip(g["d"].tolist(), g["adj_close"].astype(float).tolist()))
    except Exception as e:
        print(f"[adjusted_prices] 讀取 {y}/{m} 失敗：{e}")
    _month_cache[key] = out
    _month_sig[key] = (_sig(y, m), now)
    return out


def _prev_month(y, m):
    return (y - 1, 12) if m == 1 else (y, m - 1)


def available():
    return os.path.isdir(ADJ_ROOT)


def adj_close(code, day):
    y, m = int(day[:4]), int(day[5:7])
    return _month(y, m).get(code, {}).get(day)


def _prev_close(code, day):
    """前一個有成交日的 (日期, 還原收盤)：往前找本月與前一個月。"""
    y, m = int(day[:4]), int(day[5:7])
    for yy, mm in ((y, m), _prev_month(y, m)):
        s = _month(yy, mm).get(code, {})
        ds = [d for d in s if d < day]
        if ds:
            d = max(ds)
            return d, s[d]
    return None, None


def daily_change(code, day):
    """回傳 (漲跌幅% 或 None, 原因)。"""
    c = adj_close(code, day)
    if c is None:
        return None, "還原股價沒有這天"
    pd_, pc = _prev_close(code, day)
    if pc is None:
        return None, "沒有前一個成交日"
    gap = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(pd_, "%Y-%m-%d")).days
    if gap > MAX_GAP_DAYS:
        return None, f"前一個成交日隔 {gap} 天"
    v = round((c / pc - 1) * 100, 2)
    if abs(v) > LIMIT_PCT:
        print(f"[adjusted_prices] {code} {day} 還原漲跌 {v:+.2f}% 超過 ±10%，判定股價有問題，不補")
        return None, "超過 ±10%"
    return v, ""


def period_change(code, d0, d1):
    """處置期間漲跌幅(還原收盤比)。任一天沒有、或期間內任一天單日漲跌超過 ±10%/停牌太久(還原股價可疑)就 None。"""
    a, b = adj_close(code, d0), adj_close(code, d1)
    if not (a and b):
        return None
    y0, m0, y1, m1 = int(d0[:4]), int(d0[5:7]), int(d1[:4]), int(d1[5:7])
    series, y, m = {}, y0, m0
    while (y, m) <= (y1, m1):
        series.update(_month(y, m).get(code, {}))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    path = sorted((d, c) for d, c in series.items() if d0 <= d <= d1)
    for (pd_, pc), (d, c) in zip(path, path[1:]):
        gap = (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(pd_, "%Y-%m-%d")).days
        if gap > MAX_GAP_DAYS or abs(c / pc - 1) * 100 > LIMIT_PCT:
            return None
    return round((b / a - 1) * 100, 2)


def fill_changes(code, changes, dates, skip=("處置期間漲跌幅",)):
    """把 changes 裡已收盤但空白的欄位用還原股價補上(就地修改)，回傳補上的欄位清單。"""
    if not available() or not changes:
        return []
    today = date.today().isoformat()
    got = []
    for k, v in list(changes.items()):
        d = (dates or {}).get(k)
        if v is not None or k in skip or not d or d > today:
            continue
        val, _ = daily_change(code, d)
        if val is not None:
            changes[k] = val
            got.append(k)
    return got
