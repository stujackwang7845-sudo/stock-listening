"""
策略回測(2026-11-01 使用者要求)：把 Telegram 推播的各策略套到新制(2026-08-10 起)全部處置，
列出每筆進出場、報酬，算勝率、MDD、年化、Sharpe、同時持有數等。

規則直接呼叫推播程式 notify_disposal_golden 的函式(enrich / apply_chain / long_bought / calc_return /
short_status / nondisp_vol5)，推播改規則，這裡跟著變，不會兩套對不上。
只讀本機：data/disposal_history.db、DB 專案 parquet(行情、除權息)；不連網、不寫檔。

跟實盤的差異(畫面上也會寫)：
  - 5 天初犯預買、7 天累犯預空都要在公告前預測；這裡假設預測全中，沒中「隔天收盤賣/補」的單不在裡面。
  - 7 天累犯「處置前一天收盤空」10/22 才上線，這裡對整段新制都用現行規則。
  - 放空未扣融券手續費/借券費；放空報酬不做除權息還原(與推播一致)。
成交：收盤漲停買不到 → 隔天開盤買；收盤跌停賣不掉 → 隔天開盤賣(多單出場、融券放空都算賣；空單回補算買)。
"""
import re
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from core.runtime import get_paths
from core.utils import DateUtils

RULE_CHANGE_DATE = date(2026, 8, 10)
FEE, TAX = 0.000399, 0.003          # 手續費 0.1425%×2.8 折、證交稅 0.3%(與 scratch 回測一致)
LOT_CAPITAL = 200_000               # 每筆投入金額(損益金額換算用)

STRATEGIES = [
    ("L5", "5 天初犯做多", "多",
     "處置前一天(公告日)收盤買 → 處置最後一天收盤賣；接力處置(期間內又公告 5 天處置)續抱到那次的出關日收盤賣。"
     "停損：收盤 ≤ 進場價×0.92 當天收盤賣。不買：第一款且進場日 20MA 乖離 > 35%；第一款原文是跌幅；"
     "非處置期間 5 日均量 < 500 張。"),
    ("L7", "7 天初犯做多", "多",
     "處置首日收盤買 → 處置結束日前一天收盤賣(同一收盤改放空)。停損與不買條件同 5 天初犯(不看 500 張均量)。"),
    ("S7F", "7 天初犯放空（策略 A）", "空",
     "處置結束日前一天收盤空 → 出關日+2(出關後第 2 個交易日)收盤回補；無停損。放空日收盤漲停不空。"),
    ("S7R", "7 天累犯放空", "空",
     "處置前一天(公告日)收盤空 → 出關日+1 收盤回補；無停損。放空日收盤漲停不空；非處置期間 5 日均量 < 500 張不做。"),
]
COMBOS = [("LONG", "做多合計", ("L5", "L7")), ("SHORT", "放空合計", ("S7F", "S7R")),
          ("ALL", "全部策略", ("L5", "L7", "S7F", "S7R"))]


def _notify():
    """推播程式(規則的唯一來源)。"""
    auto = str(Path(__file__).resolve().parents[1] / "automation")
    if auto not in sys.path:
        sys.path.insert(0, auto)
    import notify_disposal_golden as n
    return n


def _d(s, default=None):
    """各種日期寫法(2026/09/08、民國 113-08-19…)轉 date；解析不了回傳 default。"""
    iso = DateUtils.to_iso_date_str(str(s or ""))
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def _trading(day):
    return DateUtils.is_trading_day(datetime.combine(day, datetime.min.time()))


def _back_trading(day, k):
    cur = day
    while k > 0:
        cur -= timedelta(days=1)
        if _trading(cur):
            k -= 1
    return cur


def load_disposals(db_path, since=RULE_CHANGE_DATE, n=None):
    """
    新制處置(處置開始日 ≥ since)，格式同推播的 disposals_from_db。
    初犯/累犯：原因寫「30 個營業日內曾發布處置」，或開始日往前 30 個營業日內公告過別次處置 → 累犯
    (上櫃有些紀錄原因欄只有「處置」兩字，例 6217 08/11，只看原因會誤判成初犯)。
    """
    n = n or _notify()
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT code, name, source, announce_date, period_start, period_end, reason "
                        "FROM disposal_records WHERE period_start IS NOT NULL AND period_end IS NOT NULL").fetchall()
    conn.close()
    anns = {}
    for code, _, _, ann, ps, _, _ in rows:
        s = _d(ps)
        if s:
            anns.setdefault(str(code).strip(), []).append((_d(ann, s), s))
    uniq = {}
    for code, name, src, ann, ps, pe, reason in rows:
        code = str(code).strip()
        if not n.is_stock(code):
            continue
        s, e = _d(ps), _d(pe)
        if not s or not e or s < since:
            continue
        a = _d(ann, s)
        lo = _back_trading(s, 30)
        prior = any(x_s < s and lo <= x_a < a for x_a, x_s in anns.get(code, []))
        again = bool(re.search(r"(三十|30)個營業日內曾發布處置", reason or "")) or prior
        uniq.setdefault((code, s, e), dict(code=code, name=str(name or "").strip(), market=src, announce=a,
                                           start=s, end=e, offense="累犯" if again else "初犯",
                                           reason=str(reason or "").strip()))
    out = []
    for d in uniq.values():
        n.postpone_for_closures(d)
        d["n_days"] = n.n_trading_days(d["start"], d["end"])
        d["entry"] = n.prev_trading_day(d["start"]) if d["n_days"] == 5 else d["start"]
    for d in uniq.values():
        if d["n_days"] in (5, 7):
            n.enrich(d)
            if d["n_days"] == 7 and d["offense"] == "累犯" and not d.get("early_short"):
                # 回測對整段新制都用現行規則：7 天累犯處置前一天收盤空(推播 10/22 才上線)
                d["short_entry"] = n.prev_trading_day(d["start"])
                d["early_short"] = True
            if d["n_days"] == 7 and d["offense"] == "初犯" and d.get("cover_lbl") != "出關日+2":
                n.extend_first_cover(d)   # 回測整段新制都用現行規則：7 天初犯回補在出關日+2(推播 11/03 才上線)
            out.append(d)
    return n.apply_chain(out)


def _days_between(a, b):
    """a(不含)～b(含)的交易日。"""
    out, cur = [], a
    while cur < b:
        cur += timedelta(days=1)
        if _trading(cur):
            out.append(cur)
    return out


def _net_long(g):
    return (1 + g) * (1 - FEE - TAX) / (1 + FEE) - 1


def _net_short(g):
    # 放空價 pin、回補價 pout=pin×(1-g)：(pin×(1-FEE-TAX) - pout×(1+FEE)) / pin
    return (1 - FEE - TAX) - (1 - g) * (1 + FEE)


def _base(d, key):
    return dict(key=key, code=d["code"], name=d["name"], market=d["market"], start=d["start"], end=d["end"],
                n_days=d["n_days"], offense=d["offense"], announce=d["announce"], chain=bool(d.get("chain")))


def _long_trade(n, d, key, as_of, daily, corp, clause_db):
    """多單：進出場、停損、收盤漲停/跌停改隔天開盤，全部由推播的 long_bought / calc_return 決定。"""
    ok, why = n.long_bought(d, clause_db, {}, daily, as_of)
    if ok is None:
        return None, None
    if not ok:
        return None, dict(_base(d, key), entry=d["entry"], why=why)
    rr = n.calc_return(d, as_of, {}, daily, corp, since=date.min)      # 回測整段都套「隔天開盤成交」
    if rr is None:
        return None, dict(_base(d, key), entry=d["entry"], why="查無進場日行情")
    if rr["status"] == "pending_entry":
        return None, None                      # 收盤漲停、明天開盤才買：還沒有部位
    gross = (rr["raw"] if rr["suspicious"] else rr["adj"]) / 100
    closed = rr["status"] == "closed"
    if rr["stopped"]:
        why = "停損（收盤 ≤ 進場價×0.92）"
    else:
        why = ("接力處置：續抱到第二次處置出關日" if d.get("chain")
               else "處置最後一天" if d["n_days"] == 5 else "處置結束日前一天")
    plan = rr["exit_day"] or d["long_exit"]
    if closed and rr["exit_mode"] == "open":
        why += "；當天收盤跌停賣不掉，隔天開盤賣"
    net = _net_long(gross)
    path = dict(rr["path"])
    if closed:
        path[rr["exit_date"]] = net
    return dict(_base(d, key), side="多", entry=rr["entry_date"], entry_mode=rr["entry_mode"], entry_px=rr["entry"],
                exit=rr["exit_date"], exit_mode=rr["exit_mode"], plan_exit=plan, exit_px=rr["cur"],
                last_date=rr["cur_date"], gross=gross, ret=net, closed=closed,
                exit_why=why if closed else (f"持有中（{plan:%m/%d} 收盤跌停，隔天開盤賣）" if rr["status"] == "pending_exit"
                                             else f"持有中（預計 {plan:%m/%d} {why}）"),
                path=path, suspicious=rr["suspicious"]), None


def _short_trade(n, d, key, as_of, daily):
    se = d["short_entry"]
    if se > as_of:
        return None, None
    low = n.low_volume_reason(d)
    if low:
        return None, dict(_base(d, key), entry=se, why=low)
    sr = n.short_status(d, as_of, {}, daily, since=date.min)
    if sr is None:
        return None, dict(_base(d, key), entry=se, why="查無放空日行情")
    if sr["blocked"]:
        return None, dict(_base(d, key), entry=se, why="放空日收盤漲停不空")
    if sr["status"] == "pending_entry":
        return None, None
    gross = sr["ret"] / 100
    closed = sr["status"] == "closed"
    net = _net_short(gross)
    path = dict(sr["path"])
    if closed:
        path[sr["exit_date"]] = net
    cover = d["short_cover"]
    why = n.cover_lbl(d) + "收盤回補" + ("；當天收盤漲停買不回，隔天開盤回補" if sr.get("exit_mode") == "open" else "")
    return dict(_base(d, key), side="空", entry=sr["entry_date"], entry_mode=sr["entry_mode"], entry_px=sr["entry"],
                exit=sr.get("exit_date"), exit_mode=sr.get("exit_mode"), plan_exit=cover, exit_px=sr["cur"],
                last_date=sr["cur_date"], gross=gross, ret=net, closed=closed,
                exit_why=why if closed else (f"持有中（{cover:%m/%d} 收盤漲停，隔天開盤回補）" if sr["status"] == "pending_exit"
                                             else f"持有中（預計 {cover:%m/%d} {why}）"),
                path=path, suspicious=False), None


def nav_stats(dates, nav):
    """一條淨值曲線的績效：總報酬、年化、年化波動、Sharpe、Sortino、MDD、Calmar(年化 / |MDD|)。"""
    peak, mdd = nav[0], 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    dr = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    sd = statistics.stdev(dr) if len(dr) > 1 else 0.0
    mean = statistics.mean(dr) if dr else 0.0
    down = (sum(min(r, 0.0) ** 2 for r in dr) / len(dr)) ** 0.5 if dr else 0.0
    total = nav[-1] / nav[0] - 1
    days = max((dates[-1] - dates[0]).days, 1)
    ann = (1 + total) ** (365 / days) - 1 if total > -1 else -1.0
    return dict(total=total, ann=ann, vol=sd * 252 ** 0.5, sharpe=mean / sd * 252 ** 0.5 if sd else 0.0,
                sortino=mean / down * 252 ** 0.5 if down else None, mdd=mdd, calmar=ann / -mdd if mdd < 0 else None)


def benchmark(dates, code="0050"):
    """同期買進持有(還原價)的淨值；缺價的日子沿用前一天。"""
    from core import adjusted_prices
    out, last = [], None
    for d in dates:
        p = adjusted_prices.adj_close(code, d.isoformat()) or last
        last = p
        out.append(p)
    first = next((p for p in out if p), None)
    if not first:
        return None
    return [(p or first) / first for p in out]


def metrics(trades, calendar):
    """
    單筆統計＋等額組合(每筆資金 = 1/最大同時持有數，逐日收盤估值)。
    不複利：權益 = 1 + Σ 每筆資金×該筆至今報酬；複利：每天的損益(占原始本金)用當時淨值滾算。
    """
    if not trades:
        return dict(n=0)
    rets = [t["ret"] for t in trades]
    closed = [t for t in trades if t["closed"]]
    wins = [r for r in rets if r > 0]
    loss = [r for r in rets if r <= 0]
    hold = [len(t["path"]) for t in closed]

    def held(t, day):
        x = t["exit"] or date.max
        return t["entry"] <= day < x
    conc = [sum(1 for t in trades if held(t, d)) for d in calendar]
    k = max(conc) if conc else 1
    k = max(k, 1)
    first = min(t["entry"] for t in trades)
    cal = [d for d in calendar if d >= first]
    eq, last = [], {}
    for d in cal:
        tot = 0.0
        for i, t in enumerate(trades):
            if d in t["path"]:
                last[i] = t["path"][d]
            tot += last.get(i, 0.0)
        eq.append(1 + tot / k)
    comp = [1.0]
    for i in range(1, len(eq)):
        comp.append(comp[-1] * (1 + eq[i] - eq[i - 1]))
    fixed, cmp_ = nav_stats(cal, eq), nav_stats(cal, comp)
    bench = benchmark(cal)
    order = sorted(trades, key=lambda t: t["entry"])
    h = len(order) // 2
    half = lambda xs: statistics.mean(t["ret"] for t in xs) if xs else None
    return dict(n=len(trades), n_closed=len(closed), n_open=len(trades) - len(closed),
                win=len(wins) / len(rets), avg=statistics.mean(rets), median=statistics.median(rets),
                best=max(rets), worst=min(rets),
                payoff=(statistics.mean(wins) / -statistics.mean(loss)) if wins and loss and statistics.mean(loss) < 0 else None,
                pf=(sum(wins) / -sum(loss)) if loss and sum(loss) < 0 else None,
                hold=statistics.mean(hold) if hold else None, **fixed,
                c_total=cmp_["total"], c_ann=cmp_["ann"], c_mdd=cmp_["mdd"], c_sharpe=cmp_["sharpe"],
                max_conc=k, now_conc=conc[-1] if conc else 0, pnl=sum(rets) * LOT_CAPITAL,
                first_half=half(order[:h]), second_half=half(order[h:]),
                bench=nav_stats(cal, bench) if bench else None,
                equity=list(zip(cal, eq)), equity_c=list(zip(cal, comp)),
                bench_nav=list(zip(cal, bench)) if bench else [], conc=list(zip(calendar, conc)))


def run(as_of=None, db_path=None, n=None):
    """
    回傳 dict(as_of, n_disposals, strategies={key: dict(name, side, rule, trades, skipped, m)}, combos=[...])。
    as_of 預設＝行情資料最新一天(本機 parquet；雲端 market_data.db)。
    n：推播模組(推播自己呼叫時傳自己，避免再載入一份)。
    """
    n = n or _notify()
    db_path = db_path or get_paths().disposal_db
    disposals = load_disposals(db_path, n=n)
    today = date.today()
    codes = {d["code"] for d in disposals}
    lo = min((min(d["entry"], d.get("short_entry", d["entry"])) for d in disposals), default=today) - timedelta(days=45)
    days = set()
    cur = lo
    while cur <= today:
        days.add(cur)
        cur += timedelta(days=1)
    daily = n.load_parquet_daily(codes, days)
    if as_of is None:
        as_of = max((k[1] for k in daily), default=today)
    corp = n.load_corp_actions(codes, lo, as_of)

    res = {k: dict(name=nm, side=side, rule=rule, trades=[], skipped=[]) for k, nm, side, rule in STRATEGIES}
    for d in sorted(disposals, key=lambda x: (x["start"], x["code"])):
        if d["start"] > as_of + timedelta(days=10):
            continue
        if d["offense"] == "初犯":
            key = "L5" if d["n_days"] == 5 else "L7"
            t, s = _long_trade(n, d, key, as_of, daily, corp, db_path)
            if t:
                res[key]["trades"].append(t)
            if s:
                res[key]["skipped"].append(s)
        if d["n_days"] == 7:
            key = "S7F" if d["offense"] == "初犯" else "S7R"
            t, s = _short_trade(n, d, key, as_of, daily)
            if t:
                res[key]["trades"].append(t)
            if s:
                res[key]["skipped"].append(s)

    all_trades = [t for v in res.values() for t in v["trades"]]
    first = min((t["entry"] for t in all_trades), default=as_of)
    calendar = [first] + _days_between(first, as_of)
    for v in res.values():
        v["m"] = metrics(v["trades"], calendar)
    combos = []
    for key, name, keys in COMBOS:
        ts = [t for k in keys for t in res[k]["trades"]]
        combos.append(dict(key=key, name=name, keys=keys, trades=ts, m=metrics(ts, calendar)))
    return dict(as_of=as_of, n_disposals=len(disposals), strategies=res, combos=combos)
