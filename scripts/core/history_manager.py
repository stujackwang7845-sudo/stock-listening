
import json
import os
import requests
from datetime import datetime
from .scraper_history import HistoryScraper

class HistoryManager:
    GITHUB_RAW_URL = "https://raw.githubusercontent.com/stujackwang7845-sudo/stock-listening/master/listening_history.json"

    def __init__(self):
        self.FILE_PATH = self._resolve_path()
        print(f"DEBUG: HistoryManager using path: {self.FILE_PATH}")
        self.history = self._load()
        # self._cleanup_bad_records() # Disable destructive cleanup

    def _resolve_path(self):
        import sys
        
        # Candidates to check
        candidates = []
        
        # 1. New Organized Path (Preferred)
        candidates.append("data/listening_history.json")
        
        # 2. CWD (Highest priority if user manually placed it)
        candidates.append("listening_history.json")
        
        # 3. Source Relative (scripts/core/../data/listening_history.json)
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates.append(os.path.join(base_dir, "data", "listening_history.json"))
        
        # 3. EXE Relative (If frozen)
        if getattr(sys, 'frozen', False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), "listening_history.json"))
            
        for path in candidates:
            if os.path.exists(path):
                return path
        
        # Default to CWD if none exist (will create new)
        return "listening_history.json"

    def sync_from_github(self):
        """Fetch remote history and merge into local."""
        try:
            print(f"Syncing from GitHub: {self.GITHUB_RAW_URL}...")
            resp = requests.get(self.GITHUB_RAW_URL, timeout=5)
            if resp.status_code == 200:
                remote_data = resp.json()
                print(f"Downloaded {len(remote_data)} records from GitHub.")
                
                added_count = 0
                for rec in remote_data:
                    # Reuse internal logic to add/update
                    # We check duplicate inside add_record logic manually to avoid redundant saves, 
                    # or just use add_record directly (slower but safer).
                    # Optimization: Batch check
                    
                    if self._merge_record(rec):
                        added_count += 1
                
                if added_count > 0:
                    self.save()
                    print(f"Sync complete. Added/Updated {added_count} records.")
                else:
                    print("Sync complete. No new data.")
                
                return True
            else:
                print(f"GitHub Sync Failed: Status {resp.status_code}")
                return False
        except Exception as e:
            print(f"GitHub Sync Error: {e}")
            return False

    def force_sync_date(self, target_date_obj):
        """
        Overwrite local history for a specific date with data from GitHub.
        1. Download listening_history.json from GitHub
        2. Filter records matching target_date
        3. Remove ALL local records for that date
        4. Insert GitHub records for that date
        5. Save
        """
        try:
            target_str = target_date_obj.strftime("%Y-%m-%d")
            print(f"Force Syncing {target_str} from GitHub...")
            
            resp = requests.get(self.GITHUB_RAW_URL, timeout=5)
            if resp.status_code != 200:
                print(f"GitHub Download Error: {resp.status_code}")
                return False, f"GitHub Download Error: {resp.status_code}"
                
            remote_data = resp.json()
            
            # Filter remote data for target date
            target_records = [r for r in remote_data if r.get("date") == target_str]
            
            if not target_records:
                print(f"No records found on GitHub for {target_str}")
                # CAUTION: If GitHub has no data, should we wipe local?
                # User said "Exactly match GitHub". So yes, if GitHub has 0, we have 0.
                pass
                
            # Remove local records for this date
            original_count = len(self.history)
            self.history = [r for r in self.history if r.get("date") != target_str]
            removed = original_count - len(self.history)
            
            # Add remote records
            from .fetcher import StockFetcher
            fetcher = StockFetcher()
            
            for r in target_records:
                 # Ensure filters (CB/Weekend) are respected?
                 # User said "Exactly match GitHub". So maybe copy as is?
                 # But our system relies on 4-digit codes.
                 if len(str(r.get("code",""))) == 5: continue 
                 
                 # [Fix] Auto-fill missing Source to prevent display issues
                 if not r.get("source"):
                     try:
                         code = str(r.get("code"))
                         # Try simple guess first? No, fetcher is better.
                         # Note: This might slow down sync slightly but ensures data quality.
                         print(f"Resolving source for {code}...")
                         src = fetcher.check_market_type(code)
                         if src:
                             r["source"] = src
                             print(f"Resolved {code} -> {src}")
                         else:
                             r["source"] = "上市" # Default
                     except:
                         r["source"] = "上市"
                 
                 # [Fix] 移除從 GitHub 強制同步時的 Auto-Tagging 重新計算
                 
                 self.history.append(r)
                 
            self.save()
            return True, f"已覆蓋 {target_str} 資料 (GitHub: {len(target_records)} 筆, 本地移除: {removed} 筆)"
            
        except Exception as e:
            print(f"Force Sync Error: {e}")
            return False, str(e)

    def _merge_record(self, record):
        """Internal helper to merge record without immediate save."""
        # [Filter] Exclude CB
        if len(str(record.get("code", ""))) == 5:
            return False

        # [Filter] Exclude Weekend (Sat/Sun)
        # Date format: YYYY-MM-DD
        try:
            d_str = record.get("date", "")
            if d_str:
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                # weekday(): Mon=0 ... Sun=6
                if dt.weekday() >= 5:
                    # print(f"Skipping weekend record: {d_str} {record.get('code')}")
                    return False
        except:
            pass

        # Check duplicate
        for existing in self.history:
            if existing.get("date") == record["date"] and existing.get("code") == record["code"]:
                # Found existing. Update invalid/missing fields?
                # Ideally we respect local changes (tags/comments).
                # But fetcher data (trigger_info) might be better?
                # Let's update trigger_info if widely different?
                # [2026-10-04] 本機紀錄的 trigger_info 常是自己解析的 {"10/02": "一"}，
                # 看不出官方是因哪條規則聽牌。GitHub 那份是官方原文(連續二次/九個營業日已有五次…)，
                # 另存成 official_reason 欄位給 conditions_engine 用；只新增欄位，不動既有資料。
                remote_ti = record.get("trigger_info", "")
                if (isinstance(remote_ti, str) and ("連續" in remote_ti or "營業日已有" in remote_ti)
                        and existing.get("official_reason") != remote_ti):
                    existing["official_reason"] = remote_ti
                    return True
                return False # Assume local is up to date or same

        # If not found, append
        if "tags" not in record: record["tags"] = []
        if "comment" not in record: record["comment"] = ""
        # [Fix] 移除從 GitHub 同步時的 Auto-Tagging 重新計算，避免開啟軟體時下載幾百檔股票的 K 線導致當機/極慢
        self.history.append(record)
        return True

    def _cleanup_bad_records(self):
        """Remove 5-digit codes (CBs) from existing history."""
        original_len = len(self.history)
        self.history = [
            x for x in self.history 
            if len(str(x.get("code", ""))) != 5
        ]
        if len(self.history) < original_len:
            print(f"Cleaned up {original_len - len(self.history)} CB records.")
            self.save()

    def _load(self):
        if not os.path.exists(self.FILE_PATH):
            return []
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading history: {e}")
            return []

    def save(self):
        try:
            abs_path = os.path.abspath(self.FILE_PATH)
            print(f"DEBUG: Saving history to: {abs_path}")
            print(f"DEBUG: Total records to save: {len(self.history)}")
            
            with open(self.FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=4)
            
            # Verify file was written
            if os.path.exists(self.FILE_PATH):
                file_size = os.path.getsize(self.FILE_PATH)
                print(f"DEBUG: File saved successfully. Size: {file_size} bytes")
            else:
                print(f"ERROR: File was not created at {abs_path}")
                
        except Exception as e:
            print(f"Error saving history: {e}")
            import traceback
            traceback.print_exc()

    def add_record(self, record):
        """
        Add a new record if it doesn't exist for the same date & code.
        record: dict with keys [date, code, name, ...]
        """
        # [Filter 1] Only allow 4-digit stock codes
        code_str = str(record.get("code", ""))
        if len(code_str) != 4:
            return  # Skip non-4-digit codes (CBs, warrants, ETFs, etc.)
        
        # [Filter 2] Prevent future dates and today's data before market close
        from datetime import datetime, timedelta
        try:
            record_date = datetime.strptime(record.get("date", ""), "%Y-%m-%d").date()
            now = datetime.now()
            today = now.date()
            market_close_hour = 17  # 5 PM
            
            # Determine cutoff date
            if now.hour < market_close_hour:
                # Before market close: only allow yesterday or earlier
                cutoff_date = today - timedelta(days=1)
            else:
                # After market close: can allow today's data
                cutoff_date = today
            
            if record_date > cutoff_date:
                # Skip future dates or today's data before market close
                return
        except:
            # If date parsing fails, skip this record
            return

        # Check duplicate
        for existing in self.history:
            if existing.get("date") == record["date"] and existing.get("code") == record["code"]:
                # Update existing (merge info but keep tags/comments?)
                # Requirement implies we overwrite or update trigger info. 
                # Let's keep existing tags/comments if present.
                if "tags" in existing: record["tags"] = existing["tags"]
                if "comment" in existing: record["comment"] = existing["comment"]
                
                existing.update(record)
                self.save()
                return

        # New record
        if "tags" not in record: record["tags"] = []
        if "comment" not in record: record["comment"] = ""

        # [AUTO-PULL HISTORY]
        # 已關閉：新紀錄加入時不再同步從官方網頁回填歷史條款（因為會導致啟動時抓取數百次造成卡頓）
        # 如果需要歷史條款，可依靠 github 同步或改為非同步執行。
        try:
            # 確認是否有期貨
            if "has_futures" not in record:
                record["has_futures"] = self._check_has_futures(code_str)

            # [DISABLED FOR PERFORMANCE]
            # fetched = HistoryScraper.backfill_stock(code_str, lookback_days=9)
        except Exception as e:
            print(f"[HistoryManager] Auto-backfill setup failed for {code_str}: {e}")
            
        # [Auto-Tagging Logic]
        try:
            self._auto_tag_record(record)
        except Exception as e:
            print(f"[HistoryManager] Auto-tagging failed for {code_str}: {e}")
        
        self.history.append(record)
        self.save()

    def _auto_tag_record(self, record):
        """Automatically assign tag_001, tag_002, tag_009 based on conditions."""
        import re
        code_str = record.get("code")
        date_str = record.get("date")
        source = record.get("source")
        if not code_str or not date_str: return
        
        tags = record.get("tags", [])
        original_tags = list(tags)  # Snapshot for change detection
        
        try:
            from core.market_cache import MarketDataCache
            from core.calculator import DispositionCalculator
            from core.tick_utils import TickUtils
            from core.disposal_database import DisposalDatabase
            
            # 使用 MarketDataCache（market_data.db）取代空的 PriceDatabase
            cache = MarketDataCache()
            df = cache.get_price_history(code_str)
            
            if df is not None and not df.empty:
                # 過濾到 date_str 以前的資料（MarketDataCache 不支援 end_date）
                df = df[df.index <= date_str]
                # 只取最近 30 筆
                if len(df) > 30:
                    df = df.iloc[-30:]
            
            if df is not None and not df.empty and len(df) >= 2:
                idx = df.index[-1].strftime("%Y-%m-%d")
                
                # Check Limit Lock (一字鎖) logic
                # [Fix] record 存在於 listening_history = 已在聽牌區
                # 條件：close == limit_up 且 high == low（盤後確認）
                #
                # 如果 MarketDataCache 尚未更新到 date_str，嘗試即時下載
                if idx != date_str:
                    try:
                        from core.fetcher import StockFetcher
                        fetcher = StockFetcher()
                        fresh_df, _ = fetcher.fetch_stock_history(code_str, source, "30d", allow_fetch=True)
                        if fresh_df is not None and not fresh_df.empty:
                            fresh_df = fresh_df[fresh_df.index <= date_str]
                            if not fresh_df.empty and len(fresh_df) >= 2:
                                df = fresh_df
                                idx = df.index[-1].strftime("%Y-%m-%d")
                    except Exception as e:
                        print(f"[AutoTag] Fetch fresh data for {code_str} failed: {e}")
                
                # 日期匹配（允許差距 3 天，因為週五→週一有 2 天差距，加上假日可能更多）
                date_match = (idx == date_str)
                if not date_match:
                    try:
                        from datetime import datetime as _dt
                        idx_d = _dt.strptime(idx, "%Y-%m-%d").date()
                        rec_d = _dt.strptime(date_str, "%Y-%m-%d").date()
                        gap = abs((rec_d - idx_d).days)
                        # 允許 3 天的差距（週五→週一 = 2天，連假可能更多）
                        if gap <= 3:
                            date_match = True
                    except:
                        pass
                
                if date_match:
                    curr_row = df.iloc[-1]
                    high = curr_row['High']
                    low = curr_row['Low']
                    close = curr_row['Close']
                    
                    prev_row = df.iloc[-2]
                    prev_close = prev_row['Close']
                    
                    limit_up = TickUtils.calculate_limit_up(prev_close)
                    
                    open_price = curr_row['Open']
                    
                    # 一字鎖條件: 開盤價==漲停價 且 最低價==漲停價
                    is_limit_up = False
                    if limit_up > 0:
                        is_open_limit = abs(open_price - limit_up) < 1e-5
                        is_low_limit = abs(low - limit_up) < 1e-5
                        if is_open_limit and is_low_limit:
                            is_limit_up = True
                            
                    if is_limit_up and "tag_009" not in tags:
                        tags.append("tag_009")
                        print(f"[AutoTag] {code_str} ({date_str}) added tag_009 (一字鎖)")
                    elif not is_limit_up and "tag_009" in tags:
                        tags.remove("tag_009")
                        print(f"[AutoTag] {code_str} ({date_str}) removed tag_009 (一字鎖)")
                
                # Check Mandatory Disposal (必進處置) conditions
                # 取得 shares_outstanding
                shares = record.get("shares_outstanding", 0)
                if not shares:
                    shares = cache.get_stock_info(code_str) or 0
                
                # 將上櫃 source 統一格式（TPEX → 上櫃）
                calc_source = source
                if source and source.upper() in ("TPEX", "OTC"):
                    calc_source = "上櫃"
                elif source and source.upper() in ("TWSE", "TSE"):
                    calc_source = "上市"
                
                results_lines, _, _ = DispositionCalculator.calculate_conditions(
                    history_df=df, source=calc_source, 
                    shares_outstanding=shares, 
                    stock_name=record.get("name", "")
                )
                
                # 判定必進處置：
                # 嚴格條件：calculator 明確輸出「必進處置」(Clause 1 目標 < 跌停價)
                # 不使用「可跌」fallback，因為 auto_tag 無法知道 needed 次數
                is_must_enter = any("必進處置" in line for line in results_lines)

                if is_must_enter and "tag_001" not in tags:
                    tags.append("tag_001")
                    print(f"[AutoTag] {code_str} ({date_str}) added tag_001 (必進處置)")
                elif not is_must_enter and "tag_001" in tags:
                    tags.remove("tag_001")
                    print(f"[AutoTag] {code_str} ({date_str}) removed tag_001 (必進處置)")

            # Check 5to20 (20-minute) logic
            # [Fix] 增加韌性：若 DisposalDatabase 缺失資料，則從 agg_cache 補完。
            # [Fix] 同時查詢 T+1（下一交易日），因為聽牌日(T)的紀錄代表股票「即將」進處置，
            #       實際處置從 T+1 才開始，所以 T 日的 active_disposals 查不到。
            disposal_db = DisposalDatabase()
            
            max_freq_min = 0
            
            def _extract_freq_from_measure(measure_text):
                """從處置措施文字中提取撮合頻率（分鐘數）"""
                m_text = str(measure_text)
                for zh, ar in {"四十五": "45", "二十五": "25", "二十": "20", "五": "5", "十": "10", "二": "2"}.items():
                    m_text = m_text.replace(zh, ar)
                m_match = re.search(r'(\d+)\s*分鐘', m_text)
                return int(m_match.group(1)) if m_match else 0
            
            # 1. 查詢 T 日的 active_disposals（股票已在處置中的情況）
            active_recs = disposal_db.get_active_disposals(date_str)
            for r in active_recs:
                if str(r.get('code')) == str(code_str):
                    freq = _extract_freq_from_measure(r.get('measure', ''))
                    max_freq_min = max(max_freq_min, freq)
            
            # 2. [Fix] 查詢 T+1 日的 active_disposals（股票即將進入處置的情況）
            if max_freq_min == 0:
                try:
                    from core.utils import DateUtils
                    t_plus_1 = DateUtils.get_next_trading_day(
                        datetime.strptime(date_str, "%Y-%m-%d")
                    )
                    t_plus_1_str = t_plus_1.strftime("%Y-%m-%d")
                    future_recs = disposal_db.get_active_disposals(t_plus_1_str)
                    for r in future_recs:
                        if str(r.get('code')) == str(code_str):
                            freq = _extract_freq_from_measure(r.get('measure', ''))
                            max_freq_min = max(max_freq_min, freq)
                except Exception as e:
                    print(f"[AutoTag] T+1 disposal check failed for {code_str}: {e}")
            
            # 3. [Enhancement] 如果資料庫沒查到，改查 agg_cache（包含 T, T-1, T+1）
            if max_freq_min == 0:
                try:
                    from core.cache import CacheManager
                    from core.utils import DateUtils
                    cm = CacheManager()
                    # 嘗試比對當天、前一天、以及下一交易日的快取
                    check_dates = []
                    for offset in [0, 1]:
                        check_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=offset)
                        check_dates.append(check_dt)
                    # 加入 T+1
                    try:
                        t_plus_1 = DateUtils.get_next_trading_day(
                            datetime.strptime(date_str, "%Y-%m-%d")
                        )
                        check_dates.append(t_plus_1)
                    except:
                        pass
                    
                    for check_dt in check_dates:
                        agg_date_key = check_dt.strftime("%Y%m%d")
                        agg_data = cm.get_agg_data(agg_date_key)
                        if agg_data and str(code_str) in agg_data:
                            ad_rec = agg_data[str(code_str)]
                            # 檢查 is_disposed 或 future_measure
                            if ad_rec.get("is_disposed", False):
                                freq = _extract_freq_from_measure(ad_rec.get('measure', ''))
                                if freq > 0:
                                    max_freq_min = max(max_freq_min, freq)
                                    break
                            # [Fix] 檢查 future_measure（即將進入處置的措施）
                            future_meas = ad_rec.get('future_measure', '')
                            if future_meas:
                                freq = _extract_freq_from_measure(future_meas)
                                if freq > 0:
                                    max_freq_min = max(max_freq_min, freq)
                                    break
                except: pass

            # [2026-08-10 修法] 處置撮合頻率新制統一改為約2分鐘一次（第一次與再次處置皆同），
            # 不再有「第一次5分鐘、再次處置20分鐘」的階段性差異，故 tag_002(5to20) 這個
            # 「提醒即將從5分鐘變20分鐘」的判斷只對新制生效前的舊資料有意義。
            RULE_CHANGE_DATE = "2026-08-10"
            is_pre_rule_change = date_str < RULE_CHANGE_DATE

            if is_pre_rule_change and max_freq_min == 5 and "tag_002" not in tags:
                tags.append("tag_002")
                print(f"[AutoTag] {code_str} ({date_str}) added tag_002 (5to20)")
            # 若已在 10 分以上，移除既有的 tag_002（可能是之前在 5 分時加的）
            elif max_freq_min >= 10 and "tag_002" in tags:
                tags.remove("tag_002")
                print(f"[AutoTag] {code_str} ({date_str}) removed tag_002 (already at {max_freq_min}分)")
            # 新制生效後：處置撮合頻率不再有5分鐘階段，tag_002 對新資料不應再被加註
            elif not is_pre_rule_change and max_freq_min > 0 and "tag_002" in tags:
                tags.remove("tag_002")
                print(f"[AutoTag] {code_str} ({date_str}) removed tag_002 (新制後不再適用5to20判斷)")
                    
        except Exception as e:
            print(f"[AutoTag] Error processing tags for {code_str} {date_str}: {e}")
            import traceback
            traceback.print_exc()
            
        # 無論 tag 是增加或移除，只要有變化就更新
        if tags != original_tags:
            record["tags"] = tags

    def retag_recent(self, days=7):
        """重新計算最近 N 天紀錄的自動標籤（不影響手動標籤）。"""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        updated = 0
        for record in self.history:
            if record.get("date", "") >= cutoff and len(str(record.get("code", ""))) == 4:
                old_tags = list(record.get("tags", []))
                try:
                    self._auto_tag_record(record)
                except Exception as e:
                    print(f"[retag] Error for {record.get('code')} {record.get('date')}: {e}")
                if record.get("tags", []) != old_tags:
                    updated += 1
                    print(f"[retag] {record.get('code')} ({record.get('date')}): {old_tags} -> {record.get('tags', [])}")
        if updated > 0:
            self.save()
            print(f"[retag] Updated {updated} records.")
        else:
            print("[retag] No changes.")
        return updated

    def get_all(self):
        # Double check filter
        valid = []
        for x in self.history:
            code = str(x.get("code", ""))
            if len(code) > 4 or "." in code: continue
            valid.append(x)
        return sorted(valid, key=lambda x: x["date"], reverse=True)

    def get_listening_data(self, date_obj):
        """Retrieve listening records for a specific date (datetime object or string YYYY-MM-DD)."""
        if isinstance(date_obj, str):
            target_str = date_obj
        else:
            target_str = date_obj.strftime("%Y-%m-%d")
            
        results = []
        for record in self.history:
            code = str(record.get("code", ""))
            if len(code) > 4 or "." in code: continue
            
            if record.get("date") == target_str:
                results.append(record)
        return results

    def update_tags(self, date, code, tags):
        for item in self.history:
            if item["date"] == date and item["code"] == code:
                item["tags"] = tags
                self.save()
                return

    def update_comment(self, date, code, comment):
        for item in self.history:
            if item["date"] == date and item["code"] == code:
                item["comment"] = comment
                self.save()
                return

    def update_record(self, date, code, tags=None, comment=None, **kwargs):
        """Generic update method for UI calls."""
        updated = False
        for item in self.history:
            if item["date"] == date and item["code"] == code:
                if tags is not None:
                    item["tags"] = tags
                    updated = True
                if comment is not None:
                    item["comment"] = comment
                    updated = True
                if "review" in kwargs:
                    item["review"] = kwargs["review"]
                    updated = True
                
                if updated:
                    self.save()
                return
    
    
    def get_records_by_tag(self, tag_id):
        """取得包含特定標籤的所有紀錄"""
        return [
            record for record in self.history
            if tag_id in record.get("tags", [])
        ]
    
    def get_tag_usage_count(self, tag_id):
        """取得特定標籤被使用的次數"""
        count = 0
        for record in self.history:
            if tag_id in record.get("tags", []):
                count += 1
        return count
    
    def get_all_used_tags(self):
        """取得所有已被使用的標籤 ID（不重複）"""
        used_tags = set()
        for record in self.history:
            tags = record.get("tags", [])
            used_tags.update(tags)
        return list(used_tags)
    
    def remove_tag_from_all_records(self, tag_id):
        """從所有紀錄中移除特定標籤（用於刪除標籤時清理）"""
        updated = False
        for record in self.history:
            tags = record.get("tags", [])
            if tag_id in tags:
                tags.remove(tag_id)
                record["tags"] = tags
                updated = True
        
        if updated:
            self.save()
            print(f"已從所有紀錄中移除標籤: {tag_id}")

    def _get_futures_list(self):
        """Lazy load futures list from cache or fetch new"""
        if hasattr(self, '_cached_futures_list') and self._cached_futures_list:
            return self._cached_futures_list
        
        try:
            from .fetcher import StockFetcher
            from .parser import StockParser
            
            print("[HistoryManager] Fetching futures list for new record check...")
            fetcher = StockFetcher()
            parser = StockParser()
            raw_list = fetcher.fetch_taifex_futures_list()
            if raw_list:
                self._cached_futures_list = set(parser.parse_taifex_futures_list(raw_list))
                print(f"[HistoryManager] Cached {len(self._cached_futures_list)} futures codes.")
                return self._cached_futures_list
        except Exception as e:
            print(f"[HistoryManager] Failed to fetch futures list: {e}")
        
        return set()

    def _check_has_futures(self, code):
        """Check if stock has futures"""
        futures_list = self._get_futures_list()
        return code in futures_list

