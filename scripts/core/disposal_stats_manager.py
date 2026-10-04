"""
處置統計數據管理模組

提供處置股票的統計分析功能，包括：
- 處置期間解析
- 漲跌幅數據計算
- 統計數據計算
"""

from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import pandas as pd
from core.utils import DateUtils
from core.cache import CacheManager
from core.finmind_client import FinMindClient
from core.cache import CacheManager
from core.finmind_client import FinMindClient
from core.price_database import PriceDatabase
from core.market_cache import MarketDataCache


class DisposalStatsManager:
    """處置統計數據管理類別"""
    
    def __init__(self, cache_manager=None, fm_client=None, price_db=None, fetcher=None):
        """
        初始化
        
        Args:
            cache_manager: CacheManager 實例（可選）
            fm_client: FinMindClient 實例 （可選）
            price_db: PriceDatabase 實例（可選）
            fetcher: StockFetcher 實例（可選，用於交易日驗證）
        """
        self.cache = cache_manager if cache_manager else CacheManager()
        
        # 初始化 FinMind 客戶端（備用）
        if fm_client:
            self.fm_client = fm_client
        else:
            self.fm_client = FinMindClient(None)
        
        # 初始化本地價格資料庫（優先使用）
        self.price_db = price_db if price_db else PriceDatabase("stock_prices.db")
        
        # 初始化市場數據快取 (用於股本等)
        self.market_cache = MarketDataCache()
        
        # 初始化 Fetcher（用於交易日驗證）
        self.fetcher = fetcher
    
    def parse_disposal_period(self, period_str: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        解析處置期間字串（民國年格式）
        
        Args:
            period_str: 處置期間字串，格式：YYY/MM/DD~YYY/MM/DD 或 YYYMMDD-YYYMMDD
        
        Returns:
            tuple: (start_date, end_date) 或 (None, None)
        """
        if not period_str or not period_str.strip():
            return None, None
        
        # 1. Split logic
        s = period_str.strip()
        parts = []
        
        if "~" in s or "～" in s:
            s = s.replace("～", "~")
            parts = s.split("~")
        elif "-" in s:
            # Handle "1150113-1150126"
            if s.count("-") == 1:
                parts = s.split("-")
        
        if len(parts) != 2:
            return None, None
        
        start_str = parts[0].strip()
        end_str = parts[1].strip()
        
        def parse_roc_date(roc_str: str) -> Optional[datetime]:
            """解析民國年日期字串"""
            try:
                roc_str = roc_str.strip()
                if not roc_str or roc_str == "-":
                    return None
                
                # Format 1: YYY/MM/DD
                if "/" in roc_str:
                    parts = roc_str.split("/")
                    if len(parts) == 3:
                        roc_year = int(parts[0])
                        month = int(parts[1])
                        day = int(parts[2])
                        return datetime(roc_year + 1911, month, day)
                        
                # Format 2: YYYMMDD (Compact)
                if len(roc_str) == 7 and roc_str.isdigit():
                    roc_year = int(roc_str[:3])
                    month = int(roc_str[3:5])
                    day = int(roc_str[5:])
                    return datetime(roc_year + 1911, month, day)
                    
                return None
            except (ValueError, IndexError):
                return None
        
        start_date = parse_roc_date(start_str)
        end_date = parse_roc_date(end_str)
        
        return start_date, end_date
    
    def get_trading_days_in_range(self, start_date: datetime, end_date: datetime, allow_download: bool = True, check_na_dates: List[datetime] = None) -> List[datetime]:
        """
        取得指定範圍內的所有交易日
        使用實際市場資料驗證，正確處理國定假日
        
        Args:
            start_date: 開始日期
            end_date: 結束日期
            allow_download: 是否允許下載驗證 (Default: True)
            check_na_dates: 需要額外檢查的日期清單(例如 -1 和處置日),只有這些日期會觸發 TAIEX 下載
        
        Returns:
            list: 交易日列表（datetime 物件）
        """
        trading_days = []
        
        # [Optimization] 批量預先下載/快取交易日資料
        # 只有當 check_na_dates 有值時才下載 TAIEX (即只在 -1 和處置日為 N/A 時)
        if allow_download and self.fetcher is not None and self.fm_client is not None:
            if check_na_dates:
                # 只下載需要檢查的日期範圍
                if check_na_dates:
                    min_date = min(check_na_dates)
                    max_date = max(check_na_dates)
                    s_str = min_date.strftime("%Y%m%d")
                    e_str = max_date.strftime("%Y%m%d")
                    print(f"DEBUG: Downloading TAIEX for specific dates: {s_str} to {e_str}")
                    self.fetcher.ensure_trading_days_cache(s_str, e_str, self.fm_client)
            # 如果 check_na_dates 為 None,則完全不下載 TAIEX

        current = start_date
        
        while current <= end_date:
            is_valid_day = False
            
            # 優先使用 fetcher 的 verify_market_open（有快取支援）
            if self.fetcher is not None:
                # 轉換為 YYYYMMDD 格式
                date_str = current.strftime("%Y%m%d")
                
                # 判斷是否需要下載:只有在 check_na_dates 包含此日期時才允許下載
                should_allow_download = False
                if allow_download and check_na_dates:
                    if current in check_na_dates:
                        should_allow_download = True
                
                # 傳入 allow_download，如果為 False 且無快取將返回 None
                verified = self.fetcher.verify_market_open(
                    date_str, 
                    fm_client=self.fm_client, 
                    allow_download=should_allow_download
                )
                
                if verified is True:
                    is_valid_day = True
                elif verified is False:
                    is_valid_day = False
                else: 
                    # verified is None (無法判定) -> 回退到舊邏輯
                    # 這是為了避免在離線模式下卡住，雖然可能誤判國定假日
                    if DateUtils.is_trading_day(current):
                        is_valid_day = True
            else:
                # 回退到舊邏輯（僅判斷週末，未處理國定假日）
                if DateUtils.is_trading_day(current):
                    is_valid_day = True
            
            if is_valid_day:
                trading_days.append(current)
            
            current += timedelta(days=1)
        
        return trading_days
    
    def fetch_price_data(self, code: str, trading_days: List[datetime], allow_download: bool = True) -> Dict[datetime, float]:
        """
        獲取指定交易日的收盤價數據（優先使用本地資料庫）
        
        Args:
            code: 股票代號
            trading_days: 交易日列表
            allow_download: 是否允許從網路下載數據 (Defualt: True)
        
        Returns:
            dict: {date: close_price}，價格為 None 表示無數據
        """
        if not trading_days:
            return {}
            
        # [Fix] Sanitize code (remove .TW/TWO suffixes and .0 decimal)
        code = str(code).upper().replace(".TW", "").replace(".TWO", "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        
        prices = {}
        missing_days = []  # 本地無數據的日期
        
        # 1. 優先從本地資料庫查詢
        for day in trading_days:
            date_str = day.strftime("%Y-%m-%d")
            price_record = self.price_db.get_price(code, date_str)
            
            if price_record is not None:
                val = price_record.get('close')
                # [Fix] If cached value is None but we allow download, treat as missing to retry
                # preventing permanent N/A if first attempt failed
                if val is None and allow_download:
                    missing_days.append(day)
                else:
                    prices[day] = val
            else:
                missing_days.append(day)
        
        # 2. 如果有缺失
        if missing_days:
            # [Fix] Filter out future dates to avoid redundant downloads
            # Ensure we don't try to download data for tomorrow or later
            today_date = datetime.now().date()
            
            download_candidates = []
            for d in missing_days:
                if d.date() <= today_date:
                    download_candidates.append(d)
                else:
                    prices[d] = None # Future date, no data expected
            
            if download_candidates and allow_download:
                print(f"遺失 {code} 共 {len(download_candidates)} 天數據，嘗試從快取與網路抓取...")
                
                start_str = min(download_candidates).strftime("%Y-%m-%d")
                
                # 1. 嘗試從本地 MarketDataCache 抓取 (快取優先策略)
                # [Fix 2026-09-01] get_price_history() 回傳的 DataFrame 日期是叫做
                # 'Date'(大寫)的 index，不是叫 'date'(小寫)的欄位——原本這裡一律
                # KeyError、被 except 吞掉，導致這層本地快取形同虛設，每次都直接
                # 落到下面的 Shioaji/FinMind 網路請求，明明本地已經有資料也要重抓。
                try:
                    df_cache = self.market_cache.get_price_history(code, start_str)
                    if df_cache is not None and not df_cache.empty:
                        import pandas as pd
                        for day in list(download_candidates):
                            day_normalized = pd.Timestamp(day).normalize()
                            if day_normalized in df_cache.index:
                                close_val = df_cache.loc[day_normalized, 'Close']
                                if pd.notna(close_val):
                                    prices[day] = float(close_val)
                                    self.price_db.save_price(code, day.strftime("%Y-%m-%d"), {"close": prices[day]})
                                    download_candidates.remove(day)
                except Exception as e:
                    print(f"MarketDataCache 讀取錯誤: {e}")
                    
                # 2. 如果還有缺失，嘗試 Shioaji API (不會被限流)
                if download_candidates:
                    try:
                        import sys, os
                        import pandas as pd
                        if os.path.abspath('scripts') not in sys.path:
                            sys.path.insert(0, os.path.abspath('scripts'))
                        from core.shioaji_client import ShioajiClient
                        sj_client = ShioajiClient()
                        
                        end_str = max(download_candidates).strftime("%Y-%m-%d")
                        df_sj = sj_client.get_kbars(code, start_str, end_str)
                        
                        if df_sj is not None and not df_sj.empty:
                            for day in list(download_candidates):
                                day_normalized = pd.Timestamp(day).normalize()
                                if day_normalized in df_sj.index:
                                    prices[day] = float(df_sj.loc[day_normalized, 'Close'])
                                    self.price_db.save_price(code, day.strftime("%Y-%m-%d"), {"close": prices[day]})
                                    download_candidates.remove(day)
                    except Exception as e:
                        print(f"ShioajiAPI 抓取失敗: {e}")

                # 3. 如果還是有缺失，退回使用 FinMind (可能被限流)
                if download_candidates:
                    try:
                        import pandas as pd
                        from datetime import timedelta
                        start = min(download_candidates) - timedelta(days=5)
                        df_fm = self.fm_client.fetch_daily_price(
                            stock_id=code,
                            start_date=start.strftime("%Y-%m-%d")
                        )
                        if df_fm is not None and not df_fm.empty:
                            df_fm['date'] = pd.to_datetime(df_fm['date'])
                            for day in list(download_candidates):
                                day_normalized = pd.Timestamp(day).normalize()
                                matching = df_fm[df_fm['date'] == day_normalized]
                                if not matching.empty:
                                    prices[day] = float(matching.iloc[0]['close'])
                                    self.price_db.save_price(code, day.strftime("%Y-%m-%d"), {"close": prices[day]})
                                    download_candidates.remove(day)
                    except Exception as e:
                        print(f"FinMind 抓取失敗: {e}")

                # 4. 剩下真的找不到的，存成 None 避免重複查詢
                for day in download_candidates:
                    prices[day] = None
                    self.price_db.save_price(code, day.strftime("%Y-%m-%d"), {"close": None})
            else:
                for day in download_candidates:
                    prices[day] = None
        
        return prices
    
    def calculate_price_changes(
        self, 
        code: str, 
        disposal_start: datetime, 
        disposal_end: datetime, 
        days_after: int = 5,
        allow_download: bool = True
    ) -> Tuple[List[str], Dict[str, Optional[float]], Dict[str, str], bool]:
        """
        計算處置日前後的漲跌幅
        
        Args:
            code: 股票代號
            disposal_start: 處置開始日
            disposal_end: 處置結束日
            days_after: 出關後計算幾日（預設 5）
        
        Returns:
            tuple: (column_headers, changes_dict, dates_dict, fetched_flag)
            - column_headers: ["-1", "處置日", "+1", "+2", ..., "出關日", "出+1", "出+2", ...]
            - changes_dict: {header: change_pct or None}
            - dates_dict: {header: date_str} (例如 "2023-01-01")
            - fetched_flag: bool (True 表示此次計算有觸發網路下載，False 表示全命中快取)
        """
        changes = {}
        dates = {}
        headers = []
        
        # [Fix] Sanitize code (remove .TW/TWO suffixes and .0 decimal)
        code = str(code).upper().replace(".TW", "").replace(".TWO", "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        
        # 1. 取得處置期間所有交易日（包含前一日和出關後 days_after 日）
        range_start = disposal_start - timedelta(days=30)  # 預留空間找前一交易日 (延長至30天以跨越春節等長假)
        range_end = disposal_end + timedelta(days=20)      # 預留空間找出關後交易日
        
        # [Optimization] 設定需要檢查交易日(下載 TAIEX)的範圍：只針對處置開始前的這段期間
        # 這樣可以確保 "-1" 和 "處置日" 的交易日判斷是準確的，同時避免下載整個期間的 TAIEX
        # 範圍：[range_start, disposal_start]
        check_na_dates = []
        curr_check = range_start
        while curr_check <= disposal_start:
            check_na_dates.append(curr_check)
            curr_check += timedelta(days=1)
            
        all_trading_days = self.get_trading_days_in_range(
            range_start, 
            range_end, 
            allow_download=allow_download,
            check_na_dates=check_na_dates
        )
        
        if not all_trading_days:
            return [], {}, {}, False
        
        # 2. 找出處置開始日和結束日在交易日列表中的索引
        try:
            start_idx = next(i for i, d in enumerate(all_trading_days) if d >= disposal_start)
            end_idx = next(i for i, d in enumerate(all_trading_days) if d >= disposal_end)
        except StopIteration:
            return [], {}, {}, False  # 數據不足
        
        # 3. 抓取價格數據
        needed_days = [d for d in all_trading_days]
        missing_in_cache = []
        
        # Pre-check cache to determine if download WILL happen
        for day in needed_days:
            # Check DB
            cached_rec = self.price_db.get_price(code, day.strftime("%Y-%m-%d"))
            
            # Condition: Record missing OR Record exists but value is None (and we want to retry)
            is_missing = (cached_rec is None)
            if not is_missing and allow_download and cached_rec.get('close') is None:
                 is_missing = True
            
            if is_missing:
                # Check if it's a future date (which won't trigger download)
                if day.date() <= datetime.now().date():
                    missing_in_cache.append(day)
        
        # If missing_in_cache is not empty AND allow_download is True, then we are fetching.
        fetched_flag = (len(missing_in_cache) > 0) and allow_download

        prices = self.fetch_price_data(code, all_trading_days, allow_download=allow_download)
        
        # 4. 計算漲跌幅並生成欄位標題
        
        # 4. 計算漲跌幅並生成欄位標題
        
        # -1（處置前一日）
        if start_idx > 0:
            prev_idx = start_idx - 1
            change = self._calculate_change(prices, all_trading_days, prev_idx)
            headers.append("-1")
            changes["-1"] = change
            dates["-1"] = all_trading_days[prev_idx].strftime("%Y-%m-%d")
        
        # 處置日 (Start) 到 處置結束日 (End)
        # 用戶要求：Start, +1, +2... End (中間全部列出)
        # 重新定義：
        # Start = 處置日
        # End = 處置結束
        # 其餘天數為 +N
        
        # 計算處置期間總交易日數
        disposal_days_count = end_idx - start_idx + 1
        
        for i in range(disposal_days_count):
            current_day_idx = start_idx + i
            
            if i == 0:
                label = "處置日"
            elif i == disposal_days_count - 1:
                label = "處置結束"
            else:
                # [New] 用戶要求：處置日後 + 到 5 就好
                if i > 5:
                    continue
                label = f"+{i}"
            
            change = self._calculate_change(prices, all_trading_days, current_day_idx)
            headers.append(label)
            changes[label] = change
            dates[label] = all_trading_days[current_day_idx].strftime("%Y-%m-%d")
            
        # 出關日 (End + 1) 及相關欄位
        # 新的順序：出關日 → 出-5 ~ 出-1 → 出+1 ~ 出+5
        
        exit_day_idx = end_idx + 1
        
        # 1. 先加入「出關日」欄位
        if exit_day_idx < len(all_trading_days):
            label = "出關日"
            change = self._calculate_change(prices, all_trading_days, exit_day_idx)
            headers.append(label)
            changes[label] = change
            dates[label] = all_trading_days[exit_day_idx].strftime("%Y-%m-%d")
        
        # 2. 新增「出-5」～「出-1」（出關前倒數，由遠到近排序）
        for offset in range(5, 0, -1):  # 5, 4, 3, 2, 1
            idx = exit_day_idx - offset
            if idx >= 0 and idx < len(all_trading_days):
                label = f"出-{offset}"
                change = self._calculate_change(prices, all_trading_days, idx)
                headers.append(label)
                changes[label] = change
                dates[label] = all_trading_days[idx].strftime("%Y-%m-%d")
        
        # 3. 最後加入「出+1」～「出+N」（出關後）
        for offset in range(1, days_after + 1):
            idx = exit_day_idx + offset
            if idx < len(all_trading_days):
                label = f"出+{offset}"
                change = self._calculate_change(prices, all_trading_days, idx)
                headers.append(label)
                changes[label] = change
                dates[label] = all_trading_days[idx].strftime("%Y-%m-%d")
        
        return headers, changes, dates, fetched_flag
    
    def _calculate_change(
        self, 
        prices: Dict[datetime, Optional[float]], 
        trading_days: List[datetime], 
        current_idx: int
    ) -> Optional[float]:
        """
        計算特定交易日的漲跌幅
        會往前尋找最近一個有效的收盤價（跳過無成交日）
        
        Args:
            prices: 價格字典
            trading_days: 交易日列表
            current_idx: 當前索引
        
        Returns:
            float: 漲跌幅百分比，若無數據或無成交則返回 None
        """
        if current_idx == 0:
            return None  # 第一天沒有前日數據
        
        curr_day = trading_days[current_idx]
        curr_close = prices.get(curr_day)
        
        # 當日收盤價無效，返回 None
        if curr_close is None or curr_close <= 0:
            return None
        
        # 往前尋找最近一個有效的收盤價
        prev_close = None
        for i in range(current_idx - 1, -1, -1):
            prev_day = trading_days[i]
            prev_close = prices.get(prev_day)
            if prev_close is not None and prev_close > 0:
                break  # 找到有效的前一日收盤價
        
        # 如果找到有效的前一日收盤價，計算漲跌幅
        if prev_close is not None and prev_close > 0:
            return round((curr_close - prev_close) / prev_close * 100, 2)
        else:
            return None  # 找不到有效的前一日收盤價
    
    def calculate_column_statistics(self, values: List[Optional[float]]) -> Dict[str, float]:
        """
        計算單一欄位的統計數據
        
        Args:
            values: 漲跌幅列表（可能包含 None）
        
        Returns:
            dict: {
                "total": 總漲跌幅,
                "average": 平均漲跌幅,
                "up_pct": 上漲百分比,
                "down_pct": 下跌百分比
            }
        """
        # 過濾掉 None 值
        valid_values = [v for v in values if v is not None]
        
        if not valid_values:
            return {
                "total": 0.0,
                "average": 0.0,
                "up_pct": 0.0,
                "down_pct": 0.0
            }
        
        total = sum(valid_values)
        average = total / len(valid_values)
        
        # 零值算上漲
        up_count = sum(1 for v in valid_values if v >= 0)
        down_count = sum(1 for v in valid_values if v < 0)
        total_count = len(valid_values)
        
        up_pct = (up_count / total_count * 100) if total_count > 0 else 0.0
        down_pct = (down_count / total_count * 100) if total_count > 0 else 0.0
        
        return {
            "total": round(total, 2),
            "average": round(average, 2),
            "up_pct": round(up_pct, 1),
            "down_pct": round(down_pct, 1)
        }
    
    def get_stock_capital(self, code: str, allow_download: bool = True) -> Optional[float]:
        """
        取得股本資料（億元）
        
        Args:
            code: 股票代號
            allow_download: 是否允許下載 (Default: True)
        
        Returns:
            float: 股本（億元），若無資料則返回 None
        """
        try:
            # [Fix] Sanitize code (remove .TW/TWO suffixes and .0 decimal)
            code = str(code).upper().replace(".TW", "").replace(".TWO", "").strip()
            if code.endswith(".0"):
                code = code[:-2]

            # 1. Check Cache
            shares = self.market_cache.get_stock_info(code)
            
            # 2. If missing, Fetch from FinMind
            if shares is None or shares == 0:
                if allow_download:
                    print(f"DEBUG: Fetching capital for {code} from FinMind...")
                    shares = self.fm_client.fetch_stock_info(code)
                    
                    if shares and shares > 0:
                        self.market_cache.save_stock_info(code, shares)
                    else:
                        # [Fix] Cache 0 (No Data) to prevent repeated downloads
                        self.market_cache.save_stock_info(code, 0)
                else:
                    return None
            
            if shares is not None:
                if shares == 0: return None # Handle cached 0
                # StockInfo returns Shares (Capital / 10).
                # We want Capital in Billions (Capital / 100,000,000).
                # Billions = (Shares * 10) / 100,000,000 = Shares / 10,000,000
                return shares / 10000000.0
            
            return None
            
        except Exception as e:
            print(f"Error fetching capital for {code}: {e}")
            return None
