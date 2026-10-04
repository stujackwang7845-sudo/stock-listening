"""
處置預測總覽的計算核心(原 ui/forecast_page.py 的 ForecastWorker.run 與 ForecastPage.load_data
組輸入的部分，2026-10-04 P2 原樣搬出，不改邏輯)。

桌面版 ForecastWorker 只剩執行緒與進度訊號，雲端直接呼叫這裡；本模組不依賴 PyQt6。
- build_forecast_inputs(anchor_dt) -> (agg_data, attention_list)
- run_forecast(...) -> {"date_str", "listening", "one_step", "disposed"}
- predict_exact_disposal_frequency(...) 預測下一次進處置的頻率與初犯/累犯
"""
from datetime import datetime, timedelta

from core.utils import DateUtils
from core.predictor import DispositionPredictor


def parse_frequency(interval_str):
    """強健的處置頻率通用解析器 (支援 API 控制字元、中文字與措施代碼)"""
    from core.measure_parser import MeasureParser
    return MeasureParser.parse_frequency(interval_str)


def predict_exact_disposal_frequency(code, source, anchor_date, disp_map, days_until_trigger=0):
    """精確預測最快進處置的頻率與次數

    days_until_trigger: 距離連續觸發條件成立，還需要幾個「額外」交易日
    （例如「連3日第1款 2/3」還差1天達標，值為1；已達標或無預測意義時傳0）。
    用來把初犯/累犯的30個營業日判定基準，從「今天」往後推到「實際掛牌
    進處置的那一天」——因為條件當天成立，處置是下一個交易日才生效。
    """
    # 條件成立日：從 anchor_date 往後推 days_until_trigger 個交易日
    trigger_complete_date = anchor_date
    steps = 0
    while steps < days_until_trigger:
        trigger_complete_date += timedelta(days=1)
        if DateUtils.is_trading_day(trigger_complete_date):
            steps += 1

    # 處置實際生效日 = 條件成立日的次一個交易日
    predicted_start_date = trigger_complete_date + timedelta(days=1)
    while not DateUtils.is_trading_day(predicted_start_date):
        predicted_start_date += timedelta(days=1)

    # 「最近30個營業日內」的窗口是含 predicted_start_date 本身在內共30天，
    # 所以只需再往前多找29天（不是30天），窗口才會剛好是30個營業日整。
    trading_days_back = 0
    check_date = predicted_start_date
    while trading_days_back < 29:
        check_date -= timedelta(days=1)
        if DateUtils.is_trading_day(check_date):
            trading_days_back += 1
    limit_start_date = check_date

    past_disposals = []
    records = disp_map.get(str(code), [])
    is_full_delivery = False

    for r in records:
        p_start_str = r.get('period_start')
        if p_start_str:
            try:
                p_start = datetime.strptime(p_start_str, "%Y-%m-%d").date()
                # [Fix 2026-08-31] 這支股票「這次」的處置紀錄本身，period_start
                # 就等於 predicted_start_date（呼叫端就是在問「這次算初犯還累犯」），
                # 用 <= 會把自己也算進「過去」的次數，next_count 因此多算一次，
                # 導致單筆(從未處置過)的股票被誤判成累犯。過去的紀錄嚴格早於
                # predicted_start_date 才算數，自己不算「之前」。
                if limit_start_date <= p_start < predicted_start_date:
                    past_disposals.append(p_start)
            except:
                pass
    
    if records:
        # [Fix] 原本只查 measure 欄位(如"10分")，但這是解析過的短標籤，不會有
        # "變更交易方法"這種關鍵字——關鍵字只會出現在 reason 欄位的官方原文裡。
        # 曾發生在6225：measure="10分"，關鍵字判斷永遠抓不到，導致 is_full_delivery
        # 恆為False，累犯時就跟一般案件一樣顯示2分，而不是變更交易方法該有的25分。
        last_measure = records[0].get('measure', '') or ''
        last_reason = records[0].get('reason', '') or ''
        combined = f"{last_measure} {last_reason}"
        if any(k in combined for k in ['變更交易', '每十分鐘', '每貳拾伍分鐘', '每四十五分鐘']):
            is_full_delivery = True

    unique_past_starts = sorted(list(set(past_disposals)))
    disposal_count = len(unique_past_starts)
    next_count = disposal_count + 1

    # [2026-08-10 修法] 新制統一初犯/累犯的撮合頻率為2分鐘，僅變更交易方法／
    # 分盤方式交易／管理股票等特殊情境例外（見 is_full_delivery）。
    # 判定基準：最近30個營業日內是否已有處置紀錄 -> 有則累犯，無則初犯。
    offense_label = "初犯" if next_count == 1 else "累犯"

    if next_count == 1:
        freq = "10分" if is_full_delivery else "2分"
    else:
        # [Fix] TWSE/TPEx「第六條修正對照表」的「第二次（含）以上」是同一組頻率結構，
        # 不分實際是第2次、第3次或更多次——之前把 next_count==2 和 >=3 拆成兩支、
        # 而且 next_count==2 時上市誤寫成20分（官方原文是25分，曾發生在6225：
        # 目前處置10分為初犯，若今天再被注意觸發下一次，會是累犯，應顯示25分累犯，
        # 不是20分累犯），一併修正、合併。
        freq = "25分" if is_full_delivery else "2分"

    return f"{freq}{offense_label}"


def guess_reason(data):
    clauses = data.get("clauses", {})
    c1_count = sum(1 for v in clauses.values() if v and "一" in v)
    any_count = sum(1 for v in clauses.values() if v)
    if c1_count >= 3: return "連續3日第1款"
    if any_count >= 6: return f"10日內{any_count}次注意"
    if any_count >= 5: return "連續5日注意"
    return "處置"


def build_forecast_inputs(anchor_dt):
    """
    組 run_forecast 的輸入：快取底稿 agg_data(往前找 10 個交易日) + listening_history 補條款
    + attention_clauses 補條款 + 處置紀錄補處置狀態，以及當天官方聽牌清單 attention_list。
    """
    date_str = anchor_dt.strftime("%Y%m%d")

    # === 第一步：嘗試從快取讀取 agg_data（完整股票清單）===
    # 注意：快取只提供股票清單底稿，date_str 永遠使用使用者選擇的日期
    agg_data = None
    from core.cache import CacheManager
    cm = CacheManager()
    search_dt = anchor_dt
    for _ in range(10):
        candidate = search_dt.strftime("%Y%m%d")
        cached = cm.get_agg_data(candidate)
        if cached:
            agg_data = cached
            print(f"[ForecastPage] 使用 {candidate} 的快取作為底稿 (顯示日期: {date_str})")
            break
        search_dt -= timedelta(days=1)
        while not DateUtils.is_trading_day(search_dt):
            search_dt -= timedelta(days=1)

    if not agg_data:
        agg_data = {}
        print(f"[ForecastPage] 無快取 agg_data，將純從 listening_history 建構")

    # === 第二步：從 listening_history.json 覆蓋/補齊 clauses ===
    # 這是條款資料的真實來源，比快取更準確
    attention_list = []
    hm = None
    try:
        from core.history_manager import HistoryManager
        hm = HistoryManager()

        # 讀取過去 45 天的所有注意紀錄
        lookback_days = 45
        start_date = anchor_dt - timedelta(days=lookback_days)
        current = start_date
        enriched_count = 0

        while current <= anchor_dt:
            day_records = hm.get_listening_data(current)
            if day_records:
                for r in day_records:
                    code = str(r["code"])
                    if len(code) > 4 or "." in code:
                        continue

                    # 如果快取沒有這支股票，新建記錄
                    if code not in agg_data:
                        agg_data[code] = {
                            "name": r.get("name", ""),
                            "source": r.get("source", "上市"),
                            "clauses": {},
                            "is_disposed": False,
                            "period": "",
                            "measure": "",
                        }

                    # 從 trigger_info 補充 clauses（只補空，不降級覆蓋）
                    trigger_info = r.get("trigger_info", {})
                    if isinstance(trigger_info, str):
                        try:
                            import json as _json
                            trigger_info = _json.loads(trigger_info)
                        except:
                            trigger_info = {}

                    if trigger_info:
                        if "clauses" not in agg_data[code]:
                            agg_data[code]["clauses"] = {}
                        for date_key, clause_val in trigger_info.items():
                            # [Fix 2026-09-03] 聽牌(官方)清單的 reason 是「累積次數已達
                            # X次」的多日彙總描述，不是當天的單日觸發事件。若在這裡當成
                            # 當天的新條款補入，會跟 attention_clauses 裡已經逐日記錄的
                            # 真實條款重複計算(例如 4991 被灌成 6 次而非官方文字的 5 次)。
                            # 比照 dashboard.py 的 _merge_clauses_from_listening_history，
                            # 只補「注」「注意」以外的精確條款(1~8款)。
                            if clause_val in ("注", "注意"):
                                continue
                            existing = agg_data[code]["clauses"].get(date_key, "")
                            if not existing:
                                # 快取沒有此日期 → 補入
                                agg_data[code]["clauses"][date_key] = clause_val
                                enriched_count += 1
                            elif existing == "注意" and clause_val and clause_val != "注意":
                                # 快取只有「注意」但新值更精確 → 升級
                                agg_data[code]["clauses"][date_key] = clause_val
                                enriched_count += 1
                            # 否則保留快取的精確條款（如 一,四），不被「注意」覆蓋

            current += timedelta(days=1)

        # 建構當天的 attention_list
        today_records = hm.get_listening_data(anchor_dt)
        if today_records:
            for r in today_records:
                code = str(r["code"])
                if len(code) > 4 or "." in code:
                    continue
                attention_list.append({
                    "code": code, "name": r.get("name", ""),
                    "reason": r.get("reason", ""),
                    "source": r.get("source", "上市"),
                    "trigger_info": r.get("official_reason") or r.get("trigger_info", ""),
                })

        print(f"[ForecastPage] agg_data={len(agg_data)} 支, 從 history 補齊 {enriched_count} 筆 clauses")
    
    # === 補充步驟：從 disposal_history.db 的 attention_clauses 補齊 clauses ===
        # [Fix] 這裡原本多寫了一次 "from core.utils import DateUtils"。DateUtils
        # 在檔案最上面(module級)已經 import 過了，這裡多此一舉的區域 import 反而
        # 讓 Python 把 DateUtils 視為整個 load_data() 函式的區域變數——導致函式
        # 前面（第876行左右）尚未執行到這行 import 就先用到 DateUtils 時，
        # 直接丟 UnboundLocalError 整個崩潰。拿掉這行區域 import，改用 module級的即可。
        import sqlite3
        from core import database_clause_ext
        import datetime as dt

        from core.runtime import get_paths
        conn = sqlite3.connect(get_paths().disposal_db)
        past_dates = []
        curr = anchor_dt
        count = 0
        while count < 30:
            if DateUtils.is_trading_day(curr):
                past_dates.append(curr)
                count += 1
            curr = curr - dt.timedelta(days=1)
            
        total_db_loaded = 0
        for date_obj in past_dates:
            date_str_mmdd = date_obj.strftime("%m/%d")
            clauses_from_db = database_clause_ext.get_clauses_for_date(conn, date_str_mmdd)
            if not clauses_from_db:
                continue
            for code_str, db_data in clauses_from_db.items():
                clause_val = db_data.get("clauses", "")
                if not clause_val: continue
                
                if code_str not in agg_data:
                    agg_data[code_str] = {
                        "name": db_data.get("name", ""),
                        "source": db_data.get("source", "上市"),
                        "clauses": {},
                        "is_disposed": False,
                        "period": "",
                        "measure": "",
                    }
                if "clauses" not in agg_data[code_str]:
                    agg_data[code_str]["clauses"] = {}
                
                agg_data[code_str]["clauses"][date_str_mmdd] = clause_val
                total_db_loaded += 1
        conn.close()
        print(f"[ForecastPage] disposal_history.db: 從 attention_clauses 補充 {total_db_loaded} 筆")

    except Exception as e:
        print(f"[ForecastPage] Error enriching from history: {e}")

    # === 第三步：從 disposal_history.db 補充處置狀態 ===
    # 只補充快取中 is_disposed 缺失的股票
    try:
        from core.disposal_database import DisposalDatabase
        disposal_db = DisposalDatabase()
        ref_date = anchor_dt.strftime("%Y-%m-%d")
        active_disposals = disposal_db.get_active_disposals(ref_date)

        supplemented = 0
        for d in active_disposals:
            code = str(d.get("code", ""))
            # 使用原始的 period_raw（民國年格式），確保 parse_period_end 能解析
            period_raw = d.get("period_raw", "")
            if not period_raw:
                # 如果沒有 period_raw，用 period_start~period_end 組合
                ps = d.get("period_start", "")
                pe = d.get("period_end", "")
                period_raw = f"{ps}~{pe}" if ps and pe else ""

            if code not in agg_data:
                agg_data[code] = {
                    "name": d.get("name", ""),
                    "source": d.get("source", "上市"),
                    "clauses": {},
                    "is_disposed": True,
                    "period": period_raw,
                    "measure": d.get("measure", ""),
                }
                supplemented += 1
            elif not agg_data[code].get("is_disposed"):
                # 快取沒標記處置中，但 DB 有 → 補充
                agg_data[code]["is_disposed"] = True
                if not agg_data[code].get("period"):
                    agg_data[code]["period"] = period_raw
                if not agg_data[code].get("measure"):
                    agg_data[code]["measure"] = d.get("measure", "")
                supplemented += 1

        disposal_db.close()
        print(f"[ForecastPage] disposal_history.db: {len(active_disposals)} 筆, 補充 {supplemented} 筆")
    except Exception as e:
        print(f"[ForecastPage] Error loading disposal data: {e}")

    return agg_data, attention_list


def run_forecast(agg_data, date_str, attention_list, mf_db, cb_db, punish_df=None,
                 is_history=False, progress=None):
    """
    聽牌(官方) / 一進聽(預測) / 處置中 三個清單。
    agg_data 會被就地寫回 calc_results 等欄位，並存進 cache.db 的 agg_cache。
    progress(current, total) 給桌面版進度條用，可省略。
    """
    attention_list = attention_list or []
    _emit = progress or (lambda c, t: None)

    listening = []
    one_step = []
    disposed = []

    anchor_dt = datetime.strptime(date_str, "%Y%m%d")
    
    try:
        from core.disposal_database import DisposalDatabase
        disposal_db = DisposalDatabase()
    except:
        disposal_db = None

    # 過去 30 個交易日
    pred_dates_30 = []
    temp = anchor_dt
    while len(pred_dates_30) < 30:
        if DateUtils.is_trading_day(temp):
            pred_dates_30.insert(0, temp.strftime("%m/%d"))
        temp -= timedelta(days=1)

    # 官方聽牌清單 (code set)
    official_set = set(item["code"] for item in attention_list)
    official_reason_map = {item["code"]: item.get("trigger_info", "") for item in attention_list}

    # [Fix] 確保所有聽牌代碼都能進入迴圈處理，即使不在 agg_data 內
    valid_codes = set(c for c in agg_data.keys() if len(str(c)) == 4 and "." not in str(c))
    valid_codes.update(official_set)
    codes = sorted(list(valid_codes), key=str)
    total = len(codes)

    # 讀取最後回補日資料 (參考股期套利)
    stop_short_data = {}
    try:
        import os, json
        from core.runtime import get_paths
        stop_short_path = get_paths().stop_short_json
        if os.path.exists(stop_short_path):
            with open(stop_short_path, "r", encoding="utf-8") as f:
                stop_short_data = json.load(f)
    except Exception as e:
        print(f"[ForecastWorker] 讀取 stop_short.json 失敗: {e}")

    # 預先載入所有處置紀錄 (取代慢速 HTTP 請求)
    disp_map = {}
    local_active_records_list = []
    try:
        from core.disposal_database import DisposalDatabase
        disp_db = DisposalDatabase()
        all_disp_records = disp_db.get_all_records()
        for r in all_disp_records:
            c = r['code']
            if c not in disp_map:
                disp_map[c] = []
            disp_map[c].append(r)
        
        # [Fix] 將資料庫認定的有效處置清單提取出來，做為後續 fallback 的權威來源
        local_active_records_list = disp_db.get_active_disposals(anchor_dt.strftime("%Y-%m-%d"))
        
        disp_db.close()
    except Exception as e:
        print(f"[ForecastWorker] DB load error: {e}")

    # 預先解析與整理永豐 API 的處置名單 (active_punish_map)，徹底修復類型比對與同步問題
    active_punish_map = {}
    if punish_df is not None and not punish_df.empty:
        for _, p_row in punish_df.iterrows():
            p_code = str(p_row.get('code', '')).strip()
            p_start = p_row.get('start_date')
            p_end = p_row.get('end_date')
            p_interval = str(p_row.get('interval', ''))
            
            if p_code and p_start and p_end:
                try:
                    p_start_dt = DateUtils.parse_period_start(str(p_start))
                    p_end_dt = DateUtils.parse_period_start(str(p_end))
                    
                    if p_start_dt and p_end_dt:
                        p_start_date = p_start_dt.date()
                        p_end_date = p_end_dt.date()

                        # 判斷基準日是否在處置期間內
                        if p_start_date <= anchor_dt.date() <= p_end_date:
                            freq = parse_frequency(p_interval)
                            if not freq:
                                freq = p_interval[:10]
                            active_punish_map[p_code] = {
                                "freq": freq,
                                "start": p_start_date,
                                "end": p_end_date,
                                "interval": p_interval,
                                "reason": str(p_row.get('reason', '')) or str(p_row.get('description', ''))
                            }
                except Exception as ex:
                    print(f"[ForecastWorker] Parse punish row error for {p_code}: {ex}")

    for idx, code in enumerate(codes):
        if idx % 5 == 0:
            _emit(idx, total)

        data = agg_data.get(code, {})
        name = data.get("name", "")
        source = data.get("source", "")
        
        # 若 agg_data 缺資料，嘗試從 attention_list 補齊
        if not name or not source:
            for att in attention_list:
                if str(att["code"]) == str(code):
                    name = name or att.get("name", "")
                    source = source or att.get("source", "上市")
                    break
        if "購" in name or "售" in name or "DR" in name:
            continue
        if source in ("TWSE", "tse"): source = "上市"
        elif source in ("TPEX", "otc", "OTC"): source = "上櫃"

        is_disposed = data.get("is_disposed", False)
        future_period = data.get("future_period", "")
        future_measure = data.get("future_measure", "")
        if future_period:
            period = future_period
        else:
            period = data.get("period", "")

        # 標記
        has_futures = data.get("has_futures", False)
        if not has_futures and mf_db:
            try: has_futures = mf_db.has_futures(code)
            except: pass
        has_cb = False
        if cb_db:
            try: has_cb = cb_db.has_cb_now(code)
            except: pass
        can_short = data.get("can_short", False)
        suffix = ""
        if has_futures: suffix += "(期)"
        if has_cb: suffix += "(CB)"
        if can_short: suffix += "(借)"

        last_close = data.get("last_close")
        try: close_txt = f"{float(last_close):.2f}" if last_close else "-"
        except: close_txt = "-"

        # === 計算「目前狀態」與「進處置」===
        current_status = "非處置"
        in_active_punish = str(code) in active_punish_map
        
        if in_active_punish:
            current_status = active_punish_map[str(code)]["freq"]
        else:
            # 備用：若不在 API 處置中，則嘗試從 punish_df 進行相容性比對 (通常已由 active_punish_map 涵蓋)
            if punish_df is not None and not punish_df.empty:
                punish_rows = punish_df[punish_df['code'] == str(code)]
                if not punish_rows.empty:
                    for _, p_row in punish_rows.iterrows():
                        p_start = p_row.get('start_date')
                        p_end = p_row.get('end_date')
                        p_interval = str(p_row.get('interval', ''))
                        try:
                            p_start_dt = DateUtils.parse_period_start(str(p_start))
                            p_end_dt = DateUtils.parse_period_start(str(p_end))
                            if p_start_dt and p_end_dt and p_start_dt.date() <= anchor_dt.date() <= p_end_dt.date():
                                current_status = parse_frequency(p_interval)
                                if not current_status:
                                    current_status = p_interval[:10]
                                break
                        except:
                            pass
                        
        disp_start_dt = DateUtils.parse_period_start(period) if period else None
        disp_end_dt = DateUtils.parse_period_end(period) if period else None

        # === 處置中 ===
        actually_disposed = False
        is_future_disposed = False
        freq = ""
        reason = ""
        
        override_active_punish = False
        if in_active_punish and future_period:
            f_start = DateUtils.parse_period_start(future_period)
            if f_start and f_start.date() > active_punish_map[str(code)]["start"]:
                override_active_punish = True

        # [Fix 2026-10-01] 永豐/StockGateway 的處置清單偶爾會落後本機資料庫(本機是直接
        # 從證交所/櫃買官方公告抓的)——例如 2221 剛公告「再次處置」(10/01~10/12)，永豐
        # 那邊還停在舊的「第一次處置」(09/24~10/06)，導致畫面顯示過期的處置期間。
        # 比照上面 future_period 的做法：本機資料庫若有更新(period_start 更晚)的現行
        # 處置紀錄，視為永豐資料已過期，改走下面 fallback 分支，相信本機資料庫。
        if in_active_punish and not override_active_punish:
            local_rec = next((r for r in local_active_records_list if str(r.get('code')) == str(code)), None)
            if local_rec and local_rec.get('period_start'):
                try:
                    local_start = datetime.strptime(local_rec['period_start'], "%Y-%m-%d").date()
                    if local_start > active_punish_map[str(code)]["start"]:
                        override_active_punish = True
                except Exception:
                    pass

        if in_active_punish and not override_active_punish:
            # 優先使用永豐 API 的處置資訊，強制將森崴能源等個股列入處置中
            actually_disposed = True
            disp_start_dt = datetime.combine(active_punish_map[str(code)]["start"], datetime.min.time())
            disp_end_dt = datetime.combine(active_punish_map[str(code)]["end"], datetime.min.time())
            freq = active_punish_map[str(code)]["freq"]
            reason = active_punish_map[str(code)]["reason"] or guess_reason(data)
        else:
            # 備用 fallback 到本地資料庫狀態
            is_in_local_active = any(str(r['code']) == str(code) for r in local_active_records_list)
            db_rec = None
            
            if is_in_local_active:
                actually_disposed = True
                # 更新 disp_start_dt 和 disp_end_dt
                db_rec = next((r for r in local_active_records_list if str(r['code']) == str(code)), None)
                if db_rec:
                    try:
                        if db_rec.get('period_start'):
                            disp_start_dt = datetime.strptime(db_rec['period_start'], "%Y-%m-%d")
                        if db_rec.get('period_end'):
                            disp_end_dt = datetime.strptime(db_rec['period_end'], "%Y-%m-%d")
                    except:
                        pass
            elif (is_disposed or future_period) and disp_start_dt:
                if disp_start_dt.date() <= anchor_dt.date():
                    # Shioaji 沒有、本地資料庫也沒有，那它肯定不是處置股
                    actually_disposed = False
                else:
                    is_future_disposed = True
            else:
                # [Fix 2026-08-31] agg_data 快取是「顯示日」當天的舊底稿，若該股票是
                # 快取產生「之後」才公告要處置（例：3163/3441/4188/6103 於
                # 2026-08-28 公告，但快取是公告前就已存在），快取裡的 is_disposed/
                # future_period 會是空的，上面兩個分支都不會觸發。改查即時從
                # disposal_records 讀出的 disp_map：只要有一筆公告日等於顯示日，
                # 就算快取沒跟上，也視為「即將處置」，不受快取過期影響。
                anchor_iso = anchor_dt.strftime("%Y-%m-%d")
                for rec in disp_map.get(str(code), []):
                    ann_iso = DateUtils.to_iso_date_str(rec.get('announce_date'))
                    if ann_iso == anchor_iso and rec.get('period_start'):
                        try:
                            rec_start_dt = datetime.strptime(rec['period_start'], "%Y-%m-%d")
                        except Exception:
                            continue
                        if rec_start_dt.date() > anchor_dt.date():
                            is_future_disposed = True
                            disp_start_dt = rec_start_dt
                            if rec.get('period_end'):
                                disp_end_dt = datetime.strptime(rec['period_end'], "%Y-%m-%d")
                            future_period = rec.get('period_raw', '') or future_period
                            future_measure = rec.get('measure', '') or future_measure
                            break

            # If we used future_period, we should also use future_measure
            if future_period:
                measure_str = future_measure or data.get("measure", "")
            else:
                measure_str = data.get("measure", "")
                
            if not measure_str and future_measure:
                measure_str = future_measure
            
            # [Fix] 如果 measure_str 為空，但它是處置股，提供預設顯示
            if actually_disposed and db_rec and not measure_str:
                measure_str = db_rec.get("measure", "")
                
            if actually_disposed:
                from core.measure_parser import MeasureParser
                freq = MeasureParser.get_effective_frequency(measure_str, anchor_dt.strftime("%Y-%m-%d"))
            else:
                freq = parse_frequency(measure_str)
            if not freq and actually_disposed:
                freq = measure_str if measure_str.strip() else "處置中"
                
            reason = guess_reason(data)
            if actually_disposed or is_future_disposed:
                current_status = freq

        if actually_disposed or is_future_disposed:
            exit_date = ""
            remaining = 0
            days_elapsed = 0
            days_total = 0
            if disp_end_dt:
                exit_dt = disp_end_dt + timedelta(days=1)
                while not DateUtils.is_trading_day(exit_dt):
                    exit_dt += timedelta(days=1)
                exit_date = exit_dt.strftime("%m-%d")

                count_dt = anchor_dt + timedelta(days=1)
                if is_future_disposed:
                    # 如果是最新盤後公告（尚未生效），從開始日當天起算處置天數
                    count_dt = disp_start_dt

                while count_dt <= disp_end_dt:
                    if DateUtils.is_trading_day(count_dt):
                        remaining += 1
                    count_dt += timedelta(days=1)

                # 已過天數/處置總天數 (跟 Dashboard 的「處置天數」欄同一套工作日算法)
                if disp_start_dt:
                    def _count_trading_days_inclusive(s_dt, e_dt):
                        if not s_dt or not e_dt or s_dt.date() > e_dt.date():
                            return 0
                        n = 0
                        cur = s_dt
                        while cur.date() <= e_dt.date():
                            if DateUtils.is_trading_day(cur):
                                n += 1
                            cur += timedelta(days=1)
                        return n

                    days_total = _count_trading_days_inclusive(disp_start_dt, disp_end_dt)
                    effective_today = disp_end_dt if anchor_dt.date() > disp_end_dt.date() else anchor_dt
                    if effective_today.date() >= disp_start_dt.date():
                        days_elapsed = _count_trading_days_inclusive(disp_start_dt, effective_today)

            display_freq = freq
            if is_future_disposed:
                display_freq = f"{freq} (最新)"

            disposed.append({
                "code": code, "name": name, "suffix": suffix,
                "source": source, "close": close_txt,
                "freq": display_freq,
                "start": disp_start_dt.strftime("%m-%d") if disp_start_dt else "",
                "end": disp_end_dt.strftime("%m-%d") if disp_end_dt else "",
                "exit": exit_date, "remaining": remaining,
                "days_elapsed": days_elapsed, "days_total": days_total,
                "reason": reason,
            })
            # [Fix] 應使用者要求，已處置的股票若是官方注意股，仍應顯示在聽牌區並標示目前狀態
            # 故不在此處 continue
            
        # === 計算四大規則 (含處置期間重置) ===
        clauses_map = data.get("clauses", {})
        filtered_clauses = {}
        for dk, cv in clauses_map.items():
            if cv:
                # 只接受第 1~8 款，「注意」等泛稱不精確，不計入
                valid = [c.strip() for c in cv.split(',')
                         if c.strip() in ['一','二','三','四','五','六','七','八']]
                if valid:
                    filtered_clauses[dk] = ','.join(valid)

        # 1. 預先計算未過濾處置期間的最糟狀況 (Worst-case)
        naive_hist_items = []
        for d in pred_dates_30:
            c_str = filtered_clauses.get(d, "")
            naive_hist_items.append({
                "is_clause1": "一" in c_str,
                "is_any": len(c_str) > 0,
                "is_disposed": False
            })
        _, _, naive_min_needed = DispositionPredictor.analyze(naive_hist_items, future_days=5)

        disposal_periods = []
        has_c2_disp_60 = False
        
        # 2. 如果是重點股票 (聽牌或一進聽或最糟狀況達標)，才去獲取精確的處置歷史
        is_candidate = code in official_set or naive_min_needed <= 2
        
        if is_candidate:
            try:
                records = disp_map.get(code, [])
                for r in records:
                    ps_str = r.get('period_start')
                    pe_str = r.get('period_end')
                    
                    if ps_str and pe_str:
                        try:
                            ps_dt = datetime.strptime(ps_str, "%Y-%m-%d").date()
                            pe_dt = datetime.strptime(pe_str, "%Y-%m-%d").date()
                            
                            # 只有已經發生(或正在發生)的處置才算歷史，不能拿未來處置重置
                            if ps_dt <= anchor_dt.date():
                                disposal_periods.append((ps_dt, pe_dt))

                            if (anchor_dt.date() - ps_dt).days <= 60:
                                r_str = f"{r.get('measure', '')} {r.get('reason', '')}"
                                if "第二次" in r_str or "第2次" in r_str or ("二" in r_str and "款" in r_str) or \
                                   "第二次處置" in r_str or "延長" in r_str:
                                    has_c2_disp_60 = True
                        except:
                            pass
            except:
                pass

        # [2026-10-04] 逐日 history_items 組裝搬到 core/conditions_engine.py，跟儀表板共用
        from core.conditions_engine import build_history_items
        hist_items = build_history_items(clauses_map, disposal_periods, pred_dates_30, anchor_dt)

        trigger_progress = DispositionPredictor.get_trigger_progress(hist_items)
        _, _, min_needed = DispositionPredictor.analyze(hist_items, future_days=5)

        # 呼叫精確預測演算法計算最快進處置頻率與次數
        # [Fix] 初犯/累犯的30個營業日判定基準，不是「今天」，而是「實際掛牌進處置的那一天」：
        # 條件成立當天不會立刻進處置，是下一個交易日才生效，所以要把 min_needed
        # （還差幾個交易日達標）也算進去，才不會把剛好卡在30天邊界的股票分類判斷提早一天。
        #
        # [Fix 2026-09-04] 「目前狀態」跟「進處置」是兩個語意不同的欄位，不能共用同一個
        # anchor_date：
        #   - 「目前狀態」問的是「這支股票『現在』(如果已經在處置中/已公告即將處置)這次
        #     處置本身是初犯還是累犯」，基準應該是這次處置自己的 period_start，不是今天
        #     ——用今天當基準會把「正在進行/即將開始的這次處置」自己誤判成「之前發生過
        #     的處置」，導致它自己被貼上錯誤的「累犯」標籤(3406、6933 已用真實資料驗證：
        #     用今天當 anchor 得到「累犯」，用這次處置自己的 period_start 前一個交易日
        #     當 anchor 才正確算出「初犯」)。
        #   - 「進處置」問的是「如果(不論現在是否已經處置中)之後再觸發一次新的處置，那會
        #     是初犯還是累犯」，這是一個跟『今天』有關的假設性預測，基準本來就該用今天
        #     (+還差幾天觸發)，不該套用「目前狀態」那個修正。
        if (actually_disposed or is_future_disposed) and disp_start_dt:
            _current_status_anchor = DateUtils.get_last_trading_day(
                disp_start_dt - timedelta(days=1)
            ).date()
            _current_status_freq = predict_exact_disposal_frequency(
                code, source, _current_status_anchor, disp_map, days_until_trigger=0
            )
            current_status = _current_status_freq

        _days_until = min_needed if isinstance(min_needed, int) and 0 <= min_needed <= 10 else 0
        enter_freq = predict_exact_disposal_frequency(
            code, source, anchor_dt.date(), disp_map, days_until_trigger=_days_until
        )

        # [Fix 2026-08-24，2026-09-28 修正] 處置中清單也要標初犯/累犯，但這裡問的是
        # 「這次已經在處置中/即將處置的這次，本身是初犯還是累犯」，屬於[2026-09-04]
        # 註解說明的「目前狀態」語意，基準應該用這次處置自己的 period_start，不是用
        # 「今天」當基準的 enter_freq(那是回答另一個問題：「如果之後再觸發一次新的
        # 處置，那會是初犯還是累犯」)。
        # 2026-08-24 當時的假設「enter_freq 已經包含這次，可以直接拿來用」在
        # 2026-09-04 被驗證是錯的(3406、6933 實測：用今天當基準會把正在進行的這次
        # 處置自己誤判成累犯)，[2026-09-04]已經修好 current_status 給「目前狀態」用，
        # 但這裡忘了跟著改，一直沿用舊的 enter_freq，導致處置中清單顯示跟 Dashboard
        # 對不上(2305 案例：Dashboard 用 current_status 顯示「初犯」才對，這裡用
        # enter_freq 顯示成「累犯」；已用真實資料庫紀錄確認 2305 上次處置在
        # 2026-07-07 結束、離這次 2026-09-18 開始超過 30 個營業日，本來就該是初犯)。
        if actually_disposed or is_future_disposed:
            disposed[-1]["enter_freq"] = current_status if current_status and current_status != "非處置" else enter_freq

        # 從 hist_items 判斷是否有第一款 (30日內)
        has_c1_30 = any(item.get("is_clause1", False) for item in hist_items)

        # 計算進處置條件
        calc_results = []
        is_clause2_risk = False
        exclusion_lines = []
        # 所有出現在聽牌清單或接近進處置的股票都計算
        should_calc = is_candidate or code in official_set
        if should_calc:
            if "1467" in code or "南緯" in name:
                print(f"[ForecastWorker] Processing {code} - {name}, should_calc={should_calc}")
            
            from core.conditions_engine import compute_stock_conditions, CONDITIONS_VERSION
            cached_res = data.get("calc_results", [])
            # [Fix 2026-10-04] 舊版快取的分區是錯的(沒傳還差次數，全部當成進處置)，
            # 版本不符就重算，不沿用。
            has_cache = (bool(cached_res) and cached_res != ["無法取得歷史股價"]
                         and data.get("calc_ver") == CONDITIONS_VERSION)

            if is_history and has_cache:
                # 快取優先：如果是歷史日期且已有計算結果，直接取用，不重新執行耗時的 API 下載與重算
                calc_results = data.get("calc_results", [])
                exclusion_lines = data.get("exclusion_lines", [])
            else:
                try:
                    # [Fix 2026-10-04] 跟儀表板 CalculationWorker 呼叫同一支核心函式，
                    # 還差次數由 trigger_progress + 官方聽牌原因決定(見 conditions_engine.py)。
                    cond = compute_stock_conditions(
                        code, source, name, anchor_dt,
                        clauses_map=clauses_map,
                        disp_records=disp_map.get(code, []),
                        official_reason=official_reason_map.get(code, ""),
                    )
                    calc_results = cond["lines"]
                    exclusion_lines = cond["exclusion_lines"]
                    is_clause2_risk = cond["is_clause2_risk"]
                except Exception as e:
                    import traceback
                    err = traceback.format_exc()
                    print(f"[{code}] Calc Error: {err}")
                    calc_results = [f"計算錯誤: {str(e)[:40]}"]

        # 計算最後回補日與剩餘天數
        last_cover_date_str = ""
        cover_remaining_days = -1
        if str(code) in stop_short_data:
            last_cover_date_str = stop_short_data[str(code)].get("last_cover_date", "")
            if last_cover_date_str:
                try:
                    cover_dt = datetime.strptime(last_cover_date_str, "%Y/%m/%d").date()
                    cover_remaining_days = (cover_dt - anchor_dt.date()).days
                except:
                    pass

        # [Fix] 已經在處置中時，仍要顯示條款觸發進度與目前的處置分類(初犯/累犯)，
        # 只是 min_needed 不再具有「還差多少會進處置」的意義，固定設為 99 避免誤判排序。
        # （先前這裡會把 enter_freq/trigger_progress/calc_results 整個清空，
        # 導致已處置股票在表格上完全沒有款項與條件資訊可看。）
        # 強制前的真實值留一份給下面「一進聽」分類用：處置中的股票條款累計不會因為
        # 目前正在處置就歸零，還差2次一樣是有進新一輪處置風險的候選，不該被排除
        # (2026-09-29 使用者確認：一進聽門檻維持「還差2次」，因為差1次會被歸進官方
        # 聽牌清單，兩者互斥)。
        real_min_needed = min_needed
        if actually_disposed:
            min_needed = 99

        record = {
            "code": code, "name": name, "suffix": suffix,
            "source": source, "close": close_txt,
            "current_status": current_status,
            "enter_freq": enter_freq,
            "trigger_progress": trigger_progress,
            "is_clause2_risk": is_clause2_risk,
            "exclusion_lines": exclusion_lines,
            "calc_results": calc_results,
            "min_needed": min_needed,
            "last_cover_date": last_cover_date_str,
            "cover_remaining_days": cover_remaining_days,
        }

        # === 分類 ===
        # [Fix 2026-09-03 撤回] official_set(聽牌)與 min_needed==2(一進聽)本來
        # 就該用 if/elif 互斥——official_set 是官方(TWSE notetrans / TPEx
        # bulletin/warning)的正式聽牌清單，代表累積次數已達/接近處置標準，跟
        # min_needed==2 不該同時成立。之前誤判兩者可以並存，一度改成各自獨立
        # 判斷，後來查明真因是 attention_clauses 缺了 2026-08-20 之後的資料
        # (ClauseDownloadWorker 只能手動觸發)，導致本地累積次數低估、把「其實
        # 只差最後一次」的股票誤算成「還差兩次」，才會看起來需要並存。資料補齊、
        # 且已加上自動更新後，改回 if/elif 互斥。
        if code in official_set:
            listening.append(record)
        elif real_min_needed == 2:
            one_step.append(record)
        # min_needed >= 3 不顯示
        
        # [Fix] 將計算結果寫回 agg_data 準備進行快取
        if code in agg_data:
            agg_data[code]["calc_results"] = calc_results
            agg_data[code]["exclusion_lines"] = exclusion_lines
            if should_calc:
                from core.conditions_engine import CONDITIONS_VERSION
                agg_data[code]["calc_ver"] = CONDITIONS_VERSION
            agg_data[code]["min_needed"] = min_needed
            agg_data[code]["trigger_progress"] = trigger_progress
            # [Fix 2026-08-22] enter_freq(如"2分初犯"/"25分累犯")之前只存在這次執行的
            # record 裡，沒寫回 agg_data，導致沒有進 cache.db 的 agg_cache 表——「雙刀戰法」
            # 專案讀取 agg_cache 當軟依賴時完全拿不到初犯/累犯資訊。補上這欄位供外部消費。
            agg_data[code]["enter_freq"] = enter_freq

    _emit(total, total)

    listening.sort(key=lambda x: (x.get("min_needed", 99), x["code"]))
    one_step.sort(key=lambda x: (x.get("min_needed", 99), x["code"]))
    disposed.sort(key=lambda x: (x.get("remaining", 99), x["code"]))
    
    # [Fix] 儲存快取，解決切換歷史日期過於緩慢的問題
    try:
        from core.cache import CacheManager
        cm = CacheManager()
        cm.save_agg_data(date_str, agg_data)
        print(f"[ForecastWorker] 已將 {date_str} 的計算結果 {len(agg_data)} 筆存入快取")
    except Exception as e:
        print(f"[ForecastWorker] 儲存快取失敗: {e}")

    return {
        "date_str": date_str,
        "listening": listening, "one_step": one_step, "disposed": disposed,
    }
