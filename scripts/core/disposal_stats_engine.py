"""
處置統計頁的逐筆計算核心(原 ui/disposal_stats_page.py 的 StatsWorker.run，2026-10-04 P2 原樣
搬出，不改邏輯)。桌面版 StatsWorker 只剩執行緒與訊號；本模組不依賴 PyQt6。

compute_stats_rows(disposal_records, ...) -> 每筆處置紀錄加上 start_date/end_date/capital/
freq_text/duration_text/offense_text/headers/changes/dates 的 list，並把算好的漲跌幅寫回
disposal_history.db 的 calculated_stats 欄位當快取。
"""
import re
from datetime import datetime, timedelta

from core.utils import DateUtils
from core.runtime import get_paths
from core.finmind_client import FinMindClient
from core.fetcher import StockFetcher
from core.disposal_stats_manager import DisposalStatsManager
from core.disposal_database import DisposalDatabase


def compute_stats_rows(disposal_records, allow_download=True, api_token=None, target_year=None,
                       force_refresh=False, progress=None, should_stop=None):
    """
    allow_download：連線模式(網頁更新)；False 為離線模式，只讀本地快取。
    force_refresh：使用者手動「更新選取資料」，不沿用快取、一律重算。
    progress(msg) 進度文字；should_stop() 回傳 True 就提前結束(桌面版的中斷按鈕)。
    """
    _emit = progress or (lambda msg: None)
    _should_stop = should_stop or (lambda: False)

    """在背景執行數據處理"""
    print(f"DEBUG: StatsWorker run started. Records: {len(disposal_records)}")
    print(f"DEBUG: StatsWorker allow_download: {allow_download}, target_year: {target_year}")
    
    initial_update_mode = allow_download
    
    # [Fix] 使用選定的 API Token 初始化 Manager
    fm_client = FinMindClient(api_token) if api_token else None
    
    # [Fix] 初始化 Fetcher 用於交易日驗證 (使用相同的 API Token)
    fetcher = StockFetcher(db_path=get_paths().prices_db, api_token=api_token)
    stats_manager = DisposalStatsManager(fm_client=fm_client, fetcher=fetcher)
    
    # [New] Worker 自行連接 DB 用於讀寫緩存
    worker_db = DisposalDatabase(get_paths().disposal_db)

    # [Fix 2026-09-16] disposal_records 是用 get_all_records() 取得的，那裡是
    # ORDER BY announce_date DESC(最新公告排最前面)——這對表格顯示很合理，但
    # 對這裡的處理順序卻是最壞的排法：最近公告、真的需要重新查價的那一小撮
    # 紀錄(通常只有 100 筆左右)全部擠在最前面，導致下面的「連續10筆命中快取
    # 就停止下載」智慧停止機制遲遲沒有機會觸發(因為連續的都是慢的)，逼著
    # 這些「真的需要查」的紀錄一開始就全部老老實實逐筆發網路請求，才好不容易
    # 湊到10筆連續命中——使用者回報「按網頁更新要3分多鐘」正是卡在這裡(而不是
    # 總處理量本身的問題：同樣一萬六千多筆，只要讓「早就出關很久、結果不會再
    # 變」的舊紀錄先跑，智慧停止機制在幾十筆內就會觸發，全部跑完只要20幾秒)。
    # 改成在這裡自己依 period_end 由舊到新重新排序(不動 get_all_records()本身，
    # 避免影響其他也呼叫它、需要「最新公告在前」順序的地方，例如表格顯示、
    # 年度篩選)，讓早就出關的舊紀錄先處理、觸發智慧停止，真正需要查的近期
    # 紀錄留到最後，那時 allow_download 通常已經被智慧停止關掉，一樣快速跳過。
    def _sort_key(rec):
        pe = rec.get("period_end") or ""
        return pe
    try:
        disposal_records_ordered = sorted(disposal_records, key=_sort_key)
    except Exception:
        disposal_records_ordered = disposal_records
    disposal_records = disposal_records_ordered

    processed_data = []
    total = len(disposal_records)

    consecutive_cache_hits = 0
    SMART_UPDATE_THRESHOLD = 999999 if force_refresh else (10 if total > 20 else 999999)

    # [Fix 2026-09-01] get_stock_capital() 每次呼叫都會開一個新的 SQLite 連線
    # 查詢股本，但同一支股票在歷史紀錄裡常常出現幾十筆(例如 5314 有 44 筆)，
    # 原本每一筆都重查一次，兩萬多筆紀錄實際上只對應幾千支不重複的股票代號。
    # 用這個 run() 範圍內的簡單字典記住查過的結果，同一支股票只查一次 DB，
    # 這是這次「開軟體自動更新」耗時的主要來源之一。
    # key 含 allow_download，避免「先以 False 查過(可能因未下載而是 None)」
    # 後面又用 True 查同一支股票時，被錯誤的舊快取值蓋住重新下載的機會。
    capital_cache = {}
    def get_capital_cached(code, allow_download):
        key = (code, allow_download)
        if key not in capital_cache:
            capital_cache[key] = stats_manager.get_stock_capital(code, allow_download=allow_download)
        return capital_cache[key]

    # [Fix 2026-09-09] disposal_records 裡有大量「同一檔股票、同一段處置期間
    # (period_start/period_end 完全相同)」的重複列(實測：20763筆裡有3388組、
    # 共4380筆是這種完全重複的紀錄，來自不同時間點的重複抓取/匯入，沒有做
    # 去重)。這些重複列各自獨立算漲跌幅，卻是在問FinMind同一支股票、同一段
    # 完全相同的日期——每次「網頁更新」都要重新對同一組資料發好幾次網路請求，
    # 是耗時的主因之一。用這個 run() 範圍內的字典記住「這段(code, start, end)
    # 已經算過的結果」，同一組合下一次遇到直接沿用，不用重新查價。
    computed_stats_cache = {}

    # [New 2026-09-28] 供「初犯/累犯」欄位判定用：以代號分組的完整處置歷史。
    # 用同一支股票所有紀錄建表一次，之後每筆只查字典，不用每筆都重新掃一次
    # disposal_records。_predict_exact_disposal_frequency() 內部自己會用
    # 日期嚴格篩掉「晚於或等於這筆」的紀錄，所以這裡不用先排序或先過濾。
    disp_map = {}
    for _r in disposal_records:
        _c = str(_r.get("code", "")).upper().replace(".TW", "").replace(".TWO", "").strip()
        if _c.endswith(".0"):
            _c = _c[:-2]
        disp_map.setdefault(_c, []).append(_r)

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

    import json

    for idx, record in enumerate(disposal_records):
        if _should_stop():
            break

        _emit(f"處理中... ({idx + 1}/{total})")
        
        code = record.get("code", "")
        code = str(code).upper().replace(".TW", "").replace(".TWO", "").strip()
        if code.endswith(".0"):
           code = code[:-2]
           
        name = record.get("name", "")
        period = record.get("period_raw", "")
        record_id = record.get("id")
        
        start_date, end_date = stats_manager.parse_disposal_period(period)
        
        # [New] Parse frequency from measure
        measure = record.get("measure", "")
        freq_text = ""
        if measure:
            from core.measure_parser import MeasureParser
            freq_text = MeasureParser.parse_frequency(measure)
        else:
            freq_text = "-"
        
        if not start_date or not end_date:
            continue

        # [New 2026-09-28] 處置天數 (工作日計算，跟 Dashboard/處置預測頁同一套邏輯)
        duration_days = _count_trading_days_inclusive(start_date, end_date)
        duration_text = f"{duration_days}天" if duration_days > 0 else ""

        # [New 2026-09-28] 初犯/累犯 (跟 Dashboard/處置預測頁「目前狀態」同一套邏輯：
        # 以這次處置自己的 period_start 前一個交易日當基準，往前找30個營業日內是否
        # 已有其他處置紀錄——不能用「今天」當基準，那是回答另一個問題，見
        # forecast_page.py 2026-09-04/2026-09-28 的說明)
        offense_text = ""
        try:
            from core.forecast_engine import predict_exact_disposal_frequency
            _anchor = DateUtils.get_last_trading_day(start_date - timedelta(days=1)).date()
            _full_label = predict_exact_disposal_frequency(
                code, record.get("source", ""), _anchor, disp_map, days_until_trigger=0
            )
            offense_text = "累犯" if "累犯" in _full_label else "初犯"
        except Exception as e:
            print(f"DEBUG: 計算 {code} 初犯/累犯失敗: {e}")

        try:
            # [Fix] 跳過「處置尚未開始」的未來股票，避免計算大量未來日期觸發 API HANG 住
            today = datetime.now().date()
            if start_date.date() > today:
                print(f"DEBUG: 跳過未來處置股 {code}（處置開始日: {start_date.date()}，尚未到）")
                result_item = record.copy()
                result_item.update({
                    "code": code,
                    "name": name,
                    "start_date": start_date.strftime("%Y/%m/%d") if start_date else "",
                    "end_date": end_date.strftime("%Y/%m/%d") if end_date else "",
                    "capital": get_capital_cached(code, False),
                    "freq_text": freq_text,
                    "duration_text": duration_text,
                    "offense_text": offense_text,
                    "headers": [],
                    "changes": {},
                    "dates": {},
                })
                processed_data.append(result_item)
                continue
            
            # [Optimization] Check Calculated Stats Cache
            cached_stats_json = record.get("calculated_stats")
            headers = {}
            changes = {}
            dates = {}
            fetched_flag = False

            stats_loaded_from_cache = False

            # [Fix 2026-09-10] 使用者指出：一筆處置紀錄只要「出關+5天」都已經是
            # 很久以前的事(這裡抓20個曆日當緩衝，含跨假日)，就不可能再有新資料
            # 出現——不管當初抓到的結果完不完整，剩下的空缺永遠是那樣了，每次
            # 「網頁更新」還逐筆檢查完整度、甚至重新查價，等於在對「已經蓋棺
            # 定論」的舊資料做白工。改成：只要有算過(calculated_stats 有值，
            # 不論完不完整)且已經沉澱超過這個緩衝期，直接沿用，完全不再檢查
            # 完整度、也不重新抓價；真正需要每次檢查的只有「還在緩衝期內」
            # 的近期紀錄(還有機會補到新一天的資料)。第一次還沒算過的舊紀錄
            # (calculated_stats 全空)不受影響，仍然會算一次，只是算過就不會
            # 再被打擾——這是唯一的一次性成本，之後就徹底解決。
            _settle_buffer_days = 20
            is_settled = (end_date.date() + timedelta(days=_settle_buffer_days)) < today
            if is_settled and cached_stats_json and not force_refresh:
                try:
                    cached_data = json.loads(cached_stats_json)
                    headers = cached_data.get("headers", [])
                    changes = cached_data.get("changes", {})
                    dates = cached_data.get("dates", {})
                    stats_loaded_from_cache = True
                except Exception as e:
                    print(f"DEBUG: Failed to load settled stats cache: {e}")

            if not stats_loaded_from_cache and cached_stats_json and not force_refresh:
                try:
                    cached_data = json.loads(cached_stats_json)

                    # [Fix 2026-09-03] 代號本身就不是合法台股代號(例如編碼壞掉留下的
                    # 5位數亂碼)，FinMind 永遠查不到，之前每次「網頁更新」都會重新對
                    # 這種代號發一次網路請求、卡1-2秒才判定失敗，兩萬多筆裡有3000多筆
                    # 是這種情況，白白浪費時間。查不到就記住(見下面寫入端)，這裡直接
                    # 當作處理完成跳過，不論 allow_download 為何。
                    if cached_data.get("_unresolvable"):
                        stats_loaded_from_cache = True
                        headers, changes, dates = [], {}, {}
                    else:
                        # [Fix] headers is stored as a list, no need to call keys()
                        c_headers = cached_data.get("headers", [])
                        c_changes = cached_data.get("changes", {})
                        c_dates = cached_data.get("dates", {})

                        # [Fix 2026-08-31] 若這筆快取是「處置尚未開始」被跳過、或當時股價
                        # 還沒收盤更新時存的結果，changes 字典雖然有 key，但值全部是 null，
                        # 而現在處置已經開始(甚至結束)了，這個全 null 的快取已經過期，不能
                        # 再沿用，否則-1/處置日等欄位會永遠卡在 N/A，即使股價早就收盤更新
                        # 了。只有「changes 至少有一個非 null 的值」或「處置真的還沒開始」
                        # 才信任快取。
                        has_real_value = any(v is not None for v in c_changes.values())

                        # [Fix 2026-09-03] 「網頁更新」按鈕(allow_download=True)原本無條件
                        # 忽略這份快取、逐筆重新向 FinMind 查價，即使該筆處置早就結束、
                        # -5~+5 全部欄位都已經有值——兩萬多筆歷史紀錄裡真正還缺資料的
                        # 只有少數，其餘的每次點「網頁更新」都被重算一次，是使用者回報
                        # 「按了網頁更新一樣要處理很久」的主因。改成：只要這筆快取已經
                        # 「完整」(所有已經過去的日期欄位都有值，只剩尚未發生的未來日期
                        # 合理地是 null)，不論 allow_download 是否為 True 都直接沿用快取、
                        # 跳過重算；只有「已經過去卻還是 null」(代表之前抓漏了)的紀錄才會
                        # 落到下面 calculate_price_changes() 重新抓取補齊，這樣才真正達到
                        # 「已處理過的舊紀錄直接跳過」的效果，同時保留手動更新原本的用途
                        # (補齊真正缺漏的資料)。
                        today_date = datetime.now().date()
                        is_complete = True
                        for k, v in c_changes.items():
                            if v is not None:
                                continue
                            date_str = c_dates.get(k)
                            if not date_str:
                                is_complete = False
                                break
                            try:
                                d = datetime.strptime(date_str, "%Y-%m-%d").date()
                            except Exception:
                                is_complete = False
                                break
                            if d <= today_date:
                                is_complete = False
                                break

                        if not allow_download:
                            if has_real_value or start_date.date() > today_date:
                                headers, changes, dates = c_headers, c_changes, c_dates
                                stats_loaded_from_cache = True
                        else:
                            if is_complete and (has_real_value or start_date.date() > today_date):
                                headers, changes, dates = c_headers, c_changes, c_dates
                                stats_loaded_from_cache = True
                except Exception as e:
                    print(f"DEBUG: Failed to load cached stats: {e}")
            
            if not stats_loaded_from_cache:
                # [Fix 2026-09-09] 先查本次執行內的重複計算快取(見上方
                # computed_stats_cache 註解)，同一(代號,起始日,結束日)組合
                # 在這次「網頁更新」裡已經算過就直接沿用，不重新發網路請求。
                _dedupe_key = (code, start_date.date().isoformat(), end_date.date().isoformat())
                if _dedupe_key in computed_stats_cache:
                    headers, changes, dates = computed_stats_cache[_dedupe_key]
                    fetched_flag = False
                else:
                    # [Fix 2026-09-18] 2026-09-09 那次把 disposal_records 依出關日
                    # 由舊到新排序，讓「連續10筆命中本地快取」的智慧停止機制能提早
                    # 觸發、加速大量舊紀錄的處理——但這連帶造成一個副作用：真正
                    # 「從來沒算過」的全新紀錄(例如剛公告的處置股，calculated_stats
                    # 完全是空的)因為出關日最晚、被排在最後面處理，如果智慧停止已經
                    # 在處理前面「近期但非全新」的紀錄時被觸發(allow_download
                    # 被設為 False)，這些真正全新的紀錄反而會被跳過，永遠抓不到
                    # 資料、-1/處置日等欄位卡在 N/A(使用者回報：昨天9/17剛公告的
                    # 處置股，百分比一直沒更新上去)。這裡明確判斶：如果這筆紀錄
                    # 完全沒算過(cached_stats_json 是空的，不是「算過但不完整」)、
                    # 且這次執行本來就是連線模式(initial_update_mode，不是離線模式
                    # 從頭就不該連網)，這次呼叫強制用 True，不管智慧停止有沒有把
                    # allow_download 關掉，確保真正全新的紀錄至少被嘗試抓一次；
                    # 離線模式(initial_update_mode=False)完全不受影響，不會意外在
                    # 離線模式下發網路請求。
                    _force_download = allow_download or (initial_update_mode and not cached_stats_json)
                    headers, changes, dates, fetched_flag = stats_manager.calculate_price_changes(
                        code, start_date, end_date, days_after=5,
                        allow_download=_force_download
                    )
                    computed_stats_cache[_dedupe_key] = (headers, changes, dates)

                # [Optimization] Save to DB Cache if valid
                # [Fix 2026-09-03] calculate_price_changes 就算完全查不到資料，回傳的
                # changes 字典也一定帶著 headers 對應的 key(只是值全部是 None)，不是
                # 真的空字典——原本 `if changes:` 只檢查字典是否為空，永遠是 True，
                # 導致下面判斷「是否為查不到資料的亂碼代號」的 elif 分支永遠不會執行。
                # 改成明確檢查「是否有任何一個非 None 的實際數值」才算真的有查到資料。
                has_real_data = changes and any(v is not None for v in changes.values())
                if has_real_data:
                    cache_payload = {
                        "headers": headers,
                        "changes": changes,
                        "dates": dates
                    }
                    try:
                        json_str = json.dumps(cache_payload)
                        if record_id:
                            worker_db.update_calculated_stats(record_id, json_str)
                    except Exception as e:
                        print(f"DEBUG: Failed to save stats cache: {e}")
                elif record_id and not re.fullmatch(r"\d{4}[A-Z]?", code):
                    # [Fix 2026-09-03] 代號格式本身就不是合法台股代號(標準是4位數字，
                    # 可能加一個英文字母後綴)，查不到資料不是暫時性問題(網路/API限流)，
                    # 而是這個代號永遠不可能查到——很可能是早期資料匯入編碼壞掉留下的
                    # 亂碼(例如 45691、68061 這種5位數)。記住「查不到」，下次(不論
                    # 網頁更新或離線模式)直接跳過，不再重新發網路請求浪費時間。只對
                    # 「格式明顯不合法」的代號這樣做，格式正常但暫時查無資料的代號
                    # (可能只是網路問題或還沒有資料)不列入，避免誤判成永久放棄。
                    try:
                        json_str = json.dumps({"headers": [], "changes": {}, "dates": {}, "_unresolvable": True})
                        worker_db.update_calculated_stats(record_id, json_str)
                    except Exception as e:
                        print(f"DEBUG: Failed to save unresolvable marker: {e}")
            
            if allow_download:
                if not fetched_flag and not stats_loaded_from_cache: # if fetched_flag is False, it means data was in stock_prices.db
                    consecutive_cache_hits += 1
                else:
                    consecutive_cache_hits = 0
                
                if consecutive_cache_hits >= SMART_UPDATE_THRESHOLD:
                    print(f"DEBUG: Smart Update triggered. consecutively hit {SMART_UPDATE_THRESHOLD} cached records. Stopping downloads.")
                    allow_download = False
                    _emit(f"已銜接歷史資料，停止下載... ({idx + 1}/{total})")
            
            # Merge custom stats
            custom_stats_json = record.get("custom_stats")
            if custom_stats_json:
                try:
                    custom_stats = json.loads(custom_stats_json)
                    if isinstance(custom_stats, dict):
                        for col_name, val in custom_stats.items():
                            try:
                                if val is not None and str(val).strip():
                                    changes[col_name] = float(val)
                                else:
                                    changes[col_name] = val
                            except ValueError:
                                changes[col_name] = val
                except Exception as e:
                    print(f"解析 custom_stats 失敗: {e}")
            
            # [Fix 2026-09-17] 使用者明確要求：股本不該跟著「網頁更新」的連線模式
            # 一起被動觸發下載，只有使用者自己按「更新股本」按鈕(CapitalWorker)時
            # 才該真的發網路請求查股本。這裡固定傳 False，永遠只讀本地已有的快取
            # (get_stock_capital 內部：allow_download=False 時純讀本地、缺資料就
            # 回 None，不會發網路請求)，不再沿用 allow_download(連線模式時
            # 是 True，會導致每次網頁更新都連帶對幾千支不重複股票代號查一次股本)。
            capital = get_capital_cached(code, False)
            
            result_item = record.copy()
            result_item.update({
                "code": code,
                "name": name,
                "start_date": start_date.strftime("%Y/%m/%d") if start_date else "",
                "end_date": end_date.strftime("%Y/%m/%d") if end_date else "",
                "capital": capital,
                "freq_text": freq_text,
                "duration_text": duration_text,
                "offense_text": offense_text,
                "headers": headers,
                "changes": changes,
                "dates": dates,
            })
            processed_data.append(result_item)

        except Exception as e:
            print(f"DEBUG: 處理 {code} 時發生異常: {e}")
            # 確保就算單筆崩潰，資料還是能放回去，避免整列空白
            result_item = record.copy()
            result_item.update({
                "code": code,
                "name": name,
                "start_date": start_date.strftime("%Y/%m/%d") if start_date else "",
                "end_date": end_date.strftime("%Y/%m/%d") if end_date else "",
                "capital": None,
                "freq_text": freq_text,
                "duration_text": duration_text,
                "offense_text": offense_text,
                "headers": [],
                "changes": {},
                "dates": {},
            })
            processed_data.append(result_item)
    
    # [New] Close worker DB connection
    if worker_db:
         worker_db.close()
    
    print(f"DEBUG: StatsWorker finished. Sending {len(processed_data)} records via data_ready signal")
    return processed_data


def normalize_period_format(period_raw):
    """
    標準化處置期間格式，避免格式不一致導致重複資料

    範例：
    - "114/8/29~114/9/11" → "114/08/29~114/09/11"
    - "114/08/29~114/09/11" → "114/08/29~114/09/11" (不變)
    - "1140829~1140911" → "114/08/29~114/09/11"
      [Fix 2026-09-01] 舊版 TPEx OpenAPI 回傳的 period_raw 是這種不帶斜線的
      緊湊格式(7碼數字：民國年3碼+月2碼+日2碼)，而新版 Web Portal 端點回傳
      帶斜線的格式。同一次處置事件如果先後被兩種來源各存過一次，兩種格式
      字串完全不同，這裡原本遇到緊湊格式會直接放棄正規化、原樣保留，導致
      兩筆其實是同一事件的紀錄產生不同的去重鍵值，在畫面上重複顯示(4971/
      3234/3362 等多檔都遇到)。這裡補上緊湊格式的解析，統一轉成同一種
      斜線格式，兩種來源才會真正對到同一把鍵值。

    Args:
        period_raw: 原始處置期間字串

    Returns:
        標準化後的處置期間字串
    """
    if not period_raw or '~' not in period_raw:
        return period_raw

    try:
        parts = period_raw.split('~')
        normalized_parts = []

        for part in parts:
            part = part.strip()
            # 分割日期部分：114/8/29 → ['114', '8', '29']
            date_parts = part.split('/')
            if len(date_parts) == 3:
                year, month, day = date_parts
                # 補零：'8' → '08', '29' → '29'
                normalized = f"{year}/{month.zfill(2)}/{day.zfill(2)}"
                normalized_parts.append(normalized)
            elif len(date_parts) == 1 and part.isdigit() and len(part) == 7:
                # 緊湊格式：1140829 → 民國114年08月29日
                year, month, day = part[:3], part[3:5], part[5:7]
                normalized_parts.append(f"{year}/{month}/{day}")
            else:
                # 格式異常，保留原始值
                normalized_parts.append(part)

        return '~'.join(normalized_parts)
    except Exception:
        # 解析失敗，返回原始值
        return period_raw

def deduplicate_records(records):
    """
    去除重複記錄，保留資料較完整的版本

    Args:
        records: 記錄列表

    Returns:
        去重後的記錄列表
    """
    seen = {}
    for record in records:
        # [Fix 2026-08-27] 原本用 (Code, AnnounceDate) 當唯一鍵值，但資料庫裡
        # 有大量(全庫掃過有3334組)同一次處置事件(相同 code+處置期間)、
        # announce_date 卻因為來源不同、擷取時間不同而有一兩天落差或格式不同
        # (如"2026/07/29" vs "2026-07-30")的紀錄，導致同一次處置在畫面上被
        # 當成兩筆不同事件重複顯示(完全相同的處置期間、頻率、逐日漲跌%)。
        # 改用 (Code, 標準化處置期間) 當鍵值——這才是真正代表「同一次處置事件」
        # 的識別方式，且跟本檔案其他地方(on_data_ready 合併資料、更新自訂統計)
        # 已經在用的 (code, normalize_period_format(period_raw)) 鍵值一致，不是
        # 這次新發明的邏輯。
        code = str(record.get("code", "")).strip()
        period_raw = record.get("period_raw")

        if period_raw and str(period_raw).strip():
            period_key = normalize_period_format(str(period_raw).strip())
        else:
            # [Fix] period_raw 缺漏(全庫掃過約佔三分之一，多半是2010年以前沒有
            # 解析出處置期間的舊資料)時，不能直接讓它們共用同一把「空字串」
            # 鍵值——那樣同一檔股票所有沒有處置期間資料的舊紀錄會被誤判成
            # 同一次事件、整批合併成一筆，吃掉真正不同次的歷史紀錄。這種情況
            # 才退回用 announce_date 當鍵值(舊邏輯)，只有真的有處置期間可比對
            # 時才用處置期間去重。
            period_key = f"__no_period__{DateUtils.to_iso_date_str(record.get('announce_date', ''))}"

        # Key definition
        key = (code, period_key)

        if key not in seen:
            seen[key] = record
        else:
            # 保留資料較完整的版本 (比較 changes 數量)
            # 如果 changes 數量一樣，保留較新的 (updated_at?) 目前沒 updated_at, 保留後面的?
            # 假設資料庫是 append 的，後面的可能是新的?
            # 但如果是重複抓取，內容可能差不多。

            existing_changes = len(seen[key].get("changes", {}))
            current_changes = len(record.get("changes", {}))

            # Priority 1: More calculated data
            if current_changes > existing_changes:
                seen[key] = record
            # [Fix 2026-08-28] Priority 1.5: changes 數量打平時，優先保留有 announce_date
            # 的版本。改用「代碼+處置期間」去重後才發現：同一次處置常常有一筆
            # announce_date=None 的紀錄(舊資料匯入時沒解析出公告日)混在裡面，
            # changes 算出來的天數通常一樣(反正處置期間相同)，原本這裡打平時就是
            # 誰先被迭代到就留誰，完全沒管公告日有沒有值，導致畫面上出現一堆
            # 「公告日」欄位空白的列(6225/8033/4979等多檔都遇到)。
            elif current_changes == existing_changes:
                existing_ann = str(seen[key].get("announce_date") or "").strip()
                current_ann = str(record.get("announce_date") or "").strip()
                if not existing_ann and current_ann:
                    seen[key] = record
                elif existing_ann and current_ann:
                    # [Fix 2026-08-30] 兩筆都有公告日但日期不一樣時，優先保留較早
                    # 的那個。全庫掃過同一次處置事件常常同時存在兩種公告日紀錄：
                    # 一筆是處置開始前一天(正確——依規定公告隔一個交易日才生效)，
                    # 另一筆恰好等於處置開始日當天(可疑，很可能是某個資料來源沒有
                    # 真的公告日欄位、退回用處置開始日頂替，實測上市/上櫃、各種
                    # 股票都是同一個模式，公告不可能晚於自己生效的那天)。原本這裡
                    # 完全沒比較「兩個都有值但不同」這種情況，誰先被迭代到就留誰，
                    # 導致像4979這種案例畫面上顯示的公告日忽早忽晚、不一致。
                    existing_iso = DateUtils.to_iso_date_str(existing_ann)
                    current_iso = DateUtils.to_iso_date_str(current_ann)
                    if current_iso and existing_iso and current_iso < existing_iso:
                        seen[key] = record

    return list(seen.values())


def sort_change_columns(columns):
    """排序漲跌幅欄位名稱"""
    def sort_key(col):
        if col == "-1": return (0, 0)
        elif col == "處置日": return (1, 0)
        elif col.startswith("+"):
            try: return (2, int(col[1:]))
            except: return (99, 0)
        elif col == "處置結束": return (2, 999)
        elif col == "出關日": return (3, 0)
        elif col.startswith("出-"):
            # 新增：出-5 → 出-4 → 出-3 → 出-2 → 出-1
            try: return (3, int(col[2:]))  # 使用正數讓它從小到大排（出-5 先，出-1 後）
            except: return (99, 0)
        elif col.startswith("出+"):
            try: return (4, int(col[2:]))
            except: return (99, 0)
        else: return (5, 0)
    return sorted(columns, key=sort_key)


def column_numeric_values(records, col_name):
    """某個漲跌幅欄位的數值清單(原 DisposalStatsPage.add_statistics_rows 收集 values 的部分)。"""
    values = []
    for record in records:
        change_value = record.get("changes", {}).get(col_name)
        if change_value is not None:
            if isinstance(change_value, (int, float)):
                values.append(change_value)
            elif isinstance(change_value, str):
                try:
                    val = float(change_value)
                    values.append(val)
                except ValueError:
                    pass
    return values


def summary_stat_texts(values):
    """
    處置統計表底部 10 列(總計、平均、上漲%、下跌%、>9%、<-9%、期望值、獲利因子、風險報酬比、凱利倉位)
    的顯示文字(原 DisposalStatsPage.add_statistics_rows 的計算，2026-10-05 P5 原樣搬出)。
    values 為空回傳 None(畫面顯示 N/A)。網頁版 JS 要與這裡逐字相同。
    """
    if not values:
        return None
    texts = []
    for stat_idx in range(10):
        # 分離上漲和下跌數據
        up_values = [v for v in values if v >= 0]
        down_values = [v for v in values if v < 0]

        if stat_idx == 0:  # 總計
            stat_value = sum(values)
            text = f"{stat_value:+.2f}%"
        elif stat_idx == 1:  # 平均
            stat_value = sum(values) / len(values)
            text = f"{stat_value:+.2f}%"
        elif stat_idx == 2:  # 上漲%
            up_count = sum(1 for v in values if v >= 0)
            stat_value = (up_count / len(values)) * 100
            text = f"{stat_value:.1f}%"
        elif stat_idx == 3:  # 下跌%
            down_count = sum(1 for v in values if v < 0)
            stat_value = (down_count / len(values)) * 100
            text = f"{stat_value:.1f}%"
        elif stat_idx == 4:  # >9%
            count = sum(1 for v in values if v > 9)
            stat_value = (count / len(values)) * 100
            text = f"{stat_value:.1f}%"
        elif stat_idx == 5:  # <-9%
            count = sum(1 for v in values if v < -9)
            stat_value = (count / len(values)) * 100
            text = f"{stat_value:.1f}%"
        elif stat_idx == 6:  # 期望值
            if up_values and down_values:
                avg_up = sum(up_values) / len(up_values)
                avg_down = sum(down_values) / len(down_values)
                win_rate = len(up_values) / len(values)
                expected = win_rate * avg_up + (1 - win_rate) * avg_down
                text = f"{expected:+.2f}%"
            elif up_values:  # 全是上漲
                expected = sum(up_values) / len(up_values)
                text = f"{expected:+.2f}%"
            else:  # 全是下跌
                expected = sum(down_values) / len(down_values)
                text = f"{expected:+.2f}%"
        elif stat_idx == 7:  # 獲利因子
            if up_values and down_values:
                total_profit = sum(up_values)
                total_loss = abs(sum(down_values))
                profit_factor = total_profit / total_loss if total_loss > 0 else 99.99
                text = f"{profit_factor:.2f}倍"
            else:
                text = "99.99倍"  # 全上漲或全下跌
        elif stat_idx == 8:  # 風險報酬比
            if up_values and down_values:
                avg_up = sum(up_values) / len(up_values)
                avg_down = abs(sum(down_values) / len(down_values))
                rr_ratio = avg_up / avg_down if avg_down > 0 else 99.99
                text = f"{rr_ratio:.2f}"
            else:
                text = "99.99"  # 全上漲或全下跌
        else:  # 凱利倉位
            if up_values and down_values:
                avg_up = sum(up_values) / len(up_values)
                avg_down = abs(sum(down_values) / len(down_values))
                win_rate = len(up_values) / len(values)
                kelly = (win_rate * avg_up - (1 - win_rate) * avg_down) / avg_up if avg_up > 0 else 0
                kelly_pct = max(0, kelly * 100)  # 轉為百分比，負值顯示 0
                text = f"{kelly_pct:.1f}%"
            else:
                text = "0.0%"  # 全上漲或全下跌都不建議（保守）
        texts.append(text)
    return texts
