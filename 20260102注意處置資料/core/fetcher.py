
import requests
import json
import time
from datetime import datetime

class StockFetcher:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json"
        }
        
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
