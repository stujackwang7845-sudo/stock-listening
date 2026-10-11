"""
處置新制「初犯」策略 D + 黃金濾網標註：Telegram 推播(盤後＋盤前)
=======================================================================
策略 D（2026-10-12 起，原本是「處置首日收盤買」的方案 A）：
  - 只做「初犯」（上市：處置措施「第一次處置」；上櫃：條件文字沒有「最近30個營業日內曾發布處置」）
  - 進場：<b>5 天初犯 = 處置前一天(處置公告當天)收盤買</b>；7 天初犯 = 處置第一天收盤買
  - 出場：處置最後一天收盤賣出；<b>收盤價(還原)跌破進場價 8%，當天收盤賣出(停損)</b>
  - 進場日收盤漲停買不到：回測排除、推播標示
  - 回測(新制 8/10～10/08、排除漲停)：D 方案 52 筆，每筆平均 +5.6%、勝率 75%、單筆最差 -10.3%、
    風報比 5.23(scratch/four_plans_20261012.py --no-limit-up)
  - 5 天初犯要在公告前買進，所以另有「盤前提醒」模式(--pre-close，交易日 12:50)：以官方聽牌＋預測引擎
    列出「今天收盤再被注意就進處置、初犯、預估 5 天」的候選與觸發條件，並檢查持股距離停損價。
  - 黃金濾網（三項都符合才算通過）：
      1. 非生技醫療（官方產業別代碼 22）
      2. 非 KY（官方「外國企業註冊地國」有值）
      3. 處置開始前 5 個交易日平均成交量 ≥ 100 張
推播時機：盤前 12:50(5 天初犯候選與停損檢查)；處置公告當天盤後（明天就是處置首日），以及處置最後一天的前一天（提醒明天收盤出場）。
資料來源（全部官方公開資料，不需券商帳號）：
  - 處置公告：證交所 announcement/punish、櫃買 tpex_disposal_information
  - 產業別／KY：證交所 t187ap03_L、櫃買 mopsfin_t187ap03_O
  - 成交量：前 4 天讀 DB 專案行情 parquet（隔天早上才入庫），公告當天讀官方收盤行情 API
靜默原則：沒有明日進場、明日出場的初犯股就不推播。同一訊號只推一次（data/disposal_golden_signals.json）。
回測可靠度（2026-10-04 Claude 複核，見 scratch/claude_reverify_20261004_newonly.py）：
  新制初犯 N=57 勝率 77%、平均淨 +5.9%、最大虧損 -16.7%；黃金濾網是樣本內挑出來的條件，
  前後半段驗證方向不一致，推播只做標註，請以不加濾網的數字估計風險。
用法：
  uv run python scripts/automation/notify_disposal_golden.py --pre-close   # 盤前提醒(交易日 12:50)
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
SIGNAL_LOG = PROJ / "data" / "disposal_golden_signals.json"
REPORT_HTML = PROJ / "reports" / "latest_disposal_golden_push.html"
MIN_LOTS = 100
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


def tick(p):
    """台股升降單位。"""
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5


def limit_up_price(prev_close):
    """漲停價 = 前收 ×1.1 無條件捨去到升降單位。"""
    t = tick(prev_close * 1.1)
    return round(math.floor(prev_close * 1.1 / t + 1e-9) * t, 2)


def stop_price_of(entry):
    """停損價：收盤 ≤ 進場價×(1-8%) 即停損，收盤價落在升降單位上，所以向下取到升降單位。"""
    x = entry * (1 - STOP_PCT)
    t = tick(x)
    return round(math.floor(x / t + 1e-9) * t, 2)


def n_trading_days(s, e):
    return sum(1 for i in range((e - s).days + 1)
               if DateUtils.is_trading_day(datetime.combine(s + timedelta(days=i), datetime.min.time())))


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
    for r in fetch_json("twse_punish"):
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
        tpex_rows = fetch_json("tpex_disp")
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
    # 同一股票同一處置期間只留一筆
    uniq = {}
    for d in out:
        uniq.setdefault((d["code"], d["start"], d["end"]), d)
    res = list(uniq.values())
    for d in res:
        d["n_days"] = n_trading_days(d["start"], d["end"])
        # 方案 D 進場日：5 天初犯＝處置前一天(公告日)收盤買；其餘＝處置首日收盤買
        d["entry"] = prev_trading_day(d["start"]) if d["n_days"] == 5 else d["start"]
    return res


def load_company():
    m = {}
    for r in fetch_json("twse_company"):
        reg = str(r.get("外國企業註冊地國", "")).strip()
        m[str(r["公司代號"]).strip()] = dict(industry=str(r.get("產業別", "")).strip(),
                                         ky=reg not in ("", "－", "-"))
    for r in fetch_json("tpex_company"):
        reg = str(r.get("Registration", "")).strip()
        m[str(r["SecuritiesCompanyCode"]).strip()] = dict(industry=str(r.get("SecuritiesIndustryCode", "")).strip(),
                                                         ky=reg not in ("", "－", "-"))
    return m


def load_today_volume():
    """官方收盤行情：{code: (日期, 成交股數, 收盤價)}"""
    vol = {}
    for r in fetch_json("twse_quote"):
        try:
            vol[str(r["Code"]).strip()] = (roc_to_date(r["Date"]), float(str(r["TradeVolume"]).replace(",", "")),
                                           float(str(r["ClosingPrice"]).replace(",", "")))
        except Exception:
            pass
    for r in fetch_json("tpex_quote"):
        try:
            vol[str(r["SecuritiesCompanyCode"]).strip()] = (roc_to_date(r["Date"]),
                                                           float(str(r["TradingShares"]).replace(",", "")),
                                                           float(str(r["Close"]).replace(",", "")))
        except Exception:
            pass
    return vol


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


def load_corp_actions(codes, since, until):
    """官方除權息事件(DB corporate_actions_raw)：{code: [(日期, 除權息前收盤, 參考價)]}"""
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


def fmt_return(d, rr):
    if rr is None:
        return "　報酬：查無行情資料"
    s = f"　進場 {rr['entry']:g}（{rr['entry_date']:%m/%d} 收盤）→ {rr['cur']:g}（{rr['cur_date']:%m/%d}）"
    if rr["suspicious"]:
        s += (f"｜<b>原始價報酬 {rr['raw']:+.2f}%</b>\n"
              f"　⚠️ 期間除權息資料可疑（換算還原為 {rr['adj']:+.2f}%，不採用），請自行確認")
    else:
        s += f"｜<b>還原報酬 {rr['adj']:+.2f}%</b>"
        if rr["n_events"]:
            s += f"（期間有除權息，原始價 {rr['raw']:+.2f}%）"
    s += f"\n　🛡 停損價 {rr['stop_price']:g}（進場價 −{STOP_PCT*100:.0f}%，收盤 ≤ 此價當天收盤賣出）"
    if rr["stopped"]:
        st = rr["stopped"]
        s += f"\n　⛔ {st['date']:%m/%d} 收盤 {st['close']:g} 已觸發停損，還原報酬 {st['ret']:+.2f}%"
    return s


def fmt_stock(d, g, action, rr=None, show_filter=True, show_return=False, extra_lines=()):
    head = "✅ 符合黃金濾網" if g["golden"] else "❌ 不符合黃金濾網"
    lines = [f"<b>{html.escape(d['code'])} {html.escape(d['name'])}</b>（{d['market']}）{head}",
             f"　處置 {d['start']:%m/%d}～{d['end']:%m/%d}（{d['n_days']} 個交易日，{d['n_days']} 天初犯）→ {action}"]
    lines += list(extra_lines)
    if show_return:
        lines.append(fmt_return(d, rr))
    if show_filter:
        lines.append(f"　{html.escape(fmt_filter(g))}")
    return "\n".join(lines)


def avg_summary(items):
    """區塊標題用的摘要：平均還原報酬（排除資料可疑的），另列符合濾網者。"""
    vals = [rr["adj"] for _, g, rr in items if rr is not None and not rr["suspicious"]]
    gold = [rr["adj"] for _, g, rr in items if rr is not None and not rr["suspicious"] and g["golden"]]
    if not vals:
        return ""
    s = f"，平均 {sum(vals)/len(vals):+.2f}%"
    if gold and len(gold) != len(vals):
        s += f"（符合濾網 {len(gold)} 檔 {sum(gold)/len(gold):+.2f}%）"
    return s


def section(title, body_lines):
    """
    Telegram 可展開引用區塊：收合時只看得到第一行(標題＋摘要)，點「展開」才看明細。
    Telegram 不支援真正的整塊收合，這是最接近的作法。
    """
    body = "\n".join(body_lines).strip()
    return f"<blockquote expandable><b>{title}</b>\n\n{body}</blockquote>"


RULES_HTML = (
    "<blockquote expandable>📘 方案 D 規則與可靠度\n"
    "• 只做初犯。<b>聽牌條件進場＝5 天初犯：處置公告當天(處置前一天)收盤買</b>；<b>處置後進場＝7 天初犯：處置首日收盤買</b>；處置最後一天收盤賣\n"
    f"• <b>停損 {STOP_PCT*100:.0f}%</b>：收盤價 ≤ 進場價×{1-STOP_PCT:.2f}(還原後報酬 ≤ −{STOP_PCT*100:.0f}%)，當天收盤賣出；停損價已標在每檔下方\n"
    "• 進場日收盤漲停買不到，回測已排除；候選若盤中已漲停會標示\n"
    "• 5 天初犯要在公告前買進＝賭今天收盤會達標，沒達標就不會處置；盤前候選只是「可能」，不是確定\n"
    "• 回測(新制 8/10～10/08，排除漲停買不到，每筆等額)：D 方案 52 筆，每筆平均 +5.6%、勝率 75%、"
    "單筆最差 −10.3%、風報比 5.23；同期不加停損的初犯 5 天前一天買 −40.7% 為最差\n"
    "• 樣本只有 8 週且全程多頭；停損以收盤價假設，處置期間每 2 分鐘撮合、跌停鎖死時可能賣不掉\n"
    "• 報酬為還原報酬(含持有期間除權息調整)，未扣手續費與證交稅(來回約 0.38%)\n"
    "• 黃金濾網只做標註(樣本內挑出，前後半段驗證方向不一致)，不影響進出場\n"
    "• 處置期間每 2 分鐘撮合，大額委託需預收款券</blockquote>"
)


def build_message(today, next_td, entries, exits, holds, stops_today, n_repeat, cand_codes):
    lines = [f"🔔 <b>處置新制初犯策略 D｜{today:%Y/%m/%d} 盤後</b>", ""]

    # 0. 今日觸發停損
    if stops_today:
        body = []
        for d, g, rr in sorted(stops_today, key=lambda x: x[0]["code"]):
            st = rr["stopped"]
            body += [fmt_stock(d, g, "今日收盤已觸發停損", rr, show_filter=False, show_return=True),
                     f"　👉 若今天收盤沒賣，請盡快處理（回測假設當天收盤 {st['close']:g} 賣出）", ""]
        lines += [section(f"⛔ 今日觸發停損｜{len(stops_today)} 檔", body), ""]

    # 1. 持有中
    if holds:
        body = []
        for d, g, rr in sorted(holds, key=lambda x: (not x[1]["golden"], x[0]["end"], x[0]["code"])):
            body += [fmt_stock(d, g, f"持有至 {d['end']:%m/%d} 收盤賣出", rr, show_filter=False, show_return=True), ""]
        lines += [section(f"📊 持有中｜{len(holds)} 檔{avg_summary(holds)}", body), ""]
    else:
        lines += ["📊 <b>持有中｜無</b>", ""]

    # 2. 預計出場
    if exits:
        body = []
        for d, g, rr in sorted(exits, key=lambda x: (not x[1]["golden"], x[0]["code"])):
            body += [fmt_stock(d, g, f"{d['end']:%m/%d} 收盤賣出", rr, show_filter=False, show_return=True), ""]
        lines += [section(f"🔴 預計出場（明日 {next_td:%m/%d} 收盤賣）｜{len(exits)} 檔{avg_summary(exits)}", body), ""]
    else:
        lines += [f"🔴 <b>預計出場（明日 {next_td:%m/%d}）｜無</b>", ""]

    # 3. 進場：5 天初犯(進場日=今天收盤，已過) / 7 天初犯(明日首日收盤買)
    five = [e for e in entries if e[0]["n_days"] == 5]
    seven = [e for e in entries if e[0]["n_days"] != 5]
    if five:
        body = []
        for d, g, rr, lu in sorted(five, key=lambda x: (x[3], not x[1]["golden"], x[0]["code"])):
            hit = "✅ 盤前已列入候選" if d["code"] in cand_codes else "未列入盤前候選（沒預先買進，進場日已過）"
            if lu:
                price_line = f"　進場日 {today:%m/%d}(今日)收盤 {rr['entry']:g}：⚠️ <b>漲停鎖死，多半買不到（方案 D 不計入）</b>"
            elif rr:
                price_line = (f"　進場日 {today:%m/%d}(今日)收盤買進價 {rr['entry']:g}\n"
                              f"　🛡 停損價 {rr['stop_price']:g}（收盤 ≤ 此價當天收盤賣出）")
            else:
                price_line = "　進場價：查無今日收盤行情"
            body += [fmt_stock(d, g, f"{d['end']:%m/%d} 收盤賣出", show_filter=True, extra_lines=[price_line, f"　{hit}"]), ""]
        n_ok = sum(1 for e in five if not e[3])
        lines += [section(f"🟣 聽牌條件進場（5 天初犯，公告前一天收盤買）｜{len(five)} 檔（可買 {n_ok} 檔）", body), ""]
    if seven:
        body = []
        for d, g, rr, lu in sorted(seven, key=lambda x: (not x[1]["golden"], x[0]["code"])):
            body += [fmt_stock(d, g, f"{next_td:%m/%d} 收盤買進，{d['end']:%m/%d} 收盤賣出",
                               extra_lines=[f"　🛡 停損價 = 買進價 × {1-STOP_PCT:.2f}（明日收盤買進後再算）"]), ""]
        lines += [section(f"🟢 處置後進場（7 天初犯，處置首日 {next_td:%m/%d} 收盤買）｜{len(seven)} 檔", body), ""]
    if not five and not seven:
        extra = f"（累犯 {n_repeat} 檔不做）" if n_repeat else ""
        lines += [f"🟣🟢 <b>新進場｜無</b>（聽牌條件進場、處置後進場都沒有）{extra}", ""]
    elif n_repeat:
        lines += [f"（另有 {n_repeat} 檔累犯明日開始處置，本策略不做）", ""]

    lines.append(RULES_HTML)
    return "\n".join(x for x in lines if x is not None).strip()


# ---------------------------------------------------------------- 盤前提醒
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
    items = [f"{'tse' if m == '上市' else 'otc'}_{c}.tw" for c, m in code_markets]
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
                out[x.get("c")] = dict(price=last if last else prev, traded=bool(last), prev=prev,
                                       up=f("u"), down=f("w"))
        except Exception as e:
            print(f"[WARN] MIS 查價失敗：{e}")
    return out


def fmt_candidate(c, mis, prev_close_hint=None):
    q = mis.get(c["code"], {})
    price, up = q.get("price"), q.get("up")
    locked = bool(price and up and price >= up - 1e-6)
    px_txt = "現價查無" if not price else f"現價 {price:g}（漲停價 {up:g}）" if up else f"現價 {price:g}"
    if locked:
        px_txt += "｜⚠️ <b>已漲停，恐買不到</b>"
    p = c["progress"]
    prog = "、".join(f"{lbl} {p[k]['current']}/{p[k]['target']}" for lbl, k in
                    (("連3日第1款", "rule1"), ("連5日1-8款", "rule2"), ("10日內6次", "rule3"), ("30日內12次", "rule4"))
                    if p.get(k))
    cond = [f"　　{html.escape(x)}" for x in c["conditions"]] or ["　　（今日再被注意即進處置，無單一價位條件）"]
    return "\n".join([
        f"<b>{html.escape(c['code'])} {html.escape(c['name'])}</b>（{html.escape(str(c['market']))}）{px_txt}",
        f"　昨收 {q.get('prev') or c['close']}｜{c['enter_freq']}｜預估處置 {c['days']} 天",
        f"　聽牌進度：{prog}",
        "　進處置條件（今日收盤達下列任一）：", *cond, ""]), locked


def build_pre_message(today, anchor, cands5, cands7, n_rep, warn_holds, exits_today, mis, hit_note):
    lines = [f"⏰ <b>處置新制初犯策略 D｜{today:%Y/%m/%d} 盤前提醒</b>", f"（預測以 {anchor:%m/%d} 收盤為準，現價取自即時行情）", ""]
    if cands5:
        body, n_locked = [], 0
        for c in cands5:
            txt, lk = fmt_candidate(c, mis)
            body.append(txt)
            n_locked += lk
        body.append("👉 要不要買：今天收盤前買進(收盤價)，若今晚公告進處置 → 明天起處置 5 天；沒達標就不會處置。\n"
                    f"　買進後的停損價 = 買進價 × {1 - STOP_PCT:.2f}（收盤 ≤ 此價當天收盤賣出）。{hit_note}")
        lines += [section(f"🟣 聽牌條件進場（5 天初犯）｜今天收盤前買進｜{len(cands5)} 檔" + (f"（已漲停 {n_locked} 檔）" if n_locked else ""), body), ""]
    else:
        lines += ["🟣 <b>聽牌條件進場（5 天初犯）｜今天沒有候選</b>", ""]
    if cands7:
        body = [fmt_candidate(c, mis)[0] for c in cands7]
        body.append("說明：這些預估處置 7 天(計算期間內有第十三款當沖占比過高)，方案 D 不提早買，公告後處置首日收盤買。")
        lines += [section(f"🟢 處置後進場（預估 7 天初犯）｜不預買，公告後處置首日收盤買｜{len(cands7)} 檔", body), ""]
    if n_rep:
        lines += [f"（另有 {n_rep} 檔聽牌股若進處置會是累犯，方案 D 不做）", ""]

    if warn_holds or exits_today:
        def gap(x):  # 現價離停損價的距離(負 = 已跌破)，沒有現價排最後
            price = x[3].get("price") if x[3] else None
            return (price / x[2]["stop_price"] - 1) if price else 9
        body, n_danger = [], 0
        for d, g, rr, q in sorted(warn_holds, key=gap):
            price = q.get("price") if q else None
            tag = ""
            if price and price <= rr["stop_price"]:
                tag, n_danger = " ⚠️ <b>已在停損價之下，收盤若仍 ≤ 停損價就收盤賣出</b>", n_danger + 1
            elif price and price <= rr["stop_price"] * 1.03:
                tag, n_danger = " ⚠️ 距停損價不到 3%", n_danger + 1
            body.append(f"{html.escape(d['code'])} {html.escape(d['name'])}｜進場 {rr['entry']:g}｜🛡 停損價 {rr['stop_price']:g}｜"
                        + (f"現價 {price:g}（{(price / rr['entry'] - 1) * 100:+.1f}%）" if price else "現價查無") + tag)
        for d, g, rr, q in exits_today:
            body.append(f"<b>{html.escape(d['code'])} {html.escape(d['name'])}</b>：處置最後一天，🔴 今天收盤賣出")
        title = f"🛡 持有部位停損檢查｜{len(warn_holds)} 檔" + (f"，⚠️ 需留意 {n_danger} 檔" if n_danger else "，都離停損價夠遠")             + (f"｜今日出場 {len(exits_today)} 檔" if exits_today else "")
        lines += [section(title, body), ""]
    lines.append(RULES_HTML)
    return "\n".join(lines).strip()


def split_message(msg, limit=4000):
    """Telegram 單則上限 4096 字：太長就以區塊為單位拆成多則（區塊之間以空行＋<blockquote 開頭分隔）。"""
    if len(msg) <= limit:
        return [msg]
    parts = msg.split("\n\n<blockquote")
    blocks = [parts[0]] + ["<blockquote" + p for p in parts[1:]]
    chunks, cur = [], ""
    for b in blocks:
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


def push(msg):
    REPORT_HTML.parent.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(msg, encoding="utf-8")
    if send_telegram is None:
        print("[ERROR] 找不到 send_telegram 模組，未推播。")
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
    today_dt = datetime.combine(today, datetime.min.time())
    next_td = DateUtils.get_next_trading_day(today_dt).date()
    print(f"[{datetime.now()}] 今天 {today}，下一個交易日 {next_td}")

    disposals = load_disposals(today)
    first = [d for d in disposals if d["offense"] == "初犯"]
    entries_raw = [d for d in first if d["start"] == next_td]
    exits_raw = [d for d in first if d["end"] == next_td and d["entry"] <= today]
    holds_raw = [d for d in first if d["entry"] <= today and d["end"] > next_td and d["start"] != next_td]
    n_repeat = sum(1 for d in disposals if d["start"] == next_td and d["offense"] == "累犯")
    print(f"  官方處置公告(個股) {len(disposals)} 筆；明日進場初犯 {len(entries_raw)}、明日出場初犯 {len(exits_raw)}、"
          f"持有中 {len(holds_raw)}、明日累犯 {n_repeat}")

    log = load_log()
    meta = log.setdefault("_meta", {})
    key = lambda d: f"{d['code']}_{d['start']:%Y%m%d}"
    if not args.dry_run and not args.force_send:
        entries_raw = [d for d in entries_raw if not log.get(key(d), {}).get("entry_pushed_at")]
        exits_raw = [d for d in exits_raw if not log.get(key(d), {}).get("exit_pushed_at")]
        new_signal = bool(entries_raw or exits_raw)
        daily_update = bool(holds_raw) and meta.get("last_push_date") != str(today)
        if not new_signal and not daily_update:
            print("  沒有新的進出場訊號，今天的持股報酬也已推播過，靜默結束。")
            return

    company = load_company()
    quotes = load_today_volume()
    targets = entries_raw + exits_raw + holds_raw
    daily, corp = load_prices_for(targets, today)

    entries = []
    for d in entries_raw:
        g = golden_check(d, company, daily, quotes)
        if d["n_days"] == 5:
            rr = calc_return(d, today, quotes, daily, corp)
            entries.append((d, g, rr, is_limit_up(d["code"], d["entry"], quotes, daily)))
        else:
            entries.append((d, g, None, False))
    exits_all = [(d, golden_check(d, company, daily, quotes), calc_return(d, today, quotes, daily, corp)) for d in exits_raw]
    holds_all = [(d, golden_check(d, company, daily, quotes), calc_return(d, today, quotes, daily, corp)) for d in holds_raw]
    exits, exit_stops = split_stops(exits_all, today)
    holds, hold_stops = split_stops(holds_all, today)
    stops_today = [s for s in exit_stops + hold_stops if not log.get(key(s[0]), {}).get("stop_pushed_at")]
    if not args.dry_run and not args.force_send and stops_today and not (entries or exits or holds):
        pass
    cand_codes = set(log.get("_cand", {}).get(str(today), []))

    msg = build_message(today, next_td, entries, exits, holds, stops_today, n_repeat, cand_codes)
    print("\n" + msg + "\n")
    if args.dry_run:
        print("[dry-run] 未推播、未寫紀錄。")
        return
    push(msg)

    now = datetime.now().isoformat(timespec="seconds")
    meta["last_push_date"] = str(today)
    for d, g, rr, lu in entries:
        rec = log.setdefault(key(d), {})
        rec.update(code=d["code"], name=d["name"], market=d["market"], announce=str(d["announce"]),
                   start=str(d["start"]), end=str(d["end"]), n_days=d["n_days"], entry_date=str(d["entry"]),
                   entry_price=None if rr is None else rr["entry"], entry_limit_up=lu,
                   was_candidate=d["code"] in cand_codes, golden=g["golden"], is_bio=g["is_bio"],
                   is_ky=g["is_ky"], vol5_lots=g["vol5_lots"], vol_days=g["vol_days"], entry_pushed_at=now)
    for d, g, rr in exits:
        rec = log.setdefault(key(d), {})
        rec.update(code=d["code"], name=d["name"], market=d["market"], start=str(d["start"]),
                   end=str(d["end"]), golden=rec.get("golden", g["golden"]), exit_pushed_at=now,
                   ret_before_exit=None if rr is None else round(rr["adj"], 2))
    for d, g, rr in stops_today:
        rec = log.setdefault(key(d), {})
        rec.update(code=d["code"], name=d["name"], stop_pushed_at=now, stop_date=str(rr["stopped"]["date"]),
                   stop_ret=round(rr["stopped"]["ret"], 2))
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
    anchor = prev_trading_day(today)
    print(f"[{datetime.now()}] 盤前提醒：今天 {today}，預測錨點(最近收盤日) {anchor}")
    if not args.date and not args.dry_run and not args.force_send and datetime.now().time() > datetime.strptime("13:20", "%H:%M").time():
        print("  已過 13:20，盤前提醒沒有意義(排程補跑)，結束。")
        return
    log = load_log()
    meta = log.setdefault("_meta", {})
    if not args.dry_run and not args.force_send and meta.get("pre_close_date") == str(today):
        print("  今天的盤前提醒已推播過，結束。")
        return

    disposals = load_disposals(today)
    first = [d for d in disposals if d["offense"] == "初犯"]
    busy = {d["code"] for d in disposals if d["end"] >= today}          # 已在處置或已公告處置的不是候選
    out = Path(PROJ / "temp" / f"preclose_{today:%Y%m%d}.json")
    data = run_worker(anchor, out)
    cands = [c for c in data["candidates"] if c["code"] not in busy]
    cands5 = [c for c in cands if c["offense"] == "初犯" and c["days"] == 5]
    cands7 = [c for c in cands if c["offense"] == "初犯" and c["days"] != 5]
    n_rep = sum(1 for c in cands if c["offense"] == "累犯")

    holds_raw = [d for d in first if d["entry"] < today <= d["end"]]
    quotes = load_today_volume()
    daily, corp = load_prices_for(holds_raw, anchor)
    company = load_company()
    warn, exits_today = [], []
    for d in holds_raw:
        rr = calc_return(d, anchor, quotes, daily, corp)
        if rr is None or rr["stopped"]:
            continue                                    # 已停損出場的不再列
        g = golden_check(d, company, daily, quotes)
        (exits_today if d["end"] == today else warn).append((d, g, rr, None))
    mis = fetch_mis([(c["code"], c["market"]) for c in cands5 + cands7] +
                    [(d["code"], d["market"]) for d, _, _, _ in warn + exits_today])
    warn = [(d, g, rr, mis.get(d["code"])) for d, g, rr, _ in warn]
    exits_today = [(d, g, rr, mis.get(d["code"])) for d, g, rr, _ in exits_today]

    hit_note = HIT_RATE_NOTE
    if not (cands5 or cands7 or warn or exits_today):
        print("  今天沒有候選也沒有持股要提醒，靜默結束。")
        return
    msg = build_pre_message(today, anchor, cands5, cands7, n_rep, warn, exits_today, mis, hit_note)
    print("\n" + msg + "\n")
    if args.dry_run:
        print("[dry-run] 未推播、未寫紀錄。")
        return
    push(msg)
    if args.force_send:
        print("[force-send] 手動測試推播：不寫入「今天已推播」與候選紀錄，正式排程照常執行。")
        return
    meta["pre_close_date"] = str(today)
    log.setdefault("_cand", {})[str(today)] = [c["code"] for c in cands5]
    save_log(log)


HIT_RATE_NOTE = ("\n命中率樣本不足：目前唯一沒有資料外洩的回測日(10/02)，官方聽牌「非處置」8 檔中 6 檔隔天公告處置"
                 "(沒中的 2 檔是累犯)；之後每天的候選與結果會記在 disposal_golden_signals.json，盤後推播會標示有沒有列入候選。")


def main():
    ap = argparse.ArgumentParser(description="處置新制初犯策略 D Telegram 推播")
    ap.add_argument("--dry-run", action="store_true", help="只印出內容，不推播、不寫紀錄")
    ap.add_argument("--force-send", action="store_true", help="沒有訊號也推播（測試用）")
    ap.add_argument("--pre-close", action="store_true", help="盤前提醒模式(交易日 12:50 左右執行)：5 天初犯聽牌候選與停損檢查")
    ap.add_argument("--date", help="以這天當作「今天」(YYYYMMDD)，預設今天")
    args = ap.parse_args()

    today = datetime.strptime(args.date, "%Y%m%d").date() if args.date else date.today()
    if not DateUtils.is_trading_day(datetime.combine(today, datetime.min.time())) and not args.force_send:
        print(f"[{datetime.now()}] {today} 不是交易日，結束。")
        return
    (main_pre if args.pre_close else main_post)(args, today)


if __name__ == "__main__":
    main()
