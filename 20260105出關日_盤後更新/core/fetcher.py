
import requests
import json
import time
from datetime import datetime
import requests
import json
import time
from datetime import datetime
import pandas as pd
from core.finmind_client import FinMindClient
from core.market_cache import MarketDataCache

class StockFetcher:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json"
        }
        # Initialize FinMind and Cache
        token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMS0wMyAwNzo0Njo0MyIsInVzZXJfaWQiOiJzdHVyYWluYm93c3Blcm0iLCJpcCI6IjIwMy4yMTEuMTA1LjIwNCJ9.JUyqymsQdWt4aQ4SgkPcSRv4mub13CaTUpYnPrdlKbU"
        self.fm_client = FinMindClient(token)
        self.cache = MarketDataCache()
        
    def _convert_to_roc_date(self, date_str):
        """YYYYMMDD -> YYY/MM/DD (ROC)"""
        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            roc_year = dt.year - 1911
            return f"{roc_year}/{dt.month:02d}/{dt.day:02d}"
        except:
            return date_str # Fallback

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
            url += f"&date={date_str}"
            
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            return data
        except Exception as e:
            # print(f"Error fetching TWSE disposition {date_str}: {e}")
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

    def verify_market_open(self, date_str):
        """
        Verify if market was open by checking TAIEX daily stats (FMTQIK)
        date_str: YYYYMMDD
        """
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_str}&response=json"
        try:
            res = requests.get(url, headers=self.headers, timeout=5)
            # data = res.json() # Raise if not json
            # But sometimes even 200 OK html comes back on maintenance?
            # Assuming json.
            data = res.json()
            stat = data.get("stat", "")
            if "OK" in stat:
                # Double check data length
                if len(data.get("data", [])) > 0:
                    return True
            return False
        except Exception as e:
            # print(f"Error checking market status {date_str}: {e}")
            # If error (timeout), play safe? Assume Open? Or Closed?
            # Assume Closed to avoid ghost data? 
            # Or Assume Open and let Attention list decide?
            # User wants to remove 12/25. If API fails, better to assume False (Holiday).
            return False

    def fetch_tpex_disposition(self, date_str=None):
        """抓取櫃買中心處置股票 (OpenAPI)"""
        # Ref: https://www.tpex.org.tw/openapi/v1/tpex_disposal_information
        url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
        # OpenAPI typically returns simple JSON list
        
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            # print(f"DEBUG: TPEX API Data: {data[:2]}") # Debug
            return data
        except Exception as e:
            print(f"Error fetching TPEX disposition: {e}")
            return None

    def fetch_twse_margin_list(self, date_str):
        """
        抓取上市融資融券餘額表 (MI_MARGN)
        Ref: https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date}&selectType=STOCK&response=json
        """
        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=STOCK&response=json"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None

    def fetch_tpex_margin_list(self, date_str):
        """
        抓取上櫃融資融券餘額
        Ref: https://www.tpex.org.tw/www/zh-tw/margin/balance?response=json&startDate={Y/M/D}&endDate={Y/M/D}
        """
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
        """
        抓取股票期貨標的清單 (TAIFEX OpenAPI)
        Ref: https://openapi.taifex.com.tw/v1/SSFLists
        """
        url = "https://openapi.taifex.com.tw/v1/SSFLists"
        try:
             res = requests.get(url, headers=self.headers, timeout=10)
             if res.status_code == 200:
                 return res.json()
        except Exception:
             pass
        return None
    def fetch_stock_attention_history(self, code, start_date, end_date, source="上市"):
        """
        Fetch Attention History for a specific stock (TWSE/TPEX).
        start_date/end_date: YYYYMMDD
        """
        if source == "上市":
            # TWSE RWD API
            url = f"https://www.twse.com.tw/rwd/zh/announcement/notice?startDate={start_date}&endDate={end_date}&stockNo={code}&response=json"
            try:
                res = requests.get(url, headers=self.headers, timeout=10)
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
        """
        Fetch Disposition History for a specific stock.
        """
        if source == "上市":
            # TWSE Punish API
            url = f"https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={start_date}&endDate={end_date}&stockNo={code}&response=json"
            try:
                res = requests.get(url, headers=self.headers, timeout=10)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass
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
            url = f"https://www.tpex.org.tw/www/zh-tw/bulletin/disposal_information?startDate={s_fmt}&endDate={e_fmt}&stkNo={code}&response=json"
            try:
                res = requests.get(url, headers=self.headers, timeout=10)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass
        return None

    def fetch_stock_history(self, code, source=None, period="180d"):
        """
        Fetch OHLC using FinMind + Local Cache.
        Returns (DataFrame, shares_outstanding).
        """
        try:
            # 1. Check Cache
            cached_df = self.cache.get_price_history(code)
            
            last_date = None
            if cached_df is not None and not cached_df.empty:
                last_date = cached_df.index.max()
            
            # 2. Update if needed
            today = pd.Timestamp.now().normalize()
            need_update = False
            start_fetch_date = None
            
            if last_date is None:
                need_update = True
                # Fetch more than needed to ensure overlap/completeness
                start_fetch_date = (today - pd.Timedelta(days=365*2)).strftime('%Y-%m-%d')
            else:
                if last_date < today:
                    need_update = True
                    start_fetch_date = (last_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            
            if need_update:
                new_df = self.fm_client.fetch_daily_price(code, start_fetch_date)
                
                if new_df is not None and not new_df.empty:
                    # FinMind: date, stock_id, Trading_Volume, Trading_money, open, max, min, close, spread, transaction
                    # Map: date->Date, open->Open, max->High, min->Low, close->Close, Trading_Volume->Volume
                    
                    new_df = new_df.rename(columns={
                        'date': 'Date', 'open': 'Open', 'max': 'High', 
                        'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'
                    })
                    
                    # Ensure numeric
                    cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                    for c in cols:
                        new_df[c] = pd.to_numeric(new_df[c], errors='coerce')
                        
                    # Set index
                    new_df['Date'] = pd.to_datetime(new_df['Date'])
                    new_df.set_index('Date', inplace=True)
                    
                    # Save to Cache
                    self.cache.save_price_history(code, new_df)
                    
                    # Merge with cached
                    if cached_df is not None:
                         full_df = pd.concat([cached_df, new_df])
                         full_df = full_df[~full_df.index.duplicated(keep='last')]
                         full_df.sort_index(inplace=True)
                         cached_df = full_df
                    else:
                         cached_df = new_df
            
            # 3. Get Ratios (PER/PBR)
            ratios_df = self.cache.get_ratios(code)
            
            # Decide if update needed for Ratios
            # (Similar logic: check if last date < today)
            ratio_upd = False
            r_start = None
            
            if ratios_df is None or ratios_df.empty:
                ratio_upd = True
                r_start = (today - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
            else:
                last_r = ratios_df.index.max()
                if last_r < today:
                    ratio_upd = True
                    r_start = (last_r + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                    
            if ratio_upd:
                new_r = self.fm_client.fetch_per_pbr(code, r_start)
                if new_r is not None and not new_r.empty:
                    # Rename cols
                    # benefit_ratio -> PER, pb_ratio -> PBR
                    new_r = new_r.rename(columns={'benefit_ratio': 'PER', 'pb_ratio': 'PBR', 'date': 'Date'})
                    new_r['Date'] = pd.to_datetime(new_r['Date'])
                    new_r.set_index('Date', inplace=True)
                    new_r = new_r[['PER', 'PBR']] # Keep only needed
                    
                    self.cache.save_ratios(code, new_r)
                    
                    if ratios_df is not None:
                        ratios_df = pd.concat([ratios_df, new_r])
                        ratios_df = ratios_df[~ratios_df.index.duplicated(keep='last')]
                        ratios_df.sort_index(inplace=True)
                    else:
                        ratios_df = new_r

            # 4. Get Stock Info (Shares)
            shares = self.cache.get_stock_info(code)
            if shares is None or shares == 0:
                shares = self.fm_client.fetch_stock_info(code)
                if shares > 0:
                    self.cache.save_stock_info(code, shares)
            
            if cached_df is not None and not cached_df.empty:
                 # Ensure standard columns only
                 cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                 # Filter if cols exist
                 valid_cols = [c for c in cols if c in cached_df.columns]
                 final_df = cached_df[valid_cols].copy()
                 
                 # Merge Ratios
                 if ratios_df is not None and not ratios_df.empty:
                     final_df = final_df.join(ratios_df, how='left')
                     
                 # Fill NA for ratios? Forward fill
                 final_df['PER'] = final_df['PER'].ffill()
                 final_df['PBR'] = final_df['PBR'].ffill()
                 
                 return final_df, shares
                 
        except Exception as e:
            print(f"Error fetching history for {code}: {e}")
            
        return None, 0
