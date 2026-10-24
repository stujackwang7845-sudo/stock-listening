"""
第十三款(當日沖銷占比過高)預估(2026-10-24 使用者要求：5 天初犯候選要標出會達成第十三款的成交量)。

條文(證交所 115.08.03 版第十三款；櫃買同架構)：T 日公告的第十三款看「前一營業日」(T-1)的資料——
  ① 最近 6 個營業日(到 T-1)累積當沖成交量 / 累積成交量 > 60%
  ② T-1 當沖成交量 / T-1 成交量 > 60%
  除外：T-1 週轉率 ≤ 5%、T-1 成交值 ≤ 5 億、T-1 當沖量 ≤ 5,000 張(上市官方數字)；
        前 5 個營業日內已列過第十三款，且這次 T-1 當沖量沒有超過那幾次的 → 不列。
  上櫃門檻查不到條文全文，用新制資料反推：被列過的最小當沖 2,010 張、成交值 2.05 億 → 用 2,000 張、2 億(推估)。
驗證(scratch/clause13_model_20261024.py，8/03 以後、排除處置中)：上市 117 次中對 115、多報 8；上櫃 131 次中對 129、多報 15。

所以「明天會不會有第十三款」在今天收盤後就已經確定(看的是今天的當沖)，盤中提醒看的是昨天的資料。
資料：當沖量＝證交所 TWTB4U／櫃買 intraday/stat(逐日快取在資料夾 daytrade_cache/)；成交量與收盤＝market_data.db
price_history，缺的日子用官方日行情補；股本＝market_data.db stock_info。成交值用 成交量×收盤 估算(標「約」)。
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

TWSE_DT = ("https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={ymd}&selectType=All&response=json",
           "https://www.twse.com.tw/zh/trading/day-trading/twtb4u.html")
TPEX_DT = ("https://www.tpex.org.tw/www/zh-tw/intraday/stat?type=Daily&date={slash}&response=json",
           "https://www.tpex.org.tw/zh-tw/mainboard/trading/day-trading/statistics/day.html")

RATIO = 0.60
TURNOVER = 0.05
LIMITS = {"上市": dict(lots=5000, value=5e8, inferred=False), "上櫃": dict(lots=2000, value=2e8, inferred=True)}


def _mk(market):
    m = str(market or "")
    return "上櫃" if m in ("上櫃", "TPEX", "TPEx", "tpex", "OTC") else "上市"


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def parse_daytrade(payload, target):
    """TWTB4U / 櫃買 intraday/stat → {code: 當沖成交股數}；那天沒有逐檔量(休市或未公布)回傳 None。"""
    if str(payload.get("stat", "")).upper() != "OK":
        return None
    pd_ = str(payload.get("date") or "").replace("/", "")
    if pd_ and len(pd_) == 8 and pd_ != target.strftime("%Y%m%d"):
        return None
    for t in payload.get("tables") or []:
        f = t.get("fields") or []
        if "證券代號" in f and "當日沖銷交易成交股數" in f and t.get("data"):
            i, j = f.index("證券代號"), f.index("當日沖銷交易成交股數")
            out = {}
            for r in t["data"]:
                v = _num(r[j])
                if v is not None:
                    out[str(r[i]).strip()] = v
            return out or None
    return None


class Clause13Data:
    """抓資料(含快取)。client 預設 official_quotes.OfficialClient；測試可換假的。"""

    def __init__(self, data_dir, market_db, client=None):
        self.cache_dir = os.path.join(data_dir, "daytrade_cache")
        self.market_db = market_db
        self._client = client
        self._dt = {}
        self._quotes = {}

    @property
    def client(self):
        if self._client is None:
            from core.official_quotes import OfficialClient
            self._client = OfficialClient(min_interval=1.5, jitter=0.5, retries=3)
        return self._client

    def daytrade(self, market, day):
        key = (market, day)
        if key in self._dt:
            return self._dt[key]
        path = os.path.join(self.cache_dir, f"{'twse' if market == '上市' else 'tpex'}_{day:%Y%m%d}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self._dt[key] = json.load(f)
            return self._dt[key]
        url, ref = TWSE_DT if market == "上市" else TPEX_DT
        try:
            data = parse_daytrade(self.client.get_json(url.format(ymd=f"{day:%Y%m%d}", slash=f"{day:%Y/%m/%d}"), ref), day)
        except Exception as e:
            print(f"[clause13] {market} {day} 當沖資料抓取失敗：{e}")
            data = None
        if data:                                      # 只快取有資料的日子(沒公布的下次再抓)
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        self._dt[key] = data
        return data

    def price(self, code, day):
        """(成交股數, 收盤)；資料庫沒有就抓那天官方日行情(整個市場，同一天只抓一次)。"""
        iso = day.strftime("%Y-%m-%d")
        if os.path.exists(self.market_db):
            conn = sqlite3.connect(self.market_db)
            try:
                r = conn.execute("SELECT volume, close FROM price_history WHERE stock_id=? AND date=?", (code, iso)).fetchone()
            except sqlite3.OperationalError:
                r = None
            finally:
                conn.close()
            if r and r[0] is not None:
                return float(r[0]), r[1]
        if day not in self._quotes:
            try:
                from core.official_quotes import fetch_day
                q = fetch_day(self.client, day, today=day + timedelta(days=1))
                self._quotes[day] = q.quotes if q.status in ("ok", "partial") else {}
            except Exception as e:
                print(f"[clause13] {day} 官方日行情抓取失敗：{e}")
                self._quotes[day] = {}
        q = self._quotes[day].get(code)
        return (float(q["volume"]), q["close"]) if q and q.get("volume") is not None else (None, None)

    def shares(self, code):
        if not os.path.exists(self.market_db):
            return None
        conn = sqlite3.connect(self.market_db)
        try:
            r = conn.execute("SELECT shares_outstanding FROM stock_info WHERE stock_id=?", (code,)).fetchone()
        except sqlite3.OperationalError:
            r = None
        finally:
            conn.close()
        return float(r[0]) if r and r[0] else None


def _trading_days_back(day, n):
    from core.utils import DateUtils
    out, cur = [], day
    while len(out) < n:
        if DateUtils.is_trading_day(datetime.combine(cur, datetime.min.time())):
            out.append(cur)
        cur -= timedelta(days=1)
    return sorted(out)


def outlook(code, market, data_day, src, recent13=()):
    """
    data_day(T-1，已收盤)的資料 → 下一個交易日公告第十三款的預估。
    recent13：data_day(含)往前 5 個營業日內已公告過第十三款的日期(重複除外用)。
    回傳 dict：hit(True/False/None)、各數字、fails(沒過的門檻)、need(成交量/當沖量門檻)。
    """
    mk = _mk(market)
    lim = LIMITS[mk]
    days = _trading_days_back(data_day, 6)
    dts, vols = [], []
    close = None
    for d in days:
        dt_map = src.daytrade(mk, d)
        v, c = src.price(code, d)
        if dt_map is None or v is None:
            return dict(hit=None, market=mk, data_day=data_day, missing=d, lim=lim)
        dts.append(dt_map.get(code, 0.0))      # 不在當沖標的清單＝0
        vols.append(v)
        close = c
    dt1, v1 = dts[-1], vols[-1]
    r1 = dt1 / v1 if v1 else 0.0
    r6 = sum(dts) / sum(vols) if sum(vols) else 0.0
    value = v1 * (close or 0)
    sh = src.shares(code)
    turn = v1 / sh if sh else None
    prev_lots = []
    for a in recent13:                             # 那次公告看的是公告日前一營業日的當沖量
        p = _trading_days_back(a - timedelta(days=1), 1)[0]
        m = src.daytrade(mk, p)
        if m is not None:
            prev_lots.append(m.get(code, 0.0) / 1000)
    fails = []
    if r1 <= RATIO:
        fails.append("當日當沖比")
    if r6 <= RATIO:
        fails.append("6 日累積當沖比")
    if dt1 / 1000 <= lim["lots"]:
        fails.append("當沖量")
    if value <= lim["value"]:
        fails.append("成交值")
    if turn is not None and turn <= TURNOVER:
        fails.append("週轉率")
    if prev_lots and dt1 / 1000 <= max(prev_lots):
        fails.append("重複除外")
    return dict(hit=not fails, market=mk, data_day=data_day, lim=lim, r1=r1, r6=r6, dt_lots=dt1 / 1000,
                vol_lots=v1 / 1000, value=value, turn=turn, shares=sh, fails=fails, prev_lots=prev_lots,
                need_vol_lots=(sh * TURNOVER / 1000) if sh else None)


def describe(o, ann_day):
    """一行說明(推播用)。ann_day＝會公告的那天。"""
    lim = o["lim"]
    tag = "（上櫃門檻為推估）" if lim["inferred"] else ""
    head = f"第十三款看 {o['data_day']:%m/%d} 的當沖（{ann_day:%m/%d} 公告用前一營業日資料）"
    if o["hit"] is None:
        return (f"{head}：{o['missing']:%m/%d} 資料還沒有，無法預估。達成門檻：當沖比 > 60%、6 日累積當沖比 > 60%、"
                f"當沖量 > {lim['lots']:,} 張、成交值 > {lim['value'] / 1e8:.0f} 億、週轉率 > 5%{tag}")
    ok = lambda name: "✓" if name not in o["fails"] else "✗"
    parts = [f"當沖量 {o['dt_lots']:,.0f} 張(需 > {lim['lots']:,}){ok('當沖量')}",
             f"當沖比 {o['r1'] * 100:.1f}%(需 > 60%){ok('當日當沖比')}",
             f"6 日累積 {o['r6'] * 100:.1f}%(需 > 60%){ok('6 日累積當沖比')}",
             f"成交值約 {o['value'] / 1e8:.1f} 億(需 > {lim['value'] / 1e8:.0f}){ok('成交值')}"]
    if o["turn"] is not None:
        parts.append(f"成交量 {o['vol_lots']:,.0f} 張＝週轉率 {o['turn'] * 100:.1f}%"
                     f"(需 > 5%，即 > {o['need_vol_lots']:,.0f} 張){ok('週轉率')}")
    if o["prev_lots"]:
        parts.append(f"近 5 日已列過，當沖量需 > {max(o['prev_lots']):,.0f} 張{ok('重複除外')}")
    verdict = (f"→ 預估 {ann_day:%m/%d} 會出現第十三款" if o["hit"]
               else f"→ 預估 {ann_day:%m/%d} 不會出現第十三款")
    return f"{head}：" + "、".join(parts) + f" {verdict}{tag}"
