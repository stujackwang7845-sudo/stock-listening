import time
import requests
import urllib3
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from .utils import DateUtils, ClauseParser
import logging

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class HistoryScraper:
    """
    Scrapes historical 'Trigger Info' (Clauses) from TWSE/TPEX Notice boards.
    Target URLs:
    - TWSE: https://www.twse.com.tw/zh/announcement/notice.html
    - TPEX: https://www.tpex.org.tw/zh-tw/announce/market/attention.html
    """
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest"
    }

    @staticmethod
    def backfill_stock(code, lookback_days=9):
        """
        Fetches trigger info for 'code' for the past 'lookback_days'.
        Returns: { "mm/dd": "clause_string" }
        """
        results = {}
        today = datetime.now()
        
        print(f"[HistoryScraper] Backfilling {code} for past {lookback_days} days...")
        
        # Determine source
        # Simple heuristic: 4-digit code. Not perfect differentiation without DB.
        # But we can try both or check a list.
        # For now, try TWSE then TPEX if empty? Or run both?
        # Running both is safer but slower.
        
        current = today
        scanned = 0
        while scanned < lookback_days:
            if not DateUtils.is_trading_day(current):
                current -= timedelta(days=1)
                continue
                
            date_roc_slash = f"{current.year-1911}/{current.month:02d}/{current.day:02d}" # 115/01/05
            date_roc_pure = f"{current.year-1911}{current.month:02d}{current.day:02d}" # 1150105
            date_iso = current.strftime("%Y%m%d") # 20260105
            mm_dd = current.strftime("%m/%d")
            
            # print(f"  Checking {mm_dd} ({date_iso})...")
            
            clause = None
            
            # 1. Try TWSE
            clause = HistoryScraper._fetch_twse(date_iso, code)
            
            # 2. If not found, Try TPEX
            if not clause:
                clause = HistoryScraper._fetch_tpex(date_roc_slash, code)
            
            if clause:
                print(f"    -> Found {mm_dd}: {clause}")
                results[mm_dd] = clause
            
            current -= timedelta(days=1)
            scanned += 1
            time.sleep(0.5) # Be polite
            
        return results

    @staticmethod
    def _fetch_twse(date_iso, code):
        """
        TWSE Notice API
        URL: https://www.twse.com.tw/rwd/zh/announcement/notice
        Params: date=YYYYMMDD, response=json
        """
        url = "https://www.twse.com.tw/rwd/zh/announcement/notice"
        params = {"date": date_iso, "response": "json"}
        
        try:
            r = requests.get(url, params=params, headers=HistoryScraper.HEADERS, verify=False, timeout=5)
            data = r.json()
            
            if 'data' in data:
                for row in data['data']:
                    # Row: [Seq, Code, Name, Reason, ...]
                    if len(row) > 3 and row[1] == code:
                        raw_reason = row[3]
                        return ClauseParser.parse_clauses(raw_reason)
        except Exception:
            pass
        return None

    @staticmethod
    def _fetch_tpex(date_roc_slash, code):
        """
        TPEX Attention API (HTML preferred as JSON is tricky)
        URL: https://www.tpex.org.tw/web/bulletin/attention/attention_stk_result.php
        Params: d=115/01/05, l=zh-tw
        """
        url = "https://www.tpex.org.tw/web/bulletin/attention/attention_stk_result.php"
        params = {"d": date_roc_slash, "l": "zh-tw", "s": "0,asc,0"}
        
        try:
            r = requests.get(url, params=params, headers=HistoryScraper.HEADERS, verify=False, timeout=5)
            # Parse HTML
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.find_all('tr')
            
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 4:
                    curr_code = cols[1].get_text(strip=True)
                    if curr_code == code:
                        raw_reason = cols[3].get_text(strip=True)
                        return ClauseParser.parse_clauses(raw_reason)
        except Exception:
            pass
        return None
