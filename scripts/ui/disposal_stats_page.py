"""
處置統計頁面 UI

提供處置股票統計分析的視覺化界面
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QLineEdit, QComboBox, QFrame, QPushButton,
    QScrollArea, QGridLayout, QFileDialog, QMessageBox, QDateEdit, QApplication,
    QMenu, QCheckBox, QWidgetAction
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QDate, QObject, QTimer
from PyQt6.QtGui import QColor, QBrush
from datetime import datetime, timedelta
import re
from core.utils import DateUtils
from core.runtime import get_paths
from core.finmind_client import FinMindClient
from core.fetcher import StockFetcher  # [New] Import Fetcher

from core.disposal_stats_manager import DisposalStatsManager
from core.disposal_database import DisposalDatabase
from core.history_manager import HistoryManager
from core.cache import CacheManager

class WebUpdateWorker(QThread):
    progress_update = pyqtSignal(str)
    finished_update = pyqtSignal(int, str) # count, error_msg

    def __init__(self, target_year):
        super().__init__()
        self.target_year = target_year

    def run(self):
        try:
            import update_disposal_from_web
            import importlib
            importlib.reload(update_disposal_from_web)
            
            # 傳遞 progress_callback 讓 worker 可以發送進度
            def progress_callback(msg):
                self.progress_update.emit(msg)
                
            count = update_disposal_from_web.update_disposal_from_web(
                target_year=self.target_year, 
                progress_callback=progress_callback
            )
            self.finished_update.emit(count, "")
        except Exception as e:
            self.finished_update.emit(0, str(e))

class StatsWorker(QThread):
    """背景計算統計數據的工作執行緒"""
    progress_update = pyqtSignal(str)
    data_ready = pyqtSignal(list)
    
    def __init__(self, disposal_records, allow_download=True, api_token=None, target_year=None, force_refresh=False):
        super().__init__()
        self.disposal_records = disposal_records
        self.allow_download = allow_download
        self.api_token = api_token
        self.target_year = target_year
        # 使用者手動「更新選取資料」：不沿用任何既有快取、一律重算，也不讓智慧停止機制關掉下載。
        # 否則已沉澱但當初沒抓到資料(漲跌幅全是 null)的舊紀錄，手動更新也永遠只會讀回那份空快取。
        self.force_refresh = force_refresh
    
    def run(self):
        """在背景執行數據處理"""
        print(f"DEBUG: StatsWorker run started. Records: {len(self.disposal_records)}")
        print(f"DEBUG: StatsWorker allow_download: {self.allow_download}, target_year: {self.target_year}")
        
        initial_update_mode = self.allow_download
        
        # [Fix] 使用選定的 API Token 初始化 Manager
        fm_client = FinMindClient(self.api_token) if self.api_token else None
        
        # [Fix] 初始化 Fetcher 用於交易日驗證 (使用相同的 API Token)
        fetcher = StockFetcher(db_path=get_paths().prices_db, api_token=self.api_token)
        self.stats_manager = DisposalStatsManager(fm_client=fm_client, fetcher=fetcher)
        
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
            disposal_records_ordered = sorted(self.disposal_records, key=_sort_key)
        except Exception:
            disposal_records_ordered = self.disposal_records
        self.disposal_records = disposal_records_ordered

        processed_data = []
        total = len(self.disposal_records)

        consecutive_cache_hits = 0
        SMART_UPDATE_THRESHOLD = 999999 if self.force_refresh else (10 if total > 20 else 999999)

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
                capital_cache[key] = self.stats_manager.get_stock_capital(code, allow_download=allow_download)
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
        # self.disposal_records。_predict_exact_disposal_frequency() 內部自己會用
        # 日期嚴格篩掉「晚於或等於這筆」的紀錄，所以這裡不用先排序或先過濾。
        disp_map = {}
        for _r in self.disposal_records:
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

        for idx, record in enumerate(self.disposal_records):
            if self.isInterruptionRequested():
                break

            self.progress_update.emit(f"處理中... ({idx + 1}/{total})")
            
            code = record.get("code", "")
            code = str(code).upper().replace(".TW", "").replace(".TWO", "").strip()
            if code.endswith(".0"):
               code = code[:-2]
               
            name = record.get("name", "")
            period = record.get("period_raw", "")
            record_id = record.get("id")
            
            start_date, end_date = self.stats_manager.parse_disposal_period(period)
            
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
                from ui.forecast_page import ForecastWorker
                _anchor = DateUtils.get_last_trading_day(start_date - timedelta(days=1)).date()
                _full_label = ForecastWorker._predict_exact_disposal_frequency(
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
                if is_settled and cached_stats_json and not self.force_refresh:
                    try:
                        cached_data = json.loads(cached_stats_json)
                        headers = cached_data.get("headers", [])
                        changes = cached_data.get("changes", {})
                        dates = cached_data.get("dates", {})
                        stats_loaded_from_cache = True
                    except Exception as e:
                        print(f"DEBUG: Failed to load settled stats cache: {e}")

                if not stats_loaded_from_cache and cached_stats_json and not self.force_refresh:
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

                            if not self.allow_download:
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
                        # 在處理前面「近期但非全新」的紀錄時被觸發(self.allow_download
                        # 被設為 False)，這些真正全新的紀錄反而會被跳過，永遠抓不到
                        # 資料、-1/處置日等欄位卡在 N/A(使用者回報：昨天9/17剛公告的
                        # 處置股，百分比一直沒更新上去)。這裡明確判斶：如果這筆紀錄
                        # 完全沒算過(cached_stats_json 是空的，不是「算過但不完整」)、
                        # 且這次執行本來就是連線模式(initial_update_mode，不是離線模式
                        # 從頭就不該連網)，這次呼叫強制用 True，不管智慧停止有沒有把
                        # self.allow_download 關掉，確保真正全新的紀錄至少被嘗試抓一次；
                        # 離線模式(initial_update_mode=False)完全不受影響，不會意外在
                        # 離線模式下發網路請求。
                        _force_download = self.allow_download or (initial_update_mode and not cached_stats_json)
                        headers, changes, dates, fetched_flag = self.stats_manager.calculate_price_changes(
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
                
                if self.allow_download:
                    if not fetched_flag and not stats_loaded_from_cache: # if fetched_flag is False, it means data was in stock_prices.db
                        consecutive_cache_hits += 1
                    else:
                        consecutive_cache_hits = 0
                    
                    if consecutive_cache_hits >= SMART_UPDATE_THRESHOLD:
                        print(f"DEBUG: Smart Update triggered. consecutively hit {SMART_UPDATE_THRESHOLD} cached records. Stopping downloads.")
                        self.allow_download = False
                        self.progress_update.emit(f"已銜接歷史資料，停止下載... ({idx + 1}/{total})")
                
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
                # 回 None，不會發網路請求)，不再沿用 self.allow_download(連線模式時
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
        self.data_ready.emit(processed_data)

class CapitalWorker(QThread):
    """背景更新股本數據的工作執行緒"""
    progress_update = pyqtSignal(str)
    finished = pyqtSignal(int, int) # updated_count, error_count

    def __init__(self, codes: list, api_token=None):
        super().__init__()
        self.codes = list(set(codes)) 
        self.api_token = api_token
        
    def run(self):
        fm_client = FinMindClient(self.api_token) if self.api_token else None
        
        # [Fix] 傳入 fetcher for consistency
        fetcher = StockFetcher(db_path=get_paths().prices_db, api_token=self.api_token)
        manager = DisposalStatsManager(fm_client=fm_client, fetcher=fetcher)
        
        total = len(self.codes)
        updated = 0
        errors = 0
        
        for i, code in enumerate(self.codes):
            if self.isInterruptionRequested():
                break
                
            self.progress_update.emit(f"更新股本: {code} ({i+1}/{total})")
            
            cap = manager.get_stock_capital(code, allow_download=True)
            if cap is not None:
                updated += 1
            else:
                pass
                
        self.finished.emit(updated, errors)


class DisposalStatsPage(QWidget):
    """處置統計頁面"""
    
    def __init__(self, history_manager=None):
        super().__init__()
        self.history_manager = history_manager if history_manager else HistoryManager()
        self.stats_manager = DisposalStatsManager()
        self.cache_manager = CacheManager()
        
        # 初始化資料庫
        self.disposal_db = DisposalDatabase(get_paths().disposal_db)
        self.all_records = []
        self.filtered_data = []
        
        # [Fix] Pre-initialize ShioajiClient in the main thread to prevent COM/C++ deadlocks
        # when background threads create the singleton and then die.
        try:
            from core.shioaji_client import ShioajiClient
            import shioaji as sj
            # Only create the object to bind event loop to main thread. Do not login here to save time.
            ShioajiClient().api = sj.Shioaji()
        except Exception as e:
            print(f"DEBUG: Pre-initialize ShioajiClient failed: {e}")
        
        # CB 資料庫
        self.cb_db = None
        try:
            from core.cb_data import CBDatabase
            self.cb_db = CBDatabase()
        except Exception:
            pass
        
        # 期貨資料庫
        self.mf_db = None
        try:
            from core.margin_futures_db import MarginFuturesDatabase
            self.mf_db = MarginFuturesDatabase()
        except Exception:
            pass
        
        self.change_columns = []  # 漲跌幅欄位名稱（動態）
        self.worker = None # 初始化 worker 為 None
        
        # 資料儲存
        self.all_records = [] # 原始資料 (Master Copy, 從 DB 載入的所有資料)
        self.data = [] # 目前顯示的資料 (可能僅包含 filtered_data，為相容舊代碼保留)
        self.filtered_data = [] # 過濾後的資料 (用於排序與顯示)

        # 排序狀態
        self.sort_col = -1
        self.sort_order = Qt.SortOrder.AscendingOrder
        self.column_na_filters = {}  # 記錄每個欄位的 N/A 過濾狀態 {欄位索引: True/False}
        self.hidden_freqs = set()    # 紀錄隱藏的頻率 (供頻率欄位過濾使用)
        self.hidden_durations = set()  # 紀錄隱藏的處置天數 (供處置天數欄位過濾使用)
        self.hidden_offenses = set()   # 紀錄隱藏的初犯/累犯 (供初犯/累犯欄位過濾使用)
        
        # API Tokens (User Provided, from .env via FinMindClient)
        self.API_TOKENS = FinMindClient.get_token_dict()
        
        self.init_ui()
        
        # 自動載入資料 - 離線模式 (download_missing=False)
        # 不會自動下載缺失數據，只顯示資料庫現有資料
        # 使用者需手動點「更新資料」按鈕或右鍵選單才會下載
        self.load_data_from_db(download_missing=False)
    
    def init_ui(self):
        """初始化 UI"""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.main_widget_content = QWidget()
        layout = QVBoxLayout(self.main_widget_content)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.main_scroll.setWidget(self.main_widget_content)
        outer_layout.addWidget(self.main_scroll)
        
        # 工具列
        toolbar = QHBoxLayout()
        
        # 搜尋框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜尋股號/股名...")
        self.search_input.setFixedWidth(120) # 縮小寬度以容納 API 選擇
        self.search_input.textChanged.connect(self.apply_filters)
        toolbar.addWidget(QLabel("搜尋:"))
        toolbar.addWidget(self.search_input)
        
        # [New] Filter Checkbox
        self.only_stocks_checkbox = QCheckBox("僅普通股(4碼)")
        self.only_stocks_checkbox.setChecked(True) # Default Checked
        self.only_stocks_checkbox.stateChanged.connect(self.apply_filters)
        toolbar.addWidget(self.only_stocks_checkbox)
        
        # API 選擇
        self.api_combo = QComboBox()
        self.api_combo.addItems(list(self.API_TOKENS.keys()))
        # 預設選第三個 (stujackwang7845) 或第一個 (sturainbowsperm)?
        # 為了方便，預設第一個就好，或者選最常用的？User request didn't specify default.
        # Let's verify list order via .keys() is implicitly insertion order in Python 3.7+.
        toolbar.addWidget(QLabel("API使用者:"))
        toolbar.addWidget(self.api_combo)
        
        # 排序選擇
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["處置開始日 (新→舊)", "處置開始日 (舊→新)", "股號"])
        self.sort_combo.currentIndexChanged.connect(self.apply_filters)
        toolbar.addWidget(QLabel("排序:"))
        toolbar.addWidget(self.sort_combo)

        # [New] 年度過濾 (更新用)
        toolbar.addWidget(QLabel("更新年度:"))
        self.year_combo = QComboBox()
        years = ["全部"] + [str(y) for y in range(2026, 2019, -1)]
        self.year_combo.addItems(years)
        toolbar.addWidget(self.year_combo)

        
        # 重新載入按鈕
        self.reload_btn = QPushButton("🔄 更新資料 (續傳)")
        self.reload_btn.setToolTip("開始或繼續下載歷史資料")
        self.reload_btn.setIcon(QApplication.style().standardIcon(
            QApplication.style().StandardPixmap.SP_BrowserReload
        ))
        self.reload_btn.clicked.connect(self.on_reload_btn_clicked)
        
        # 股本更新按鈕 (新)
        self.cap_btn = QPushButton("💰 更新股本")
        self.cap_btn.setToolTip("僅更新資料庫中股票的股本資料")
        self.cap_btn.clicked.connect(self.update_capital_only)
        
        toolbar.addWidget(self.reload_btn)
        toolbar.addWidget(self.cap_btn)
        toolbar.addWidget(self.reload_btn)

        # 停止按鈕
        self.stop_btn = QPushButton("⛔ 中斷更新")
        self.stop_btn.clicked.connect(self.stop_loading)
        self.stop_btn.setEnabled(False)
        toolbar.addWidget(self.stop_btn)
        
        # 匯入更新按鈕
        self.import_btn = QPushButton("📥 匯入CSV")
        self.import_btn.clicked.connect(self.import_csv_update)
        toolbar.addWidget(self.import_btn)
        
        self.web_update_btn = QPushButton("🌐 網頁更新")
        self.web_update_btn.clicked.connect(self.update_from_web)
        toolbar.addWidget(self.web_update_btn)
        
        # [New] 更新說明按鈕
        self.help_btn = QPushButton("❓ 說明")
        self.help_btn.setToolTip("查看各更新功能的使用說明")
        self.help_btn.clicked.connect(self.show_update_help)
        toolbar.addWidget(self.help_btn)
        
        toolbar.addWidget(QLabel("|"))  # 分隔線
        
        # 日期範圍選擇
        # 日期範圍選擇
        toolbar.addWidget(QLabel("開始日:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate(2000, 1, 1))  # 預設 2000/01/01 以顯示所有資料
        toolbar.addWidget(self.start_date_edit)
        
        toolbar.addWidget(QLabel("結束日:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())  # 預設今天
        toolbar.addWidget(self.end_date_edit)
        
        # [New] 套用按鈕
        apply_btn = QPushButton("套用")
        apply_btn.clicked.connect(self.apply_filters)
        toolbar.addWidget(apply_btn)
        
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        # 表格
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection) # 支援多選
        self.table.setSortingEnabled(False) # 禁用原生排序 (改用自定義排序以保持統計行在底部)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self.on_header_clicked)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        
        # 右鍵選單與編輯
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.itemChanged.connect(self.on_item_changed)
        
        # 表頭右鍵選單
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.show_header_context_menu)
        
        layout.addWidget(self.table)
        
        self.status_label = QLabel("準備就緒")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)
    
    def stop_loading(self):
        """停止載入數據"""
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status_label.setText("正在停止...")
            self.stop_btn.setEnabled(False)
    
    def on_worker_finished(self):
        """工作完成或停止時的處理"""
        self.reload_btn.setEnabled(True)
        if hasattr(self, 'cap_btn'): self.cap_btn.setEnabled(True)
        if hasattr(self, 'import_btn'): self.import_btn.setEnabled(True)
        if hasattr(self, 'web_update_btn'): self.web_update_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        current_count = self.table.rowCount() - 4 if self.change_columns else self.table.rowCount()
        if current_count < 0: current_count = 0
        
        total_count = len(self.all_records) if hasattr(self, 'all_records') else 0
        self.status_label.setText(f"就緒 (顯示 {current_count} 筆 / 總共 {total_count} 筆)")

        # [Fix] 如果剛載入完，自動執行一次 apply_filters 以確保 label 和 filter 狀態一致
        # (但這可能會再次觸發 table repaint，視情況而定。其實 apply_filters 會自己更新 label，
        # 所以更簡單的做法是直接呼叫 apply_filters 更新 label? 不，finished 是最後一步，
        # apply_filters 已經在 on_data_ready 呼叫過了。這裡只是為了把狀態從 "處理中" 改成 "就緒"。
        # 所以直接改 setText 即可。)

        # [Fix 2026-08-30] auto_refresh_on_startup() 串起來的整條鏈(網頁更新→補齊漲跌幅
        # 計算)最終會走到這裡結束，通知呼叫端(main_window.py)可以接著刷新依賴同一份
        # disposal_history.db 的「統計圖表」分頁了。用完就清掉，避免使用者之後手動點
        # 「更新資料」時又誤觸發一次。
        callback = getattr(self, '_auto_refresh_on_complete', None)
        self._auto_refresh_progress_callback = None
        if callback:
            self._auto_refresh_on_complete = None
            try:
                callback()
            except Exception as e:
                print(f"[DisposalStatsPage] auto_refresh_on_startup 完成回呼失敗: {e}")

    def auto_refresh_on_startup(self, on_complete=None, progress_callback=None):
        """
        [Fix 2026-08-30] 開啟軟體時自動觸發一次「跟按了網頁更新+更新資料一樣」的
        完整刷新流程，取代原本只能靠使用者每天手動點兩個按鈕才會有最新資料的行為。
        跟 forecast_page.py/dashboard.py 開啟軟體就自動抓最新資料的體驗一致。

        流程：update_from_web(auto=True) 抓最新處置公告寫入 disposal_history.db
        → on_web_update_finished 自動接著跑 load_data_from_db() 補齊漲跌幅計算
        → on_worker_finished 收尾時呼叫 on_complete(如果有給)，讓呼叫端接著刷新
        依賴同一份 DB 的「統計圖表」分頁，避免兩邊同時讀寫 SQLite 檔案互相卡到。

        全程靜默(見 update_from_web 的 auto 參數)：不彈確認對話框、不彈完成提示，
        只更新頁面自己的狀態列文字，使用者不會被打斷。

        [Fix 2026-09-01] processed_records 有近 4500 筆，光是「離線模式」不下載
        也要跑 60-90 秒，這段時間表格是空的、頁面自己的狀態列文字使用者又不一定
        看得到（可能還沒切到這個分頁），容易誤以為「沒在動」。加一個可選的
        progress_callback，讓 main_window.py 可以把同一份進度文字（例如「處理中...
        (1234/4489)」）也接到開啟軟體時的主畫面狀態列，不用切分頁就看得到進度。
        """
        self._auto_refresh_on_complete = on_complete
        self._auto_refresh_progress_callback = progress_callback
        self._emit_status_throttle_count = 0

        # [Fix 2026-09-03] __init__ 已經在建構分頁時呼叫過一次 load_data_from_db()
        # 啟動了離線模式的 StatsWorker，全庫兩萬多筆紀錄要跑好幾分鐘。如果這裡直接
        # 呼叫 update_from_web(auto=True)，走到 on_web_update_finished 還是會呼叫
        # load_data_from_db()，裡面看到「worker 還在跑」就會直接中斷丟棄目前的進度、
        # 從 0 重新跑一次一模一樣的兩萬多筆——等於同一份工作做兩遍，時間直接翻倍，
        # 這是使用者回報「開軟體/按網頁更新還是要處理很久」的主因之一。改成：如果
        # 偵測到 __init__ 那個 worker 還沒跑完，先等它自然跑完（不浪費已經做的進度），
        # 才開始 update_from_web(auto=True) 抓最新公告、觸發真正的自動刷新。
        if self.worker and self.worker.isRunning():
            self.worker.finished.connect(lambda: self.update_from_web(auto=True))
            return

        self.update_from_web(auto=True)

    def _emit_status(self, text):
        """同時更新頁面自己的狀態列，以及(若有提供)外部的進度回呼。

        [Fix 2026-09-01] 這裡原本每一筆紀錄(近2萬筆)都會直接同步呼叫一次外部
        progress_callback，而 main.py 的 update_splash() 裡有呼叫
        app.processEvents()——這會讓 Qt 在 _emit_status 這次呼叫「還沒返回」時
        就提前處理下一個排隊中的 progress_update 訊號，一路巢狀遞迴下去，兩萬筆
        訊號疊出兩萬層的呼叫堆疊，最終堆疊溢位讓整個視窗直接崩潰關閉（原生層級
        的堆疊耗盡，不會留下可攔截的 Python 例外訊息，實測重現過)。
        改成兩層防護：
        1. 節流：外部回呼固定每處理 50 筆才轉發一次(頁面自己的狀態列不受影響，
           每筆都照樣即時更新，因為單純 setText 不會遞迴)。
        2. 用 QTimer.singleShot(0, ...) 把外部回呼排程到「下一輪事件迴圈」才執行，
           而不是在目前這個訊號處理的呼叫堆疊裡直接往下呼叫——這樣即使外部回呼
           內部又呼叫 processEvents()，頂多只會處理當下佇列裡的訊號，不會疊加在
           這次呼叫的堆疊之上，徹底切斷遞迴鏈，不只是降低頻率而已。
        """
        self.status_label.setText(text)
        cb = getattr(self, '_auto_refresh_progress_callback', None)
        if cb:
            count = getattr(self, '_emit_status_throttle_count', 0) + 1
            self._emit_status_throttle_count = count
            if count % 50 != 0:
                return

            def _deferred_call(cb=cb, text=text):
                try:
                    cb(text)
                except Exception as e:
                    print(f"[DisposalStatsPage] progress_callback error: {e}")

            QTimer.singleShot(0, _deferred_call)

    def update_year_filter_options(self, records):
        """根據現有資料更新年份選項"""
        years = set()
        for record in records:
            period = str(record.get("period_raw") or "")
            start_date = DateUtils.parse_period_start(period)
            if start_date:
                years.add(str(start_date.year))
        
        # 確保包含至少 2020-2026 (若無資料時)
        for y in range(2020, 2027):
            years.add(str(y))
            
        sorted_years = sorted(list(years), key=lambda x: int(x), reverse=True)
        
        current_selection = self.year_combo.currentText()
        
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItem("全部")
        self.year_combo.addItems(sorted_years)
        
        # Restore selection if possible
        index = self.year_combo.findText(current_selection)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)
        else:
            # Default to "All" (which is usually index 0 since we prepend it)
            self.year_combo.setCurrentIndex(0)
            
        self.year_combo.blockSignals(False)

    def on_reload_btn_clicked(self):
        """「更新資料(續傳)」按鈕點擊處理。

        [Fix 2026-08-30] 跟 update_from_web(auto=False) 一樣，手動點這顆按鈕視為
        「使用者自己接管了」，要先清掉可能還殘留的 auto_refresh_on_startup 一次性
        callback——不然萬一是自動刷新卡在網路階段還沒收尾，使用者這時直接點這顆
        按鈕繞過 update_from_web 直接觸發 load_data_from_db，等這次手動操作的
        worker 跑完進 on_worker_finished 時，會誤觸發那個跟這次操作無關的舊 callback
        (把統計圖表分頁重新整理一次)。
        """
        self._auto_refresh_on_complete = None
        self.load_data_from_db(download_missing=True)

    def load_data_from_db(self, download_missing=True):
        """從資料庫載入處置數據"""
        if self.worker and self.worker.isRunning():
            print("DEBUG: Worker is already running. Requesting interruption...")
            dying = self.worker
            dying.requestInterruption()
            # [Fix] 移除 dying.wait() 避免 FinMind 卡住時造成 UI 執行緒死鎖(同步阻塞等待)
            # [Fix 2026-09-16] finished 訊號原本連到 on_worker_finished，它會檢查並觸發
            # _auto_refresh_on_complete 回呼(串接「統計圖表」分頁刷新)——如果不切斷，
            # dying worker(帶著不完整/中途被打斷的結果)真正結束時就會提早、錯誤地觸發
            # 這個「全部完成」回呼。三個訊號(progress_update/data_ready/finished)全部
            # 斷開，確保 dying worker 徹底跟外界的任何既有連線脫鉤，之後只重新接上
            # 下面這個「等它結束後才開始載入新資料」專用的 finished 監聽，不會有任何
            # 舊的 handler 跟著誤觸發。
            try:
                dying.progress_update.disconnect()
                dying.data_ready.disconnect()
                dying.finished.disconnect()
            except Exception:
                pass

            if not hasattr(self, '_dying_workers'):
                self._dying_workers = []
            self._dying_workers.append(dying)
            self.worker = None

            # [Fix 2026-09-16] requestInterruption() 只是設一個旗標，dying worker 的
            # run() 迴圈要跑到下一次 isInterruptionRequested() 檢查點才會真的停止；
            # 在那之前它還在跑，還會繼續對 disposal_history.db 逐筆寫 calculated_stats。
            # 原本這裡「中斷完立刻蓋掉 self.worker、馬上開一個新的 StatsWorker」，會讓
            # 兩個 worker 各自開自己的 sqlite3 連線同時寫同一個資料庫，嚴重的鎖競爭
            # 讓新 worker 一筆一筆都卡住——這正是使用者回報「按網頁更新要3分多鐘」的
            # 根因：DisposalStatsPage 建構時自己就先觸發過一次離線模式載入(舊worker還
            # 沒跑完)，緊接著手動點「網頁更新」，兩個 worker 就這樣互相打架。改成：
            # 不要立刻開新worker，改成掛在 dying worker 自己的 finished 訊號上(它真的
            # 執行緒結束時會發出)，等它徹底停止、確定沒有第二個連線在寫DB了，才開始
            # 真正載入。這是非同步訊號等待，不會阻塞UI，跟之前拿掉的同步 wait() 是
            # 兩回事，不會重蹈 FinMind 卡住鎖死UI的覆轍。
            dying.finished.connect(lambda: self._load_data_from_db_impl(download_missing))
            return

        self._load_data_from_db_impl(download_missing)

    def _load_data_from_db_impl(self, download_missing=True):
        mode_text = "連線模式" if download_missing else "離線模式"
        print(f"DEBUG: load_data_from_db called. Mode: {mode_text}, Download: {download_missing}")
        
        self.status_label.setText(f"正在從資料庫讀取數據 ({mode_text})...")
        self.reload_btn.setEnabled(False)
        if hasattr(self, 'cap_btn'): self.cap_btn.setEnabled(False)
        if hasattr(self, 'import_btn'): self.import_btn.setEnabled(False)
        if hasattr(self, 'web_update_btn'): self.web_update_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        # 清空表格以提供明確的視覺回饋 (更新中)
        if download_missing:
            self.table.setRowCount(0)
        
        try:
            records = self.disposal_db.get_all_records()
            print(f"DEBUG: Retrieved {len(records)} records from DB.")
            
            # [New] Update Years
            self.update_year_filter_options(records)
            
            if not records:
                self.status_label.setText("資料庫中無處置數據")
                self.reload_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                return
            
            self.status_label.setText(f"正在計算 {len(records)} 筆紀錄的漲跌幅 ({mode_text})...")
            
            # 取得選擇的 API Token
            selected_user = self.api_combo.currentText()
            api_token = self.API_TOKENS.get(selected_user)
            
            # 取得目標年度
            # 離線模式：總是載入所有資料（忽略UI年度選擇）
            # 更新模式：使用UI年度選擇（加速下載）
            if download_missing:
                target_year = self.year_combo.currentText()
            else:
                target_year = "全部"  # 離線模式忽略年度選擇
            
            self.worker = StatsWorker(records, allow_download=download_missing, api_token=api_token, target_year=target_year)
            self.worker.progress_update.connect(self._emit_status)
            self.worker.data_ready.connect(self.on_data_ready)
            self.worker.finished.connect(self.on_worker_finished)
            self.worker.start()
            print("DEBUG: StatsWorker started.")
            
        except Exception as e:
            self.status_label.setText(f"載入失敗: {e}")
            print(f"Error loading from database: {e}")
            self.reload_btn.setEnabled(True)
            if hasattr(self, 'cap_btn'): self.cap_btn.setEnabled(True)
            if hasattr(self, 'import_btn'): self.import_btn.setEnabled(True)
            if hasattr(self, 'web_update_btn'): self.web_update_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            
    
    @staticmethod
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
    
    @staticmethod
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
                period_key = DisposalStatsPage.normalize_period_format(str(period_raw).strip())
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
    
    def on_data_ready(self, processed_data):
        """接收處理完成的數據並顯示"""
        print(f"DEBUG: Data ready. Rows: {len(processed_data)}")
        print(f"DEBUG: Current all_records before merge: {len(self.all_records) if hasattr(self, 'all_records') and self.all_records else 0}")

        # [Fix 2026-08-30] 這次剛算完的 processed_data 本身也可能含同一次處置事件的
        # 多筆公告日變體(見 deduplicate_records 的說明)。下面的合併邏輯(update_map)
        # 用簡單的 dict comprehension 建表，是「後蓋前」——只是剛好因為
        # get_all_records() 用 announce_date DESC 排序，日期較早(較正確)的那筆通常
        # 會排在後面而「意外」蓋掉較晚的，不是真的按 deduplicate_records 那套「留最早
        # 公告日/資料較完整」的規則來選。一旦 SQL 排序或資料筆數改變，這個巧合就會
        # 失效，導致原本已經在畫面上正確顯示的公告日/漲跌幅，在下一次自動刷新(合併)
        # 之後被换成錯的一筆蓋掉(使用者回報「開啟後一開始正常，後來自動刷新跳成
        # N/A」)。改成合併前先對這批新資料自己做一次正式去重，讓合併永遠是拿「已經
        # 選好、較正確」的那筆去比對/覆蓋，不再依賴 SQL 排序的巧合。
        processed_data = self.deduplicate_records(processed_data)

        # [Fix] 合併數據而非替換 (解決中止更新時資料遺失問題)
        if not self.all_records:
            print(f"DEBUG: First load, setting all_records to processed_data")
            self.all_records = processed_data
        else:
            print(f"DEBUG: Merging data...")
            # 建立 map 加速查找（使用標準化的 period_raw）
            update_map = {
                (r["code"], self.normalize_period_format(r["period_raw"])): r
                for r in processed_data
            }
            
            # 1. 更新現有資料
            for i, record in enumerate(self.all_records):
                 key = (record["code"], self.normalize_period_format(record["period_raw"]))
                 if key in update_map:
                     self.all_records[i] = update_map[key]
                     del update_map[key] # 移除已處理的
            
            # 2. 加入新資料 (如果有)
            if update_map:
                 print(f"DEBUG: Adding {len(update_map)} new records")
                 self.all_records.extend(list(update_map.values()))
            
            print(f"DEBUG: Merge complete. all_records now has {len(self.all_records)} records")
        
        # 3. 去除可能存在的重複資料
        original_count = len(self.all_records)
        self.all_records = self.deduplicate_records(self.all_records)
        deduped_count = len(self.all_records)
        
        if deduped_count < original_count:
            print(f"DEBUG: Removed {original_count - deduped_count} duplicate records")

        print(f"DEBUG: About to call apply_filters()")
        self.apply_filters() # 應用初始過濾與排序
        print(f"DEBUG: apply_filters() completed")

    
    def import_csv_update(self):
        """匯入CSV檔案以更新資料庫"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "選擇CSV檔案", "", "CSV檔案 (*.csv)"
        )
        
        if not file_path:
            return
        
        # 詢問來源
        reply = QMessageBox.question(
            self, "選擇來源", 
            f"此檔案是：\n上市 → 點擊 Yes\n上櫃 → 點擊 No",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        source = "上市" if reply == QMessageBox.StandardButton.Yes else "上櫃"
        
        try:
            self.status_label.setText(f"正在匯入 {source} 資料...")
            
            # 匯入到資料庫
            skip_rows = 2 if source == "上櫃" else 1
            count = self.disposal_db.import_from_csv(file_path, source, skip_rows=skip_rows)
            
            self.status_label.setText(f"✓ 成功匯入 {count} 筆 {source} 資料")
            
            # 重新載入顯示 (預設開啟下載，以補齊漲跌幅)
            self.load_data_from_db()
            
            QMessageBox.information(self, "匯入完成", f"成功匯入 {count} 筆 {source} 處置資料！")
            
        except Exception as e:
            self.status_label.setText(f"匯入失敗: {e}")
            QMessageBox.warning(self, "匯入失敗", f"錯誤：{e}")
    
    def load_data(self):
        """向後兼容的方法"""
        self.load_data_from_db()
    


    def update_from_web(self, auto=False):
        """從網頁更新資料庫

        auto=True：開啟軟體時自動觸發的靜默更新(見 auto_refresh_on_startup)，
        不彈確認對話框、完成後也不彈完成提示，只更新狀態列文字——跟
        forecast_page.py/dashboard.py 開啟軟體就自動刷新的體驗一致。
        """
        if not auto:
            # [Fix 2026-08-30] 防禦：萬一先前的自動刷新(auto_refresh_on_startup)因為
            # 網路卡住遲遲沒有完成、_auto_refresh_on_complete 還留著沒被清掉，使用者
            # 這時自己手動點了「網頁更新」，不該讓這次跟自動刷新無關的手動操作結束時
            # 意外觸發那個殘留的 callback(把統計圖表分頁重新整理一次)。手動觸發時先
            # 清掉，視為「使用者自己接管了，原本排隊的自動刷新收尾動作作廢」。
            self._auto_refresh_on_complete = None

        # 取得目前選擇的年度
        selected_year = self.year_combo.currentText()
        if selected_year == "全部":
            msg_scope = "最新 (今日)"
            target_year = None
        else:
            msg_scope = f"{selected_year} 全年度"
            target_year = selected_year

        if not auto:
            reply = QMessageBox.question(
                self, "確認更新",
                f"從 TWSE/TPEX 官網抓取 {msg_scope} 處置資料並更新資料庫？\n這可能需要幾秒鐘或更久...",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        self._web_update_auto = auto

        # UI 狀態鎖定
        self.status_label.setText(f"正在從網頁抓取 {msg_scope} 資料...")
        self.web_update_btn.setEnabled(False)
        self.reload_btn.setEnabled(False)
        if hasattr(self, 'cap_btn'): self.cap_btn.setEnabled(False)
        if hasattr(self, 'import_btn'): self.import_btn.setEnabled(False)

        # 建立並啟動背景工作執行緒
        self.web_worker = WebUpdateWorker(target_year)
        self.web_worker.progress_update.connect(self._emit_status)
        self.web_worker.finished_update.connect(self.on_web_update_finished)
        self.web_worker.start()

    def on_web_update_finished(self, count, error_msg):
        # [Fix 2026-08-30] 原本這裡就先把按鈕解鎖，但下面不管哪個分支都還會接著呼叫
        # load_data_from_db() 啟動第二階段的 StatsWorker——按鈕解鎖跟第二階段開始之間
        # 有個空檔，使用者這時候點「更新資料」會在舊 worker 還沒跑完時又啟動一個新的，
        # 兩個 worker 同時讀寫 disposal_history.db、self.worker 參照也會被新的蓋掉。
        # 改成沿用 on_worker_finished(第二階段真正結束時)才統一解鎖，那裡本來就會
        # 解鎖這四個按鈕，不用在這裡重複做、也不會有這個中間空檔。
        auto = getattr(self, '_web_update_auto', False)

        if error_msg:
            self.status_label.setText(f"網頁更新失敗: {error_msg}")
            if not auto:
                QMessageBox.warning(self, "更新失敗", f"錯誤：{error_msg}")
                # 手動觸發且失敗，不會再啟動第二階段 worker(不會走到
                # on_worker_finished)，這裡就要自己解鎖按鈕，不能指望別處收尾。
                self.web_update_btn.setEnabled(True)
                self.reload_btn.setEnabled(True)
                if hasattr(self, 'cap_btn'): self.cap_btn.setEnabled(True)
                if hasattr(self, 'import_btn'): self.import_btn.setEnabled(True)
            else:
                # [Fix] 自動更新失敗(例如剛好離線)不該卡住後續流程——沿用資料庫現有
                # 資料重新整理一次畫面，至少呈現「離線可用」的最新狀態，並讓
                # auto_refresh_on_startup 的完成回呼(串接統計圖表刷新)照常觸發。
                # 按鈕由接下來啟動的第二階段 worker 結束時(on_worker_finished)解鎖。
                self.load_data_from_db(download_missing=False)
        else:
            self.status_label.setText(f"✓ 網頁更新完成：{count} 筆")
            if not auto:
                QMessageBox.information(self, "更新完成", f"成功從網頁更新 {count} 筆處置資料！")
                # 手動點擊才補齊缺漏的漲跌幅資料(逐檔向 FinMind 查價，全庫兩萬多筆
                # 要花非常久，只有使用者主動要求時才值得等)。
                self.load_data_from_db()
            else:
                # [Fix 2026-08-30] 自動刷新只用「離線模式」(download_missing=False)
                # 重新讀 disposal_history.db 顯示，不逐檔向 FinMind 補漲跌幅——實測
                # download_missing=True 對全庫兩萬多筆紀錄逐檔查價，40秒才處理62筆，
                # 全部跑完要幾小時，開啟軟體不能被這個卡住。新公告的處置事件本來就
                # 還沒有幾天的股價資料可看(N/A)，開軟體先看到最新的處置公告名單就
                # 已經達到使用者要的效果，缺漏的漲跌幅之後用「更新資料(續傳)」按鈕
                # 手動補齊即可。
                self.load_data_from_db(download_missing=False)

    
    def update_capital_only(self):
        """僅執行股本更新 (使用 CapitalWorker)"""
        if not self.all_records:
            QMessageBox.warning(self, "無資料", "目前無載入任何資料，請先載入。")
            return
            
        codes = list(set([r.get("code") for r in self.all_records if r.get("code")]))
        
        if not codes:
            return
            
        selected_user = self.api_combo.currentText()
        api_token = self.API_TOKENS.get(selected_user)
        
        self.cap_btn.setEnabled(False)
        self.reload_btn.setEnabled(False)
        self.status_label.setText("正在準備更新股本...")
        
        self.cap_worker = CapitalWorker(codes, api_token=api_token)
        self.cap_worker.progress_update.connect(self.status_label.setText)
        self.cap_worker.finished.connect(self.on_capital_update_finished)
        self.cap_worker.start()
        
    def on_capital_update_finished(self, updated, errors):
        self.status_label.setText(f"股本更新完成: 更新 {updated} 筆")
        QMessageBox.information(
            self, 
            "更新完成", 
            f"已掃描 {len(self.all_records)} 筆記錄的股本資料。\n實際更新(獲取到非空值): {updated} 筆"
        )
        
        self.cap_btn.setEnabled(True)
        self.reload_btn.setEnabled(True)
        
        # Reload to refresh display (offline mode is fast)
        self.load_data_from_db(download_missing=False)

    def show_update_help(self):
        """顯示更新功能說明"""
        help_text = """
        <h3 style="color: #4da6ff;">更新功能說明</h3>
        <p>系統提供多種更新方式，請依需求選擇：</p>
        <ol>
            <li><b>⚡ 快速更新</b> (位於主程式上方)<br>
               <span style="color: #888;">適用：盤後日常更新</span><br>
               僅下載 <b>今天</b> 的最新股價資料，速度最快。
            </li>
            <br>
            <li><b>🔄 更新資料 (續傳)</b> (位於本頁工具列)<br>
               <span style="color: #888;">適用：資料有缺漏 (N/A) 時</span><br>
               檢查列表中的處置股，若有缺少的歷史股價資料會自動補齊。<br>
               同時會檢查並下載交易日判斷所需的資料 (如加權指數)。
            </li>
            <br>
            <li><b>💰 更新股本</b><br>
               <span style="color: #888;">適用：股本顯示為 0 時</span><br>
               從資料源重新抓取各股票的最新股本資訊。
            </li>
            <br>
            <li><b>🌐 網頁更新</b><br>
               <span style="color: #888;">適用：處置公告未更新時</span><br>
               從證交所/櫃買中心官網爬取處置公告。<br>
               <b>範圍依「更新年度」設定：</b><br>
               - 選擇 <b>2026</b>：抓取 2026 全年度公告<br>
               - 選擇 <b>全部</b>：僅抓取當日最新公告
            </li>
        </ol>
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle("更新按鈕功能說明")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(help_text)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

    def apply_filters(self):
        """應用所有過濾條件 (日期、文字、N/A) 並更新表格"""
        if not hasattr(self, 'all_records'):
            self.all_records = []
            
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        search_text = self.search_input.text().lower()
        
        print(f"DEBUG: apply_filters - 總筆數: {len(self.all_records)}")
        print(f"DEBUG: apply_filters - 日期範圍設定: {start_date} ~ {end_date}")
        
        filtered = []
        skipped_count = 0
        skipped_samples = []
        
        # [New] Filter Non-4-Digit Codes
        only_stocks = self.only_stocks_checkbox.isChecked()
        
        for record in self.all_records:
            code = str(record.get("code", "")).strip()
            
            # [Filter] Non-4-Digit Code
            if only_stocks:
                # Basic check: Length must be 4
                if len(code) != 4:
                    continue
                # Optional: Check if purely numeric? User didn't specify.
                # Warrants are 6. ETFs are 4 or 5? Most ETFs are 4 (0050).
                # New ETFs might be 5.
                # User asked "exclude codes other than 4 digits". So STRICT 4.
            
            # 1. Date Filter
            ann_date_raw = record.get("announce_date", "")
            # 使用 DateUtils 統一轉換各種格式 (含民國年) 到 YYYY-MM-DD
            ann_date = DateUtils.to_iso_date_str(ann_date_raw)
            
            # 日期比較 (字串比較 YYYY-MM-DD)
            if ann_date and not (start_date <= ann_date <= end_date):
                continue
            
            # [New] 頻率過濾
            freq_text = record.get("freq_text", "")
            if freq_text in self.hidden_freqs:
                continue

            # [New 2026-09-28] 處置天數過濾
            duration_text = record.get("duration_text", "")
            if duration_text in self.hidden_durations:
                continue

            # [New 2026-09-28] 初犯/累犯過濾
            offense_text = record.get("offense_text", "")
            if offense_text in self.hidden_offenses:
                continue

            # 2. Text Filter
            if search_text:
                name = record.get("name", "").lower()
                code_lower = code.lower()
                if search_text not in code_lower and search_text not in name:
                    continue
            
            filtered.append(record)
        
        # 4. Apply ComboBox Sort (Default Sort)
        mode = self.sort_combo.currentText()
        if "股號" in mode:
            filtered.sort(key=lambda x: x.get("code", ""))
        elif "舊→新" in mode:
            filtered.sort(key=lambda x: DateUtils.to_iso_date_str(x.get("start_date", "")))
        else:  # 新→舊
            filtered.sort(key=lambda x: DateUtils.to_iso_date_str(x.get("start_date", "")), reverse=True)

        self.filtered_data = filtered
        
        # 5. Apply Header Sort (if active)
        if self.sort_col != -1:
            self.sort_filtered_data_by_header()
            
        self.populate_table(self.filtered_data)
        
        rows = len(self.filtered_data)
        total = len(self.all_records)
        self.status_label.setText(f"顯示 {rows} 筆資料 (總共 {total} 筆)")
    
    def show_header_context_menu(self, pos):
        """顯示表頭右鍵選單"""
        # 取得點擊的欄位索引
        col_index = self.table.horizontalHeader().logicalIndexAt(pos)

        # [New 2026-09-28] 頻率/處置天數/初犯累犯欄位過濾 (共用同一套 checkbox 選單邏輯)
        checkbox_filter_cols = {
            4: ("freq_text", self.hidden_freqs),
            5: ("duration_text", self.hidden_durations),
            6: ("offense_text", self.hidden_offenses),
        }
        if col_index in checkbox_filter_cols:
            field_key, hidden_set = checkbox_filter_cols[col_index]
            self._show_checkbox_filter_menu(pos, field_key, hidden_set)
            return

        # 只有漲跌幅欄位（索引 >= 9，前面 0~8 是固定欄位：公告日/股號/股名/股本/頻率/
        # 處置天數/初犯累犯/處置開始/處置結束）才顯示 N/A 過濾選單
        if col_index < 9:
            return

        menu = QMenu(self)
        
        current_mode = self.column_na_filters.get(col_index)
        
        # 1. 隱藏 N/A Action
        hide_action = QAction("隱藏 N/A", self)
        hide_action.setCheckable(True)
        hide_action.setChecked(current_mode == "HIDE")
        hide_action.triggered.connect(lambda checked: self.set_column_na_filter(col_index, "HIDE" if checked else None))
        menu.addAction(hide_action)
        
        # 2. 僅顯示 N/A Action
        show_only_action = QAction("僅顯示 N/A", self)
        show_only_action.setCheckable(True)
        show_only_action.setChecked(current_mode == "SHOW_ONLY")
        show_only_action.triggered.connect(lambda checked: self.set_column_na_filter(col_index, "SHOW_ONLY" if checked else None))
        menu.addAction(show_only_action)
        
        # 互斥邏輯 (其實可以靠 set_column_na_filter 切換，但這裡 UI 上是 CheckBox)
        # 如果點了 Hide，就要取消 Show Only (由 set_column_na_filter 處理不太直觀，
        # 因為 QAction triggered 是單獨的。這裡 lambda 已經處理了 toggle logic)
        # 為了好的 UX，可以用 QActionGroup，但我們需要能 "取消選擇" (都為 False)
        # 所以手動簡單處理即可：
        
        # 新增分隔線
        menu.addSeparator()
        
        # 新增清除所有過濾條件選項
        clear_action = QAction("清除所有過濾條件", self)
        clear_action.triggered.connect(self.clear_all_na_filters)
        menu.addAction(clear_action)
        
        # 顯示選單
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _show_checkbox_filter_menu(self, pos, field_key, hidden_set):
        """共用的 checkbox 式欄位過濾選單 (頻率/處置天數/初犯累犯都用這個)"""
        all_values = set(record.get(field_key, "") for record in self.all_records if record.get(field_key))
        if not all_values:
            return

        def _sort_key(v):
            # 處置天數要照數字排序(5天 < 7天 < 12天)，不能照字串排序；
            # 其他欄位(頻率/初犯累犯)沒有前導數字，直接照原字串排序
            m = re.match(r'(\d+)', v)
            return (0, int(m.group(1))) if m else (1, v)

        menu = QMenu(self)
        # 延遲到選單關閉時才套用過濾，避免表格重繪導致選單失焦關閉
        menu.aboutToHide.connect(self.apply_filters)

        for value in sorted(all_values, key=_sort_key):
            action = QWidgetAction(self)
            checkbox = QCheckBox(value)
            checkbox.setStyleSheet("QCheckBox { padding: 4px 10px; margin: 0px; background: transparent; } QCheckBox:hover { background-color: #3d3d3d; }")
            checkbox.setChecked(value not in hidden_set)

            # 僅更新狀態，不立刻呼叫 apply_filters
            def on_toggle(checked, v=value, hs=hidden_set):
                if checked:
                    hs.discard(v)
                else:
                    hs.add(v)

            checkbox.toggled.connect(on_toggle)
            action.setDefaultWidget(checkbox)
            menu.addAction(action)

        menu.addSeparator()
        clear_action = QAction("顯示全部", self)
        def on_clear():
            hidden_set.clear()
            # 由於點擊 QAction 會立刻關閉選單，aboutToHide 會被觸發並套用過濾
        clear_action.triggered.connect(on_clear)
        menu.addAction(clear_action)

        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def set_column_na_filter(self, col_index, mode):
        """
        設定指定欄位的 N/A 過濾狀態
        mode: None, "HIDE", "SHOW_ONLY"
        """
        if mode is None:
            if col_index in self.column_na_filters:
                del self.column_na_filters[col_index]
        else:
            self.column_na_filters[col_index] = mode
            
        self.apply_filters()
        
    def toggle_freq_filter(self, freq, checked):
        """切換單一頻率的顯示狀態"""
        if checked:
            self.hidden_freqs.discard(freq)
        else:
            self.hidden_freqs.add(freq)
        self.apply_filters()
        
    def clear_freq_filters(self):
        """清除頻率過濾條件（顯示全部）"""
        self.hidden_freqs.clear()
        self.apply_filters()
    
    def clear_all_na_filters(self):
        """清除所有欄位的過濾條件"""
        self.column_na_filters.clear()
        self.hidden_freqs.clear()
        self.hidden_durations.clear()
        self.hidden_offenses.clear()
        self.apply_filters()
    
    def filter_na_rows(self):
        """根據欄位級 N/A 過濾狀態隱藏/顯示資料列"""
        if not self.column_na_filters:
            # 沒有任何過濾，顯示所有行
            for row in range(self.table.rowCount()):
                self.table.setRowHidden(row, False)
            return
        
        # 遍歷每一行（排除統計行）
        data_row_count = len(self.filtered_data) if hasattr(self, 'filtered_data') else 0
        for row_idx in range(data_row_count):
            should_hide = False
            
            # 檢查是否有任何被過濾的欄位為 N/A
            # 檢查是否有任何被過濾的欄位符合條件
            for col_index, mode in self.column_na_filters.items():
                if not mode:
                    continue  # 此欄位不過濾
                
                # 取得此儲存格的值
                item = self.table.item(row_idx, col_index)
                is_na = (item is None) or (item.text() == "N/A")
                
                if mode == "HIDE":
                    # 隱藏 N/A -> 若 is_na 則隱藏
                    if is_na:
                        should_hide = True
                        break
                elif mode == "SHOW_ONLY":
                    # 僅顯示 N/A -> 若 NOT is_na 則隱藏 (只留 N/A)
                    if not is_na:
                        should_hide = True
                        break
                        
            # 注意: 如果有多個欄位過濾，這裡是 AND 邏輯 (只要有一個條件導致 hide 就 hide)
            # 例如: Col1 Hide NA, Col2 Show Only NA.
            # Row1: Col1=Val, Col2=NA -> Keep
            # Row2: Col1=NA, Col2=NA -> Hide by Col1
            # Row3: Col1=Val, Col2=Val -> Hide by Col2
            # 這是合理的過濾疊加邏輯。
            
            self.table.setRowHidden(row_idx, should_hide)
        
        # 統計行永遠顯示
        for row_idx in range(data_row_count, self.table.rowCount()):
            self.table.setRowHidden(row_idx, False)

    # 相容舊介面 (apply_date_filter/filter_data/sort_data 都轉呼叫 apply_filters)
    def apply_date_filter(self):
        self.apply_filters()
        
    def filter_data(self):
        self.apply_filters()
        
    def sort_data(self):
        self.apply_filters()

    def sort_filtered_data_by_header(self):
        """根據目前的 sort_col 和 sort_order 排序 filtered_data"""
        if self.sort_col == -1:
            return

        reverse = (self.sort_order == Qt.SortOrder.DescendingOrder)
        logicalIndex = self.sort_col
        
        def get_key(record):
            # 基本欄位映射
            if logicalIndex == 0: return record.get("announce_date") or ""
            if logicalIndex == 1: return record.get("code") or ""
            if logicalIndex == 2: return record.get("name") or ""
            if logicalIndex == 3:
                try: return float(record.get("capital") or 0)
                except: return 0.0
            if logicalIndex == 4: return record.get("freq_text") or ""
            if logicalIndex == 5:
                # 處置天數要照數字排序("5天"→5)，不能照字串排序(否則"12天"會排在"5天"前面)
                m = re.match(r'(\d+)', record.get("duration_text") or "")
                return int(m.group(1)) if m else 0
            if logicalIndex == 6: return record.get("offense_text") or ""
            if logicalIndex == 7: return record.get("start_date") or ""
            if logicalIndex == 8: return record.get("end_date") or ""

            # 漲跌幅欄位
            if logicalIndex >= 9:
                col_name_idx = logicalIndex - 9
                if col_name_idx < len(self.change_columns):
                    col_name = self.change_columns[col_name_idx]
                    val = record.get("changes", {}).get(col_name)
                    
                    if val is None: 
                        return float('-inf') if reverse else float('inf')
                    try:
                        return float(val)
                    except:
                        # 無法轉為數值，視為無效值 (排在最後)
                        return float('-inf') if reverse else float('inf')
            return ""

        self.filtered_data.sort(key=get_key, reverse=reverse)

    def on_header_clicked(self, logicalIndex):
        """處理表頭點擊排序 (Asc -> Desc -> Reset)"""
        # 更新排序狀態
        if self.sort_col == logicalIndex:
            if self.sort_order == Qt.SortOrder.AscendingOrder:
                self.sort_order = Qt.SortOrder.DescendingOrder
            else:
                # 已經是 Descending -> 切換到 Reset (還原)
                self.sort_col = -1
                self.table.horizontalHeader().setSortIndicatorShown(False)
                # 重新過濾即還原到 Default Sort (ComboBox)
                self.apply_filters()
                return
        else:
            self.sort_col = logicalIndex
            self.sort_order = Qt.SortOrder.AscendingOrder
            
        # 顯示排序指標
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(logicalIndex, self.sort_order)
        
        # 執行排序
        self.sort_filtered_data_by_header()
        self.populate_table(self.filtered_data)

    def populate_table(self, data):
        """填充表格數據"""
        self.table.blockSignals(True) # 暫停訊號以避免觸發 itemChanged
        self.data = data # 更新目前顯示的 data 引用
        
        # 不要覆蓋 filtered_data，因為 populate_table 可能被其他邏輯(如 update_selected)呼叫
        # 但在這裡 filtered_data 已經是處理好的，所以沒關係。
        # 為了安全，我們這裡不修改 filtered_data，除非我們確定它是過濾後的。
        # 在 apply_filters 中我們已經設定了 self.filtered_data = filtered
        
        if not data:
            self.table.setRowCount(0)
            return
        
        # 確定所有可能的漲跌幅欄位
        all_change_columns = set()
        for record in data:
            all_change_columns.update(record.get("headers", []))
        
        # 按照邏輯順序排序欄位
        self.change_columns = self._sort_change_columns(list(all_change_columns))
        
        # 設置表格欄位
        base_headers = ["公告日", "股號", "股名", "股本(億)", "頻率", "處置天數", "初犯/累犯", "處置開始", "處置結束"]
        all_headers = base_headers + self.change_columns
        
        self.table.setColumnCount(len(all_headers))
        self.table.setHorizontalHeaderLabels(all_headers)
        self.table.setRowCount(len(data))
        
        # 填充數據
        for row_idx, record in enumerate(data):
            # 基本欄位
            # 基本欄位 - 格式化公告日
            ann_date_raw = record.get("announce_date", "")
            # 使用 DateUtils 統一轉換為 YYYY-MM-DD
            ann_date = DateUtils.to_iso_date_str(ann_date_raw)
            
            date_item = QTableWidgetItem(ann_date)
            self.table.setItem(row_idx, 0, date_item)
            
            code_val = record.get("code", "")
            code_item = QTableWidgetItem(code_val)
            self.table.setItem(row_idx, 1, code_item)

            name_val = record.get("name", "")
            suffix = ""
            
            # 檢查歷史 CB 狀態 (基於公告日)
            if hasattr(self, "cb_db") and self.cb_db:
                try:
                    if self.cb_db.had_cb_on(code_val, ann_date):
                        suffix += "(CB)"
                except Exception:
                    pass
            
            # 檢查期貨狀態 (使用 MarginFuturesDatabase)
            if hasattr(self, "mf_db") and self.mf_db:
                try:
                    if self.mf_db.has_futures(code_val):
                        suffix += "(期)"
                except Exception:
                    pass
                
            display_name = f"{name_val}{suffix}"
            name_item = QTableWidgetItem(display_name)
            self.table.setItem(row_idx, 2, name_item)
            
            capital = record.get("capital")
            capital_item = QTableWidgetItem(f"{capital:.2f}" if capital else "N/A")
            capital_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 3, capital_item)
            
            freq_text = record.get("freq_text", "")
            freq_item = QTableWidgetItem(freq_text)
            freq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 4, freq_item)

            duration_item = QTableWidgetItem(record.get("duration_text", ""))
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_idx, 5, duration_item)

            offense_text = record.get("offense_text", "")
            offense_item = QTableWidgetItem(offense_text)
            offense_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if offense_text == "累犯":
                offense_item.setForeground(QBrush(QColor(255, 140, 140)))
            elif offense_text == "初犯":
                offense_item.setForeground(QBrush(QColor(140, 200, 255)))
            self.table.setItem(row_idx, 6, offense_item)

            start_item = QTableWidgetItem(record.get("start_date", ""))
            self.table.setItem(row_idx, 7, start_item)

            end_item = QTableWidgetItem(record.get("end_date", ""))
            self.table.setItem(row_idx, 8, end_item)
            
            # 漲跌幅欄位
            changes = record.get("changes", {})
            dates = record.get("dates", {})
            for col_idx, col_name in enumerate(self.change_columns):
                change_value = changes.get(col_name)
                
                if change_value is None:
                    item = QTableWidgetItem("N/A")
                    item.setForeground(QBrush(QColor("#888888")))
                else:
                    try:
                        float_val = float(change_value)
                        item = QTableWidgetItem(f"{float_val:+.2f}%")
                        if float_val > 0:
                            item.setForeground(QBrush(QColor("red")))
                        elif float_val < 0:
                            item.setForeground(QBrush(QColor("green")))
                        else:
                            item.setForeground(QBrush(QColor("white")))
                    except (ValueError, TypeError):
                        item = QTableWidgetItem(str(change_value))
                        item.setForeground(QBrush(QColor("white")))
                
                # [Fix] Add Tooltip for Date
                date_str = dates.get(col_name)
                if date_str:
                     item.setToolTip(f"日期: {date_str}")
                
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_idx, 9 + col_idx, item)

        # [Fix 2026-09-28] 原本用 resizeColumnsToContents() 對全表(可達9千多列)逐一計算
        # 每個儲存格的文字版面大小，Windows事件記錄證實這是長期(至少從2026-09-01起)
        # 反覆發生的 Qt6Gui.dll 原生 STACK OVERFLOW 當機(0xc00000fd)成因——例外代碼、
        # 錯誤模組、錯誤位移(0x69c247)在每一次當機記錄裡完全相同，且時間點都精準對應
        # 這裡(StatsWorker 剛跑完、要把大量資料塞進表格顯示的那一刻)。改成跟漲跌幅
        # 欄位一樣直接給固定寬度，不逐格計算，使用者仍可手動拖曳調整欄寬。
        fixed_col_widths = [95, 65, 130, 75, 55, 75, 75, 95, 95]
        for col_idx, w in enumerate(fixed_col_widths):
            if col_idx < self.table.columnCount():
                self.table.setColumnWidth(col_idx, w)

        # 固定漲跌幅欄位寬度
        for col_idx in range(9, self.table.columnCount()):
            self.table.setColumnWidth(col_idx, 80)
        
        # 加入統計行
        if self.change_columns:
            self.add_statistics_rows()
        
        # 套用欄位級 N/A 過濾
        self.filter_na_rows()
        
        self.table.blockSignals(False)

    def on_item_changed(self, item):
        """處理表格單元格修改"""
        col = item.column()
        row = item.row()
        
        # 僅處理漲跌幅欄位 (index >= 9) 且排除統計行
        if col < 9 or row >= len(self.filtered_data):
            return
            
        text = item.text().strip().replace("%", "")
        if not text:
            return

        try:
            val = float(text)
            formatted_text = f"{val:+.2f}%"
            
            self.table.blockSignals(True)
            item.setText(formatted_text)
            
            if val > 0:
                item.setForeground(QBrush(QColor("red")))
            elif val < 0:
                item.setForeground(QBrush(QColor("green")))
            else:
                item.setForeground(QBrush(QColor("white")))
            self.table.blockSignals(False)
            
            # 保存到資料庫
            record = self.filtered_data[row]
            # Try to get ID
            record_id = record.get("id")
            
            # Fallback keys
            code = str(record.get("code", "")).strip()
            period_raw = str(record.get("period_raw", "")).strip()
            
            col_name_idx = col - 9
            if 0 <= col_name_idx < len(self.change_columns):
                col_name = self.change_columns[col_name_idx]
                
                import json
                custom_stats_json = record.get("custom_stats")
                custom_stats = {}
                if custom_stats_json:
                    try:
                        custom_stats = json.loads(custom_stats_json)
                    except:
                        pass
                
                custom_stats[col_name] = val
                new_json = json.dumps(custom_stats)
                
                success = False
                if record_id is not None:
                     print(f"DEBUG: Updating by ID: {record_id}")
                     success = self.disposal_db.update_custom_stats_by_id(record_id, new_json)
                else:
                     print(f"DEBUG: Updating by Code/Period: {code}, {period_raw}")
                     success = self.disposal_db.update_custom_stats(code, period_raw, new_json)
                
                if success:
                    print("DEBUG: Update successful.")
                    record["custom_stats"] = new_json
                    record["changes"][col_name] = val
                    self.add_statistics_rows()
                else:
                    print(f"DEBUG: Update FAILED.")
                    QMessageBox.warning(self, "更新失敗", "無法更新資料庫 (ID或鍵值不匹配)")
                    
        except ValueError:
            pass

    def show_context_menu(self, pos):
        """顯示右鍵選單"""
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
            
        menu = QMenu(self)
        update_action = QAction("更新選取資料", self)
        update_action.triggered.connect(self.update_selected_rows)
        menu.addAction(update_action)
        
        delete_action = QAction("刪除選取資料", self)
        delete_action.triggered.connect(self.delete_selected_rows)
        menu.addAction(delete_action)
        
        menu.exec(self.table.viewport().mapToGlobal(pos))
        
    def delete_selected_rows(self):
        """刪除選取的資料列"""
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        if not rows:
            return
            
        reply = QMessageBox.question(
            self, "確認刪除", 
            f"確定要刪除選取的 {len(rows)} 筆資料嗎？\n這將無法復原。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        deleted_count = 0
        for row in rows:
            if row >= len(self.filtered_data): continue
            
            record = self.filtered_data[row]
            code = record["code"]
            period_raw = record["period_raw"]
            
            if self.disposal_db.delete_record(code, period_raw):
                # 從 all_records 中移除
                self.all_records = [d for d in self.all_records if not (d["code"] == code and d["period_raw"] == period_raw)]
                deleted_count += 1
        
        if deleted_count > 0:
            self.apply_filters()
            self.status_label.setText(f"已刪除 {deleted_count} 筆資料")

    def update_selected_rows(self):
        """更新選取的資料列"""
        rows = sorted(set(index.row() for index in self.table.selectedIndexes()))
        if not rows:
            return
            
        selected_records = []
        for row in rows:
            if row < len(self.filtered_data):
                selected_records.append(self.filtered_data[row])
        
        if not selected_records:
            return
            
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告", "已有更新任務在執行中，請稍後再試。")
            return
            
        self.status_label.setText(f"正在更新選取的 {len(selected_records)} 筆資料...")
        self.reload_btn.setEnabled(False)
        
        # 取得選擇的 API Token
        selected_user = self.api_combo.currentText()
        api_token = self.API_TOKENS.get(selected_user)
        
        self.worker = StatsWorker(selected_records, allow_download=True, api_token=api_token, force_refresh=True)
        self.worker.data_ready.connect(self.on_partial_update_finished)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def on_partial_update_finished(self, updated_records):
        """部分更新完成後的處理"""
        # Helper to normalize code and period for matching key
        def normalize_key(c, p):
            c_clean = str(c).upper().replace(".TW", "").replace(".TWO", "").strip()
            if c_clean.endswith(".0"): c_clean = c_clean[:-2]
            # 同時標準化 period_raw 格式
            p_normalized = self.normalize_period_format(p)
            return (c_clean, p_normalized)

        # Map using normalized keys
        update_map = { normalize_key(rec["code"], rec["period_raw"]): rec for rec in updated_records }
        
        # 更新 all_records
        updated_count = 0
        for i, record in enumerate(self.all_records):
            key = normalize_key(record["code"], record["period_raw"])
            if key in update_map:
                self.all_records[i] = update_map[key]
                updated_count += 1
                
        print(f"DEBUG: Updated {updated_count} records in all_records matched by normalized key")
        
        self.apply_filters()
        self.status_label.setText(f"選取資料更新完成 ({len(updated_records)} 筆)")

    
    def add_statistics_rows(self):
        """在表格底部加入 10 行統計數據"""
        if not self.filtered_data or not self.change_columns:
            return
        
        data_row_count = len(self.filtered_data)
        self.table.setRowCount(data_row_count + 10)  # 從 6 改為 10
        
        stat_labels = [
            "📊 總計", "📊 平均", "📊 上漲%", "📊 下跌%", 
            "📊 >9% 佔比", "📊 <-9% 佔比",
            "📊 期望值", "📊 獲利因子", "📊 風險報酬比", "📊 凱利倉位"
        ]
        
        for stat_idx, label in enumerate(stat_labels):
            row_idx = data_row_count + stat_idx
            
            stat_label_item = QTableWidgetItem(label)
            stat_label_item.setBackground(QBrush(QColor("#2d2d2d")))
            stat_label_item.setForeground(QBrush(QColor("#4da6ff")))
            font = stat_label_item.font()
            font.setBold(True)
            stat_label_item.setFont(font)
            self.table.setItem(row_idx, 0, stat_label_item)
            
            for col in range(1, 9):
                empty_item = QTableWidgetItem("")
                empty_item.setBackground(QBrush(QColor("#2d2d2d")))
                self.table.setItem(row_idx, col, empty_item)
            
            for col_idx, col_name in enumerate(self.change_columns):
                values = []
                for record in self.filtered_data:
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
                
                if values:
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
                    
                    item = QTableWidgetItem(text)
                    item.setBackground(QBrush(QColor("#1e1e1e")))
                    item.setForeground(QBrush(QColor("#4da6ff")))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item = QTableWidgetItem("N/A")
                    item.setBackground(QBrush(QColor("#1e1e1e")))
                    item.setForeground(QBrush(QColor("#888888")))
                
                self.table.setItem(row_idx, 9 + col_idx, item)

    def _sort_change_columns(self, columns):
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
