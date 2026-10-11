"""
單一股票的處置詳情(「詳細條件」頁用，2026-10-11)。本模組不依賴 PyQt6，網頁版可共用。

compute_disposal_detail(code, display_date) 依使用者規則「以最新公告為準」挑出要顯示的處置：
  1. 公告日 ≤ 顯示日、且處置尚未結束(period_end ≥ 顯示日)的紀錄中，取處置開始日最晚的那筆
     (例：6672 在 10/08 同時有 10/06~10/13 進行中與 10/08 公告的 10/12~10/16，取後者)
  2. 都沒有就取最近一次已結束的處置(標示已出關)
回傳處置頻率、天數、期間、目前第幾天、出關日、初犯/累犯、處置條款、近期注意條款，
以及對應統計圖表的分桶 key(新制：「5天_初犯」等；舊制：撮合分鐘數 5/20)。
"""
import re
import sqlite3
from datetime import datetime, timedelta

from core.utils import DateUtils
from core.runtime import get_paths

CLAUSE_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}

# 處置條款：官方處置原因文字 → 簡稱(證交所/櫃買的處置原因用語)
_CONDITION_PATTERNS = [
    (r"連續三個營業日|連續3個營業日", "連續3個營業日達注意標準"),
    (r"連續五個營業日|連續5個營業日", "連續5個營業日達注意標準"),
    (r"(最近)?十個營業日內有六個營業日|(最近)?10個營業日內有6個營業日", "最近10個營業日內有6個營業日達注意標準"),
    (r"(最近)?三十個營業日內有十二個營業日|(最近)?30個營業日內有12個營業日", "最近30個營業日內有12個營業日達注意標準"),
    (r"轉\(交\)換公司債|轉換公司債之標的", "可轉債之標的股票被處置"),
]


def _iso(d):
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)


def _trading_days_inclusive(s, e):
    n, cur = 0, s
    while cur <= e:
        if DateUtils.is_trading_day(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def _next_trading_day(d):
    d += timedelta(days=1)
    while not DateUtils.is_trading_day(d):
        d += timedelta(days=1)
    return d


def official_reason_text(reason):
    """官方公告全文中「處置原因」那一段(證交所「１處置原因：…」、櫃買「* 處置原因：…」)。"""
    text = re.sub(r"<br\s*/?>", "\n", reason or "")
    m = re.search(r"處置原因[：:]\s*(.+?)(?:\n\s*[２2]?\s*處置期間|\n\s*\*|$)", text, re.S)
    if m:
        return re.sub(r"\s+", "", m.group(1)).strip()
    return re.sub(r"\s+", "", text)[:120]


def condition_label(reason):
    """處置條款簡稱；另回傳是否「最近 30 個營業日內曾發布處置」(累犯原因)。"""
    text = official_reason_text(reason)
    labels = [label for pat, label in _CONDITION_PATTERNS if re.search(pat, text)]
    again = bool(re.search(r"三十個營業日內曾|30個營業日內曾", text))
    return "、".join(labels), again


def recent_clauses(code, until_iso, n_days=10, db_path=None):
    """顯示日(含)往前 n 個交易日內的注意條款 [(日期, '一,四'), ...]，由舊到新。"""
    conn = sqlite3.connect(db_path or get_paths().disposal_db)
    try:
        rows = conn.execute(
            "SELECT announce_date, clause_number FROM attention_clauses "
            "WHERE code = ? AND announce_date <= ? ORDER BY announce_date DESC", (str(code), until_iso)
        ).fetchall()
    finally:
        conn.close()
    by_day = {}
    for d, c in rows:
        by_day.setdefault(d, set()).add(c)
    # 第 n 個交易日(含顯示日)為下限；原本取「最近 n 個有紀錄的日子」，久沒被注意會一路抓到幾個月前
    cur, count = datetime.strptime(until_iso, "%Y-%m-%d"), 0
    while True:
        if DateUtils.is_trading_day(cur):
            count += 1
            if count >= n_days:
                break
        cur -= timedelta(days=1)
    cutoff = cur.strftime("%Y-%m-%d")
    out = []
    for d in sorted(x for x in by_day if x >= cutoff):
        nums = sorted(x for x in by_day[d] if x in CLAUSE_CN)
        out.append((d, ",".join(CLAUSE_CN[x] for x in nums) or "其他"))
    return out


def next_disposal_outlook(code, source, disp_d, mine, db_path=None):
    """
    「下次進處置」：跟總覽(forecast_engine.run_forecast)同一套算法——最近 30 個交易日的注意條款
    → DispositionPredictor 的四條規則進度與還差幾次 → predict_exact_disposal_frequency 預測下次
    進處置是初犯還是累犯(例：4556 目前處置是初犯，但再 1 次注意就會在這次處置的 30 營業日內，下次是累犯)。
    """
    from core.conditions_engine import build_pred_dates, disposal_periods_from_records, build_history_items
    from core.predictor import DispositionPredictor
    from core.forecast_engine import predict_exact_disposal_frequency

    anchor_dt = datetime.combine(disp_d, datetime.min.time())
    pred_dates = build_pred_dates(anchor_dt, 30)
    clauses_map = {}
    for day, cls in recent_clauses(code, _iso(disp_d), n_days=30, db_path=db_path):
        clauses_map[day[5:].replace("-", "/")] = cls
    periods = disposal_periods_from_records(mine, disp_d)
    hist = build_history_items(clauses_map, periods, pred_dates, anchor_dt)
    progress = DispositionPredictor.get_trigger_progress(hist)
    _, _, min_needed = DispositionPredictor.analyze(hist, future_days=5)
    days_until = min_needed if isinstance(min_needed, int) and 0 <= min_needed <= 10 else 0
    label = predict_exact_disposal_frequency(code, source, disp_d, {code: mine}, days_until_trigger=days_until)
    return {"min_needed": min_needed, "trigger_progress": progress, "next_label": label}


def _next(code, source, disp_d, mine, next_hint):
    if next_hint and next_hint.get("trigger_progress") and next_hint.get("enter_freq"):
        tp = next_hint["trigger_progress"]
        return {"min_needed": tp.get("min_needed"), "trigger_progress": tp,
                "next_label": next_hint["enter_freq"], "from_forecast": True}
    out = next_disposal_outlook(code, source, disp_d, mine)
    out["min_needed"] = out["trigger_progress"].get("min_needed", out["min_needed"])
    out["from_forecast"] = False
    return out


# 四條規則的計算期間(交易日)：連3日、連5日、10日內6次、30日內12次
_RULE_WINDOW = {"rule1": 3, "rule2": 5, "rule3": 10, "rule4": 30}
_RULE_NAME = {"rule1": "連3日第1款", "rule2": "連5日1-8款", "rule3": "10日內6次", "rule4": "30日內12次"}


def clause13_days(code, until_iso, n_days, db_path=None):
    """顯示日(含)往前 n 個交易日內，出現第十三款(當沖交易占比過高)的日期。"""
    if n_days <= 0:
        return []
    days, cur = [], datetime.strptime(until_iso, "%Y-%m-%d")
    while len(days) < n_days:
        if DateUtils.is_trading_day(cur):
            days.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    conn = sqlite3.connect(db_path or get_paths().disposal_db)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT announce_date FROM attention_clauses WHERE code = ? AND clause_number = 13 "
            f"AND announce_date IN ({','.join('?' * len(days))})", (str(code), *days)).fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


def predict_next_days(code, disp_iso, trigger_progress):
    """
    下次處置是 5 天還是 7 天(證交所 2026-08-10 新制：計算處置的期間內，若同時因「當沖交易占比過高」
    (第十三款)被公布注意，處置期間 7 個營業日，否則 5 個營業日)。
    取最快達標的規則(還差 k 次、計算期間 W 天)，看最近 W-k 個交易日(含顯示日)有沒有第十三款。
    以 8/10~10/08 新制 135 件驗證：133 件吻合，7 天者 20/20；不吻合 2 件(3055、3094)皆為
    「10日內6次」觸發，期間內有第十三款但處置 5 天。
    回傳 (天數, 說明)。
    """
    tp = trigger_progress or {}
    cands = [(tp[k]["needed"], i, k) for i, k in enumerate(("rule1", "rule2", "rule3", "rule4")) if k in tp]
    if not cands:
        return 5, ""
    needed, _, rule = min(cands)
    window = _RULE_WINDOW[rule]
    past = max(window - max(needed, 0), 0)
    hits = clause13_days(code, disp_iso, past)
    if hits:
        shown = "、".join(h[5:].replace("-", "/") for h in hits)
        return 7, (f"最快達標的「{_RULE_NAME[rule]}」計算期間內已有第十三款(當沖占比過高，{shown})，"
                   f"依新制處置 7 個營業日。")
    note = f"最快達標的「{_RULE_NAME[rule]}」計算期間內目前沒有第十三款(當沖占比過高)，處置 5 個營業日"
    if needed > 0:
        note += f"；之後 {needed} 個交易日若出現第十三款會變 7 天"
    return 5, note + "。"


def next_stats_bucket(state, total, offense, period_start, next_info, code="", disp_iso=""):
    """
    「下次進處置」的統計分桶(詳細條件下方圖表用，使用者要求看下次那一次)：
    - 已公告、尚未生效：下次就是這筆，天數與初犯/累犯都已知
    - 其他：天數依 predict_next_days(第十三款規則)，初犯/累犯取「下次進處置」的判定
    回傳 (bucket, title, basis)；算不出來回傳 (None, "", "")。
    """
    if state == "已公告，尚未生效" and total in (5, 7):
        return (f"{total}天_{offense}", f"新制 {total}天{offense}",
                f"下次處置即已公告、{period_start} 生效的這次（{total} 天、{offense}）。")
    label = (next_info or {}).get("next_label") or ""
    if not label:
        return None, "", ""
    off = "累犯" if "累犯" in label else "初犯"
    days, why = predict_next_days(code, disp_iso, (next_info or {}).get("trigger_progress"))
    return (f"{days}天_{off}", f"新制 {days}天{off}", f"下次進處置預估為{off}、{days} 天：{why}")


def compute_disposal_detail(code, display_date, records=None, next_hint=None):
    """
    display_date: date/datetime(畫面上的顯示日)。
    records: disposal_records 全部紀錄(可傳入共用，省一次讀庫)；None 就自己讀。
    next_hint: 總覽(run_forecast)對這檔已算好的 {"trigger_progress", "enter_freq"}；有給就直接沿用，
               畫面數字跟總覽完全一致(總覽的條款來源是快取＋聽牌紀錄＋注意條款庫合併，自己重算可能
               在 10日/30日累計次數上差一兩次)。沒給才用注意條款庫自己算。
    """
    from core.measure_parser import MeasureParser
    from core.forecast_engine import predict_exact_disposal_frequency
    from core.disposal_stats_analytics import RULE_CHANGE_DATE

    code = str(code).strip()
    disp_d = display_date.date() if hasattr(display_date, "date") and callable(display_date.date) else display_date
    disp_iso = _iso(disp_d)

    if records is None:
        from core.disposal_database import DisposalDatabase
        db = DisposalDatabase(get_paths().disposal_db)
        records = db.get_all_records()
        db.close()
    mine = []
    for r in records:
        c = str(r.get("code", "")).strip().replace(".0", "") if str(r.get("code", "")).endswith(".0") \
            else str(r.get("code", "")).strip()
        if c == code and r.get("period_start") and r.get("period_end"):
            mine.append(r)

    def ann_iso(r):
        a = DateUtils.to_iso_date_str(r.get("announce_date") or "")
        if a:
            return a
        # 沒有公告日：依規則是處置開始日的前一個交易日
        ps = datetime.strptime(r["period_start"], "%Y-%m-%d")
        return DateUtils.get_last_trading_day(ps - timedelta(days=1)).strftime("%Y-%m-%d")

    announced = [r for r in mine if ann_iso(r) <= disp_iso]
    current = [r for r in announced if r["period_end"] >= disp_iso]
    if current:
        rec = max(current, key=lambda r: (r["period_start"], ann_iso(r)))
    elif announced:
        rec = max(announced, key=lambda r: (r["period_end"], r["period_start"]))
    else:
        nx = _next(code, "", disp_d, mine, next_hint)
        nb, nt, nbasis = next_stats_bucket("", 0, "", "", nx, code, disp_iso)
        return {"code": code, "has_disposal": False, "display_date": disp_iso,
                "recent_clauses": recent_clauses(code, disp_iso), "next": nx,
                "next_stats_bucket": nb, "next_stats_title": nt, "next_stats_basis": nbasis}

    ps = datetime.strptime(rec["period_start"], "%Y-%m-%d").date()
    pe = datetime.strptime(rec["period_end"], "%Y-%m-%d").date()
    total = _trading_days_inclusive(ps, pe)
    if disp_d < ps:
        state, day_no = "已公告，尚未生效", 0
    elif disp_d <= pe:
        state, day_no = "處置中", _trading_days_inclusive(ps, disp_d)
    else:
        state, day_no = "已出關", total
    exit_d = _next_trading_day(pe)

    measure = rec.get("measure") or ""
    freq = MeasureParser.parse_frequency(measure) or MeasureParser.parse_frequency(rec.get("reason") or "")

    # 初犯/累犯：跟儀表板/總覽同一支判定(這次處置開始日往前 30 個營業日內是否有其他處置)
    anchor = DateUtils.get_last_trading_day(datetime.combine(ps, datetime.min.time()) - timedelta(days=1)).date()
    label = predict_exact_disposal_frequency(code, rec.get("source", ""), anchor, {code: mine}, days_until_trigger=0)
    offense = "累犯" if "累犯" in label else "初犯"

    cond, again = condition_label(rec.get("reason") or "")

    # 統計圖表分桶：新制(2026-08-10 起)5/7 天依初犯累犯細分；其餘用撮合分鐘數(舊制 5分/20分)
    stats_bucket, stats_title = None, ""
    if ps >= RULE_CHANGE_DATE and total in (5, 7):
        stats_bucket = f"{total}天_{offense}"
        stats_title = f"新制 {total}天{offense}"
    elif freq in ("5分", "20分", "2分"):
        stats_bucket = int(freq[:-1])
        stats_title = f"{freq}盤" + ("(新制，全部)" if freq == "2分" else "(舊制)")

    result = {
        "code": code, "has_disposal": True, "display_date": disp_iso,
        "name": rec.get("name", ""), "source": rec.get("source", ""),
        "state": state, "freq": freq or "—", "total_days": total, "day_no": day_no,
        "period_start": _iso(ps), "period_end": _iso(pe), "exit_date": _iso(exit_d),
        "announce_date": ann_iso(rec), "offense": offense,
        "condition": cond or "—", "again_within_30": again,
        "official_reason": official_reason_text(rec.get("reason") or ""),
        "recent_clauses": recent_clauses(code, disp_iso),
        "stats_bucket": stats_bucket, "stats_title": stats_title,
        "next": _next(code, rec.get("source", ""), disp_d, mine, next_hint),
    }
    nb, nt, nbasis = next_stats_bucket(state, total, offense, _iso(ps), result["next"], code, disp_iso)
    result.update({"next_stats_bucket": nb, "next_stats_title": nt, "next_stats_basis": nbasis})
    return result
