"""
處置新制「初犯」策略 + 黃金濾網：每日盤後 Telegram 推播
=======================================================================
策略（2026-08-10 處置新制後）：
  - 只做「初犯」（上市：處置措施「第一次處置」；上櫃：條件文字沒有「最近30個營業日內曾發布處置」）
  - 進場：處置第一天收盤買進；出場：處置最後一天收盤賣出
  - 黃金濾網（三項都符合才算通過）：
      1. 非生技醫療（官方產業別代碼 22）
      2. 非 KY（官方「外國企業註冊地國」有值）
      3. 處置開始前 5 個交易日平均成交量 ≥ 100 張
推播時機：處置公告當天盤後（明天就是處置首日），以及處置最後一天的前一天（提醒明天收盤出場）。
資料來源（全部官方公開資料，不需券商帳號）：
  - 處置公告：證交所 announcement/punish、櫃買 tpex_disposal_information
  - 產業別／KY：證交所 t187ap03_L、櫃買 mopsfin_t187ap03_O
  - 成交量：前 4 天讀 DB 專案行情 parquet（隔天早上才入庫），公告當天讀官方收盤行情 API
靜默原則：沒有明日進場、明日出場的初犯股就不推播。同一訊號只推一次（data/disposal_golden_signals.json）。
回測可靠度（2026-10-04 Claude 複核，見 scratch/claude_reverify_20261004_newonly.py）：
  新制初犯 N=57 勝率 77%、平均淨 +5.9%、最大虧損 -16.7%；黃金濾網是樣本內挑出來的條件，
  前後半段驗證方向不一致，推播只做標註，請以不加濾網的數字估計風險。
用法：
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


def load_disposals():
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
    for r in fetch_json("tpex_disp"):
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
    return list(uniq.values())


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
    """官方收盤行情：{code: (日期, 成交股數)}"""
    vol = {}
    for r in fetch_json("twse_quote"):
        try:
            vol[str(r["Code"]).strip()] = (roc_to_date(r["Date"]), float(str(r["TradeVolume"]).replace(",", "")))
        except Exception:
            pass
    for r in fetch_json("tpex_quote"):
        try:
            vol[str(r["SecuritiesCompanyCode"]).strip()] = (roc_to_date(r["Date"]),
                                                           float(str(r["TradingShares"]).replace(",", "")))
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


def load_parquet_volume(codes, days):
    """DB 專案行情 parquet：{(code, date): 成交股數}"""
    out = {}
    months = {(d.year, d.month) for d in days}
    for mk in ["TWSE", "TPEX"]:
        for y, m in months:
            f = PRICE_ROOT / f"market={mk}" / f"year={y}" / f"month={m}" / "data.parquet"
            if not f.exists():
                continue
            t = pq.ParquetFile(f).read(columns=["date", "symbol", "volume"]).to_pandas()
            t["symbol"] = t["symbol"].astype(str).str.strip()
            t = t[t["symbol"].isin(codes)]
            for r in t.itertuples():
                out[(r.symbol, r.date if isinstance(r.date, date) else r.date.date())] = float(r.volume)
    return out


def golden_check(d, company, pq_vol, today_vol):
    comp = company.get(d["code"], {})
    days = prior_trading_days(d["start"], 5)
    vols = []
    for day in days:
        v = pq_vol.get((d["code"], day))
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


def fmt_stock(d, g, action):
    head = "✅ 符合黃金濾網" if g["golden"] else "❌ 不符合黃金濾網"
    n_days = sum(1 for i in range((d["end"] - d["start"]).days + 1)
                 if DateUtils.is_trading_day(datetime.combine(d["start"] + timedelta(days=i), datetime.min.time())))
    return (f"<b>{html.escape(d['code'])} {html.escape(d['name'])}</b>（{d['market']}）{head}\n"
            f"　處置 {d['start']:%m/%d}～{d['end']:%m/%d}（{n_days} 個交易日）→ {action}\n"
            f"　{html.escape(fmt_filter(g))}")


def build_message(today, next_td, entries, exits, n_repeat):
    lines = [f"🔔 <b>處置新制初犯策略｜{today:%Y/%m/%d} 盤後</b>", ""]
    if entries:
        lines.append(f"🟢 <b>明日 {next_td:%m/%d} 處置首日 → 收盤買進</b>")
        for d, g in sorted(entries, key=lambda x: (not x[1]["golden"], x[0]["code"])):
            lines += [fmt_stock(d, g, f"{next_td:%m/%d} 收盤買進，{d['end']:%m/%d} 收盤賣出"), ""]
    if exits:
        lines.append(f"🔴 <b>明日 {next_td:%m/%d} 處置最後一天 → 收盤賣出</b>")
        for d, g in sorted(exits, key=lambda x: (not x[1]["golden"], x[0]["code"])):
            lines += [fmt_stock(d, g, f"{d['end']:%m/%d} 收盤賣出"), ""]
    if n_repeat:
        lines.append(f"（明日另有 {n_repeat} 檔累犯開始處置，本策略不做）")
    lines += [
        "",
        "<blockquote expandable>📘 規則與可靠度\n"
        "• 只做初犯；處置第一天收盤買、最後一天收盤賣\n"
        "• 黃金濾網：非生技、非 KY、前 5 日均量 ≥ 100 張\n"
        "• 新制初犯回測（8/10～10/02，57 筆）：勝率 77%、平均淨 +5.9%、最大虧損 -16.7%\n"
        "• 濾網是看著同一批虧損股挑出來的，前後半段驗證方向不一致；"
        "符合濾網不代表比較安全，部位請以單筆 -16% 估風險\n"
        "• 處置期間每 2 分鐘撮合，大額委託需預收款券</blockquote>",
    ]
    return "\n".join(lines).strip()


def load_log():
    if SIGNAL_LOG.exists():
        return json.load(open(SIGNAL_LOG, encoding="utf-8"))
    return {}


def save_log(log):
    SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIGNAL_LOG.with_suffix(".tmp")
    json.dump(log, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, SIGNAL_LOG)


def main():
    ap = argparse.ArgumentParser(description="處置新制初犯＋黃金濾網 Telegram 推播")
    ap.add_argument("--dry-run", action="store_true", help="只印出內容，不推播、不寫紀錄")
    ap.add_argument("--force-send", action="store_true", help="沒有訊號也推播（測試用）")
    ap.add_argument("--date", help="以這天當作「今天」(YYYYMMDD)，預設今天")
    args = ap.parse_args()

    today = datetime.strptime(args.date, "%Y%m%d").date() if args.date else date.today()
    today_dt = datetime.combine(today, datetime.min.time())
    if not DateUtils.is_trading_day(today_dt) and not args.force_send:
        print(f"[{datetime.now()}] {today} 不是交易日，結束。")
        return
    next_td = DateUtils.get_next_trading_day(today_dt).date()
    print(f"[{datetime.now()}] 今天 {today}，下一個交易日 {next_td}")

    disposals = load_disposals()
    entries_raw = [d for d in disposals if d["start"] == next_td and d["offense"] == "初犯"]
    exits_raw = [d for d in disposals if d["end"] == next_td and d["offense"] == "初犯" and d["start"] <= today]
    n_repeat = sum(1 for d in disposals if d["start"] == next_td and d["offense"] == "累犯")
    print(f"  官方處置公告(個股) {len(disposals)} 筆；明日進場初犯 {len(entries_raw)}、明日出場初犯 {len(exits_raw)}、明日累犯 {n_repeat}")

    log = load_log()
    key = lambda d: f"{d['code']}_{d['start']:%Y%m%d}"
    if not args.dry_run and not args.force_send:
        entries_raw = [d for d in entries_raw if not log.get(key(d), {}).get("entry_pushed_at")]
        exits_raw = [d for d in exits_raw if not log.get(key(d), {}).get("exit_pushed_at")]
    if not entries_raw and not exits_raw and not args.force_send and not args.dry_run:
        print("  沒有新的進出場訊號（或已推播過），靜默結束。")
        return

    company = load_company()
    today_vol = load_today_volume() if entries_raw else {}
    targets = entries_raw + exits_raw
    all_days = {day for d in targets for day in prior_trading_days(d["start"], 5)}
    pq_vol = load_parquet_volume({d["code"] for d in targets}, all_days) if targets else {}
    entries = [(d, golden_check(d, company, pq_vol, today_vol)) for d in entries_raw]
    exits = [(d, golden_check(d, company, pq_vol, today_vol)) for d in exits_raw]

    msg = build_message(today, next_td, entries, exits, n_repeat)
    print("\n" + msg + "\n")
    if args.dry_run:
        print("[dry-run] 未推播、未寫紀錄。")
        return

    REPORT_HTML.parent.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(msg, encoding="utf-8")
    if send_telegram is None:
        print("[ERROR] 找不到 send_telegram 模組，未推播。")
        sys.exit(1)
    res = send_telegram(msg, parse_mode="HTML")
    # send_telegram 只在 Telegram 回 ok 時才回傳(回傳值就是 result 本身)，失敗會丟例外
    print(f"✅ 已推播，message_id={res.get('message_id')}")

    now = datetime.now().isoformat(timespec="seconds")
    for d, g in entries:
        rec = log.setdefault(key(d), {})
        rec.update(code=d["code"], name=d["name"], market=d["market"], announce=str(d["announce"]),
                   start=str(d["start"]), end=str(d["end"]), golden=g["golden"], is_bio=g["is_bio"],
                   is_ky=g["is_ky"], vol5_lots=g["vol5_lots"], vol_days=g["vol_days"], entry_pushed_at=now)
    for d, g in exits:
        rec = log.setdefault(key(d), {})
        rec.update(code=d["code"], name=d["name"], market=d["market"], start=str(d["start"]),
                   end=str(d["end"]), golden=rec.get("golden", g["golden"]), exit_pushed_at=now)
    save_log(log)


if __name__ == "__main__":
    main()
