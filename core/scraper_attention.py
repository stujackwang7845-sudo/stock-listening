import requests
import pandas as pd
from datetime import datetime
import urllib3
import logging

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class AttentionScraper:
    """
    Scrapes official "Attention Securities" (注意股) from TWSE and TPEX.
    """
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    @staticmethod
    def fetch_data(date_obj=None):
        """
        Fetches combined list of [Code, Name, Reason] from TWSE and TPEX.
        Returns a list of dicts: [{'code': '3308', 'name': '聯德', 'reason': '...'}, ...]
        """
        if date_obj is None:
            date_obj = datetime.now()
            
        results = []
        
        # 1. TWSE
        try:
            twse_data = AttentionScraper._fetch_twse(date_obj)
            results.extend(twse_data)
        except Exception as e:
            print(f"[Scraper] TWSE Error: {e}")
            
        # 2. TPEX
        try:
            tpex_data = AttentionScraper._fetch_tpex(date_obj)
            results.extend(tpex_data)
        except Exception as e:
            print(f"[Scraper] TPEX Error: {e}")
            
        return results

    @staticmethod
    def _fetch_twse(date_obj):
        """
        Fetch TWSE Note Trans (Json)
        """
        date_str = date_obj.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/announcement/notetrans?date={date_str}&response=json"
        
        results = []
        try:
            r = requests.get(url, headers=AttentionScraper.HEADERS, verify=False, timeout=10)
            if r.status_code != 200:
                print(f"[Scraper] TWSE HTTP {r.status_code}")
                return []
                
            data = r.json()
            if data.get('stat') == 'OK':
                # Data format: [Seq, Code, Name, Reason, ...]
                for row in data.get('data', []):
                    if len(row) >= 4:
                        results.append({
                            "code": str(row[1]).strip(),
                            "name": str(row[2]).strip(),
                            "reason": str(row[3]).strip(),
                            "source": "上市"
                        })
        except Exception as e:
            print(f"[Scraper] TWSE Exception: {e}")
            
        return results

    @staticmethod
    def _fetch_tpex(date_obj):
        """
        Fetch TPEX "Accumulated Attention Abnormal" (Approaching Disposition)
        Endpoint: /www/zh-tw/bulletin/warning
        """
        roc_year = date_obj.year - 1911
        date_roc = f"{roc_year}/{date_obj.month:02d}/{date_obj.day:02d}"
        
        # Correct Endpoint for "Accumulated Abnormal" (Listening)
        url = "https://www.tpex.org.tw/www/zh-tw/bulletin/warning"
        params = {
            "startDate": date_roc,
            "endDate": date_roc,
            "response": "json"
        }
        
        results = []
        try:
            r = requests.get(url, params=params, headers=AttentionScraper.HEADERS, verify=False, timeout=10)
            if r.status_code != 200:
                print(f"[Scraper] TPEX HTTP {r.status_code}")
                return []
            
            data = r.json()
            
            # Check for tables
            rows = []
            if 'tables' in data and len(data['tables']) > 0:
                rows = data['tables'][0].get('data', [])
            elif 'data' in data:
                rows = data['data']
                
            for row in rows:
                try:
                    # JSON Row Structure from debug: 
                    # [Index, Code, Name, Reason]
                    # Index 1: Code, Index 2: Name, Index 3: Reason
                    if len(row) < 4: continue
                    
                    code = str(row[1]).strip()
                    name = str(row[2]).strip()
                    reason = str(row[3]).strip()
                    
                    # Store result
                    results.append({
                        "code": code,
                        "name": name,
                        "reason": reason,
                        "source": "上櫃"
                    })
                except Exception as e:
                    print(f"[Scraper] TPEX Row Error: {e}")
                    continue
                    
        except Exception as e:
            print(f"[Scraper] TPEX Exception: {e}")
            
        return results
