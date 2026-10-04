
import requests
import json
import time
from datetime import datetime, timedelta
import pandas as pd
import sqlite3
from core.finmind_client import FinMindClient
from core.market_cache import MarketDataCache

class StockFetcher:
    def __init__(self, db_path=None, api_token=None):
        from core.runtime import get_paths
        self.db_path = db_path or get_paths().market_db
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive"
        }
        # Initialize FinMind and Cache
        self.fm_client = FinMindClient(api_token)
        self.cache = MarketDataCache()
        
    def _convert_to_roc_date(self, date_str):
        """YYYYMMDD -> YYY/MM/DD (ROC)"""
        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            roc_year = dt.year - 1911
            return f"{roc_year}/{dt.month:02d}/{dt.day:02d}"
        except:
            return date_str # Fallback

    def check_market_type(self, code):
        """
        Check if stock is TWSE (tse) or TPEX (otc) via MIS API.
        Returns: "上市", "上櫃", or None
        """
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{code}.tw|otc_{code}.tw"
        try:
            requests.packages.urllib3.disable_warnings()
            res = requests.get(url, headers=self.headers, timeout=5, verify=False)
            if res.status_code == 200:
                data = res.json()
                if "msgArray" in data:
                    for item in data["msgArray"]:
                        if item.get("c") == code:
                            ex = item.get("ex")
                            if ex == "tse": return "上市"
                            if ex == "otc": return "上櫃"
        except Exception as e:
            print(f"Error checking market type for {code}: {e}")
        return None

    def fetch_all_shares_outstanding(self):
        """
        一次性抓「全市場」已發行股數，取代逐檔向 FinMind 查詢季報股本的作法。

        [Fix 2026-08-23] 舊作法(fetch_stock_history 內逐檔呼叫 FinMind
        TaiwanStockBalanceSheet)曾發生股本增資後快取沒跟上的問題(8033、4971都
        發生過)，導致周轉率換算出來的張數門檻算錯。改用 TWSE/TPEX 官方「公司
        基本資料」開放資料，裡面的「已發行普通股數」是公司自己申報的最新數字，
        且一次 API 呼叫就能拿到全市場（上市約1000檔+上櫃約900檔），比逐檔查
        FinMind 更準也更快。

        Returns: dict {code: shares_outstanding(float)}，任一來源失敗則該來源
                 略過(不影響另一來源)，全部失敗回傳空 dict。
        """
        shares_map = {}

        # 上市：公司代號 + 已發行普通股數或TDR原股發行股數
        try:
            res = requests.get(
                "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                headers=self.headers, timeout=15, verify=False
            )
            if res.status_code == 200:
                for row in res.json():
                    code = row.get("公司代號")
                    try:
                        sh = float(row.get("已發行普通股數或TDR原股發行股數") or 0)
                    except (TypeError, ValueError):
                        sh = 0
                    if code and sh > 0:
                        shares_map[code] = sh
                print(f"[Fetcher] TWSE 官方股數資料: {len(shares_map)} 檔")
        except Exception as e:
            print(f"[Fetcher] TWSE 官方股數資料抓取失敗: {e}")

        # 上櫃：SecuritiesCompanyCode + IssueShares
        try:
            res = requests.get(
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
                headers=self.headers, timeout=15, verify=False
            )
            if res.status_code == 200:
                before = len(shares_map)
                for row in res.json():
                    code = row.get("SecuritiesCompanyCode")
                    try:
                        sh = float(row.get("IssueShares") or 0)
                    except (TypeError, ValueError):
                        sh = 0
                    if code and sh > 0:
                        shares_map[code] = sh
                print(f"[Fetcher] TPEX 官方股數資料: {len(shares_map) - before} 檔")
        except Exception as e:
            print(f"[Fetcher] TPEX 官方股數資料抓取失敗: {e}")

        return shares_map

    def fetch_margin_eligible_stocks(self, date_str=None):
        """
        Fetch set of stocks that have Margin Trading data (Implies Short Selling enabled).
        Returns: set of stock codes
        """
        try:
            if not date_str:
                date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                
            # Use FinMind to get ALL stocks margin data for one day
            result = self.fm_client.api.get_data(
                dataset="TaiwanStockMarginPurchaseShortSale",
                start_date=date_str,
                end_date=date_str
            )
            
            # [Fix] Handle both DataFrame and dict response
            if result is not None:
                print(f"[DEBUG] Margin API result type: {type(result)}")
                if isinstance(result, dict):
                    print(f"[DEBUG] Dict keys: {result.keys()}")
                # Case 1: Direct DataFrame
                if hasattr(result, 'empty') and not result.empty:
                    return set(result['stock_id'].astype(str).tolist())
                # Case 2: Dict with 'data' key
                elif isinstance(result, dict) and 'data' in result:
                    df = pd.DataFrame(result['data'])
                    if not df.empty:
                        print(f"[DEBUG] Margin data loaded: {len(df)} records")
                        return set(df['stock_id'].astype(str).tolist())
            print(f"[DEBUG] No margin data available")
            return set()
        except Exception as e:
            print(f"Error fetching margin list: {e}")
            return set()

    def fetch_twse_attention(self, date_str=None):
        """
        抓取證交所注意股票 (RWD API)
        date_str: YYYYMMDD
        Note: The API seems to require startDate and endDate for reliable fetching of historical data.
        """
        url = "https://www.twse.com.tw/rwd/zh/announcement/notice?response=json"
        if date_str:
            # url += f"&date={date_str}" # Old method, unreliable for history?
            url += f"&startDate={date_str}&endDate={date_str}"
            
        try:
            # TWSE rate limit is strict, adding small delay if calling in loop is advised outside
            requests.packages.urllib3.disable_warnings()
            res = requests.get(url, headers=self.headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            return data
        except Exception as e:
            # print(f"Error fetching TWSE attention {date_str}: {e}")
            return None

    def fetch_twse_disposition(self, date_str=None):
        """抓取證交所處置股票"""
        url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
        
        if date_str:
            # url += f"&date={date_str}" 
            # MODIFIED: Query a wider range (Target + 20 days) to catch "Future Dispositions" announced today.
            # Because API filters by "Active Period", querying only today misses tomorrows start.
            try:
                dt = datetime.strptime(date_str, "%Y%m%d")
                # [Fix] Widen search window start date to catch announcements from recent days 
                # (e.g. Announced Friday, Query Monday)
                start_dt = dt - timedelta(days=5)
                start_str = start_dt.strftime("%Y%m%d")
                
                end_dt = dt + timedelta(days=20)
                end_str = end_dt.strftime("%Y%m%d")
                url += f"&startDate={start_str}&endDate={end_str}"
            except:
                # Fallback to simple date param if parsing fails
                url += f"&date={date_str}"
            
        try:
            requests.packages.urllib3.disable_warnings()
            res = requests.get(url, headers=self.headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            return data
        except Exception as e:
            print(f"Error fetching TWSE disposition {date_str}: {e}")
            return None

    def fetch_tpex_attention(self, date_str=None):
        """
        抓取櫃買中心注意股票 (New Portal)
        Ref: https://www.tpex.org.tw/www/zh-tw/bulletin/attention?startDate=2025/12/31&endDate=2025/12/31&response=json
        date_str: YYYYMMDD
        """
        # Default latest if no date
        start_date = ""
        end_date = ""
        
        if date_str:
            # Convert YYYYMMDD -> YYYY/MM/DD
            formatted = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
            start_date = formatted
            end_date = formatted
            
        url = f"https://www.tpex.org.tw/www/zh-tw/bulletin/attention?startDate={start_date}&endDate={end_date}&response=json"
            
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            return data
        except Exception as e:
            # print(f"Error fetching TPEX attention {date_str}: {e}")
            return None

    def ensure_trading_days_cache(self, start_date: str, end_date: str, fm_client):
        """
        確保指定範圍內的交易日資料已快取 (批量優化 + 智能檢查)
        使用加權指數 (TAIEX) 作為交易日的參考標的
        1. 檢查 DB 中該範圍的資料覆蓋率
        2. 若覆蓋率不足，則一次性下載該範圍 (有限制 end_date)
        """
        try:
            # 1. 轉換日期格式
            try:
                s_dt = datetime.strptime(start_date, "%Y%m%d")
                e_dt = datetime.strptime(end_date, "%Y%m%d")
            except ValueError:
                return

            if s_dt > e_dt:
                return

            total_days = (e_dt - s_dt).days + 1
            if total_days <= 0:
                return

            # [Optimization] Check Cache Coverage
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM trading_day_cache WHERE date >= ? AND date <= ?",
                (start_date, end_date)
            )
            cached_count = cursor.fetchone()[0]
            conn.close()
            
            # 如果快取數量等於總天數（甚至只要大於 95%），則視為已命中，不需下載
            # 注意：trading_day_cache 包含非交易日 (is_trading_day=0)，所以 count 應該要等於 total_days
            if cached_count >= total_days:
                # print(f"DEBUG: Cache hit for {start_date}-{end_date} (Count: {cached_count}/{total_days})")
                return
            
            # FinMind 需要 YYYY-MM-DD
            fm_start = s_dt.strftime("%Y-%m-%d")
            fm_end = e_dt.strftime("%Y-%m-%d")

            # 2. 批量下載加權指數 TAIEX (Targeted Range)
            # print(f"DEBUG: Downloading TAIEX for missing range {fm_start} to {fm_end} (Cached: {cached_count}/{total_days})")
            df = fm_client.fetch_daily_price("TAIEX", fm_start, end_date=fm_end)
            
            # [Fix] If API fails (df is None), ABORT. Do NOT write zeros to DB.
            if df is None:
                print(f"DEBUG: ensure_trading_days_cache failed to fetch TAIEX data. Aborting cache update.")
                return

            valid_dates = set()
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                valid_dates = set(df['date'].dt.strftime("%Y%m%d").tolist())

            # 3. 準備批量寫入資料
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            rows_to_insert = []
            current = s_dt
            now_str = datetime.now().isoformat()
            
            while current <= e_dt:
                d_str = current.strftime("%Y%m%d")
                
                # Check if this date needs update? 
                # Ideally we blindly update or INSERT OR IGNORE. 
                # INSERT OR REPLACE is safer to ensure correctness.
                
                # [Fix] 17:00 Rule for Today's Data
                # If deemed "Closed" (0) but it is Today (before 17:00) or Future,
                # skip writing to cache to allow fallback to DateUtils.
                today_dt = datetime.now()
                today_str = today_dt.strftime("%Y%m%d")
                current_hour = today_dt.hour
                
                is_trading = 1 if d_str in valid_dates else 0
                
                if is_trading == 0:
                     if d_str > today_str:
                         current += timedelta(days=1)
                         continue # Future: Don't cache 'Closed'
                     if d_str == today_str and current_hour < 17:
                         current += timedelta(days=1)
                         continue # Today Pre-17:00: Don't cache 'Closed'
                
                rows_to_insert.append((d_str, is_trading, now_str, "TAIEX_BULK"))
                
                current += timedelta(days=1)
            
            # 4. 執行批量寫入
            cursor.executemany(
                """INSERT OR REPLACE INTO trading_day_cache 
                   (date, is_trading_day, verified_at, verification_source) 
                   VALUES (?, ?, ?, ?)""",
                rows_to_insert
            )
            conn.commit()
            conn.close()
            # print(f"DEBUG: Updated cache for {start_date}-{end_date} using TAIEX")
            
        except Exception as e:
            print(f"Error in ensure_trading_days_cache: {e}")

    def verify_market_open(self, date_str, fm_client=None, allow_download: bool = True):
        from core.utils import DateUtils
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT is_trading_day FROM trading_day_cache WHERE date = ?",
                (date_str,)
            )
            result = cursor.fetchone()
            conn.close()
            
            if result is not None:
                return bool(result[0])
            
            # If not in cache, check if allowed to download
            if not allow_download:
                return None
                
            # If allowed, fetch from API (using TAIEX as proxy)
            if fm_client:
                # Convert YYYYMMDD to YYYY-MM-DD
                d_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                df = fm_client.fetch_daily_price("TAIEX", d_fmt)
                # API 失敗(限流/逾時，df 為 None)代表「無法判定」，不是「休市」：
                # 不能寫進快取，否則該日會被永久當成休市，後續處置統計整段抓不到資料。
                if df is None:
                    return None
                is_trading_day = False
                if df is not None:
                    # check if data exists for this specific date
                    if not df.empty and d_fmt in df['date'].values:
                         is_trading_day = True
                
                # [Fix] 17:00 Rule for Verification Result
                # If API says False (Empty), but it's Today (Pre-17:00) or Future, return None.
                # This ensures we fallback to DateUtils static calendar instead of assuming Closed.
                if not is_trading_day:
                    now = datetime.now()
                    today_s = now.strftime("%Y%m%d")
                    if date_str > today_s:
                        return None # Future: Uncertain
                    if date_str == today_s and now.hour < 17:
                        # Today Pre-17:00: Uncertain (Data might be late)
                        return None

                    # [Fix 2026-08-30] TAIEX 查回「當天無資料」，對「最近幾天」的日期
                    # 不代表真的休市——更常見的原因是 FinMind 該筆資料還沒同步上來
                    # (實測8/28週五、查詢當下是8/30，被誤判成休市，當時TAIEX資料還
                    # 沒到齊，且這個誤判被永久寫進快取，導致 disposal_stats_manager.py
                    # 用交易日清單做位置索引時整批日期錯位，把「處置日」標成晚3天後
                    # 的8/31)。但這個「不要相信空資料」的例外只能限定在近期日期——
                    # 全庫掃過發現大量幾個月甚至十幾年前的日期也被驗證成休市(例如
                    # 颱風假等靜態行事曆不會知道的臨時休市)，這些是資料源早就同步
                    # 完整、真正確認休市的結果，遠比 DateUtils 那份只涵蓋週末+國定
                    # 假日的行事曆準確，不能因為這次的近期資料落後問題就連帶推翻。
                    # 門檻抓 14 天：一般資料源的落後不會超過這麼久。
                    days_since = (now.date() - datetime.strptime(date_str, "%Y%m%d").date()).days
                    if days_since <= 14 and DateUtils.is_trading_day(datetime.strptime(date_str, "%Y%m%d").date()):
                        return None

                self._save_trading_day_cache(date_str, is_trading_day)
                return is_trading_day
                
            return None
        except Exception as e:
            print(f"Error checking market open: {e}")
            return None
    
    def _save_trading_day_cache(self, date_str, is_trading_day):
        # [Fix] Apply 17:00 Rule to prevent caching false negatives
        if not is_trading_day:
            now = datetime.now()
            today_s = now.strftime("%Y%m%d")
            if date_str > today_s:
                return
            if date_str == today_s and now.hour < 17:
                return 

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO trading_day_cache 
                   (date, is_trading_day, verified_at, verification_source) 
                   VALUES (?, ?, ?, ?)""",
                (date_str, 1 if is_trading_day else 0, 
                 datetime.now().isoformat(), "TAIEX")
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def fetch_tpex_disposition(self, date_str=None):
        """
        抓取櫃買中心處置股票
        Source: Web Portal (https://www.tpex.org.tw/www/zh-tw/bulletin/disposal)

        [Fix 2026-08-31] 原本使用的 OpenAPI (openapi/v1/tpex_disposal_information)
        已確認停止更新(Last-Modified 卡在資料本身最後一次異動之前，8/28 公告的新
        處置股完全抓不到，即使晚 3 天再查也一樣)，改用官網實際使用的 Web Portal
        JSON 端點，資料即時且支援 startDate/endDate 區間查詢。parser.py 的
        parse_tpex_disposition() 已支援此 {"tables":[{"data":[...]}]} 格式
        (is_portal 分支)，不須改動 parser。

        [Fix 2026-09-04] 這個 API 是用「處置期間是否涵蓋 [startDate, endDate]」來
        篩選，而「盤後處置公告」的定義是「今天收盤後才公告、生效日是下一個交易日」
        的個股——這種個股的 period_start 必定晚於 date_str 本身，不會涵蓋 date_str，
        所以原本 startDate=endDate=date_str 的單日查詢一定會漏掉它們(實測 3664、
        4991 這兩檔 9/3 盤後公告、9/4 生效的個股，用 date_str='20260903' 單日查詢
        完全查不到，只有查到 endDate='20260904' 才會出現)。改成 endDate 往後展延
        10 天，同時涵蓋「當天已經生效中」與「當天盤後才公告、之後才生效」兩種情況。

        [經 agy 審查後調整] 原本 endDate 只展延到 DateUtils.get_next_trading_day()
        (下一個交易日)，但遇到公告日跟生效日中間隔超過 1 個交易日(例如提早公告，
        或颱風假/連假排程)時仍會漏接；且依賴 is_trading_day() 的假日行事曆，若行事
        曆資料缺漏可能導致無窮迴圈。改成固定往後展延 10 天(不查行事曆)，反正下游
        呼叫端本來就會用每筆紀錄自己的 period_start/period_end 跟顯示日期比對來分類
        「已生效」或「尚未生效」，查詢範圍只要夠寬、不會漏接即可，不需要精算到剛好
        下一個交易日。10 天足以涵蓋目前行事曆最長的連假(農曆年 9 天)。
        """
        url = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"

        params = {"response": "json"}
        if date_str:
            # Convert YYYYMMDD -> YYYY/MM/DD ; query records whose 處置期間 covers
            # date_str or up to 10 days after it (see [Fix 2026-09-04] above)
            try:
                base_dt = datetime.strptime(date_str, "%Y%m%d")
                params["startDate"] = base_dt.strftime("%Y/%m/%d")
                params["endDate"] = (base_dt + timedelta(days=10)).strftime("%Y/%m/%d")
            except Exception as e:
                print(f"[fetch_tpex_disposition] 解析 date_str={date_str!r} 失敗，查詢將不帶日期區間: {e}")

        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            res = requests.get(url, headers=self.headers, params=params, timeout=10, verify=False)
            res.raise_for_status()

            # Web Portal 回傳 {"tables": [{"data": [...]}]}，parser.py 直接支援此格式
            return res.json()

        except Exception as e:
            print(f"Error fetching TPEX disposition: {e}")
            return None

    def fetch_twse_margin_list(self, date_str):

        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=STOCK&response=json"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None

    def fetch_tpex_margin_list(self, date_str):

        # Convert YYYYMMDD -> YYYY/MM/DD
        if len(date_str) == 8:
            formatted = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
        else:
            formatted = date_str
            
        url = f"https://www.tpex.org.tw/www/zh-tw/margin/balance?response=json&startDate={formatted}&endDate={formatted}"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None

    def fetch_taifex_futures_list(self):

        url = "https://openapi.taifex.com.tw/v1/SSFLists"
        try:
             res = requests.get(url, headers=self.headers, timeout=10)
             if res.status_code == 200:
                 return res.json()
        except Exception:
             pass
        return None
    def fetch_stock_attention_history(self, code, start_date, end_date, source="上市"):

        if source == "上市":
            # TWSE RWD API
            url = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={start_date}&endDate={end_date}&stockNo={code}&response=json"
            try:
                requests.packages.urllib3.disable_warnings()
                res = requests.get(url, headers=self.headers, timeout=10, verify=False)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass
        else:
            # TPEX
            # TPEX API usually filters by date, not stock. We might need to fetch range and filter in memory?
            # Or check if stockNo param works for TPEX Bulletin API?
            # Existing fetch_tpex_attention accepts date range.
            # We can try appending &stkNo={code} (Official docs vary, but usually supported in search params)
            if len(start_date) == 8:
                s_fmt = f"{start_date[:4]}/{start_date[4:6]}/{start_date[6:]}"
                e_fmt = f"{end_date[:4]}/{end_date[4:6]}/{end_date[6:]}"
            else:
                s_fmt, e_fmt = start_date, end_date
                
            url = f"https://www.tpex.org.tw/www/zh-tw/bulletin/attention?startDate={s_fmt}&endDate={e_fmt}&stkNo={code}&response=json"
            try:
                res = requests.get(url, headers=self.headers, timeout=10)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass
        return None

    def fetch_stock_disposition_history(self, code, start_date, end_date, source="上市"):

        if source == "上市":
            # TWSE Punish API - 需要加入 selectType= 參數才能正確過濾股票
            url = f"https://www.twse.com.tw/rwd/zh/announcement/punish?selectType=&stockNo={code}&startDate={start_date}&endDate={end_date}&response=json"
            try:
                requests.packages.urllib3.disable_warnings()
                res = requests.get(url, headers=self.headers, timeout=30, verify=False)
                if res.status_code == 200 and res.text.strip():
                    try:
                        return res.json()
                    except Exception:
                        pass
            except Exception as e:
                print(f"[TWSE] {code} 取得失敗: {e}")
        else:
            # TPEX Disposition (OpenAPI) - No Date Range/Stock Filter in URL typically?
            # Actually TPEX Portal might have one: https://www.tpex.org.tw/www/zh-tw/bulletin/disposal_information?startDate=...
            # The OpenAPI one used before is "tpex_disposal_information".
            # Let's try the Web Portal Search API for targeted search
            if len(start_date) == 8:
                s_fmt = f"{start_date[:4]}/{start_date[4:6]}/{start_date[6:]}"
                e_fmt = f"{end_date[:4]}/{end_date[4:6]}/{end_date[6:]}"
            else:
                s_fmt, e_fmt = start_date, end_date
            
            # NOTE: TPEX Disposition Search URL guess
            url = f"https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?startDate={s_fmt}&endDate={e_fmt}&type=code&code={code}&response=json"
            try:
                res = requests.get(url, headers=self.headers, timeout=10)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass
        return None

    def fetch_stock_history(self, code, source=None, period="180d", allow_fetch=True):

        try:
            # 1. Check Cache
            cached_df = self.cache.get_price_history(code)
            
            last_date = None
            if cached_df is not None and not cached_df.empty:
                last_date = cached_df.index.max()
            
            # 2. Update if needed
            from core.utils import DateUtils
            
            # 取得最後一個交易日，避免在週末或盤中還沒收盤時重複下載空資料
            last_trading_dt = DateUtils.get_last_trading_day()
            target_date = pd.Timestamp(last_trading_dt).normalize()
            
            need_update = False
            start_fetch_date = None
            
            if last_date is None:
                need_update = True
                # Fetch more than needed to ensure overlap/completeness
                start_fetch_date = (target_date - pd.Timedelta(days=365*2)).strftime('%Y-%m-%d')
            else:
                if last_date < target_date:
                    need_update = True
                    start_fetch_date = (last_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            
            if need_update and allow_fetch:
                # 優先使用永豐 Shioaji API 取得歷史K線
                new_df = None
                try:
                    from core.shioaji_client import ShioajiClient
                    sj_client = ShioajiClient()
                    if sj_client.is_connected or sj_client._ensure_connected():
                        sj_df = sj_client.get_kbars(code, start_fetch_date)
                        if sj_df is not None and not sj_df.empty:
                            new_df = sj_df
                            print(f"[Fetcher] Shioaji kbars({code}): {len(new_df)} 筆")
                except Exception as e:
                    print(f"[Fetcher] Shioaji kbars({code}) 失敗: {e}")

                # 備援：FinMind
                if new_df is None:
                    fm_df = self.fm_client.fetch_daily_price(code, start_fetch_date)
                    if fm_df is not None and not fm_df.empty:
                        fm_df['date'] = pd.to_datetime(fm_df['date'])
                        fm_df.set_index('date', inplace=True)
                        fm_df.index.name = 'Date'
                        col_map = {
                            'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume',
                            'out_volume': 'Volume'
                        }
                        fm_df.rename(columns=col_map, inplace=True)
                        std_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                        avail = [c for c in std_cols if c in fm_df.columns]
                        new_df = fm_df[avail]

                if new_df is not None and not new_df.empty:
                    # 存入快取
                    self.cache.save_price_history(code, new_df)
                    
                    # Re-read full from cache to return
                    cached_df = self.cache.get_price_history(code)
            
            # 3. Get Stock Info (Shares)
            # [Fix] 股本(shares_outstanding)只在快取為空時才重抓，增資/減資後
            # 快取數字會永久卡在舊的股本，導致周轉率相關的張數門檻算錯（曾發生
            # 8033 增資1200萬股後，快取還停在3個多月前的舊股本，算出的張數少了約600張）。
            # 股本資料季報才更新一次，設 90 天視為過期即可涵蓋每季更新頻率。
            STOCK_INFO_STALE_DAYS = 90
            shares = self.cache.get_stock_info(code)
            shares_age_days = self.cache.get_stock_info_age_days(code)
            need_refresh_shares = (shares is None or shares == 0) or (
                shares_age_days is not None and shares_age_days > STOCK_INFO_STALE_DAYS
            )
            if need_refresh_shares and allow_fetch:
                fresh_shares = self.fm_client.fetch_stock_info(code)
                if fresh_shares > 0:
                    shares = fresh_shares
                    self.cache.save_stock_info(code, shares)
            
            # --- [Fix] Fetch and Merge PER/PBR ---
            if cached_df is not None and not cached_df.empty:
                # Try to get Ratios from Cache
                ratios_df = self.cache.get_ratios(code)
                
                # Check if we need to update ratios
                # If cached_df has new dates that ratios_df doesn't cover?
                last_ratio_date = None
                if ratios_df is not None and not ratios_df.empty:
                    last_ratio_date = ratios_df.index.max()
                
                need_ratio_update = False
                start_ratio_fetch = None
                
                if last_ratio_date is None:
                    need_ratio_update = True
                    start_ratio_fetch = (pd.Timestamp.now() - pd.Timedelta(days=365*2)).strftime('%Y-%m-%d')
                elif last_date and last_ratio_date < last_date: # Sync with price date
                     need_ratio_update = True
                     start_ratio_fetch = (last_ratio_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                
                if need_ratio_update and allow_fetch:
                    # Fetch New Ratios
                    new_ratios = self.fm_client.fetch_per_pbr(code, start_ratio_fetch)
                    if new_ratios is not None and not new_ratios.empty:
                        # Process
                        new_ratios['date'] = pd.to_datetime(new_ratios['date'])
                        new_ratios = new_ratios.rename(columns={'benefit_ratio': 'PER', 'pb_ratio': 'PBR'})
                        new_ratios.set_index('date', inplace=True)
                        new_ratios.index.name = 'Date'
                        
                        # Save to Cache
                        self.cache.save_ratios(code, new_ratios)
                        
                        # Reload full ratios
                        ratios_df = self.cache.get_ratios(code)
                
                # Merge Ratios into Main DF
                if ratios_df is not None and not ratios_df.empty:
                    # Join on Date index
                    # Use left join to keep all Price rows
                    cached_df = cached_df.join(ratios_df[['PER', 'PBR']], how='left')
            
            if cached_df is not None and not cached_df.empty:
                 # Ensure standard columns only + PER/PBR
                 cols = ['Open', 'High', 'Low', 'Close', 'Volume', 'PER', 'PBR']
                 # Filter if cols exist
                 valid_cols = [c for c in cols if c in cached_df.columns]
                 final_df = cached_df[valid_cols].copy()
                 print(f"[Fetcher] returning final_df for {code}, len={len(final_df)}")
                 return final_df, shares
                 
        except Exception as e:
            print(f"Error fetching history for {code}: {e}")
            import traceback
            traceback.print_exc()
            
        return None, 0
