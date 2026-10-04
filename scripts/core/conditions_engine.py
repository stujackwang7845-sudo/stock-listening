"""
進處置條件共用核心：儀表板(CalculationWorker)與處置預測總覽(ForecastWorker)
都呼叫這裡，確保同一支股票、同一天，兩頁算出完全相同的進處置條件與排除條件。

[2026-10-04] 之前兩頁各算一套：
- 總覽呼叫 calculate_conditions() 沒傳還差次數，預設成「還差1次」，所有款項都被
  當成「明天達到就進處置」。
- 儀表板逐日即時查 API 自己湊還差次數，查不到就低估/高估，且「30日內曾有第一款」
  就硬把第一款設成還差1次(第一款要「連續」3日，曾經有過不代表明天會進處置)。
現在統一：還差次數一律用 attention_clauses 逐日資料 + 處置紀錄算 trigger_progress，
官方聽牌原因(連續二次/連續四次/九個營業日已有五次/二十九個營業日已有十一次)再覆蓋，
以官方為準。
"""
from datetime import datetime, timedelta

from core.predictor import DispositionPredictor
from core.utils import DateUtils

# 快取裡的 calc_results 若不是這個版本算的，一律重算(舊版結果分區是錯的)
CONDITIONS_VERSION = 2

CLAUSE_1_8 = ['一', '二', '三', '四', '五', '六', '七', '八']


def build_pred_dates(anchor_dt, n=30):
    """anchor_dt(含)往前 n 個交易日，格式 MM/DD，由舊到新。"""
    dates = []
    temp = anchor_dt
    while len(dates) < n:
        if DateUtils.is_trading_day(temp):
            dates.insert(0, temp.strftime("%m/%d"))
        temp -= timedelta(days=1)
    return dates


def disposal_periods_from_records(records, anchor_date):
    """處置紀錄 → [(period_start, period_end)]，只取已開始(<= anchor_date)的處置。"""
    periods = []
    for r in records or []:
        ps_str = r.get('period_start')
        pe_str = r.get('period_end')
        if not (ps_str and pe_str):
            continue
        try:
            ps_dt = datetime.strptime(ps_str, "%Y-%m-%d").date()
            pe_dt = datetime.strptime(pe_str, "%Y-%m-%d").date()
        except Exception:
            continue
        # 只有已經發生(或正在發生)的處置才算歷史，不能拿未來處置重置
        if ps_dt <= anchor_date:
            periods.append((ps_dt, pe_dt))
    return periods


def build_history_items(clauses_map, periods, pred_dates, anchor_dt):
    """
    clauses_map {"MM/DD": "一,三"} → DispositionPredictor 用的逐日 history_items。
    (原 ForecastWorker.run 內的邏輯，搬出來給兩頁共用)
    """
    clauses_map = clauses_map or {}
    filtered_clauses = {}
    for dk, cv in clauses_map.items():
        if cv:
            # 只接受第 1~8 款，「注意」等泛稱不精確，不計入
            valid = [c.strip() for c in cv.split(',') if c.strip() in CLAUSE_1_8]
            if valid:
                filtered_clauses[dk] = ','.join(valid)

    hist_items = []
    for d in pred_dates:
        c_str = filtered_clauses.get(d, "")
        # [Fix 2026-09-01] is_any_all 用未過濾的原始 clauses_map，只要當天官方有任何
        # 注意紀錄就算「有觸發」；is_clause1/is_any 用過濾後的 c_str。
        raw_c_str = clauses_map.get(d, "")
        try:
            d_date = datetime.strptime(f"{anchor_dt.year}/{d}", "%Y/%m/%d").date()
            if d_date > anchor_dt.date():
                d_date = datetime.strptime(f"{anchor_dt.year - 1}/{d}", "%Y/%m/%d").date()
        except Exception:
            d_date = None

        is_disp = False
        if d_date:
            for ps, pe in periods:
                if ps <= d_date <= pe:
                    is_disp = True
                    break

        hist_items.append({
            "is_clause1": "一" in c_str,
            "is_any": len(c_str) > 0,
            "is_any_all": len(raw_c_str.strip()) > 0,
            "is_disposed": is_disp,
        })
    return hist_items


def needed_for_conditions(trigger_progress, official_reason=""):
    """
    回傳 (needed_c1, needed_any) 給 calculate_conditions() 分區用：
    <=1 列「進處置」、==2 列「聽牌」、>=3 不顯示。
    - needed_c1：連3日第一款還差幾天
    - needed_any：連5日 / 10日6次 / 30日12次(一至八款)最急的那條還差幾天
    官方聽牌原因有列到的規則，以官方為準設成 1(只會往更急迫調，不會放寬)。
    """
    tp = trigger_progress or {}
    needed_c1 = tp.get("rule1", {}).get("needed", 3)
    needed_any = min(
        tp.get("rule2", {}).get("needed", 5),
        tp.get("rule3", {}).get("needed", 6),
        tp.get("rule4", {}).get("needed", 12),
    )
    reason = str(official_reason or "")
    if "連續二次" in reason:
        needed_c1 = min(needed_c1, 1)
    if ("連續四次" in reason or "九個營業日已有五次" in reason
            or "二十九個營業日已有十一次" in reason):
        needed_any = min(needed_any, 1)
    return needed_c1, needed_any


def fetch_has_c2_disp_60(fetcher, code, source, anchor_dt):
    """
    前60日曾因第二款進處置(排除條件 Rule B)。
    (原 forecast_page.py [Fix 2026-09-30] 那段，比照 dashboard 的即時 API 查詢)
    """
    try:
        s_date = (anchor_dt - timedelta(days=90)).strftime("%Y%m%d")
        e_date = anchor_dt.strftime("%Y%m%d")
        cutoff_60 = (anchor_dt - timedelta(days=60)).strftime("%Y%m%d")
        hist_disp = fetcher.fetch_stock_disposition_history(code, s_date, e_date, source)
        rows_d = []
        if isinstance(hist_disp, dict) and 'data' in hist_disp:
            rows_d = hist_disp['data']
        elif isinstance(hist_disp, list):
            rows_d = hist_disp
        for r in rows_d:
            found_date = ""
            if isinstance(r, list):
                if len(r) > 2:
                    found_date = str(r[1])
                    if str(r[2]).strip() != code:
                        continue
            elif isinstance(r, dict):
                found_date = str(r.get("Date", ""))
                c = r.get("Code", "") or r.get("code", "") or r.get("StkNo", "")
                if c and str(c).strip() != code:
                    continue
            found_date = found_date.replace(".", "/")
            ad_date = ""
            if "/" in found_date:
                ps = found_date.split('/')
                if len(ps) == 3:
                    ad_date = f"{int(ps[0]) + 1911}{ps[1].zfill(2)}{ps[2].zfill(2)}"
            if ad_date and ad_date >= cutoff_60:
                r_str = str(r)
                if "第二款" in r_str or "第2款" in r_str or ("款" in r_str and "二" in r_str) or \
                   "第二次處置" in r_str or "六個營業日" in r_str:
                    return True
    except Exception as e:
        print(f"[conditions_engine] {code} has_c2_disp_60 查詢失敗(不影響其他計算): {e}")
    return False


def compute_stock_conditions(code, source, name, anchor_dt, clauses_map, disp_records,
                             official_reason="", fetcher=None, history_df=None, shares_outstanding=None):
    """
    單一股票的進處置條件，儀表板與總覽共用。
    Returns dict: lines, exclusion_lines, is_clause2_risk, needed_c1, needed_any, trigger_progress
    history_df/shares_outstanding 可由呼叫端傳入(測試用)，否則用 StockFetcher 取 180 日。
    """
    from core.calculator import DispositionCalculator

    if source in ("TWSE", "tse"):
        source = "上市"
    elif source in ("TPEX", "otc", "OTC"):
        source = "上櫃"

    pred_dates = build_pred_dates(anchor_dt, 30)
    periods = disposal_periods_from_records(disp_records, anchor_dt.date())
    hist_items = build_history_items(clauses_map, periods, pred_dates, anchor_dt)
    trigger_progress = DispositionPredictor.get_trigger_progress(hist_items)
    needed_c1, needed_any = needed_for_conditions(trigger_progress, official_reason)

    result = {
        "lines": [], "exclusion_lines": [], "is_clause2_risk": False,
        "needed_c1": needed_c1, "needed_any": needed_any,
        "trigger_progress": trigger_progress,
    }

    if fetcher is None:
        from core.fetcher import StockFetcher
        fetcher = StockFetcher()

    if history_df is None:
        history_df, shares_outstanding = fetcher.fetch_stock_history(code, source, period="180d", allow_fetch=True)
    if history_df is None or history_df.empty:
        result["lines"] = ["無法取得歷史股價"]
        return result

    import pandas as pd
    history_df = history_df[pd.to_datetime(history_df.index).date <= anchor_dt.date()]
    if history_df.empty:
        result["lines"] = ["資料截斷後為空"]
        return result

    # 前30個營業日曾發生第一款(排除條件 Rule A)：用同一份逐日條款資料
    has_c1_30 = any(item["is_clause1"] for item in hist_items)
    has_c2_disp_60 = fetch_has_c2_disp_60(fetcher, code, source, anchor_dt)

    lines, is_clause2_risk, exclusion_lines = DispositionCalculator.calculate_conditions(
        history_df, source=source, shares_outstanding=shares_outstanding or 0,
        needed_c1=needed_c1, needed_any=needed_any, stock_name=name,
        has_c1_30=has_c1_30, has_c2_disp_60=has_c2_disp_60,
    )
    result.update({
        "lines": lines,
        "exclusion_lines": exclusion_lines,
        "is_clause2_risk": is_clause2_risk,
    })
    return result
