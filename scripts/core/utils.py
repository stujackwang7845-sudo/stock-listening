from core.runtime import get_paths
import re
from datetime import datetime, timedelta
import pandas as pd
import requests
import json
import os
class ClauseParser:
    @staticmethod
    @staticmethod
    def parse_clauses(reason_text):
        if not reason_text:
            return ""
            
        # Mapping for Chinese and Arabic numerals
        mapping = {
            "一": "一", "二": "二", "三": "三", "四": "四",
            "五": "五", "六": "六", "七": "七", "八": "八",
            "1": "一", "2": "二", "3": "三", "4": "四",
            "5": "五", "6": "六", "7": "七", "8": "八",
             # Extended support if needed (9-12 for full completeness)
            "9": "九", "10": "十", "11": "十一", "12": "十二"
        }
        
        # Pattern to catch: "第X款", "第 X 款", leading "X款"
        # Matches Chinese 一-八 or Arabic 0-9
        # Pattern to catch: "第X款", "第 X 款", leading "X款"
        # Matches Chinese 一-八 or Arabic 1-8
        pattern = r"第\s*([一二三四五六七八1-8])\s*款"
        matches = re.findall(pattern, reason_text)
        
        found_clauses = set()
        for m in matches:
            if m in mapping:
                val = mapping[m]
                # Double check to ensure we only keep 一 to 八
                if val in ["一", "二", "三", "四", "五", "六", "七", "八"]:
                    found_clauses.add(val)
        
        order = ["一", "二", "三", "四", "五", "六", "七", "八"]
        sorted_clauses = sorted(list(found_clauses), key=lambda x: order.index(x) if x in order else 99)
        
        if not sorted_clauses:
            # Check if there are explicit clauses > 8 (e.g. 第十款, 第10款)
            # If there are, we shouldn't fallback to guessing 1 and 4.
            has_other_clauses = re.search(r"第\s*[一二三四五六七八九十百千0-9]+\s*款", reason_text)
            
            if not has_other_clauses:
                # Fallbacks for Accumulated Warnings ("連續四次" etc.)
                fallback_clauses = set()
                if "連續" in reason_text or "累積" in reason_text:
                    fallback_clauses.add("一")
                if "週轉" in reason_text:
                    fallback_clauses.add("四")
                
                if fallback_clauses:
                    return ",".join(sorted(list(fallback_clauses)))
                return "注意"
            return ""
            
        return ",".join(sorted_clauses)

class DateUtils:
    # 簡易假日表 (YYYY-MM-DD)，實際開發建議串接 OpenData 或完整日曆庫
    HOLIDAYS = {
        "2026-01-01", # 元旦
        "2025-01-01", 
        "2025-12-25", # User reported holiday
        # 2026 農曆新年連假 (2/12-2/20 休市)
        "2026-02-12", "2026-02-13", "2026-02-14",
        "2026-02-15", "2026-02-16", "2026-02-17",
        "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-02-27", # 228連假
        # 新增假日
        "2026-04-03", "2026-04-06", "2026-05-01", 
        "2026-06-19", "2026-07-10", "2026-09-25", "2026-09-28", 
        "2026-10-09", "2026-10-26", "2026-12-25",
        # 可依需求擴充
    }
    
    _TWSE_CACHE_FILE = get_paths().twse_holidays_json
    _TWSE_HOLIDAYS_LOADED = False

    @classmethod
    def _sync_twse_holidays(cls):
        """同步證交所休市日並合併至 HOLIDAYS"""
        if getattr(cls, '_TWSE_HOLIDAYS_LOADED', False):
            return
            
        cls._TWSE_HOLIDAYS_LOADED = True
        
        cached_holidays = set()
        if os.path.exists(cls._TWSE_CACHE_FILE):
            try:
                with open(cls._TWSE_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cached_holidays = set(data.get("holidays", []))
            except Exception as e:
                print(f"[DateUtils] Error reading holiday cache: {e}")
                
        cls.HOLIDAYS.update(cached_holidays)
        
        current_year = str(datetime.now().year)
        year_count = sum(1 for d in cls.HOLIDAYS if d.startswith(current_year))
        
        if year_count < 5:
            try:
                print(f"[DateUtils] 偵測到缺少 {current_year} 年的假日快取，正從 TWSE 下載中...")
                url = 'https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule'
                res = requests.get(url, timeout=10).json()
                
                new_fetched = set()
                for item in res:
                    name = item.get("Name", "")
                    desc = item.get("Description", "")
                    date_val = item.get("Date", "")
                    
                    if "開始交易" in name or "開始交易" in desc:
                        continue
                        
                    if len(date_val) == 7: 
                        y = int(date_val[:3]) + 1911
                        m = int(date_val[3:5])
                        d = int(date_val[5:])
                        iso_date = f"{y}-{m:02d}-{d:02d}"
                        new_fetched.add(iso_date)
                        
                if new_fetched:
                    cls.HOLIDAYS.update(new_fetched)
                    all_new = sorted(list(cls.HOLIDAYS))
                    with open(cls._TWSE_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump({"holidays": all_new}, f, indent=4)
                    print(f"[DateUtils] 成功下載並快取證交所資料！")
            except Exception as e:
                print(f"[DateUtils] 從 TWSE 同步假日失敗: {e}")

    @classmethod
    def is_trading_day(cls, date_obj):
        """判斷是否為交易日 (排除週末與假日)"""
        cls._sync_twse_holidays()
        
        if date_obj.weekday() >= 5: # Sat=5, Sun=6
            return False
            
        if date_obj.strftime("%Y-%m-%d") in cls.HOLIDAYS:
            return False
        return True

    @staticmethod
    def get_last_trading_day(base_date=None):
        """取得基準日(含)以前的最近一個交易日"""
        if base_date is None:
            now = datetime.now()
            # 如果現在時間早於 18:00，視今日為尚未結束（資料未產出），從昨天開始找
            if now.hour < 18:
                base_date = now - timedelta(days=1)
            else:
                base_date = now
        
        current = base_date
        # 往回找，直到找到交易日
        while not DateUtils.is_trading_day(current):
            current -= timedelta(days=1)
        return current

    @staticmethod
    def get_next_trading_day(base_date):
        """取得基準日(不含)以後的最近一個交易日"""
        if base_date is None:
            base_date = datetime.now()
            
        current = base_date + timedelta(days=1)
        # 往後找，直到找到交易日
        while not DateUtils.is_trading_day(current):
            current += timedelta(days=1)
        return current

    @staticmethod
    def get_market_calendar(anchor_date=None, past_days=8, future_days=5):
        """
        以 anchor_date 為基準 (中間那格)，
        往前找 past_days 個交易日，
        往後找 future_days 個交易日。
        """
        if anchor_date is None:
            anchor_date = DateUtils.get_last_trading_day()
        elif isinstance(anchor_date, str):
            anchor_date = pd.to_datetime(anchor_date)
            
        # 往回找 past_days
        current = anchor_date - timedelta(days=1)
        past_dates = []
        while len(past_dates) < past_days:
            if DateUtils.is_trading_day(current):
                past_dates.insert(0, current.strftime("%m/%d"))
            current -= timedelta(days=1)
            
        # 往後找 future_days
        current = anchor_date + timedelta(days=1)
        future_dates = []
        while len(future_dates) < future_days:
            if DateUtils.is_trading_day(current):
                future_dates.append(current.strftime("%m/%d"))
            current += timedelta(days=1)
            
        return {
            "past": past_dates,
            "current": anchor_date.strftime("%m/%d"),
            "future": future_dates,
            "anchor_obj": anchor_date
        }

    @staticmethod
    def parse_period_start(period_str):
        """
        Parses "114/12/31 ~ 115/01/14" or "1141231~1150114"
        Supports separators: ~, -, ～, —, –
        Supports date formats: 114/01/01, 114.01.01, 1140101
        """
        if not period_str: return None
        try:
            s = period_str.strip()
            
            # [Fix] Try parsing standard AD formats first (YYYY/MM/DD or YYYY-MM-DD)
            # Before replacing '-', checking if it looks like a date
            # Regex or simple split check
            # YYYY-MM-DD
            if "-" in s and len(s.split("-")) == 3:
                try:
                    parts = s.split("-")
                    if len(parts[0]) == 4: # Year
                        return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                except: pass
            
            # YYYY/MM/DD
            if "/" in s and len(s.split("/")) == 3:
                try:
                    parts = s.split("/")
                    if len(parts[0]) == 4: # Year
                        return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                except: pass
            
            # 1. Clean string (Handle various range separators)
            # Normalize to single tilde
            for sep in ["～", "—", "–", "-"]:
                s = s.replace(sep, "~")
            
            s = s.split('~')[0].strip() # Take first part
            
            # Normalize Date Separators (. to /)
            s = s.replace(".", "/")
            
            # 2. Try Standard format "114/12/31" (ROC)
            parts = s.split('/')
            if len(parts) == 3:
                y = int(parts[0])
                # If year < 1000, assume ROC
                if y < 1000: y += 1911
                
                m = int(parts[1])
                d = int(parts[2])
                return datetime(y, m, d)
                
            # 3. Try Compact format "1141231"
            if len(s) == 7 and s.isdigit():
                 y = int(s[:3]) + 1911
                 m = int(s[3:5])
                 d = int(s[5:])
                 return datetime(y, m, d)
            # Try Compact AD "20260203"
            if len(s) == 8 and s.isdigit():
                 y = int(s[:4])
                 m = int(s[4:6])
                 d = int(s[6:])
                 return datetime(y, m, d)
            
        except Exception as e:
            # print(f"Date Parse Error: {e}")
            pass
        return None

    @staticmethod
    def parse_period_end(period_str):
        """
        Parses end date from "114/12/31 ~ 115/01/14"
        Returns datetime object or None.
        """
        if not period_str: return None
        try:
            # 1. Clean and Split
            s = period_str
            for sep in ["～", "—", "–", "-"]:
                s = s.replace(sep, "~")
                
            parts = s.split('~')
            if len(parts) < 2: return None
            
            s = parts[1].strip()
            
            # Normalize Date Separators
            s = s.replace(".", "/")
            
            # 2. Try Standard format "114/12/31"
            parts = s.split('/')
            if len(parts) == 3:
                y = int(parts[0]) + 1911
                m = int(parts[1])
                d = int(parts[2])
                return datetime(y, m, d)
                
            # 3. Try Compact format "1150114"
            if len(s) == 7 and s.isdigit():
                 y = int(s[:3]) + 1911
                 m = int(s[3:5])
                 d = int(s[5:])
                 return datetime(y, m, d)
                 
        except:
            pass
        return None

    @staticmethod
    def to_iso_date_str(date_input):
        """
        統一將各種日期格式 (西元/民國/datetime) 轉為 YYYY-MM-DD
        """
        if not date_input:
            return ""
            
        try:
            # 1. If it's already datetime
            if isinstance(date_input, datetime):
                return date_input.strftime("%Y-%m-%d")
            
            s = str(date_input).strip()
            if not s:
                return ""
                
            # 2. Try YYYY-MM-DD (ISO)
            if "-" in s and len(s) == 10:
                try:
                    datetime.strptime(s, "%Y-%m-%d")
                    return s
                except: pass
                
            # 3. Try YYYY/MM/DD or YYYY-MM-DD (Ensure 0-padding)
            if "/" in s or "-" in s:
                s_norm = s.replace("/", "-")
                parts = s_norm.split("-")
                if len(parts) == 3 and len(parts[0]) == 4:
                     # Re-assemble with padding
                     return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                 
            # 4. Try YYYYMMDD (Western Compact)
            if len(s) == 8 and s.isdigit():
                return f"{s[:4]}-{s[4:6]}-{s[6:]}"
                
            # 5. Try ROC format: 114/01/01 or 114.01.01
            # Normalize separators
            s_roc = s.replace("-", "/").replace(".", "/")
            parts = s_roc.split('/')
            if len(parts) == 3:
                # Assuming simple ROC input like 114/1/1 or 114/01/01
                y = int(parts[0])
                if y < 1900: # Clearly ROC
                    y += 1911
                return f"{y}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                
            # 6. Try ROC Compact: 1140101 (7 digits)
            if len(s) == 7 and s.isdigit():
                y = int(s[:3]) + 1911
                m = int(s[3:5])
                d = int(s[5:])
                return f"{y}-{m:02d}-{d:02d}"
                
        except Exception as e:
            # print(f"Date conversion error: {e} for {date_input}")
            pass
            
        return str(date_input) # Fallback to original string
