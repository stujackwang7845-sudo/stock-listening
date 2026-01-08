import re
from datetime import datetime, timedelta
import pandas as pd

class ClauseParser:
    @staticmethod
    def parse_clauses(reason_text):
        if not reason_text:
            return ""
        mapping = {
            "第一款": "一", "第二款": "二", "第三款": "三", "第四款": "四",
            "第五款": "五", "第六款": "六", "第七款": "七", "第八款": "八"
        }
        pattern = r"第([一二三四五六七八])款"
        matches = re.findall(pattern, reason_text)
        order = ["一", "二", "三", "四", "五", "六", "七", "八"]
        unique_matches = sorted(list(set(matches)), key=lambda x: order.index(x) if x in order else 99)
        if not unique_matches:
            return "" 
        return ",".join(unique_matches)

class DateUtils:
    # 簡易假日表 (YYYY-MM-DD)，實際開發建議串接 OpenData 或完整日曆庫
    HOLIDAYS = {
        "2026-01-01", # 元旦
        "2025-01-01", 
        "2025-12-25", # User reported holiday
        # 可依需求擴充
    }

    @staticmethod
    def is_trading_day(date_obj):
        """判斷是否為交易日 (排除週末與假日)"""
        if date_obj.weekday() >= 5: # Sat=5, Sun=6
            return False
        if date_obj.strftime("%Y-%m-%d") in DateUtils.HOLIDAYS:
            return False
        return True

    @staticmethod
    def get_last_trading_day(base_date=None):
        """取得基準日(含)以前的最近一個交易日"""
        if base_date is None:
            base_date = datetime.now()
        
        current = base_date
        # 往回找，直到找到交易日
        while not DateUtils.is_trading_day(current):
            current -= timedelta(days=1)
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
        """
        if not period_str: return None
        try:
            # 1. Clean string (Handle fullwidth tilde)
            s = period_str.replace("～", "~").split('~')[0].strip() # Take first part
            
            # 2. Try Standard format "114/12/31"
            parts = s.split('/')
            if len(parts) == 3:
                y = int(parts[0]) + 1911
                m = int(parts[1])
                d = int(parts[2])
                return datetime(y, m, d)
                
            # 3. Try Compact format "1141231"
            if len(s) == 7 and s.isdigit():
                 y = int(s[:3]) + 1911
                 m = int(s[3:5])
                 d = int(s[5:])
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
            parts = period_str.replace("～", "~").split('~')
            if len(parts) < 2: return None
            
            s = parts[1].strip()
            
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
