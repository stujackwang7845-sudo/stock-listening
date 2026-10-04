import requests
import pandas as pd
from datetime import datetime
import urllib3
import logging
import csv

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
        Returns a list of dicts: [{'code': '3308', 'name': '聯德', 'reason': '...', 'source': 'TWSE'}, ...]
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
        Fetch TWSE Accumulated Attention Notice (累積注意交易資訊)
        使用 /notetrans endpoint 以獲得累積注意股（聽牌股）
        注意：不需要日期參數，總是返回最新的累積注意股資料
        """
        # TWSE 使用簡化URL，不需要日期參數
        url = "https://www.twse.com.tw/rwd/zh/announcement/notetrans"
        params = {
            "response": "json"
        }
        
        results = []
        try:
            # Use Session for cookies
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.twse.com.tw/zh/announcement/notetrans.html",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            })
            
            r = s.get(url, params=params, timeout=10)
            if r.status_code != 200:
                print(f"[Scraper] TWSE HTTP {r.status_code}")
                return []
                
            data = r.json()
            if data.get('stat', '') == 'OK' or data.get('stat', '').lower() == 'ok':
                # Data structure (notetrans endpoint):
                # [0]: 編號, [1]: 證券代號, [2]: 證券名稱, [3]: 近期達標準資訊
                for row in data.get('data', []):
                    if len(row) >= 4:
                        results.append({
                            "code": str(row[1]).strip(),
                            "name": str(row[2]).strip(),
                            "reason": str(row[3]).strip(),
                            "source": "TWSE"
                        })
        except Exception as e:
            print(f"[Scraper] TWSE Exception: {e}")
            import traceback
            traceback.print_exc()
            
        return results

    @staticmethod
    def _fetch_tpex(date_obj):
        """
        Fetch TPEX Accumulated Attention Securities (累積注意股) via JSON
        使用 /bulletin/warning endpoint 獲取累積注意股（聽牌股）
        注意：TPEX不需要日期參數，總是返回最新的累積注意股資料
        """
        # TPEX 使用簡化URL，不需要日期參數
        url = "https://www.tpex.org.tw/www/zh-tw/bulletin/warning"
        params = {
            "id": "",
            "response": "json"
        }
        
        results = []
        try:
            # Skip Verify because TPEX cert chain often issues
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                print(f"[Scraper] TPEX HTTP {r.status_code}")
                return []
                
            # Parse JSON response - TPEX uses 'tables' structure
            data = r.json()
            
            # Data structure: {'stat': 'ok', 'tables': [{'date': '20260205', 'data': [...]}]}
            if 'tables' in data and data['tables']:
                table = data['tables'][0]
                
                # Extract data rows
                for row in table.get('data', []):
                    # Row format: [序號, 代號, 名稱, 原因]
                    if len(row) >= 4:
                        code = row[1].strip()
                        name = row[2].strip()
                        reason = row[3].strip()
                        
                        results.append({
                            "code": code,
                            "name": name,
                            "reason": reason,
                            "source": "TPEX"
                        })
                    
        except Exception as e:
            print(f"[Scraper] TPEX Exception: {e}")
            
        return results
