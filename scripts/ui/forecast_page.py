"""
處置預測總覽頁面
分三個區塊：聽牌(官方) → 一進聽(預測) → 處置中
與 Dashboard 觀察區使用完全相同的判斷邏輯。
"""
import re, math, json
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QLineEdit, QPushButton, QProgressBar,
    QFrame, QSizePolicy, QCalendarWidget, QDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from core.utils import DateUtils
from core.cache import CacheManager
from core.predictor import DispositionPredictor

class SelectableExpandLabel(QLabel):
    def __init__(self, text, table, row, page):
        super().__init__(text)
        self.table = table
        self.row = row
        self.page = page

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.hasSelectedText():
            self.page._on_cell_clicked(self.row, 10, self.table)

class ForecastWorker(QThread):
    """非同步載入預測資料"""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(dict)

    def __init__(self, agg_data, date_str, attention_list, mf_db, cb_db, punish_df=None, is_history=False):
        super().__init__()
        self.agg_data = agg_data
        self.date_str = date_str
        self.attention_list = attention_list or []
        self.mf_db = mf_db
        self.cb_db = cb_db
        self.punish_df = punish_df  # Shioaji api.punish() 結果
        self.is_history = is_history

    @staticmethod
    def _parse_frequency(interval_str):
        """強健的處置頻率通用解析器 (支援 API 控制字元、中文字與措施代碼)"""
        from core.measure_parser import MeasureParser
        return MeasureParser.parse_frequency(interval_str)

    @staticmethod
    def _predict_exact_disposal_frequency(code, source, anchor_date, disp_map, days_until_trigger=0):
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

    def run(self):
        listening = []
        one_step = []
        disposed = []

        anchor_dt = datetime.strptime(self.date_str, "%Y%m%d")
        
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
        official_set = set(item["code"] for item in self.attention_list)

        # [Fix] 確保所有聽牌代碼都能進入迴圈處理，即使不在 agg_data 內
        valid_codes = set(c for c in self.agg_data.keys() if len(str(c)) == 4 and "." not in str(c))
        valid_codes.update(official_set)
        codes = sorted(list(valid_codes), key=str)
        total = len(codes)

        # 讀取最後回補日資料 (參考股期套利)
        stop_short_data = {}
        try:
            import os, json
            stop_short_path = r"e:\Vibe Coding\Stock\股期套利\data\stop_short.json"
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
        if self.punish_df is not None and not self.punish_df.empty:
            for _, p_row in self.punish_df.iterrows():
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
                                freq = self._parse_frequency(p_interval)
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
                self.progress.emit(idx, total)

            data = self.agg_data.get(code, {})
            name = data.get("name", "")
            source = data.get("source", "")
            
            # 若 agg_data 缺資料，嘗試從 attention_list 補齊
            if not name or not source:
                for att in self.attention_list:
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
            if not has_futures and self.mf_db:
                try: has_futures = self.mf_db.has_futures(code)
                except: pass
            has_cb = False
            if self.cb_db:
                try: has_cb = self.cb_db.has_cb_now(code)
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
                if self.punish_df is not None and not self.punish_df.empty:
                    punish_rows = self.punish_df[self.punish_df['code'] == str(code)]
                    if not punish_rows.empty:
                        for _, p_row in punish_rows.iterrows():
                            p_start = p_row.get('start_date')
                            p_end = p_row.get('end_date')
                            p_interval = str(p_row.get('interval', ''))
                            try:
                                p_start_dt = DateUtils.parse_period_start(str(p_start))
                                p_end_dt = DateUtils.parse_period_start(str(p_end))
                                if p_start_dt and p_end_dt and p_start_dt.date() <= anchor_dt.date() <= p_end_dt.date():
                                    current_status = self._parse_frequency(p_interval)
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
                reason = active_punish_map[str(code)]["reason"] or self._guess_reason(data)
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
                    freq = self._parse_frequency(measure_str)
                if not freq and actually_disposed:
                    freq = measure_str if measure_str.strip() else "處置中"
                    
                reason = self._guess_reason(data)
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

            hist_items = []
            for d in pred_dates_30:
                c_str = filtered_clauses.get(d, "")
                # [Fix 2026-09-01] c_str 是「只保留第1~8款」過濾後的版本，官方
                # 已列入注意但 ClauseParser 解析不出具體款別的日子（例如泛稱的
                # 「注意」、或「等九個營業日已有五次」這種非標準措辭）在這裡永遠
                # 是空字串。rule2/3/4 問的是「有沒有觸發任一款」不是「哪一款」，
                # 所以 is_any_all 改用未過濾的原始 clauses_map，只要當天官方有任何
                # 注意紀錄就算「有觸發」；is_clause1/is_any 維持用過濾後的 c_str，
                # 因為這兩者需要明確知道是不是第一款/哪幾款。
                raw_c_str = clauses_map.get(d, "")
                try:
                    d_date = datetime.strptime(f"{anchor_dt.year}/{d}", "%Y/%m/%d").date()
                    if d_date > anchor_dt.date():
                        d_date = datetime.strptime(f"{anchor_dt.year - 1}/{d}", "%Y/%m/%d").date()
                except:
                    d_date = None

                is_disp = False
                if d_date:
                    for ps, pe in disposal_periods:
                        if ps <= d_date <= pe:
                            is_disp = True
                            break

                hist_items.append({
                    "is_clause1": "一" in c_str,
                    "is_any": len(c_str) > 0,
                    "is_any_all": len(raw_c_str.strip()) > 0,
                    "is_disposed": is_disp,
                })

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
                _current_status_freq = self._predict_exact_disposal_frequency(
                    code, source, _current_status_anchor, disp_map, days_until_trigger=0
                )
                current_status = _current_status_freq

            _days_until = min_needed if isinstance(min_needed, int) and 0 <= min_needed <= 10 else 0
            enter_freq = self._predict_exact_disposal_frequency(
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
                
                cached_res = data.get("calc_results", [])
                has_cache = bool(cached_res) and cached_res != ["無法取得歷史股價"]
                
                if self.is_history and has_cache:
                    # 快取優先：如果是歷史日期且已有計算結果，直接取用，不重新執行耗時的 API 下載與重算
                    calc_results = data.get("calc_results", [])
                    exclusion_lines = data.get("exclusion_lines", [])
                else:
                    try:
                        from core.calculator import DispositionCalculator
                        from core.fetcher import StockFetcher
                        fetcher = StockFetcher()

                        # [Fix 2026-09-30] has_c2_disp_60 從未計算過，calculate_conditions()
                        # 收不到就用預設值 False，導致排除條件框「2.前60日曾因第二款進處置」
                        # 永遠顯示「否」，跟 Dashboard 的 CalcWorker(有做這段即時 API 查詢)對不上
                        # (2305 案例：Dashboard 顯示「是」，這裡顯示「否」)。比照 dashboard.py 的
                        # check_exclusion_rules 前置計算(Rule B)，用同一支
                        # fetch_stock_disposition_history API、同樣的關鍵字判斷，確保兩邊一致。
                        has_c2_disp_60 = False
                        try:
                            d90_ago = anchor_dt - timedelta(days=90)
                            s_date = d90_ago.strftime("%Y%m%d")
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
                                        has_c2_disp_60 = True
                                        break
                        except Exception as e:
                            print(f"[ForecastWorker] {code} has_c2_disp_60 查詢失敗(不影響其他計算): {e}")

                        # [Fix] 避免 ForecastWorker 大量觸發慢速的 FinMind API 下載，設定 allow_fetch=False
                        # 但為了確保官方聽牌區不出現空白，針對聽牌股票允許強制從網路下載
                        allow_fetch_flag = should_calc
                        history_df, shares_outstanding = fetcher.fetch_stock_history(code, source, allow_fetch=allow_fetch_flag)
                        if history_df is not None and not history_df.empty:
                            import pandas as pd
                            # filter by index which is Date
                            history_df = history_df[pd.to_datetime(history_df.index).date <= anchor_dt.date()]
                            
                            c_lines, is_clause2_risk, exc_lines = DispositionCalculator.calculate_conditions(
                                history_df, source=source, stock_name=name, has_c1_30=has_c1_30,
                                has_c2_disp_60=has_c2_disp_60, shares_outstanding=shares_outstanding
                            )
                            calc_results = c_lines
                            exclusion_lines = exc_lines
                        else:
                            calc_results = ["無法取得歷史股價"]
                            exclusion_lines = []
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
            if code in self.agg_data:
                self.agg_data[code]["calc_results"] = calc_results
                self.agg_data[code]["exclusion_lines"] = exclusion_lines
                self.agg_data[code]["min_needed"] = min_needed
                self.agg_data[code]["trigger_progress"] = trigger_progress
                # [Fix 2026-08-22] enter_freq(如"2分初犯"/"25分累犯")之前只存在這次執行的
                # record 裡，沒寫回 agg_data，導致沒有進 cache.db 的 agg_cache 表——「雙刀戰法」
                # 專案讀取 agg_cache 當軟依賴時完全拿不到初犯/累犯資訊。補上這欄位供外部消費。
                self.agg_data[code]["enter_freq"] = enter_freq

        self.progress.emit(total, total)

        listening.sort(key=lambda x: (x.get("min_needed", 99), x["code"]))
        one_step.sort(key=lambda x: (x.get("min_needed", 99), x["code"]))
        disposed.sort(key=lambda x: (x.get("remaining", 99), x["code"]))
        
        # [Fix] 儲存快取，解決切換歷史日期過於緩慢的問題
        try:
            from core.cache import CacheManager
            cm = CacheManager()
            cm.save_agg_data(self.date_str, self.agg_data)
            print(f"[ForecastWorker] 已將 {self.date_str} 的計算結果 {len(self.agg_data)} 筆存入快取")
        except Exception as e:
            print(f"[ForecastWorker] 儲存快取失敗: {e}")

        self.finished.emit({
            "date_str": self.date_str,
            "listening": listening, "one_step": one_step, "disposed": disposed,
        })

    def _guess_reason(self, data):
        clauses = data.get("clauses", {})
        c1_count = sum(1 for v in clauses.values() if v and "一" in v)
        any_count = sum(1 for v in clauses.values() if v)
        if c1_count >= 3: return "連續3日第1款"
        if any_count >= 6: return f"10日內{any_count}次注意"
        if any_count >= 5: return "連續5日注意"
        return "處置"


class ForecastPage(QWidget):
    """處置預測總覽頁面"""
    status_message_updated = pyqtSignal(str)
    initial_load_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.cache_manager = CacheManager()
        self.mf_db = None
        self.cb_db = None
        try:
            from core.margin_futures_db import MarginFuturesDatabase
            self.mf_db = MarginFuturesDatabase()
        except: pass
        try:
            from core.cb_data import CBDatabase
            self.cb_db = CBDatabase()
        except: pass

        self.worker = None
        self.is_loaded = False
        self.current_display_date = DateUtils.get_last_trading_day()
        self.init_ui()

    def showEvent(self, event):
        if not self.is_loaded:
            self.load_data()
            self.is_loaded = True
        super().showEvent(event)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 工具列
        toolbar = QHBoxLayout()
        
        btn_style = """
            QPushButton {
                background-color: #333333; color: #FFFFFF;
                border: 1px solid #555555; border-radius: 4px;
                padding: 3px 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #444444; }
        """
        self.btn_prev = QPushButton("<")
        self.btn_prev.setFixedSize(30, 26)
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.clicked.connect(lambda: self.change_date(-1))
        toolbar.addWidget(self.btn_prev)
        
        self.date_btn = QPushButton("載入中...")
        self.date_btn.setFixedSize(120, 26)
        self.date_btn.setStyleSheet("""
            QPushButton {
                background-color: #1E1E1E; color: #4da6ff;
                border: 1px solid #4da6ff; border-radius: 4px;
                font-weight: bold; font-size: 14px;
            }
        """)
        self.date_btn.clicked.connect(self.toggle_calendar)
        toolbar.addWidget(self.date_btn)
        
        self.btn_next = QPushButton(">")
        self.btn_next.setFixedSize(30, 26)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.clicked.connect(lambda: self.change_date(1))
        toolbar.addWidget(self.btn_next)
        
        toolbar.addSpacing(15)
        toolbar.addWidget(QLabel("搜尋:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("股號/股名...")
        self.search_input.setFixedWidth(150)
        self.search_input.textChanged.connect(self._apply_search)
        toolbar.addWidget(self.search_input)
        toolbar.addSpacing(10)
        self.refresh_btn = QPushButton("🔄 重新載入")
        self.refresh_btn.clicked.connect(self.reload_data)
        toolbar.addWidget(self.refresh_btn)
        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#888;")
        toolbar.addWidget(self.stats_lbl)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 進度條
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(6)
        layout.addWidget(self.progress_bar)

        # === 外層 ScrollArea (整頁可捲動，但每個表格展開全部列) ===
        from PyQt6.QtWidgets import QScrollArea
        from PyQt6.QtCore import pyqtSignal
        
        class ClickableLabel(QLabel):
            clicked = pyqtSignal()
            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.clicked.emit()
                super().mousePressEvent(event)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        content = QVBoxLayout(container)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(6)

        # 聽牌區
        # official_set 來自 history_manager 的聽牌名單，資料源頭是 TWSE notetrans
        # + TPEx bulletin/warning——官方「累積注意次數已達/接近處置標準」正式清單，
        # 「聽牌」用詞是對的。真正需要留意的是這份清單依賴 attention_clauses 表
        # 保持最新(見 dashboard.py 的 auto_refresh_clauses_on_startup)，否則本地
        # 累積次數計算(min_needed)會低估，才會誤以為聽牌股也該出現在一進聽。
        self.listening_lbl = ClickableLabel("🟧 聽牌 (官方) ▼")
        self.listening_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.listening_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#FFC850; padding:2px 0;")
        content.addWidget(self.listening_lbl)
        self.listening_table = self._create_forecast_table()
        content.addWidget(self.listening_table)
        self.listening_lbl.clicked.connect(lambda: self._toggle_section(self.listening_lbl, self.listening_table, "🟧 聽牌 (官方)"))

        # 一進聽區
        self.onestep_lbl = ClickableLabel("🔵 一進聽 (預測) ▼")
        self.onestep_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.onestep_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#78B4FF; padding:2px 0;")
        content.addWidget(self.onestep_lbl)
        self.onestep_table = self._create_forecast_table()
        content.addWidget(self.onestep_table)
        self.onestep_lbl.clicked.connect(lambda: self._toggle_section(self.onestep_lbl, self.onestep_table, "🔵 一進聽 (預測)"))

        # 處置中區
        self.disposed_lbl = ClickableLabel("🔴 處置中 ▼")
        self.disposed_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disposed_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#FF7878; padding:2px 0;")
        content.addWidget(self.disposed_lbl)
        self.disposed_table = self._create_disposed_table()
        content.addWidget(self.disposed_table)
        self.disposed_lbl.clicked.connect(lambda: self._toggle_section(self.disposed_lbl, self.disposed_table, "🔴 處置中"))

        content.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _create_forecast_table(self):
        """建立聽牌/一進聽表格"""
        table = QTableWidget()
        # 新增「最後回補日」於市場右側
        cols = ["代號","名稱","市場","最後回補日","目前狀態","進處置",
                "連3日第1款","連5日1-8款","10日內6次","30日內12次",
                "進處置條件"]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)
        table.setWordWrap(True)
        table.cellClicked.connect(self._on_cell_clicked)

        header = table.horizontalHeader()
        for i in range(6):  # 代號/名稱/市場/最後回補日/目前狀態/進處置
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        for i in range(6, 10):  # 四大規則
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(i, 105)
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)  # 進處置條件

        # 每個表格展開全部列、關閉自身捲動條
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setMinimumHeight(50)
        return table

    def _create_disposed_table(self):
        """建立處置中表格"""
        table = QTableWidget()
        cols = ["代號","名稱","市場","撮合","初犯/累犯","開始","結束","出關日","剩餘交易日","處置原因"]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)

        header = table.horizontalHeader()
        for i in range(len(cols)):
            if i == len(cols) - 1:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        table.setWordWrap(True)
        table.cellClicked.connect(self._on_cell_clicked)

        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setMinimumHeight(50)
        return table

    def _auto_height(self, table):
        """自動調整表格高度以顯示全部內容（不需要捲動）"""
        rows = table.rowCount()
        if rows == 0:
            table.setFixedHeight(50)
            return
        # 計算所有行的實際高度
        total_h = table.horizontalHeader().height() + 4
        for r in range(rows):
            total_h += table.rowHeight(r)
        table.setFixedHeight(total_h)

    def reload_data(self):
        self.is_loaded = False
        self.load_data()

    def _toggle_section(self, lbl, table, base_text):
        """控制表格收合，並更新標題的箭頭與計數"""
        is_visible = not table.isVisible()
        table.setVisible(is_visible)
        
        # 取得當前計數 (如果有)
        text = lbl.text()
        count_str = ""
        import re
        match = re.search(r'\(\d+ 檔\)', text)
        if match:
            count_str = f" {match.group(0)}"
            
        arrow = "▼" if is_visible else "▶"
        lbl.setText(f"{base_text} {arrow}{count_str}")

    def load_data(self):
        """讀取當前 current_display_date 的預測與處置資料，建構表格。"""
        if not hasattr(self, "search_input"): return
        
        date_str = self.current_display_date.strftime("%Y%m%d")
        anchor_dt = self.current_display_date
        
        # 用於防範 Race Condition 的指標
        self._expected_date = date_str

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

            conn = sqlite3.connect("data/disposal_history.db")
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

        if not agg_data:
            self.date_btn.setText("無資料")
            self.stats_lbl.setText("⚠ 無快取且 listening_history 無此日期資料")
            self.initial_load_finished.emit()
            return

        self.date_btn.setText(anchor_dt.strftime('%Y-%m-%d'))
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.refresh_btn.setEnabled(False)

        # 嘗試從 Shioaji 取得處置清單（用於「目前狀態」欄位）
        punish_df = None
        try:
            from core.shioaji_client import ShioajiClient
            sj_client = ShioajiClient()
            punish_df = sj_client.get_punish()
            if punish_df is not None:
                print(f"[ForecastPage] Shioaji punish: {len(punish_df)} 筆處置資料")
        except Exception as e:
            print(f"[ForecastPage] Shioaji punish 取得失敗 (將使用本地資料): {e}")

        self.worker = ForecastWorker(agg_data, date_str, attention_list, self.mf_db, self.cb_db, punish_df=punish_df)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, c, t):
        if t > 0:
            self.progress_bar.setMaximum(t)
            self.progress_bar.setValue(c)
            # 發送給 Splash Screen
            self.status_message_updated.emit(f"正在分析處置預測 ({c}/{t})...")

    def _on_finished(self, result):
        self.progress_bar.setVisible(False)
        self.refresh_btn.setEnabled(True)
        self.is_loaded = True
        self._result = result
        self._populate_all(result)
        self.initial_load_finished.emit()

    def _apply_search(self):
        if hasattr(self, '_result'):
            self._populate_all(self._result)

    def _populate_all(self, result):
        s = self.search_input.text().lower()
        ls = result["listening"]
        os_ = result["one_step"]
        ds = result["disposed"]
        if s:
            ls = [r for r in ls if s in r["code"].lower() or s in r["name"].lower()]
            os_ = [r for r in os_ if s in r["code"].lower() or s in r["name"].lower()]
            ds = [r for r in ds if s in r["code"].lower() or s in r["name"].lower()]

        self.listening_lbl.setText(f"🟧 聽牌 (官方) {'▼' if self.listening_table.isVisible() else '▶'} ({len(ls)} 檔)")
        self.onestep_lbl.setText(f"🔵 一進聽 (預測) {'▼' if self.onestep_table.isVisible() else '▶'} ({len(os_)} 檔)")
        self.disposed_lbl.setText(f"🔴 處置中 {'▼' if self.disposed_table.isVisible() else '▶'} ({len(ds)} 檔)")
        self.stats_lbl.setText(f"聽牌:{len(result['listening'])} | 一進聽:{len(result['one_step'])} | 處置中:{len(result['disposed'])}")

        self._fill_forecast_table(self.listening_table, ls)
        self._fill_forecast_table(self.onestep_table, os_)
        self._fill_disposed_table(self.disposed_table, ds)

    def _fill_forecast_table(self, table, records):
        table.setSortingEnabled(False)
        table.setRowCount(len(records))
        for row, r in enumerate(records):
            self._set_item(table, row, 0, r["code"], align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 1, f"{r['name']}{r['suffix']}")
            self._set_item(table, row, 2, r["source"], align=Qt.AlignmentFlag.AlignCenter)

            # 最後回補日 (col 3)
            last_cover = r.get("last_cover_date", "")
            rem_days = r.get("cover_remaining_days", -1)
            if last_cover and rem_days >= 0:
                cover_txt = f"{last_cover}\n餘{rem_days}日"
                self._set_item(table, row, 3, cover_txt, fg=QColor(245, 158, 11), align=Qt.AlignmentFlag.AlignCenter)
            else:
                self._set_item(table, row, 3, "-", align=Qt.AlignmentFlag.AlignCenter)

            # 目前狀態 (col 4)
            status = r.get("current_status", "非處置")
            if status == "非處置":
                self._set_item(table, row, 4, status,
                               fg=QColor(80, 200, 80), align=Qt.AlignmentFlag.AlignCenter)
            else:
                self._set_item(table, row, 4, status,
                               fg=QColor(255, 100, 100), align=Qt.AlignmentFlag.AlignCenter)

            # 進處置 (col 5)
            enter_f = r.get("enter_freq", "5分")
            ef_color = QColor(255, 200, 80) if enter_f == "5分" else QColor(255, 80, 80)
            self._set_item(table, row, 5, enter_f,
                           fg=ef_color, align=Qt.AlignmentFlag.AlignCenter)

            tp = r.get("trigger_progress")
            if tp:
                self._set_blocks(table, row, 6, tp["rule1"])
                self._set_blocks(table, row, 7, tp["rule2"])
                self._set_blocks(table, row, 8, tp["rule3"])
                self._set_blocks(table, row, 9, tp["rule4"])

            # 進處置條件 — 用 HTML QLabel 顯示，支援多色粗體 (col 10)
            lines = r.get("calc_results", [])
            html_parts = []
            for line in lines:
                txt = str(line).strip()
                if not txt: continue
                # 過濾非條件行
                plain = re.sub(r'<[^>]+>', ' ', txt).strip()
                if '最新收盤' in plain: continue
                if '進處置:' in plain or '進處置：' in plain: continue
                if '達以下任一' in plain: continue
                if '無 (' in plain and '全部條件' in plain: continue
                # 保留原始 HTML (含 <b> 標記)
                html_parts.append(txt)
            
            # 第二款排除條款 (詳細格式)
            excl = r.get("exclusion_lines", [])
            if excl:
                html_parts.extend(excl)
            
            if html_parts:
                full_html = "<br>".join(html_parts)
                lbl = SelectableExpandLabel(full_html, table, row, self)
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("padding:2px 4px; color:#E0E0E0; font-size:12px;")
                table.setCellWidget(row, 10, lbl)
            else:
                # 若有 calc_results 但全被過濾 → 顯示「條件未達標」
                if lines:
                    self._set_item(table, row, 10, "條件未達標",
                                   fg=QColor(120, 120, 120),
                                   align=Qt.AlignmentFlag.AlignCenter)
                else:
                    self._set_item(table, row, 10, "")

        for row in range(len(records)):
            table.setRowHeight(row, 50)

        table.setSortingEnabled(True)
        self._auto_height(table)

    def _fill_disposed_table(self, table, records):
        table.setSortingEnabled(False)
        table.setRowCount(len(records))
        for row, r in enumerate(records):
            self._set_item(table, row, 0, r["code"], align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 1, f"{r['name']}{r['suffix']}")
            self._set_item(table, row, 2, r["source"], align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 3, r.get("freq", ""), align=Qt.AlignmentFlag.AlignCenter)

            offense = ""
            enter_freq = r.get("enter_freq", "") or ""
            if "累犯" in enter_freq:
                offense = "累犯"
            elif "初犯" in enter_freq:
                offense = "初犯"
            offense_fg = QColor(255, 140, 140) if offense == "累犯" else (
                QColor(140, 200, 255) if offense == "初犯" else None)
            self._set_item(table, row, 4, offense, fg=offense_fg, align=Qt.AlignmentFlag.AlignCenter)

            self._set_item(table, row, 5, r.get("start", ""), align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 6, r.get("end", ""), align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 7, r.get("exit", ""),
                           fg=QColor(255, 100, 100), align=Qt.AlignmentFlag.AlignCenter)
            remaining = r.get("remaining", 0)
            days_elapsed = r.get("days_elapsed", 0)
            days_total = r.get("days_total", 0)
            days_text = f"{days_elapsed}/{days_total}" if days_total > 0 else ""
            if remaining <= 0:
                self._set_item(table, row, 8, days_text,
                               fg=QColor(80, 200, 80), align=Qt.AlignmentFlag.AlignCenter)
            elif remaining <= 2:
                self._set_item(table, row, 8, days_text,
                               fg=QColor(255, 180, 80), align=Qt.AlignmentFlag.AlignCenter)
            else:
                self._set_item(table, row, 8, days_text,
                               fg=QColor(120, 180, 255), align=Qt.AlignmentFlag.AlignCenter)
            self._set_item(table, row, 9, r.get("reason", ""))

        for row in range(len(records)):
            table.setRowHeight(row, 50)
            
        table.setSortingEnabled(True)
        self._auto_height(table)

    def _set_item(self, table, row, col, text, align=None, bg=None, fg=None):
        item = QTableWidgetItem(str(text))
        if align: item.setTextAlignment(align)
        if bg: item.setBackground(QBrush(bg))
        if fg: item.setForeground(QBrush(fg))
        table.setItem(row, col, item)
        return item

    def _set_blocks(self, table, row, col, rule_data):
        """
        色塊：從左到右連續顯示觸發天數(橘)，右側補灰色表示未達標部分。
        """
        current = rule_data.get("current", 0)
        target = rule_data.get("target", 3)
        needed = rule_data.get("needed", 99)

        orange = min(current, target)
        grey = target - orange
        # [Fix 2026-08-27] 原本後面接的是未封頂的 current(如 "8/3")。真正根因後來在
        # predictor.py 的 _compute_live_state() 修掉了(股票處置期間內被連續升級、
        # 同一輪從未真正出關時，每次規則觸發都要各自歸零重新算，不是只在第一次進入
        # 這段連續處置時歸零一次)，修完 current 理論上不會再超過 target。這裡的封頂
        # 純粹當防禦——萬一還有沒想到的邊界情況，也不要再顯示出「分子比分母大、
        # 長得像日期(如8/3)」這種容易誤讀的文字。
        current_display = f"{orange}+" if current > target else str(orange)
        text = "🟧" * orange + "⬜" * grey + f" {current_display}/{target}"

        if needed <= 0:
            fg = QColor(255, 80, 80)    # 已觸發 - 紅
        elif needed == 1:
            fg = QColor(255, 200, 80)   # 差1天 - 黃
        elif needed == 2:
            fg = QColor(120, 180, 255)  # 差2天 - 藍
        else:
            fg = QColor(150, 150, 150)  # 安全 - 灰

        self._set_item(table, row, col, text, fg=fg, align=Qt.AlignmentFlag.AlignCenter)

    def change_date(self, offset):
        from datetime import timedelta
        self.current_display_date += timedelta(days=offset)
        # Skip non-trading days
        while not DateUtils.is_trading_day(self.current_display_date):
            self.current_display_date += timedelta(days=1 if offset > 0 else -1)
        self.reload_data()

    def toggle_calendar(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QCalendarWidget, QPushButton
        from datetime import datetime
        dialog = QDialog(self)
        dialog.setWindowTitle("選擇日期")
        layout = QVBoxLayout(dialog)
        cal = QCalendarWidget()
        cal.setSelectedDate(self.current_display_date.date())
        layout.addWidget(cal)
        btn = QPushButton("確定")
        layout.addWidget(btn)
        
        def on_date_selected():
            qdate = cal.selectedDate()
            self.current_display_date = datetime(qdate.year(), qdate.month(), qdate.day())
            dialog.accept()
            self.reload_data()
            
        btn.clicked.connect(on_date_selected)
        dialog.exec()

    def _on_cell_clicked(self, row, col, table=None):
        if table is None:
            table = self.sender()
        current_height = table.rowHeight(row)
        if current_height == 50:
            table.resizeRowToContents(row)
            if table.rowHeight(row) < 50:
                table.setRowHeight(row, 50)
        else:
            table.setRowHeight(row, 50)
            
        self._auto_height(table)

    def _show_full_conditions(self, title, html):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton
        d = QDialog(self)
        d.setWindowTitle(title)
        d.setMinimumSize(450, 400)
        lay = QVBoxLayout(d)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 13px; line-height: 1.5; padding: 10px;")
        scroll.setWidget(lbl)
        lay.addWidget(scroll)
        
        btn = QPushButton("關閉")
        btn.setFixedHeight(30)
        btn.clicked.connect(d.accept)
        lay.addWidget(btn)
        
        d.exec()
