"""
處置新制「初犯」策略 D + 黃金濾網標註：Telegram 推播(盤後＋盤中)
=======================================================================
策略 D（2026-10-12 起，原本是「處置首日收盤買」的方案 A）：
  - 只做「初犯」（上市：處置措施「第一次處置」；上櫃：條件文字沒有「最近30個營業日內曾發布處置」）
  - 進場：<b>5 天初犯 = 處置前一天(處置公告當天)收盤買</b>；7 天初犯 = 處置第一天收盤買
  - 出場：處置最後一天收盤賣出；<b>收盤價(還原)跌破進場價 8%，當天收盤賣出(停損)</b>
  - 進場日收盤漲停買不到：回測排除、推播標示
  - 濾網(2026-10-12 使用者採用)：進場日前最近一次注意公告「只有第一款(漲跌幅)」且進場日收盤 20MA 乖離 > 35% → 不買。
    回測(新制、排除漲停、統一資金)：52→47 筆，年化 +230%→+274%、MDD -4.3%→-3.6%、前後半都不輸；
    樣本小(第一款 16 筆、被排除 5 筆)，且是測了約 20 種變化後挑出的，之後用新樣本繼續驗證。
  - 回測(新制 8/10～10/08、排除漲停)：D 方案 52 筆，每筆平均 +5.6%、勝率 75%、單筆最差 -10.3%、
    風報比 5.23(scratch/four_plans_20261012.py --no-limit-up)
  - 5 天初犯要在公告前買進，所以另有「盤中提醒」模式(--pre-close，交易日 12:50)：以官方聽牌＋預測引擎
    列出「今天收盤再被注意就進處置、初犯、預估 5 天」的候選與觸發條件，並檢查持股距離停損價。
  - 黃金濾網（三項都符合才算通過）：
      1. 非生技醫療（官方產業別代碼 22）
      2. 非 KY（官方「外國企業註冊地國」有值）
      3. 處置開始前 5 個交易日平均成交量 ≥ 100 張
推播時機：盤中 12:50(5 天初犯候選與停損檢查)；處置公告當天盤後（明天就是處置首日），以及處置最後一天的前一天（提醒明天收盤出場）。
資料來源（全部官方公開資料，不需券商帳號）：
  - 處置公告：證交所 announcement/punish、櫃買 tpex_disposal_information
  - 產業別／KY：證交所 t187ap03_L、櫃買 mopsfin_t187ap03_O
  - 成交量：前 4 天讀 DB 專案行情 parquet（隔天早上才入庫），公告當天讀官方收盤行情 API
靜默原則：沒有明日進場、明日出場的初犯股就不推播。同一訊號只推一次（data/disposal_golden_signals.json）。
回測可靠度（2026-10-04 Claude 複核，見 scratch/claude_reverify_20261004_newonly.py）：
  新制初犯 N=57 勝率 77%、平均淨 +5.9%、最大虧損 -16.7%；黃金濾網是樣本內挑出來的條件，
  前後半段驗證方向不一致，推播只做標註，請以不加濾網的數字估計風險。
用法：
  uv run python scripts/automation/notify_disposal_golden.py --pre-close   # 盤中提醒(交易日 12:50)
  uv run python scripts/automation/notify_disposal_golden.py            # 正式(交易日才跑、有訊號才推)
  uv run python scripts/automation/notify_disposal_golden.py --dry-run  # 只印出內容不推播、不寫紀錄
  uv run python scripts/automation/notify_disposal_golden.py --date 20261001 --dry-run  # 指定日期預覽
=======================================================================
"""
import argparse
import html
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import math
import re

import pyarrow.parquet as pq
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJ = Path(__file__).resolve().parents[2]
os.chdir(PROJ)
sys.path.insert(0, str(PROJ / "scripts"))
from core.utils import DateUtils  # noqa: E402

TG_SKILL_PATH = r"C:\Users\user\.gemini\config\plugins\robust-dev-plugin\skills\telegram-notifier\scripts"
if os.path.exists(TG_SKILL_PATH) and TG_SKILL_PATH not in sys.path:
    sys.path.insert(0, TG_SKILL_PATH)
try:
    from send_telegram import send_telegram
except ImportError:
    send_telegram = None

PRICE_ROOT = Path(r"E:\Vibe Coding\Stock\DB\parquet_data\price_daily_raw")
CORP_ROOT = Path(r"E:\Vibe Coding\Stock\DB\parquet_data\corporate_actions_raw")
DATA_DIR = os.environ.get("DISPO_DATA_DIR")          # 雲端：狀態資料夾；本機：不設，用 data/
SIGNAL_LOG = (Path(DATA_DIR) if DATA_DIR else PROJ / "data") / "disposal_golden_signals.json"


def use_parquet(root):
    """本機 DB 專案 parquet 在就用；雲端沒有(或設 DISPO_NO_PARQUET=1 模擬雲端)改用官方資料。"""
    return root.exists() and not os.environ.get("DISPO_NO_PARQUET")


def data_file(name):
    return (Path(DATA_DIR) if DATA_DIR else PROJ / "data") / name
REPORT_HTML = PROJ / "reports" / "latest_disposal_golden_push.html"
MIN_LOTS = 100
MIN_PUSH_LOTS = 500        # 聽牌候選：非處置期間 5 日均量低於這個不推播(2026-10-30 使用者要求)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
      "Accept": "application/json"}
URLS = {
    "twse_punish": "https://openapi.twse.com.tw/v1/announcement/punish",
    "tpex_disp": "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information",
    "twse_company": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "tpex_company": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "twse_quote": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "tpex_quote": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
}


STOP_PCT = 0.08          # 方案 D：收盤(還原)報酬跌破 -8% 當天收盤出場
DEV_MAX = 0.35           # 濾網：注意條款只有「第一款」且進場日收盤 20MA 乖離 > 35% 不買(回測 2026-10-12)


def tick(p):
    """台股升降單位。"""
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5


def limit_up_price(prev_close):
    """漲停價 = 前收 ×1.1 無條件捨去到升降單位。"""
    t = tick(prev_close * 1.1)
    return round(math.floor(prev_close * 1.1 / t + 1e-9) * t, 2)


def limit_down_price(prev_close):
    """跌停價 = 前收 ×0.9 無條件進位到升降單位。"""
    t = tick(prev_close * 0.9)
    return round(math.ceil(prev_close * 0.9 / t - 1e-9) * t, 2)


def stop_price_of(entry):
    """停損價：收盤 ≤ 進場價×(1-8%) 即停損，收盤價落在升降單位上，所以向下取到升降單位。"""
    x = entry * (1 - STOP_PCT)
    t = tick(x)
    return round(math.floor(x / t + 1e-9) * t, 2)


def n_trading_days(s, e):
    return sum(1 for i in range((e - s).days + 1)
               if DateUtils.is_trading_day(datetime.combine(s + timedelta(days=i), datetime.min.time())))


def is_trading(day):
    return DateUtils.is_trading_day(datetime.combine(day, datetime.min.time()))


def closures_between(a, b):
    """a(不含)～b(不含)之間的臨時休市日(颱風等；週末與既定假日不算)。"""
    out, cur = [], a + timedelta(days=1)
    while cur < b:
        if DateUtils.is_unscheduled_closure(datetime.combine(cur, datetime.min.time())):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def postpone_for_closures(d):
    """
    [2026-10-24] 臨時休市(颱風)順延：交易所公告的處置期間是用公告當時的行事曆算的，期間內碰到臨時休市
    「順延執行」(公告原文：如遇休市…則順延執行)。例：8437 公告 10/23～10/29(5 個營業日)，10/23 颱風休市
    → 10/27～11/02。原公告起訖留在 start_official / end_official。
    """
    s, e = d["start"], d["end"]
    start, end, hit = DateUtils.postpone_period(s, e)
    if not hit:
        return d
    d.update(start_official=s, end_official=e, start=start, end=end, postponed=hit)
    print(f"[INFO] {d['code']} 處置 {s}～{e} 碰到臨時休市 {[str(x) for x in hit]} → 順延為 {start}～{end}")
    return d


def prev_trading_day(day):
    return DateUtils.get_last_trading_day(datetime.combine(day, datetime.min.time()) - timedelta(days=1)).date()


def fetch_json(name):
    """官方 OpenAPI；每次請求間隔 2 秒，失敗指數退避重試 3 次。"""
    for attempt in range(3):
        try:
            time.sleep(2)
            r = requests.get(URLS[name], headers=UA, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            wait = 5 * (2 ** attempt)
            print(f"[WARN] {name} 第 {attempt + 1} 次失敗：{e}，{wait} 秒後重試")
            time.sleep(wait)
    raise RuntimeError(f"{name} 連續失敗，放棄")


def fetch_json_opt(name):
    """同 fetch_json，失敗回傳 None(由呼叫端改用本地資料)。"""
    try:
        return fetch_json(name)
    except Exception as e:
        print(f"[WARN] {name} 抓不到，改用本地資料：{e}")
        return None


def disposals_from_db(market, today):
    """官方處置公告抓不到時：用資料夾裡 disposal_records(run_daily/桌面版每天更新)當備援。"""
    import sqlite3
    db = data_file("disposal_history.db")
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    lo = (today - timedelta(days=60)).isoformat()
    rows = conn.execute("SELECT code, name, announce_date, period_start, period_end, reason FROM disposal_records "
                        "WHERE source=? AND period_end>=?", (market, lo)).fetchall()
    conn.close()
    out = []
    for code, name, ann, ps, pe, reason in rows:
        code = str(code).strip()
        if not is_stock(code) or not ps or not pe:
            continue
        reason = str(reason or "")
        a = str(ann or ps).replace("/", "-")[:10]
        try:
            out.append(dict(code=code, name=str(name or "").strip(), market=market,
                            announce=datetime.strptime(a, "%Y-%m-%d").date(),
                            start=datetime.strptime(ps, "%Y-%m-%d").date(), end=datetime.strptime(pe, "%Y-%m-%d").date(),
                            offense="累犯" if re.search(r"(三十|30)個營業日內曾發布處置", reason) else "初犯",
                            reason=reason.strip()))
        except ValueError:
            continue
    print(f"[INFO] {market}處置公告改用本地 disposal_records：{len(out)} 筆")
    return out


def roc_to_date(s):
    s = str(s).strip().replace("/", "")
    return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))


def parse_period(s):
    a, b = str(s).replace("～", "~").split("~")
    return roc_to_date(a), roc_to_date(b)


def is_stock(code):
    return len(code) == 4 and code.isdigit() and not code.startswith("0")


def load_tpex_portal(today=None):
    """
    櫃買處置公告(官網 Web Portal JSON)：OpenAPI 會落後一天以上(2026-10-08 實測 10/08 當天公告 5475、6708 都缺)，
    而「公告當晚→明日進場」正需要當天資料。查詢區間涵蓋「已公告未生效」到「進行中」：今天往前 15 天～往後 10 天。
    """
    today = today or date.today()
    r = requests.get("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal",
                     params={"response": "json", "startDate": (today - timedelta(days=15)).strftime("%Y/%m/%d"),
                             "endDate": (today + timedelta(days=10)).strftime("%Y/%m/%d")},
                     headers={**UA, "Referer": "https://www.tpex.org.tw/zh-tw/announce/market/disposal.html"}, timeout=30)
    r.raise_for_status()
    rows = (r.json().get("tables") or [{}])[0].get("data") or []
    out = []
    for x in rows:
        if len(x) < 9:
            continue
        code = str(x[2]).strip()
        if not is_stock(code):
            continue
        s, e = parse_period(x[5])
        measure = str(x[7])
        reason = re.sub(r"<[^>]+>", " ", str(x[6]) + " " + str(x[8]))
        again = "再次處置" in measure or "最近30個營業日內曾發布處置" in reason
        name = re.sub(r"\(.*", "", str(x[3])).strip()
        out.append(dict(code=code, name=name, market="上櫃", announce=roc_to_date(x[1]), start=s, end=e,
                        offense="累犯" if again else "初犯", reason=str(x[6]).strip()))
    return out


def load_disposals(today=None):
    out = []
    twse = fetch_json_opt("twse_punish")
    if twse is None:
        out += disposals_from_db("上市", today or date.today())
    for r in twse or []:
        code = str(r.get("Code", "")).strip()
        if not is_stock(code):
            continue
        s, e = parse_period(r["DispositionPeriod"])
        measure = str(r.get("DispositionMeasures", ""))
        out.append(dict(code=code, name=str(r.get("Name", "")).strip(), market="上市",
                        announce=roc_to_date(r["Date"]), start=s, end=e,
                        offense="初犯" if "第一次" in measure else "累犯",
                        reason=str(r.get("ReasonsOfDisposition", "")).strip()))
    try:
        time.sleep(2)
        out += load_tpex_portal(today)
        tpex_rows = []
    except Exception as e:
        print(f"[WARN] 櫃買官網處置公告失敗({e})，改用 OpenAPI(可能落後一天以上)")
        tpex_rows = fetch_json_opt("tpex_disp")
        if tpex_rows is None:
            out += disposals_from_db("上櫃", today or date.today())
            tpex_rows = []
    for r in tpex_rows:
        code = str(r.get("SecuritiesCompanyCode", "")).strip()
        if not is_stock(code):
            continue
        s, e = parse_period(r["DispositionPeriod"])
        cond = str(r.get("DisposalCondition", ""))
        out.append(dict(code=code, name=str(r.get("CompanyName", "")).strip(), market="上櫃",
                        announce=roc_to_date(r["Date"]), start=s, end=e,
                        offense="累犯" if "最近30個營業日內曾發布處置" in cond else "初犯",
                        reason=str(r.get("DispositionReasons", "")).strip()))
    # 2026-10-16：補上資料庫裡近期的處置紀錄(官方清單可能已拿掉剛結束的)，空單出關日回補、持有中才找得到
    try:
        out += disposals_from_db("上市", today or date.today()) + disposals_from_db("上櫃", today or date.today())
    except Exception as e:
        print(f"[WARN] 讀本地處置紀錄失敗：{e}")
    # 同一股票同一處置期間只留一筆(官方優先，資料庫補缺)
    uniq = {}
    for d in out:
        uniq.setdefault((d["code"], d["start"], d["end"]), d)
    res = list(uniq.values())
    for d in res:
        postpone_for_closures(d)
        d["n_days"] = n_trading_days(d["start"], d["end"])
        # 方案 D 進場日：5 天初犯＝處置前一天(公告日)收盤買；其餘＝處置首日收盤買
        d["entry"] = prev_trading_day(d["start"]) if d["n_days"] == 5 else d["start"]
    return res


def load_company():
    m = {}
    for r in fetch_json_opt("twse_company") or []:
        reg = str(r.get("外國企業註冊地國", "")).strip()
        m[str(r["公司代號"]).strip()] = dict(industry=str(r.get("產業別", "")).strip(),
                                         ky=reg not in ("", "－", "-"))
    for r in fetch_json_opt("tpex_company") or []:
        reg = str(r.get("Registration", "")).strip()
        m[str(r["SecuritiesCompanyCode"]).strip()] = dict(industry=str(r.get("SecuritiesIndustryCode", "")).strip(),
                                                         ky=reg not in ("", "－", "-"))
    return m


def load_today_volume():
    """官方收盤行情：{code: (日期, 成交股數, 收盤價)}"""
    vol = {}
    tw, tp = fetch_json_opt("twse_quote"), fetch_json_opt("tpex_quote")
    if tw is None or tp is None:
        vol.update(quotes_from_db())          # 先放本地最新一天，官方有抓到的再覆蓋
    for r in tw or []:
        try:
            vol[str(r["Code"]).strip()] = (roc_to_date(r["Date"]), float(str(r["TradeVolume"]).replace(",", "")),
                                           float(str(r["ClosingPrice"]).replace(",", "")))
        except Exception:
            pass
    for r in tp or []:
        try:
            vol[str(r["SecuritiesCompanyCode"]).strip()] = (roc_to_date(r["Date"]),
                                                           float(str(r["TradingShares"]).replace(",", "")),
                                                           float(str(r["Close"]).replace(",", "")))
        except Exception:
            pass
    return vol


def quotes_from_db():
    """官方收盤行情抓不到時：資料夾 market_data.db 最新一天(run_daily 用官方 MI_INDEX/dailyQuotes 寫入)。"""
    import sqlite3
    db = data_file("market_data.db")
    if not db.exists():
        return {}
    conn = sqlite3.connect(str(db))
    d = conn.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    out = {}
    if d:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        for code, v, c in conn.execute("SELECT stock_id, volume, close FROM price_history WHERE date=?", (d,)):
            if c is not None:
                out[str(code)] = (dd, float(v or 0), float(c))
    conn.close()
    print(f"[INFO] 收盤行情改用本地 market_data.db {d}：{len(out)} 檔")
    return out


def prior_trading_days(start, n=5):
    days, cur = [], start
    while len(days) < n:
        cur -= timedelta(days=1)
        if DateUtils.is_trading_day(datetime(cur.year, cur.month, cur.day)):
            days.append(cur)
    return sorted(days)


def load_parquet_daily(codes, days):
    """DB 專案行情 parquet：{(code, date): (成交股數, 收盤價)}"""
    out = {}
    if not use_parquet(PRICE_ROOT):
        return load_official_daily(codes, days)
    months = {(d.year, d.month) for d in days}
    for mk in ["TWSE", "TPEX"]:
        for y, m in months:
            f = PRICE_ROOT / f"market={mk}" / f"year={y}" / f"month={m}" / "data.parquet"
            if not f.exists():
                continue
            t = pq.ParquetFile(f).read(columns=["date", "symbol", "volume", "close"]).to_pandas()
            t["symbol"] = t["symbol"].astype(str).str.strip()
            t = t[t["symbol"].isin(codes)]
            for r in t.itertuples():
                out[(r.symbol, r.date if isinstance(r.date, date) else r.date.date())] = (float(r.volume), float(r.close))
    return out


def golden_check(d, company, pq_vol, today_vol):
    comp = company.get(d["code"], {})
    days = prior_trading_days(d["start"], 5)
    vols = []
    for day in days:
        v = pq_vol.get((d["code"], day), (None,))[0]
        if v is None:
            tv = today_vol.get(d["code"])
            if tv and tv[0] == day:
                v = tv[1]
        if v is not None:
            vols.append(v)
    vol5 = sum(vols) / len(vols) / 1000 if vols else None
    is_bio = comp.get("industry") == "22"
    is_ky = bool(comp.get("ky")) or "KY" in d["name"].upper()
    vol_ok = vol5 is not None and len(vols) == 5 and vol5 >= MIN_LOTS
    return dict(is_bio=is_bio, is_ky=is_ky, vol5_lots=vol5, vol_days=len(vols),
                industry_known=bool(comp), golden=(not is_bio) and (not is_ky) and vol_ok)


def disposal_days_of(codes, upto):
    """{code: set(處置期間的交易日)}：近 90 天 disposal_records，期間碰到臨時休市照順延。"""
    import sqlite3
    out = {c: set() for c in codes}
    db = data_file("disposal_history.db")
    if not codes or not db.exists():
        return out
    conn = sqlite3.connect(str(db))
    marks = ",".join("?" * len(codes))
    rows = conn.execute(f"SELECT code, period_start, period_end FROM disposal_records WHERE code IN ({marks}) "
                        "AND period_end>=?", (*codes, (upto - timedelta(days=90)).isoformat())).fetchall()
    conn.close()
    for code, ps, pe in rows:
        try:
            s, e, _ = DateUtils.postpone_period(datetime.strptime(ps, "%Y-%m-%d").date(),
                                                datetime.strptime(pe, "%Y-%m-%d").date())
        except (TypeError, ValueError):
            continue
        cur = s
        while cur <= e:
            out.setdefault(str(code).strip(), set()).add(cur)
            cur += timedelta(days=1)
    return out


def nondisp_vol5(codes, upto):
    """
    [2026-10-30 使用者要求] 非處置期間的 5 日均量(張)：upto(含)往前、跳過處置期間(分盤撮合量會縮)的最近 5 個交易日。
    回傳 {code: 均量 或 None(資料不足 5 天)}。
    """
    codes = sorted(set(codes))
    if not codes:
        return {}
    days = prior_trading_days(upto + timedelta(days=1), 45)
    vol = load_parquet_daily(set(codes), days)
    disp = disposal_days_of(codes, upto)
    out = {}
    for c in codes:
        vs = [vol[(c, d)][0] for d in reversed(days) if d not in disp.get(c, set()) and (c, d) in vol][:5]
        out[c] = sum(vs) / 5 / 1000 if len(vs) == 5 else None
    return out


def drop_low_volume(cands, upto, label):
    """非處置期間 5 日均量 < MIN_PUSH_LOTS 的候選不推播；資料不足的保留(寧可多推不漏推)。"""
    if not cands:
        return cands
    v5 = nondisp_vol5([c["code"] for c in cands], upto)
    keep = []
    for c in cands:
        v = v5.get(c["code"])
        if v is not None and v < MIN_PUSH_LOTS:
            print(f"[INFO] {label} {c['code']} {c['name']} 非處置期間 5 日均量 {v:,.0f} 張 < {MIN_PUSH_LOTS}，不推播")
            continue
        if v is None:
            print(f"[WARN] {label} {c['code']} 非處置期間 5 日均量資料不足，照常推播")
        keep.append(c)
    return keep


def fmt_filter(g):
    bio = "是 ❌" if g["is_bio"] else "否 ✅"
    ky = "是 ❌" if g["is_ky"] else "否 ✅"
    if g["vol5_lots"] is None:
        vol = "無資料 ❌"
    elif g["vol_days"] < 5:
        vol = f"{g['vol5_lots']:,.0f} 張（只有 {g['vol_days']} 天資料）❌"
    else:
        vol = f"{g['vol5_lots']:,.0f} 張 {'≥' if g['vol5_lots'] >= MIN_LOTS else '<'} {MIN_LOTS} {'✅' if g['vol5_lots'] >= MIN_LOTS else '❌'}"
    note = "" if g["industry_known"] else "（官方公司資料查無此股，生技/KY 以名稱判斷）"
    return f"生技：{bio}｜KY：{ky}｜前5日均量：{vol}{note}"


def load_official_daily(codes, days):
    """雲端版行情：狀態資料夾 market_data.db 的 price_history(官方 MI_INDEX/dailyQuotes，成交量為股數)。"""
    import sqlite3
    out = {}
    db = data_file("market_data.db")
    if not db.exists() or not codes or not days:
        return out
    conn = sqlite3.connect(str(db))
    lo, hi = str(min(days)), str(max(days))
    marks = ",".join("?" * len(codes))
    for code, d, vol, close in conn.execute(
            f"SELECT stock_id, date, volume, close FROM price_history WHERE date BETWEEN ? AND ? AND stock_id IN ({marks})",
            (lo, hi, *sorted(codes))):
        if close is not None:
            out[(str(code), datetime.strptime(d, "%Y-%m-%d").date())] = (float(vol or 0), float(close))
    conn.close()
    return out


def load_official_corp_actions(codes, since, until):
    """雲端版除權息：證交所 TWT49U(上市)＋櫃買 exDailyQ(上櫃)，{code: [(日期, 除權息前收盤, 參考價)]}。"""
    out = {}
    if not codes:
        return out

    def idx(fields, name):
        return next(i for i, f in enumerate(fields) if f.strip() == name)

    def num(x):
        try:
            return float(str(x).replace(",", ""))
        except ValueError:
            return None
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
                         params={"startDate": f"{since:%Y%m%d}", "endDate": f"{until:%Y%m%d}", "response": "json"},
                         headers=UA, timeout=30).json()
        f = r.get("fields") or []
        i_d, i_c, i_b, i_a = idx(f, "資料日期"), idx(f, "股票代號"), idx(f, "除權息前收盤價"), idx(f, "除權息參考價")
        for row in r.get("data") or []:
            code = str(row[i_c]).strip()
            m = re.match(r"(\d+)年(\d+)月(\d+)日", str(row[i_d]))
            if code in codes and m:
                dd = date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
                b, a = num(row[i_b]), num(row[i_a])
                if since < dd <= until and b and a:
                    out.setdefault(code, []).append((dd, b, a))
    except Exception as e:
        print(f"[WARN] 上市除權息(TWT49U)抓取失敗：{e}")
    try:
        time.sleep(2)
        r = requests.get("https://www.tpex.org.tw/web/stock/exright/dailyquo/exDailyQ_result.php",
                         params={"l": "zh-tw", "d": f"{since.year - 1911}/{since:%m/%d}",
                                 "ed": f"{until.year - 1911}/{until:%m/%d}", "o": "json"}, headers=UA, timeout=30).json()
        t = (r.get("tables") or [{}])[0]
        f = t.get("fields") or []
        i_d, i_c, i_b, i_a = idx(f, "除權息日期"), idx(f, "代號"), idx(f, "除權息前收盤價"), idx(f, "除權息參考價")
        for row in t.get("data") or []:
            code = str(row[i_c]).strip()
            y, mth, dy = (int(x) for x in str(row[i_d]).split("/"))
            dd = date(y + 1911, mth, dy)
            b, a = num(row[i_b]), num(row[i_a])
            if code in codes and since < dd <= until and b and a:
                out.setdefault(code, []).append((dd, b, a))
    except Exception as e:
        print(f"[WARN] 上櫃除權息(exDailyQ)抓取失敗：{e}")
    return out


def load_corp_actions(codes, since, until):
    """官方除權息事件(DB corporate_actions_raw)：{code: [(日期, 除權息前收盤, 參考價)]}"""
    if not use_parquet(CORP_ROOT):
        return load_official_corp_actions(codes, since, until)
    out = {}
    for mk in ["TWSE", "TPEX"]:
        for y in range(since.year, until.year + 1):
            f = CORP_ROOT / f"market={mk}" / f"year={y}" / "data.parquet"
            if not f.exists():
                continue
            t = pq.ParquetFile(f).read(columns=["date", "symbol", "before_price", "after_price"]).to_pandas()
            t["symbol"] = t["symbol"].astype(str).str.strip()
            t = t[t["symbol"].isin(codes)]
            for r in t.itertuples():
                dd = r.date if isinstance(r.date, date) else r.date.date()
                if since < dd <= until and r.before_price and r.after_price:
                    out.setdefault(r.symbol, []).append((dd, float(r.before_price), float(r.after_price)))
    return out


def close_of(code, day, quotes, daily):
    q = quotes.get(code)
    if q and q[0] == day:
        return q[2]
    v = daily.get((code, day))
    return v[1] if v else None


def is_limit_up(code, day, quotes, daily):
    """進場日收盤是否漲停(收盤 ≥ 漲停價)。漲停鎖死多半買不到，方案 D 回測排除這種單。"""
    c = close_of(code, day, quotes, daily)
    p = close_of(code, prev_trading_day(day), quotes, daily)
    return bool(c and p and c >= limit_up_price(p) - 1e-6)


def calc_return(d, as_of, quotes, daily, corp):
    """
    方案 D 持有部位：進場價 = 進場日(5 天初犯＝處置前一天，7 天初犯＝處置首日)收盤；
    收盤報酬(還原)跌破 -8% 的第一天視為停損，當天收盤出場。
    還原方式：原始價漲跌 × 持有期間官方除權息調整(除權息前收盤 ÷ 參考價)。
    不直接用 DB 的 adj_close：它的還原因子曾誤判(6225 天瀚 2026-08-18)，且比原始行情晚一天入庫。
    除權息當天收盤若超出參考價 ±10%(漲跌停不可能)，標記可疑並同時給原始價報酬。
    """
    code = d["code"]
    ed = d["entry"]
    entry = close_of(code, ed, quotes, daily)
    if entry is None:
        return None
    events = sorted(e for e in corp.get(code, []) if ed < e[0] <= as_of)

    def factor_to(day):
        f = 1.0
        for ev_date, before, after in events:
            if ev_date <= day:
                f *= before / after
        return f

    suspicious = False
    for ev_date, before, after in events:
        c = close_of(code, ev_date, quotes, daily)
        if c is not None and not (after * 0.89 <= c <= after * 1.11):
            suspicious = True
    series, cur_day = [], ed + timedelta(days=1)
    while cur_day <= as_of:
        c = close_of(code, cur_day, quotes, daily)
        if c is not None:
            series.append((cur_day, c))
        cur_day += timedelta(days=1)
    stopped = None
    for day, c in series:
        r = c / entry * factor_to(day) - 1
        if r <= -STOP_PCT + 1e-9:   # 浮點誤差：收盤剛好等於停損價(例 92/100-1)也算觸發，與回測「收盤 ≤ 進場價×0.92」一致
            stopped = dict(date=day, close=c, ret=r * 100)
            break
    if stopped:
        cur_date, cur = stopped["date"], stopped["close"]
    elif series:
        cur_date, cur = series[-1]
    else:
        cur_date, cur = ed, entry
    raw = cur / entry - 1
    f = factor_to(cur_date)
    return dict(entry=entry, entry_date=ed, cur=cur, cur_date=cur_date, raw=raw * 100,
                adj=((1 + raw) * f - 1) * 100, n_events=sum(1 for e in events if e[0] <= cur_date),
                suspicious=suspicious, stop_price=stop_price_of(entry), stopped=stopped)


def load_clause_db(today):
    """注意條款資料庫副本：把桌面版 attention_clauses 複製到暫存檔，再補抓到 today(不動使用者的資料庫)。
    回傳 (暫存檔路徑 or None, 最新公告日)。補抓失敗就用現有資料，最新公告日讓呼叫端判斷是否落後。"""
    import shutil
    import sqlite3
    import tempfile
    src = data_file("disposal_history.db")
    if not src.exists():
        return None, None
    tmp = os.path.join(tempfile.mkdtemp(prefix="dispo_clause_"), "disposal_history.db")
    s_conn, d_conn = sqlite3.connect(str(src)), sqlite3.connect(tmp)
    s_conn.backup(d_conn)
    s_conn.close()
    d_conn.close()
    try:
        os.environ.setdefault("DISPO_NO_SHIOAJI", "1")
        sys.path.insert(0, str(PROJ / "scripts"))
        from core.dashboard_engine import fill_attention_clauses_gap
        fill_attention_clauses_gap(datetime.combine(today, datetime.min.time()), tmp)
    except Exception as e:
        print(f"[WARN] 注意條款補抓失敗，用現有資料：{e}")
    conn = sqlite3.connect(tmp)
    latest = conn.execute("SELECT MAX(announce_date) FROM attention_clauses").fetchone()[0]
    conn.close()
    return tmp, latest


def clause_type(db_path, code, entry):
    """進場日(不含)之前最近一次注意公告的條款類型：'一'(只有第一款) / '六' / '其他' / '無'(查不到)。
    與回測一致：款 0(解析不出)不算；有第六款→六；有第一款以外的款→其他；只有第一款→一。"""
    if not db_path:
        return "無"
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT announce_date, clause_number FROM attention_clauses WHERE code=? AND announce_date<? "
                        "AND clause_number>0 ORDER BY announce_date DESC", (code, str(entry))).fetchall()
    conn.close()
    if not rows:
        return "無"
    last = {r[1] for r in rows if r[0] == rows[0][0]}
    return "六" if 6 in last else "其他" if any(c != 1 for c in last) else "一"


CLAUSE_TXT = {"一": "第一款(漲跌幅)", "六": "第六款(週轉率)", "其他": "其他款", "無": "條款查無"}


def dev20_info(code, entry, today, quotes, daily, now_price=None):
    """進場日收盤相對 20 日均線的乖離。now_price 給盤中用(以現價當進場日收盤的估計)。
    回傳 dict(dev=乖離, thr=進場日收盤超過這個價就算乖離>35%, close=進場日收盤/估計)；資料不足回傳 None。"""
    prev = []
    cur = prev_trading_day(entry)
    for _ in range(40):
        c = close_of(code, cur, quotes, daily)
        if c is not None and c == c:          # 跳過沒行情(None)與 NaN
            prev.append(c)
        if len(prev) == 19:
            break
        cur = prev_trading_day(cur)
    if len(prev) < 19:
        return None
    c = now_price if now_price is not None else close_of(code, entry, quotes, daily)
    if c is not None and c != c:
        c = None
    # 前 6 日累積漲跌：進場日收盤已知 → 進場日收盤 / 6 個交易日前收盤；未知(7 天初犯明天才進場) → 最新收盤 / 再往前 6 天
    pre6 = (c / prev[5] - 1) if c is not None else (prev[0] / prev[6] - 1)
    s19 = sum(prev)
    x = DEV_MAX_FACTOR * s19 / (20 - DEV_MAX_FACTOR)
    thr = round(math.floor(x / tick(x) + 1e-9) * tick(x), 2)      # 取到升降單位(收盤價只會落在合法價位)
    return dict(dev=None if c is None else c / ((s19 + c) / 20) - 1, thr=thr, close=c, pre6=pre6)


DEV_MAX_FACTOR = 1 + DEV_MAX


def judge(clause, di, drop=None):
    """濾網判定：回傳 'drop'(因跌幅被注意而處置，不買) / 'skip'(第一款乖離過熱，不買) / 'cond'(第一款，要看進場日收盤) / 'ok'。
    drop：drop_notice() 的結果(注意公告原文是跌幅才有值)。"""
    if drop:
        return "drop"                      # 2026-10-16 使用者要求：跌太多才會處置的不買
    if clause != "一":
        return "ok"
    if di is None:
        return "cond"
    if di["dev"] is not None and di["dev"] > DEV_MAX:
        return "skip"
    return "cond"


def fail_reasons(g):
    """黃金濾網沒過的項目(生技／KY／均量)。"""
    r = []
    if g["is_bio"]:
        r.append("生技醫療")
    if g["is_ky"]:
        r.append("KY")
    if g["vol5_lots"] is None:
        r.append("均量無資料")
    elif g["vol_days"] < 5:
        r.append(f"均量資料只有 {g['vol_days']} 天")
    elif g["vol5_lots"] < MIN_LOTS:
        r.append(f"前5日均量 {g['vol5_lots']:,.0f} 張 < {MIN_LOTS}")
    return "、".join(r) or "未知"


def nm(code, name):
    """推播顯示用股票名稱：有發行股票期貨加 (期)(2026-10-15 使用者要求)。"""
    try:
        sys.path.insert(0, str(PROJ / "scripts"))
        from core.futures_stocks import name_with_futures
        return name_with_futures(code, name)
    except Exception:
        return name


def gold_tag(g):
    return "" if g["golden"] else f"｜濾網✗{html.escape(fail_reasons(g))}"


def fmt_hold(d, g, rr, tag=None):
    """持有部位一行：進場價→現價、還原報酬、停損價。"""
    head = f"• <b>{html.escape(d['code'])} {html.escape(nm(d['code'], d['name']))}</b>"
    tag = gold_tag(g) if tag is None else tag
    if rr is None:
        return f"{head}｜查無行情{tag}"
    if rr["suspicious"]:
        ret = f"原始價 {rr['raw']:+.1f}%⚠️除權息資料可疑"
    else:
        ret = f"{rr['adj']:+.1f}%"
    s = f"{head}｜進 {rr['entry']:g}→現 {rr['cur']:g}（{ret}）｜停損價 {rr['stop_price']:g}{tag}"
    if rr["stopped"]:
        st = rr["stopped"]
        s += f"\n　⛔ {st['date']:%m/%d} 收盤 {st['close']:g} 已觸發停損（{st['ret']:+.1f}%）"
    return s


def avg_summary(items):
    """區塊標題用的摘要：平均還原報酬（排除資料可疑的），另列符合濾網者。"""
    vals = [rr["adj"] for _, g, rr in items if rr is not None and not rr["suspicious"]]
    gold = [rr["adj"] for _, g, rr in items if rr is not None and not rr["suspicious"] and g["golden"]]
    if not vals:
        return ""
    s = f"，平均 {sum(vals)/len(vals):+.2f}%"
    if gold and len(gold) != len(vals):
        s += f"（符合黃金濾網 {len(gold)} 檔 {sum(gold)/len(gold):+.2f}%）"
    return s


def section(title, body_lines):
    """
    Telegram 可展開引用區塊：收合時只看得到第一行(標題＋摘要)，點「展開」才看明細。
    Telegram 不支援真正的整塊收合，這是最接近的作法。
    """
    body = "\n".join(body_lines).strip()
    return f"<blockquote expandable><b>{title}</b>\n\n{body}</blockquote>"


RULES_HTML = (
    "<blockquote expandable>📘 策略說明（點開）\n"
    "<b>做多</b>（只做初犯）\n"
    "• 5 天初犯：處置前一天(公告日)收盤買 → 處置最後一天收盤賣（要靠聽牌候選預先買）\n"
    "• 7 天初犯：處置首日收盤買 → <b>處置結束日前一天</b>收盤賣（同一收盤改放空，見下）\n"
    "• <b>接力處置</b>：5 天初犯處置期間又被公告 5 天累犯 → 續抱到<b>那次累犯處置的出關日</b>收盤賣"
    "（新制 15 件：續抱平均 +14.85%、勝率 80%；第一次結束就賣 +4.18%）\n"
    f"• 停損：收盤 ≤ 進場價×{1-STOP_PCT:.2f}，當天收盤賣；無停利，抱到出場日\n"
    f"• 不買：第一款且進場日 20MA 乖離 > {DEV_MAX*100:.0f}%；第一款原文是「跌幅」(短期跌太多被處置)；進場日收盤漲停\n"
    "<b>放空</b>（策略 A）\n"
    "• 7 天初犯：處置結束日前一天收盤空 → <b>出關日</b>收盤回補；無停損\n"
    "• 7 天累犯：<b>處置前一天收盤空</b>（聽牌、預估會進 7 天累犯）→ <b>出關日+1</b> 收盤回補；無停損\n"
    "  （新制 9 件：平均 +7.67%、中位 +5.53%、勝率 67%、最差 −10.0%；需在公告前預測，沒中隔天收盤回補）\n"
    "• 放空當下漲停不空、跌停空不到，都不做\n"
    "• 進場日收盤跌停空不到就不做；出關日若漲停鎖死，空單會回補不了\n"
    "<b>回測</b>（新制 8/10～10/15，每筆等額、不加槓桿）\n"
    "• 做多＋放空合計 77 筆：單筆平均 +5.45%、勝率 78%、組合 MDD −2.7%（只做多：MDD −3.9%）\n"
    "• 只有 8 週多頭樣本，7 天處置僅 18 筆；年化只能比較用\n"
    "• 未扣融券費/借券費；處置股能否融券、券源請先跟券商確認\n"
    "• 報酬為還原報酬，未扣手續費與證交稅(來回約 0.38%)</blockquote>"
)


def next_trading_day(day):
    return DateUtils.get_next_trading_day(datetime.combine(day, datetime.min.time())).date()


def enrich(d):
    """多單出場日、空單進場/回補日。"""
    d["long_exit"] = d["end"] if d["n_days"] == 5 else prev_trading_day(d["end"])
    if d["n_days"] == 7:
        d["short_entry"] = prev_trading_day(d["end"])
        d["short_cover"] = next_trading_day(d["end"])
        d["cover_lbl"] = "出關日"
        if d["offense"] == "累犯":
            # [2026-10-22] 7 天累犯回補延到出關日+1(出關日的隔一個交易日)：出關日、出關日+1 都偏跌
            d["short_cover"] = next_trading_day(d["short_cover"])
            d["cover_lbl"] = "出關日+1"
            # [2026-10-23] 進場提早到處置前一天(公告日)收盤空；這條規則上線前就已過了前一天的(例 4542)，
            # 照舊在結束日前一天收盤空
            pre = prev_trading_day(d["start"])
            if pre >= RECID_EARLY_START:
                d["short_entry"] = pre
                d["early_short"] = True
    return d


RECID_EARLY_START = date(2026, 10, 22)     # 7 天累犯「處置前一天收盤空」規則上線日(處置前一天在這天以後才套用)


def cover_lbl(d):
    """空單回補日的名稱：7 天初犯「出關日」、7 天累犯「出關日+1」。"""
    return d.get("cover_lbl", "出關日")


def apply_chain(disposals):
    """
    接力處置(2026-10-22 使用者要求，例 3287)：5 天初犯處置期間(首日~最後一天)內又公告下一次處置、
    且那次是 5 天 → 多單出場日改成那次處置的出關日(結束後第一個交易日)收盤。
    回測(新制 15 件，全部在第一次處置第 3 天公告、隔天開始第二次)：第一次結束就賣 +4.18%，
    續抱到第二次出關日 +14.85%、勝率 80%(去掉最好 2 件，從第一次結束起算仍 +4.08%)。
    第二次是 7 天的不套用：樣本只有 1 件，而且策略 A 會在 7 天處置結束日前一天放空，會衝突。
    """
    by = {}
    for d in disposals:
        by.setdefault(d["code"], []).append(d)
    for lst in by.values():
        for d in lst:
            if d["offense"] != "初犯" or d["n_days"] != 5:
                continue
            nxt = [x for x in lst if x is not d and x["start"] > d["start"] and x.get("announce")
                   and d["start"] <= x["announce"] <= d["end"]]
            if not nxt:
                continue
            d2 = min(nxt, key=lambda x: x["start"])
            if d2["n_days"] != 5:
                continue
            d["chain"] = d2
            d["long_exit"] = next_trading_day(d2["end"])
    return disposals


def chain_note(d):
    """接力處置的出場說明。"""
    d2 = d.get("chain")
    if not d2:
        return ""
    return (f"接力處置：{d2['announce']:%m/%d} 又公告 5 天{d2['offense']}（{d2['start']:%m/%d}～{d2['end']:%m/%d}），"
            f"續抱到出關日 {d['long_exit']:%m/%d}")


def short_why(d):
    """放空卡片附處置期間，讓人看得出為什麼是這天空(策略 A：結束日前一天收盤空、出關日收盤補)。"""
    if d.get("early_short"):
        return (f"・處置 {d['start']:%m/%d}～{d['end']:%m/%d}：{d['short_entry']:%m/%d} 處置前一天收盤空（7 天累犯），"
                f"{d['short_cover']:%m/%d}（{cover_lbl(d)}）回補")
    return (f"・處置 {d['start']:%m/%d}～{d['end']:%m/%d}：{d['short_entry']:%m/%d} 是結束日前一天，"
            f"{d['short_cover']:%m/%d}（{cover_lbl(d)}）回補")


def short_view(d):
    """給 load_prices_for/close_of 用的空單版(進場日換成空單進場日)。"""
    s = dict(d)
    s["entry"] = d["short_entry"]
    return s


def long_bought(d, clause_db, quotes, daily, today):
    """照規則這檔多單有沒有買：進場日已過才判斷。回傳 (是否買進, 不買原因)。"""
    ie = d["entry"]
    if ie > today:
        return None, ""
    if is_limit_up(d["code"], ie, quotes, daily):
        return False, "進場日收盤漲停買不到"
    cl = clause_type(clause_db, d["code"], ie)
    di = dev20_info(d["code"], ie, today, quotes, daily)
    drop = drop_notice(clause_db, d["code"], ie, inclusive=False)
    v = judge(cl, di, drop)
    if v == "drop":
        return False, f"第一款跌幅（{drop}）"
    if v == "skip":
        return False, f"第一款、進場日乖離 {di['dev'] * 100:+.0f}% > {DEV_MAX * 100:.0f}%"
    return True, ""


def long_status(d, as_of, quotes, daily, corp):
    """多單現況(最晚算到出場日)。"""
    return calc_return(d, min(as_of, d["long_exit"]), quotes, daily, corp)


def short_status(d, as_of, quotes, daily, now_price=None):
    """空單現況：報酬 = (放空價 - 現價) / 放空價(未扣成本)。"""
    code, se = d["code"], d["short_entry"]
    entry = close_of(code, se, quotes, daily)
    if entry is None:
        return None
    cur, cur_day = entry, se
    day = se
    while day < min(as_of, d["short_cover"]):
        day += timedelta(days=1)
        c = close_of(code, day, quotes, daily)
        if c is not None:
            cur, cur_day = c, day
    if now_price:
        cur = now_price
    prev = close_of(code, prev_trading_day(se), quotes, daily)
    blocked = bool(prev and (entry <= limit_down_price(prev) + 1e-6       # 進場日收盤跌停空不到
                             or entry >= limit_up_price(prev) - 1e-6))   # 漲停不空(2026-10-22 使用者規則)
    return dict(entry=entry, cur=cur, cur_date=cur_day, ret=(entry - cur) / entry * 100, blocked=blocked)


SEP = "┈┈┈┈┈┈┈┈┈┈┈┈"
BAR = "━━━━━━━━━━━━"


def who(d):
    return f"<b>{html.escape(d['code'])} {html.escape(nm(d['code'], d['name']))}</b>"


def kind(d):
    return f"{d['n_days']}天{d['offense']}"


def pct(x):
    return f"{x:+.1f}%"


def long_card(d, rr, now_price=None, head_extra="", lines_extra=()):
    """多單一檔(多行)。"""
    cur = now_price if now_price else rr["cur"]
    ret = (cur / rr["entry"] - 1) * 100 if now_price else (rr["raw"] if rr["suspicious"] else rr["adj"])
    out = [f"{who(d)}｜多單｜{kind(d)}{head_extra}",
           f"・進場：{rr['entry_date']:%m/%d} 收盤 {rr['entry']:g}",
           f"・現價：{cur:g}（{pct(ret)}）",
           f"・停損價：{rr['stop_price']:g}（收盤 ≤ 此價就賣）",
           f"・預計出場：{d['long_exit']:%m/%d} 收盤賣"]
    return "\n".join(out + list(lines_extra))


def short_card(d, sr, head_extra="", lines_extra=()):
    """空單一檔(多行)。"""
    out = [f"{who(d)}｜空單｜{kind(d)}{head_extra}",
           f"・放空：{d['short_entry']:%m/%d} 收盤 {sr['entry']:g}",
           f"・現價：{sr['cur']:g}（{pct(sr['ret'])}）",
           f"・預計回補：{d['short_cover']:%m/%d} 收盤買回（{cover_lbl(d)}）",
           "・停損：無"]
    return "\n".join(out + list(lines_extra))


def section_block(title, items, empty="無"):
    """
    一個區塊(2026-10-18 使用者要求)：做多/放空/賣出/回補/停損/持有中各自一塊，有內容的用
    Telegram 可展開引用(點一下展開/收合)，每檔之間分隔線；沒有內容的只留一行「標題：無」。
    """
    if not items:
        return f"{title}：{empty}"
    return f"<blockquote expandable>{title}（{len(items)} 檔）\n" + f"\n{SEP}\n".join(items) + "</blockquote>"


def day_lbl(word, d):
    """「明天（10/16）」：訊息裡講明天/今天一律加日期(2026-10-18 使用者要求)。"""
    return f"{word}（{d:%m/%d}）"


def c13_line(c):
    """第十三款一行結論(2026-10-30 使用者要求：只講有沒有觸發、會不會變 7 天，不要敘述)。"""
    days, hit, txt = c.get("days"), c.get("clause13_hit"), c.get("clause13") or ""
    if days == 7:
        return "・有第13款 → <b>7 天處置</b>"
    if hit:
        return "・⚠️ 預估會有第13款 → <b>可能變 7 天處置</b>"
    if hit is None and ("無法預估" in txt or "失敗" in txt):
        return "・第13款無法預估"
    return "・無第13款"


def target_line(price, prev, conditions):
    """「目前股價 51（-5.7%）距目標價 58.2 需漲 14.1%」(2026-10-30 使用者指定格式)。
    目標取進處置條件裡最容易達到的那個；條件沒有目標價只列現價。"""
    chg = f"（{(price / prev - 1) * 100:+.1f}%）" if prev else ""
    tg = [float(m.group(1)) for x in conditions or [] for m in [re.search(r"目標\s*([\d.]+)", x)] if m]
    if not tg:
        return f"・目前股價 {price:g}{chg}"
    t = min(tg, key=lambda v: v / price)            # 需漲最少(或已到目標)的那個
    gap = (f"需漲 {(t / price - 1) * 100:.1f}%" if t > price
           else f"已到目標，可跌 {(1 - t / price) * 100:.1f}%")
    return f"・目前股價 {price:g}{chg} 距目標價 {t:g}｜{gap}"


def cond_block(c, when):
    """進處置條件(收盤達任一)：放空/觀察卡也要列(2026-10-30 使用者問 3374)。"""
    cond = [f"　　{html.escape(x)}" for x in (c.get("conditions") or [])[:3]]
    return [f"・進處置條件（{when}收盤達任一）：", *(cond or ["　　（再被注意即進處置，無單一價位條件）"])]


def pre_short_card(c, when, after, px_line=None, live=None):
    """
    7 天累犯「處置前一天收盤空」(2026-10-23)：聽牌、預估再被注意就進 7 天累犯處置 → 那天收盤空。
    when = 進場日標籤(明天/今天)，after = 進場日的下一個交易日標籤(沒中時回補日)。live = 盤中即時 (price, up, down)。
    """
    lines = [f"<b>{html.escape(c['code'])} {html.escape(nm(c['code'], c['name']))}</b>（{html.escape(str(c['market']))}）"
             f"｜聽牌，預估 7 天累犯"]
    warn = ""
    if live and live[0]:
        p, up, dn = live
        if up and p >= up - 1e-6:
            warn = "⚠️ 現在漲停：漲停不空"
        elif dn and p <= dn + 1e-6:
            warn = "⚠️ 現在跌停：空不到"
    lines.append(f"👉 <b>{when}收盤：融券放空</b>（{when}收盤後若公告 7 天累犯處置）" + (f"\n　{warn}" if warn else ""))
    if px_line:
        lines.append(px_line)
    lines += [f"・回補：處置結束後的出關日+1 收盤（約 9 個交易日後，公告後推播會給日期）",
              f"・{when}收盤漲停不空、跌停空不到",
              f"・沒中（沒公告處置或不是 7 天累犯）→ {after}收盤回補",
              c13_line(c),
              *cond_block(c, when),
              *[f"・{html.escape(w)}" for w in c.get("data_warning") or []]]
    return "\n".join(x for x in lines if x)


def missed_predictions(log, day, disposals):
    """
    day 收盤進場的預測單(5 天初犯預買 _cand、7 天累犯預空 _pre_short)，當晚沒有如預期公告處置的。
    回傳 (沒中的預買 [(code, name)], 沒中的預空 [(code, name)])。下一個交易日開始處置才算中；
    預空還要是 7 天累犯。
    """
    if not is_trading(day):
        return [], []                    # [2026-10-24] 那天臨時休市(颱風)：沒進場，預測順延到下一個交易日重新列
    nxt = next_trading_day(day)
    started = {}
    for d in disposals:
        if d["start"] == nxt:
            started[d["code"]] = d

    def items(key):
        out = []
        for x in (log.get(key) or {}).get(str(day), []):
            code, name = (x, "") if isinstance(x, str) else (x.get("code"), x.get("name", ""))
            out.append((code, name))
        return out
    miss_buy = [(c, n) for c, n in items("_cand") if c not in started]
    miss_short = [(c, n) for c, n in items("_pre_short")
                  if not (c in started and started[c]["n_days"] == 7 and started[c]["offense"] == "累犯")]
    return miss_buy, miss_short


def miss_cards(miss_buy, miss_short, day, when):
    """沒中的預測單：多單賣出、空單回補。"""
    sell = [f"<b>{html.escape(c)} {html.escape(nm(c, n))}</b>（預買沒中）\n👉 <b>{when}收盤：賣出</b>（若 {day:%m/%d} 有買進）\n"
            f"・{day:%m/%d} 收盤後沒有公告進處置" for c, n in miss_buy]
    cover = [f"<b>{html.escape(c)} {html.escape(nm(c, n))}</b>（預空沒中）\n👉 <b>{when}收盤：回補（買回）</b>（若 {day:%m/%d} 有放空）\n"
             f"・{day:%m/%d} 收盤後沒有公告 7 天累犯處置" for c, n in miss_short]
    return sell, cover


def watch_card(c, when, start):
    """
    觀察(2026-10-18 使用者問 6217)：預估 7 天的聽牌股不用預買，但要講清楚「明天不動作、公告後哪天做什麼」，
    不能直接略過讓人以為漏掉。when=明天/今天標籤，start=若公告處置的處置首日標籤。
    """
    if c.get("offense") == "初犯":
        plan = f"若{when}收盤後公告處置 → {start}（處置首日）收盤買進（做多）"
    else:
        plan = "累犯不做多；若進處置，處置結束日前一天收盤融券放空（策略 A）"
    return "\n".join(x for x in [
        f"<b>{html.escape(c['code'])} {html.escape(nm(c['code'], c['name']))}</b>（{html.escape(str(c['market']))}）"
        f"｜聽牌，預估 {c.get('days')} 天{c.get('offense', '')}",
        f"👉 <b>{when}不動作</b>；{plan}",
        c13_line(c),
        *cond_block(c, when),
        *[f"・{html.escape(w)}" for w in c.get("data_warning") or []]] if x)


def _focus(parts, when, f_long, f_short, f_sell, f_cover, f_stop, stop_title, f_watch=()):
    parts += [section_block(f"🟢 <b>做多・買進｜{when}收盤</b>", f_long),
              section_block(f"🔴 <b>放空・融券賣出｜{when}收盤</b>", f_short),
              section_block(f"🟠 <b>賣出多單｜{when}收盤</b>", f_sell),
              section_block(f"🔵 <b>回補空單・買回｜{when}收盤</b>", f_cover)]
    if f_stop:
        parts.append(section_block(stop_title, f_stop))
    if f_watch:
        parts.append(section_block(f"👀 <b>觀察（7 天聽牌，{when}不動作）</b>", list(f_watch)))


def closure_note(a, b):
    """a～b 之間有臨時休市(颱風)時，開頭加一行說明。"""
    hit = closures_between(a, b)
    if not hit:
        return ""
    days = "、".join(f"{x:%m/%d}" for x in hit)
    return (f"\n🌀 <b>{days} 臨時休市</b>（颱風，臺北市停止上班）：原定那天的進出場全部順延到 {b:%m/%d}，"
            f"碰到休市的處置期間也往後順延")


def build_message(today, nxt,f_long, f_short, f_sell, f_cover, f_stop, holds_long, holds_short, notes, cand_err=None,
                  f_watch=()):
    """盤後推播：策略說明 → 明日焦點(做多/放空/賣出/回補/停損，各自可展開) → 持有中(多/空各自可展開)。"""
    wd = "一二三四五六日"
    when = day_lbl("明天", nxt)
    parts = [f"🔔 <b>處置策略｜{today:%m/%d}({wd[today.weekday()]}) 盤後</b>\n"
             f"以下是{when}({wd[nxt.weekday()]}) 收盤前要做的事；點區塊可展開/收合" + closure_note(today, nxt), RULES_HTML,
             f"{BAR}\n🎯 <b>明日焦點｜{when}收盤前</b>\n{BAR}"]
    _focus(parts, when, f_long, f_short, f_sell, f_cover, f_stop, f"⛔ <b>停損｜{when}盡快賣出</b>", f_watch)
    if cand_err:
        parts.append(f"⚠️ 5 天初犯預買候選計算失敗，請看{when}盤中提醒（{html.escape(cand_err[:80])}）")
    parts += [f"{BAR}\n📊 <b>持有中｜{today:%m/%d} 收盤</b>\n{BAR}",
              section_block("🟢 <b>持有多單</b>", holds_long),
              section_block("🔴 <b>持有空單</b>", holds_short)]
    if notes:
        parts.append("\n".join(notes))
    return "\n\n".join(parts).strip()


def build_pre_message(today, anchor, f_long, f_short, f_sell, f_cover, f_stop, holds_long, holds_short, notes,
                      f_watch=()):
    """盤中提醒：策略說明 → 今日焦點(各自可展開) → 持有中(即時價)。"""
    wd = "一二三四五六日"
    when = day_lbl("今天", today)
    parts = [f"⏰ <b>處置策略｜{today:%m/%d}({wd[today.weekday()]}) 盤中提醒</b>\n"
             f"以下是{when}收盤前要做的事（聽牌預測以 {anchor:%m/%d} 收盤為準，現價為即時行情）；點區塊可展開/收合"
             + closure_note(anchor, today),
             RULES_HTML, f"{BAR}\n🎯 <b>今日焦點｜{when}收盤前</b>\n{BAR}"]
    _focus(parts, when, f_long, f_short, f_sell, f_cover, f_stop, "⛔ <b>停損注意</b>", f_watch)
    parts += [f"{BAR}\n📊 <b>持有中｜即時價</b>\n{BAR}",
              section_block("🟢 <b>持有多單</b>", holds_long),
              section_block("🔴 <b>持有空單</b>", holds_short)]
    if notes:
        parts.append("\n".join(notes))
    return "\n\n".join(parts).strip()


def positions(disposals, today, clause_db, quotes, daily, corp):
    """照規則推算目前部位(以 today 收盤為準)。
    回傳 (多單 [(d, rr)], 多單今天停損 [(d, rr)], 沒買的說明, 空單 [(d, sr)])。"""
    longs, stops, notes, shorts = [], [], [], []

    def long_part(d):
        # 多單判斷獨立成函式：這裡的 return 只結束多單，不會連帶跳過下面的空單
        # (2026-10-18 修：原本在迴圈裡 continue，7 天初犯「同一收盤賣多再放空」那天空單被漏掉，6213 10/16)
        ok, why = long_bought(d, clause_db, quotes, daily, today)
        if not ok:
            if d["entry"] == today:
                notes.append(f"（{d['code']} {nm(d['code'], d['name'])} {today:%m/%d} 是進場日但不買：{why}）")
            return
        rr = long_status(d, today, quotes, daily, corp)
        if rr is None:
            return
        if rr["stopped"]:
            if rr["stopped"]["date"] == today:
                stops.append((d, rr))
            return                             # 更早就停損出場了
        if d["long_exit"] == today:
            return                             # 今天收盤已出場
        longs.append((d, rr))

    for d in disposals:
        if d["offense"] == "初犯" and d["entry"] <= today <= d["long_exit"]:
            long_part(d)
        if d["n_days"] == 7 and d["short_entry"] <= today < d["short_cover"]:
            sr = short_status(d, today, quotes, daily)
            if sr and not sr["blocked"]:
                shorts.append((d, sr))
    return longs, stops, notes, shorts


def cand_card(c, verdict, act, when, px_line, clause, cond_lines):
    """5 天初犯預買候選一檔(多行)。when：「明天」或「今天」。"""
    if verdict in ("drop", "skip"):
        order = f"👉 <b>不動作</b>｜{act}"
    elif act.startswith("⚠️"):
        order = f"👉 <b>{when}收盤前：買進（做多）</b>，但有條件\n　{act}"
    else:
        order = f"👉 <b>{when}收盤前：買進（做多）</b>｜{act}"
    days = c.get("days") or 5
    lines = [f"<b>{html.escape(c['code'])} {html.escape(nm(c['code'], c['name']))}</b>（{html.escape(str(c['market']))}）｜{CLAUSE_TXT[clause]}",
             order]
    if verdict in ("drop", "skip"):                      # 濾掉的只留名稱＋原因(2026-10-18 使用者要求)
        return "\n".join(lines)
    # [2026-10-30 使用者指定] 預估 → 目前股價距目標 → 進處置條件 → 第13款一行
    lines += [f"・預估：{when}收盤再被注意就進處置 → <b>{days} 天</b>、{c.get('offense', '初犯')}",
             px_line,
             *(cond_lines or ["　　（再被注意即進處置，無單一價位條件）"]),
             c13_line(c),
             *[f"・{html.escape(w)}" for w in c.get("data_warning") or []],
             f"・出場：處置最後一天收盤賣（處置期間若又被公告 5 天累犯 → 改抱到那次的出關日）；停損＝買價×{1 - STOP_PCT:.2f}"]
    return "\n".join(x for x in lines if x)


def fmt_next_cand(c, cl, di, close, locked, drop=None, when="明天", today_lbl="今天", prev=None):
    """盤後版明日預買候選(多行卡片)。"""
    v = judge(cl, di, drop)
    if v == "drop":
        act = drop_txt(drop)
    elif cl == "一" and di:
        act = f"⚠️ 第一款：{when}收盤 ≤ {di['thr']:g} 才買（超過代表乖離 > {DEV_MAX*100:.0f}%，不買）"
    elif cl == "一":
        act = f"⚠️ 第一款：乖離資料不足，收盤前自行確認乖離 ≤ {DEV_MAX*100:.0f}%"
    else:
        act = "✅ 可買"
    px_line = None if close is None else target_line(close, prev, c["conditions"][:3]) + ("（漲停）" if locked else "")
    cond = [f"　　{html.escape(x)}" for x in c["conditions"][:3]]
    return cand_card(c, "drop" if v == "drop" else "ok", act, when, px_line, cl, cond)


# ---------------------------------------------------------------- 盤中提醒
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def fetch_mis(code_markets):
    """即時行情(證交所 MIS)：{code: dict(price, prev, up, down, name)}；price 沒成交時退回昨收。"""
    out = {}
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=20)
    except Exception:
        pass
    want = {c: m for c, m in code_markets}
    # 2026-10-13 12:50 盤中提醒整批查無現價(MIS 偶發回空)：沒拿到的代號最多再試 2 輪，每輪換新連線
    for attempt in range(3):
        todo = [(c, m) for c, m in want.items() if c not in out]
        if not todo:
            break
        if attempt:
            time.sleep(5 * attempt)
            s = requests.Session()
            s.headers.update(UA)
            try:
                s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=20)
            except Exception:
                pass
        items = [f"{'tse' if m == '上市' else 'otc'}_{c}.tw" for c, m in todo]
        for i in range(0, len(items), 40):
            try:
                time.sleep(2)
                r = s.get(MIS_URL, params={"ex_ch": "|".join(items[i:i + 40]), "json": "1", "delay": "0"}, timeout=20)
                for x in r.json().get("msgArray", []):
                    def f(k):
                        try:
                            return float(x.get(k))
                        except (TypeError, ValueError):
                            return None
                    last, prev = f("z"), f("y")
                    if last is None and prev is None:
                        continue
                    out[x.get("c")] = dict(price=last if last else prev, traded=bool(last), prev=prev,
                                           up=f("u"), down=f("w"))
            except Exception as e:
                print(f"[WARN] MIS 查價失敗(第 {attempt + 1} 輪)：{e}")
    miss = [c for c in want if c not in out]
    # MIS 的 z 欄偶爾是「-」(那一刻沒有成交)，只拿得到昨收；這些也問一次 Fugle，有今天的成交價就用它
    no_trade = [c for c in want if c in out and not out[c].get("traded")]
    if miss:
        print(f"[WARN] MIS 三輪後仍查無：{miss}，改查 Fugle")
    if miss or no_trade:
        fg = fetch_fugle(miss + no_trade)
        for c, q in fg.items():
            if c in miss or q.get("traded"):
                out[c] = q
        still = [c for c in want if c not in out]
        if still:
            print(f"[WARN] Fugle 也查無：{still}")
    return out


def fetch_fugle(codes):
    """Fugle 即時報價(MIS 的備援)：{code: dict(price, prev, up, down, traded, src)}。
    金鑰 FUGLE_API_KEY 在共用 .env；報價日期不是今天(還沒開盤/休市)就當作沒有即時價，只留昨收。"""
    out = {}
    try:
        from dotenv import load_dotenv
        load_dotenv(r"E:\Vibe Coding\ANTIGRAVITY SETTINGS\.env")
    except Exception:
        pass
    key = os.getenv("FUGLE_API_KEY", "")
    if not key:
        print("[WARN] 沒有 FUGLE_API_KEY，無法用 Fugle 備援")
        return out
    for c in codes:
        try:
            r = requests.get(f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{c}",
                             headers={"X-API-KEY": key}, timeout=10)
            j = r.json()
            prev = j.get("previousClose") or j.get("referencePrice")
            last = j.get("lastPrice") or j.get("closePrice")
            if j.get("date") != str(date.today()):
                # 報價還停在上一個交易日(今天還沒開盤/休市)：那天的收盤才是「昨收」
                prev, last = last or prev, None
            if not (last or prev):
                continue
            prev = float(prev) if prev else None
            out[c] = dict(price=float(last) if last else prev, traded=bool(last), prev=prev,
                          up=limit_up_price(prev) if prev else None, down=None, src="Fugle")
            time.sleep(1.1)                     # 免費方案每分鐘 60 次
        except Exception as e:
            print(f"[WARN] Fugle 查價失敗 {c}：{e}")
    return out


def px_text(price, prev, up, fallback_close=None, traded=True, src=None):
    """現價＋漲跌幅＋漲停價；還沒成交標「尚無成交」；即時行情失敗時用昨收代並明講。"""
    if not price:
        return f"即時行情查詢失敗，昨收 {fallback_close:g}" if fallback_close else "即時行情查詢失敗"
    tag = f"（{src}）" if src else ""
    lim = f"｜漲停價 {up:g}" if up else ""
    if not traded:
        return f"尚無成交，昨收 {prev or price:g}{lim}{tag}"
    chg = f"（{(price / prev - 1) * 100:+.1f}%）" if prev else ""
    return f"現價 {price:g}{chg}{lim}{tag}"


def clause1_drop(reason):
    """注意公告原文裡，第一款那一句是不是「跌幅達x%」(短期股價跌太多被注意)。回傳跌幅數字字串或 None。
    第十一款(價差＋收盤六日最高/最低)不算：它可能是漲(最高)也可能是跌，使用者定義的跌多只看第一款。"""
    for m in re.finditer(r"(?<!漲)跌幅(?:達)?\s*([\d.]+)\s*%", reason or ""):
        k = reason.find("款", m.end())
        if k >= 2 and reason[k - 2:k + 1] == "第一款" and reason[k - 3:k - 2] != "十":
            return m.group(1)
    return None


def drop_notice(db_path, code, upto, inclusive=True):
    """
    最近一次注意公告(upto 當天含/不含)的第一款是不是因為「跌幅」被注意(2026-10-16 使用者定義：短期股價跌太多)。
    是 → 回傳說明文字(例「10/15 第一款跌幅達28.57%」)；否或查不到 → None。
    """
    if not db_path:
        return None
    import sqlite3
    conn = sqlite3.connect(db_path)
    op = "<=" if inclusive else "<"
    rows = conn.execute(f"SELECT announce_date, clause_number, reason FROM attention_clauses WHERE code=? AND announce_date {op} ? "
                        "ORDER BY announce_date DESC", (code, str(upto))).fetchall()
    conn.close()
    if not rows:
        return None
    day = rows[0][0]
    for d, k, reason in rows:
        if d != day:
            break
        pct = clause1_drop(str(reason or ""))
        if pct:
            return f"{day[5:].replace('-', '/')} 第一款跌幅達{pct}%"
    return None


def drop_txt(why):
    return f"🚫 不買：注意公告是因為跌（{why}），跌多被處置，濾掉"


def fmt_candidate(c, mis, clause, di, drop=None, when="今天"):
    """盤中候選(多行卡片)。回傳 (文字, 是否漲停鎖死, 是否不買)。"""
    q = mis.get(c["code"], {})
    price, up = q.get("price"), q.get("up")
    locked = bool(price and up and price >= up - 1e-6)
    verdict = judge(clause, di, drop)
    if verdict == "drop":
        act = drop_txt(drop)
    elif locked:
        act = "⚠️ 已漲停，恐買不到"
    elif verdict == "skip":
        act = f"🚫 不買：第一款、現價乖離 {di['dev']*100:+.0f}% > {DEV_MAX*100:.0f}%"
    elif clause == "一" and di:
        act = f"⚠️ 第一款：收盤 ≤ {di['thr']:g} 才買（超過不買）"
    elif clause == "一":
        act = "⚠️ 第一款：乖離資料不足，自行確認收盤乖離 ≤ 35%"
    else:
        act = "✅ 可買"
    if price and q.get("traded", True):
        px_line = target_line(price, q.get("prev"), c["conditions"][:3])
    else:                                            # 還沒成交/行情失敗：照舊講清楚
        px_line = "・" + px_text(price, q.get("prev"), up, c.get("close"), q.get("traded", True), q.get("src"))
    cond = [f"　　{html.escape(x)}" for x in c["conditions"][:3]]
    txt = cand_card(c, "skip" if verdict in ("skip", "drop") else verdict, act, when, px_line, clause, cond)
    return txt, locked and verdict != "drop", verdict in ("skip", "drop")


def _wrap(body, head, tail, first):
    if not head:
        return body
    return (head.replace("（續）", "") if first else head) + body + tail


def split_message(msg, limit=4000):
    """Telegram 單則上限 4096 字：太長就以段落(空行)拆；單一段落仍太長就以每檔之間的分隔線拆。"""
    if len(msg) <= limit:
        return [msg]
    pieces = []
    for para in msg.split("\n\n"):
        first = True
        if len(para) <= limit:
            pieces.append(para)
            continue
        head, tail = "", ""
        if para.startswith("<blockquote expandable>") and para.endswith("</blockquote>"):
            body = para[len("<blockquote expandable>"):-len("</blockquote>")]
            title, _, body = body.partition("\n")
            head, tail, para = f"<blockquote expandable>{title}（續）\n", "</blockquote>", body
            first = True
        cur = ""
        for it in para.split(f"\n{SEP}\n"):
            if cur and len(cur) + len(it) + len(SEP) + 2 + len(head) + len(tail) > limit:
                pieces.append(_wrap(cur, head, tail, first))
                first = False
                cur = it
            else:
                cur = f"{cur}\n{SEP}\n{it}" if cur else it
        pieces.append(_wrap(cur, head, tail, first))
    chunks, cur = [], ""
    for b in pieces:
        if cur and len(cur) + len(b) + 2 > limit:
            chunks.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    chunks.append(cur)
    return chunks


def load_log():
    if SIGNAL_LOG.exists():
        return json.load(open(SIGNAL_LOG, encoding="utf-8"))
    return {}


def save_log(log):
    SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIGNAL_LOG.with_suffix(".tmp")
    json.dump(log, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, SIGNAL_LOG)


def telegram_api(text):
    """雲端用：直接呼叫 Telegram Bot API(HTML 模式)，失敗丟例外。"""
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                      json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=30)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegram API 回傳失敗: {j.get('error_code')} {j.get('description')}")
    return j["result"]


def push(msg):
    REPORT_HTML.parent.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(msg, encoding="utf-8")
    if send_telegram is None and os.environ.get("TELEGRAM_BOT_TOKEN"):
        for c in split_message(msg):
            res = telegram_api(c)
            print(f"✅ 已推播（{len(c)} 字），message_id={res.get('message_id')}")
        return
    if send_telegram is None:
        print("[ERROR] 找不到 send_telegram 模組，也沒有 TELEGRAM_BOT_TOKEN，未推播。")
        sys.exit(1)
    for c in split_message(msg):
        res = send_telegram(c, parse_mode="HTML")
        # send_telegram 只在 Telegram 回 ok 時才回傳(回傳值就是 result 本身)，失敗會丟例外
        print(f"✅ 已推播（{len(c)} 字），message_id={res.get('message_id')}")


def load_prices_for(targets, today, extra_days=()):
    """行情 parquet / 官方收盤行情 / 除權息，涵蓋每檔 進場日前一日 ~ today。"""
    codes = {d["code"] for d in targets}
    days = set(extra_days)
    for d in targets:
        cur = prev_trading_day(d["entry"])
        while cur <= today:
            days.add(cur)
            cur += timedelta(days=1)
        days.update(prior_trading_days(d["start"], 5))
    daily = load_parquet_daily(codes, days) if targets else {}
    since = min((d["entry"] for d in targets), default=today)
    corp = load_corp_actions(codes, since, today) if targets else {}
    return daily, corp


def ma_days(targets, today):
    """算 20MA 乖離需要的行情日：每檔進場日前 ~40 個日曆天。"""
    days = set()
    for d in targets:
        cur = d["entry"]
        for _ in range(40):
            cur = cur - timedelta(days=1)
            days.add(cur)
    return days


def split_stops(items, today):
    """items: [(d, g, rr)] → (仍持有, 今日觸發停損)；更早就停損的不再顯示。"""
    live, now = [], []
    for it in items:
        rr = it[2]
        if rr and rr["stopped"]:
            if rr["stopped"]["date"] == today:
                now.append(it)
            continue
        live.append(it)
    return live, now


def main_post(args, today):
    """盤後推播：用今天收盤後的最新資料，告訴使用者明天收盤前要做什麼，並列出目前持有部位。"""
    nxt = next_trading_day(today)
    print(f"[{datetime.now()}] 今天 {today}，下一個交易日 {nxt}")
    disposals = apply_chain([enrich(d) for d in load_disposals(today) if d["n_days"] in (5, 7)])
    log = load_log()
    meta = log.setdefault("_meta", {})

    cand_raw, cand7, cand_err = [], [], None
    try:
        data = run_worker(today, Path(PROJ / "temp" / f"evening_{today:%Y%m%d}.json"))
        busy = {d["code"] for d in disposals if d["end"] >= nxt}
        cand_raw = [c for c in data["candidates"] if c["code"] not in busy and c["offense"] == "初犯" and c["days"] == 5]
        cand7 = [c for c in data["candidates"] if c["code"] not in busy and c["days"] == 7]
    except Exception as e:
        cand_err = str(e)
        print(f"[WARN] 明日預買候選計算失敗：{e}")
    cand_raw = drop_low_volume(cand_raw, today, "預買")
    cand7 = drop_low_volume(cand7, today, "7天聽牌")
    cand7_short = [c for c in cand7 if c.get("offense") == "累犯"]      # 7 天累犯：明天收盤預空
    cand7 = [c for c in cand7 if c.get("offense") != "累犯"]            # 7 天初犯：觀察(處置首日才買)

    quotes = load_today_volume()
    longs_t = [d for d in disposals if d["offense"] == "初犯" and d["entry"] <= nxt and d["long_exit"] >= today]
    shorts_t = [short_view(d) for d in disposals if d["n_days"] == 7 and d["short_entry"] <= nxt and d["short_cover"] >= today]
    cand_t = [dict(code=c["code"], entry=nxt, start=nxt) for c in cand_raw]
    daily, corp = load_prices_for(longs_t + shorts_t, today, extra_days=ma_days(longs_t + cand_t, today) | {today})
    if cand_raw:
        daily.update(load_parquet_daily({c["code"] for c in cand_raw}, ma_days(cand_t, today) | {today}))
    clause_db, _ = load_clause_db(today)
    company = load_company()

    T, N = day_lbl("今天", today), day_lbl("明天", nxt)
    f_long, f_short, f_sell, f_cover, f_stop = [], [], [], [], []
    for c in cand_raw:                                   # 做多 1：5 天初犯預買
        cl = clause_type(clause_db, c["code"], nxt)
        di = dev20_info(c["code"], nxt, today, quotes, daily)
        f_long.append(fmt_next_cand(c, cl, di, close_of(c["code"], today, quotes, daily), False,
                                    drop_notice(clause_db, c["code"], today), when=N, today_lbl=T,
                                    prev=close_of(c["code"], prev_trading_day(today), quotes, daily)))
    for d in disposals:                                  # 做多 2：7 天初犯明天處置首日
        if d["offense"] == "初犯" and d["n_days"] == 7 and d["start"] == nxt:
            cl = clause_type(clause_db, d["code"], nxt)
            di = dev20_info(d["code"], nxt, today, quotes, daily)
            drop = drop_notice(clause_db, d["code"], today)
            if judge(cl, di, drop) == "drop":
                order = f"👉 <b>不動作</b>｜{drop_txt(drop)}"
            elif cl == "一" and di:
                order = (f"👉 <b>{N}收盤：買進（做多）</b>，但有條件\n"
                         f"　⚠️ 第一款：{N}收盤 ≤ {di['thr']:g} 才買（超過代表乖離 > {DEV_MAX*100:.0f}%）")
            else:
                order = f"👉 <b>{N}收盤：買進（做多）</b>｜✅ 買"
            detail = [] if "不動作" in order else [
                f"・處置：{d['start']:%m/%d}～{d['end']:%m/%d}（{N}是處置首日）",
                f"・出場：{d['long_exit']:%m/%d} 收盤賣（結束日前一天，同時改放空）",
                f"・停損：買價×{1-STOP_PCT:.2f}"]
            f_long.append("\n".join([f"{who(d)}（{kind(d)}）", order, *detail]))
    for c in cand7_short:                                # 放空 1：7 天累犯預空(明天＝處置前一天)
        f_short.append(pre_short_card(c, N, day_lbl("後天", next_trading_day(nxt))))
    miss_buy, miss_short = missed_predictions(log, today, disposals)   # 今天收盤進場、當晚沒公告處置的
    _ms, _mc = miss_cards(miss_buy, miss_short, today, N)
    for d in disposals:                                  # 放空 2：7 天處置、明天是結束日前一天
        if d["n_days"] == 7 and d["short_entry"] == nxt:
            order = (f"👉 <b>{N}收盤：先賣出多單，再融券放空</b>" if d["offense"] == "初犯"
                     else f"👉 <b>{N}收盤：融券放空</b>")
            f_short.append("\n".join([f"{who(d)}（{kind(d)}）", order, short_why(d),
                                      f"・回補：{d['short_cover']:%m/%d}（{cover_lbl(d)}）收盤買回", "・停損：無"]))

    longs, stops, notes, shorts = positions(disposals, today, clause_db, quotes, daily, corp)
    for d, rr in longs:                                  # 賣出多單
        if d["long_exit"] == nxt:
            why = ("接力處置的出關日" if d.get("chain") else "處置最後一天") if d["n_days"] == 5 \
                else "結束日前一天；接著放空，見放空區"
            f_sell.append("\n".join([f"{who(d)}（{kind(d)}・多單）", f"👉 <b>{N}收盤：賣出</b>（{why}）",
                                     f"・進場：{rr['entry_date']:%m/%d} 收盤 {rr['entry']:g}",
                                     f"・現價：{rr['cur']:g}（{pct(rr['raw'] if rr['suspicious'] else rr['adj'])}）"]))
    for d, sr in shorts:
        if d["short_cover"] == nxt:
            f_cover.append("\n".join([f"{who(d)}（{kind(d)}・空單）", f"👉 <b>{N}收盤：回補（買回）</b>（{cover_lbl(d)}）",
                                     f"・放空：{d['short_entry']:%m/%d} 收盤 {sr['entry']:g}",
                                     f"・現價：{sr['cur']:g}（{pct(sr['ret'])}）"]))
    f_sell += _ms
    f_cover += _mc
    for d, rr in stops:
        st = rr["stopped"]
        f_stop.append("\n".join([f"{who(d)}（{kind(d)}・多單）", f"👉 <b>{N}盡快賣出（停損）</b>",
                                 f"・{T}收盤 {st['close']:g} ≤ 停損價 {rr['stop_price']:g}（{pct(st['ret'])}）",
                                 f"・若{T}收盤已賣出就不用動作"]))

    holds_long = []
    for d, rr in sorted(longs, key=lambda x: (x[0]["long_exit"], x[0]["code"])):
        g = golden_check(d, company, daily, quotes)
        head = (f"｜{T}進場" if d["entry"] == today else "") + (f"｜🟠 {N}賣出" if d["long_exit"] == nxt else "")
        extra = [f"・黃金濾網：✗ {html.escape(fail_reasons(g))}（只標註）"] if not g["golden"] else []
        if d.get("chain"):
            extra = [f"・🔁 {chain_note(d)}"] + extra
        holds_long.append(long_card(d, rr, head_extra=head, lines_extra=extra))
    holds_short = [short_card(d, sr, head_extra=f"｜🔵 {N}回補" if d["short_cover"] == nxt else "")
                   for d, sr in sorted(shorts, key=lambda x: (x[0]["short_cover"], x[0]["code"]))]

    f_watch = [watch_card(c, N, day_lbl("後天", next_trading_day(nxt))) for c in cand7]
    msg = build_message(today, nxt, f_long, f_short, f_sell, f_cover, f_stop, holds_long, holds_short, notes, cand_err,
                        f_watch)
    import hashlib
    sig = hashlib.md5(msg.split("📊")[0].encode("utf-8")).hexdigest()   # 焦點內容沒變就不重推
    if not args.dry_run and not args.force_send and meta.get("last_push_date") == str(today) and meta.get("last_sig") == sig:
        print("  今天的盤後推播已送過、內容沒有變，靜默結束。")
        return
    print("\n" + msg + "\n")
    if args.dry_run:
        print("[dry-run] 未推播、未寫紀錄。")
        return
    push(msg)
    meta["last_push_date"], meta["last_sig"] = str(today), sig
    if cand_err is None:
        log.setdefault("_cand", {})[str(nxt)] = [dict(code=c["code"], name=c["name"]) for c in cand_raw]
        log.setdefault("_pre_short", {})[str(nxt)] = [dict(code=c["code"], name=c["name"]) for c in cand7_short]
    save_log(log)


def run_worker(anchor, out_path):
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(PROJ / "scripts" / "automation" / "preclose_candidates.py"),
                        "--anchor", f"{anchor:%Y%m%d}", "--out", str(out_path)],
                       cwd=str(PROJ), env=env, capture_output=True, text=True, encoding="utf-8", timeout=1500)
    print(r.stdout[-600:])
    if r.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"候選計算失敗(exit {r.returncode})：{r.stderr[-800:]}")
    return json.load(open(out_path, encoding="utf-8"))


def main_pre(args, today):
    """盤中提醒(12:50)：今天收盤前要做的事(用即時價)，並列出目前持有部位。"""
    anchor = prev_trading_day(today)
    print(f"[{datetime.now()}] 盤中提醒：今天 {today}，預測錨點(最近收盤日) {anchor}")
    if not args.date and not args.dry_run and not args.force_send and datetime.now().time() > datetime.strptime("13:20", "%H:%M").time():
        print("  已過 13:20，盤中提醒沒有意義(排程補跑)，結束。")
        return
    log = load_log()
    meta = log.setdefault("_meta", {})
    if not args.dry_run and not args.force_send and meta.get("pre_close_date") == str(today):
        print("  今天的盤中提醒已推播過，結束。")
        return

    disposals = apply_chain([enrich(d) for d in load_disposals(today) if d["n_days"] in (5, 7)])
    busy = {d["code"] for d in disposals if d["end"] >= today}
    cands5, cand7, cand_err = [], [], None
    try:
        data = run_worker(anchor, Path(PROJ / "temp" / f"preclose_{today:%Y%m%d}.json"))
        cands5 = [c for c in data["candidates"] if c["code"] not in busy and c["offense"] == "初犯" and c["days"] == 5]
        cand7 = [c for c in data["candidates"] if c["code"] not in busy and c["days"] == 7]
    except Exception as e:
        cand_err = str(e)
        print(f"[WARN] 盤中候選計算失敗：{e}")
    cands5 = drop_low_volume(cands5, anchor, "預買")
    cand7 = drop_low_volume(cand7, anchor, "7天聽牌")
    cand7_short = [c for c in cand7 if c.get("offense") == "累犯"]
    cand7 = [c for c in cand7 if c.get("offense") != "累犯"]

    quotes = load_today_volume()            # 盤中拿到的是昨天收盤(官方日行情)
    longs_t = [d for d in disposals if d["offense"] == "初犯" and d["entry"] <= today and d["long_exit"] >= today]
    shorts_t = [short_view(d) for d in disposals if d["n_days"] == 7 and d["short_entry"] <= today and d["short_cover"] >= today]
    cand_t = [dict(code=c["code"], entry=today, start=today) for c in cands5]
    new7 = [d for d in disposals if d["offense"] == "初犯" and d["n_days"] == 7 and d["start"] == today]
    daily, corp = load_prices_for(longs_t + shorts_t + new7, anchor,
                                  extra_days=ma_days(longs_t + cand_t + new7, today) | {anchor})
    if cands5:
        daily.update(load_parquet_daily({c["code"] for c in cands5}, ma_days(cand_t, today) | {anchor}))
    clause_db, _ = load_clause_db(today)
    company = load_company()

    longs, _, _, shorts = positions(disposals, anchor, clause_db, quotes, daily, corp)
    short_today = [d for d in disposals if d["n_days"] == 7 and d["short_entry"] == today]
    mis_targets = ([(c["code"], c["market"]) for c in cands5 + cand7_short] + [(d["code"], d["market"]) for d in new7 + short_today]
                   + [(d["code"], d["market"]) for d, _ in longs] + [(d["code"], d["market"]) for d, _ in shorts])
    mis = fetch_mis(list(dict.fromkeys(mis_targets)))
    price = lambda code: (mis.get(code) or {}).get("price")

    def px_line(d):
        q = mis.get(d["code"]) or {}
        return "・" + px_text(q.get("price"), q.get("prev"), q.get("up"), None, q.get("traded", True), q.get("src"))

    T = day_lbl("今天", today)
    f_long, f_short, f_sell, f_cover, f_stop = [], [], [], [], []
    for c in cands5:
        cl = clause_type(clause_db, c["code"], today)
        di = dev20_info(c["code"], today, today, {}, daily, now_price=price(c["code"]))
        txt, _, _ = fmt_candidate(c, mis, cl, di, drop_notice(clause_db, c["code"], today, inclusive=False), when=T)
        f_long.append(txt)
    for d in new7:
        cl = clause_type(clause_db, d["code"], today)
        di = dev20_info(d["code"], today, today, quotes, daily, now_price=price(d["code"]))
        drop = drop_notice(clause_db, d["code"], today, inclusive=False)
        v = judge(cl, di, drop)
        if v == "drop":
            order = f"👉 <b>不動作</b>｜{drop_txt(drop)}"
        elif v == "skip":
            order = f"👉 <b>不動作</b>｜🚫 不買：第一款、現價乖離 {di['dev']*100:+.0f}% > {DEV_MAX*100:.0f}%"
        elif cl == "一" and di:
            order = f"👉 <b>{T}收盤：買進（做多）</b>，但有條件\n　⚠️ 第一款：收盤 ≤ {di['thr']:g} 才買"
        else:
            order = f"👉 <b>{T}收盤：買進（做多）</b>｜✅ 買"
        detail = [] if v in ("drop", "skip") else [
            px_line(d), f"・出場：{d['long_exit']:%m/%d} 收盤賣（結束日前一天，同時改放空）",
            f"・停損：買價×{1-STOP_PCT:.2f}"]
        f_long.append("\n".join([f"{who(d)}（{kind(d)}・{T}是處置首日）", order, *detail]))
    for c in cand7_short:                                # 7 天累犯預空：今天＝處置前一天
        q = mis.get(c["code"]) or {}
        f_short.append(pre_short_card(c, T, day_lbl("明天", next_trading_day(today)),
                                      px_line=px_line(c), live=(q.get("price"), q.get("up"), q.get("down"))))
    _ms, _mc = miss_cards(*missed_predictions(load_log(), anchor, disposals), anchor, T)
    f_sell += _ms
    f_cover += _mc
    for d in short_today:
        order = (f"👉 <b>{T}收盤：先賣出多單，再融券放空</b>" if d["offense"] == "初犯"
                 else f"👉 <b>{T}收盤：融券放空</b>")
        f_short.append("\n".join([f"{who(d)}（{kind(d)}）", order, short_why(d), px_line(d),
                                  f"・回補：{d['short_cover']:%m/%d}（{cover_lbl(d)}）收盤買回", "・停損：無"]))

    holds_long, holds_short = [], []
    for d, rr in sorted(longs, key=lambda x: (x[0]["long_exit"], x[0]["code"])):
        p = price(d["code"])
        cur = p or rr["cur"]
        ret = (cur / rr["entry"] - 1) * 100
        head = ""
        if d["long_exit"] == today:
            why = ("接力處置的出關日" if d.get("chain") else "處置最後一天") if d["n_days"] == 5 \
                else "結束日前一天；接著放空，見放空區"
            f_sell.append("\n".join([f"{who(d)}（{kind(d)}・多單）", f"👉 <b>{T}收盤：賣出</b>（{why}）",
                                     f"・進場：{rr['entry_date']:%m/%d} 收盤 {rr['entry']:g}", f"・現價：{cur:g}（{pct(ret)}）"]))
            head += f"｜🟠 {T}賣出"
        if p and p <= rr["stop_price"]:
            f_stop.append("\n".join([f"{who(d)}（{kind(d)}・多單）", "👉 <b>收盤仍 ≤ 停損價就收盤賣出</b>",
                                     f"・現價 {p:g} ≤ 停損價 {rr['stop_price']:g}（{pct(ret)}）"]))
            head += "｜⚠️ 已在停損價下"
        elif p and p <= rr["stop_price"] * 1.03:
            f_stop.append("\n".join([f"{who(d)}（{kind(d)}・多單）", "👉 注意：距停損價不到 3%",
                                     f"・現價 {p:g}，停損價 {rr['stop_price']:g}（{pct(ret)}）"]))
        g = golden_check(d, company, daily, quotes)
        extra = [f"・黃金濾網：✗ {html.escape(fail_reasons(g))}（只標註）"] if not g["golden"] else []
        if d.get("chain"):
            extra = [f"・🔁 {chain_note(d)}"] + extra
        holds_long.append(long_card(d, rr, now_price=p, head_extra=head, lines_extra=extra))
    for d, sr in sorted(shorts, key=lambda x: (x[0]["short_cover"], x[0]["code"])):
        p = price(d["code"])
        if p:
            sr = dict(sr, cur=p, ret=(sr["entry"] - p) / sr["entry"] * 100)
        head = ""
        if d["short_cover"] == today:
            f_cover.append("\n".join([f"{who(d)}（{kind(d)}・空單）", f"👉 <b>{T}收盤：回補（買回）</b>（{cover_lbl(d)}）",
                                     f"・放空：{d['short_entry']:%m/%d} 收盤 {sr['entry']:g}", f"・現價：{sr['cur']:g}（{pct(sr['ret'])}）"]))
            head = f"｜🔵 {T}回補"
        holds_short.append(short_card(d, sr, head_extra=head))
    notes = [HIT_RATE_NOTE.strip()] if cands5 else []
    if cand_err:
        notes.append(f"⚠️ 5 天初犯候選計算失敗：{html.escape(cand_err[:80])}")

    f_watch = [watch_card(c, T, day_lbl("明天", next_trading_day(today))) for c in cand7]
    msg = build_pre_message(today, anchor, f_long, f_short, f_sell, f_cover, f_stop, holds_long, holds_short, notes,
                            f_watch)
    print("\n" + msg + "\n")
    if args.dry_run:
        print("[dry-run] 未推播、未寫紀錄。")
        return
    push(msg)
    if args.force_send:
        print("[force-send] 手動測試推播：不寫入「今天已推播」，正式排程照常執行。")
        return
    meta["pre_close_date"] = str(today)
    if cand_err is None:
        # 盤中也記下今天的預買/預空(盤後推播靠它判斷「沒中要賣出/回補」；雲端前一晚的盤後可能還是舊規則)
        pre = log.setdefault("_pre_short", {}).setdefault(str(today), [])
        for c in cand7_short:
            if not any((x if isinstance(x, str) else x.get("code")) == c["code"] for x in pre):
                pre.append(dict(code=c["code"], name=c["name"]))
        buy = log.setdefault("_cand", {}).setdefault(str(today), [])
        for c in cands5:
            if not any((x if isinstance(x, str) else x.get("code")) == c["code"] for x in buy):
                buy.append(dict(code=c["code"], name=c["name"]))
    save_log(log)


def check_closure(args):
    """
    [2026-10-24] 推播前查人事總處停班停課：臺北市今天/明天停止上班 → 記成臨時休市(休市以臺北市為準)。
    dry-run 只更新這個行程的休市表、不寫檔。查不到就照舊(事後還有「兩市都沒行情」的判定)。
    """
    from core import market_closure
    hj = None if args.dry_run else DateUtils._TWSE_CACHE_FILE
    res = market_closure.check_and_record(date.today(), hj)
    for d, (st, seg) in sorted((res or {}).items()):
        print(f"[closure] 臺北市 {d}：{st}（{seg}）")


def closure_notice(args, day):
    """臨時休市當天(盤中時段)發一則短訊息，一天一次：昨晚推播寫的「今天」動作都順延。"""
    log = load_log()
    meta = log.setdefault("_meta", {})
    if meta.get("closure_notice") == str(day) and not args.force_send:
        return
    nxt = next_trading_day(day)
    wd = "一二三四五六日"
    msg = (f"🌀 <b>處置策略｜{day:%m/%d}({wd[day.weekday()]}) 臨時休市</b>\n"
           f"臺北市停止上班，證券市場今天全日休市（颱風）。\n"
           f"前一次推播寫「{day:%m/%d}」要做的買進、放空、賣出、回補全部<b>不用做</b>，順延到 "
           f"{nxt:%m/%d}({wd[nxt.weekday()]})；碰到休市的處置期間也往後順延一個交易日。\n"
           f"今晚盤後推播會依新的日期重新列出。")
    print("\n" + msg + "\n")
    if args.dry_run:
        print("[dry-run] 未推播、未寫紀錄。")
        return
    push(msg)
    meta["closure_notice"] = str(day)
    save_log(log)


HIT_RATE_NOTE = ("\n命中率樣本不足：目前唯一沒有資料外洩的回測日(10/02)，官方聽牌「非處置」8 檔中 6 檔隔天公告處置"
                 "(沒中的 2 檔是累犯)；之後每天的候選與結果會記在 disposal_golden_signals.json，盤後推播會標示有沒有列入候選。")


def main():
    ap = argparse.ArgumentParser(description="處置新制初犯策略 D Telegram 推播")
    ap.add_argument("--dry-run", action="store_true", help="只印出內容，不推播、不寫紀錄")
    ap.add_argument("--force-send", action="store_true", help="沒有訊號也推播（測試用）")
    ap.add_argument("--pre-close", action="store_true", help="盤中提醒模式(交易日 12:50 左右執行)：5 天初犯聽牌候選與停損檢查")
    ap.add_argument("--date", help="以這天當作「今天」(YYYYMMDD)，預設今天")
    args = ap.parse_args()

    start_log()
    if not args.date:
        check_closure(args)
    if args.date:
        today = datetime.strptime(args.date, "%Y%m%d").date()
    elif args.pre_close:
        today = date.today()
    else:
        # 盤後模式：「今天」= 最近一個已收盤(14:00 後)的交易日。2026-10-13 晚上電腦睡著，排程 10/14 04:39 補跑時
        # 拿 10/14(還沒開盤)去算，候選是空的、還報錯，10/13 的盤後推播整則沒發出去。
        now = datetime.now()
        ref = now if now.hour >= 14 else now - timedelta(days=1)
        today = DateUtils.get_last_trading_day(datetime.combine(ref.date(), datetime.min.time())).date()
        if today != now.date():
            print(f"[{now}] 補跑：以最近一個已收盤交易日 {today} 為盤後日")
    if not DateUtils.is_trading_day(datetime.combine(today, datetime.min.time())) and not args.force_send:
        print(f"[{datetime.now()}] {today} 不是交易日，結束。")
        if args.pre_close and DateUtils.is_unscheduled_closure(datetime.combine(today, datetime.min.time())):
            closure_notice(args, today)
        return
    (main_pre if args.pre_close else main_post)(args, today)


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, x):
        for st_ in self.streams:
            try:
                st_.write(x)
                st_.flush()
            except Exception:
                pass

    def flush(self):
        pass


def start_log():
    """排程執行時沒有畫面，輸出同時寫到 logs/notify_YYYYMMDD.log，方便事後查為什麼沒推。"""
    d = PROJ / "logs"
    d.mkdir(exist_ok=True)
    f = open(d / f"notify_{date.today():%Y%m%d}.log", "a", encoding="utf-8")
    f.write(f"\n===== {datetime.now()} {' '.join(sys.argv[1:])} =====\n")
    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)


if __name__ == "__main__":
    main()
