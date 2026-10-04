from FinMind.data import DataLoader
import pandas as pd
import time
import threading
from datetime import datetime, timedelta
import json
import base64

import os
from dotenv import load_dotenv

# [Fix 2026-10-01] 筆電外出版沒有 E 槽那個共用設定資料夾，改成先找家用電腦的共用
# 路徑，找不到才退回專案根目錄自己的 .env(筆電只需要放 FINMIND_TOKENS/FUGLE_API_KEY
# 這兩把這個專案會用到的金鑰，不需要整份共用 .env)。load_dotenv 預設不覆蓋已存在的
# 環境變數，兩邊都試不會互相打架。
env_path = r"E:\Vibe Coding\ANTIGRAVITY SETTINGS\.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    _local_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    if os.path.exists(_local_env):
        load_dotenv(_local_env)

class FinMindClient:
    # 管理備用 Tokens (從 .env 載入)
    FALLBACK_TOKENS = []
    tokens_str = os.getenv("FINMIND_TOKENS", "")
    if tokens_str:
        FALLBACK_TOKENS = [t.strip() for t in tokens_str.split(",") if t.strip()]

    def __init__(self, token=None):
        from core.runtime import no_finmind
        if no_finmind():
            # 雲端不用 FinMind：不登入、斷路器直接打開，所有查詢回傳 None
            self.tokens, self.current_token_idx, self.token = [], 0, None
            self.api, self.max_retries, self.api_timeout = None, 0, 0
            self.api_exhausted = True
            return
        self.tokens = []
        if token:
            self.tokens.append(token)
        # Add fallbacks without duplicates
        for t in self.FALLBACK_TOKENS:
            if t not in self.tokens:
                self.tokens.append(t)
                
        self.current_token_idx = 0
        self.token = self.tokens[self.current_token_idx]
        self.api = DataLoader()
        self.api.login_by_token(api_token=self.token)
        self.max_retries = 3
        self.api_timeout = 5  # [Fix] FinMind API 最長等待秒數 (從30秒降至5秒，避免UI過長卡頓)
        self.api_exhausted = False  # 斷路器：若所有 token 都失效，則不再浪費時間重試

    def switch_to_next_token(self):
        self.current_token_idx = (self.current_token_idx + 1) % len(self.tokens)
        self.token = self.tokens[self.current_token_idx]
        user = self._extract_user_id(self.token)
        print(f"[FinMind 自動切換 Token] 換為第 {self.current_token_idx + 1} 組 ({user})")
        self.api.login_by_token(api_token=self.token)

    @staticmethod
    def _extract_user_id(token):
        try:
            if not token: return "N/A"
            parts = token.split('.')
            if len(parts) >= 2:
                payload = parts[1]
                # Pad base64 string
                payload += '=' * (-len(payload) % 4)
                decoded = base64.urlsafe_b64decode(payload)
                data = json.loads(decoded)
                return data.get("user_id", "Unknown")
        except Exception:
            return "InvalidToken"
        return "Unknown"

    @classmethod
    def get_token_dict(cls):
        """為 UI 建立 user_id -> token 的字典"""
        res = {}
        for t in cls.FALLBACK_TOKENS:
            uid = cls._extract_user_id(t)
            if uid != "InvalidToken" and uid != "Unknown":
                res[uid] = t
        return res

    def _get_data_with_timeout(self, timeout_sec=30, **kwargs):
        """以 threading 包裝 get_data，超過 timeout_sec 秒強制放棄並回傳 None，若失敗則自動切換 token"""
        if self.api_exhausted:
            return None
            
        for retry_count in range(len(self.tokens)):
            result = [None]
            exception = [None]
    
            def _call():
                try:
                    result[0] = self.api.get_data(**kwargs)
                except Exception as e:
                    exception[0] = e
    
            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(timeout=timeout_sec)
    
            if t.is_alive():
                print(f"[FinMind TIMEOUT] 超過 {timeout_sec}s 未回應。")
                self.switch_to_next_token()
                continue
    
            if exception[0] is not None:
                err_msg = str(exception[0])
                if "data" in err_msg or "limit" in err_msg.lower():
                    print(f"[FinMind API 錯誤] 疑似限制已滿或無效 ({err_msg})，嘗試切換 Token...")
                    self.switch_to_next_token()
                    continue
                else:
                    raise exception[0]
                    
            return result[0]
            
        print("[FinMind API 錯誤] 所有 Token 的配額均已用盡或皆發生錯誤，開啟斷路器，放棄後續請求。")
        self.api_exhausted = True
        return None

    def fetch_daily_price(self, stock_id, start_date=None, end_date=None):
        """
        Fetch TaiwanStockPrice.
        Returns DataFrame with date, open, high, low, close, volume.
        """
        if not start_date:
            # Default to 1 year ago if not specified
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            
        try:
            # Debug: Check which token is being used (Decode User ID)
            user_id = self._extract_user_id(self.token)
            
            print(f"DEBUG: FinMind fetch_daily_price | User: {user_id} | ID: {stock_id} | Start: {start_date} | End: {end_date}")

            # 使用 get_data 而非 taiwan_stock_daily（後者有 KeyError bug）
            kwargs = {
                "dataset": "TaiwanStockPrice",
                "data_id": stock_id,
                "start_date": start_date
            }
            if end_date:
                kwargs["end_date"] = end_date

            # [Fix] 使用 timeout 包裝，避免 API 無回應導致 HANG 住
            df = self._get_data_with_timeout(timeout_sec=self.api_timeout, **kwargs)
            return df
        except Exception as e:
            print(f"FinMind API Error (Price): {e}")
            return None

    def fetch_per_pbr(self, stock_id, start_date=None):
        """
        Fetch TaiwanStockPER (PER, PBR).
        Returns DataFrame with date, benefit_ratio (PER), pb_ratio (PBR).
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            
        try:
            # [Fix] 使用 timeout 包裝
            df = self._get_data_with_timeout(
                timeout_sec=self.api_timeout,
                dataset="TaiwanStockPER",
                data_id=stock_id,
                start_date=start_date
            )
            return df
        except Exception as e:
            print(f"FinMind API Error (PER): {e}")
            return None

    def fetch_stock_info(self, stock_id):
        """
        Get Shares Outstanding from TaiwanStockBalanceSheet.
        Returns: shares (float) or 0
        """
        try:
            # Fetch Balance Sheet for the last available quarter
            # We need 'OrdinaryShareCapital' (普通股股本)
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            
            # [Fix] 使用 timeout 包裝
            df = self._get_data_with_timeout(
                timeout_sec=self.api_timeout,
                dataset="TaiwanStockBalanceSheet",
                data_id=stock_id,
                start_date=start_date
            )
            
            if df is not None and not df.empty:
                # Filter origin_name='普通股股本'
                target = df[df['origin_name'].str.contains('普通股股本', na=False)]
                
                if target.empty:
                    # Fallback to '股本合計'
                    target = df[df['origin_name'].str.contains('股本合計', na=False)]
                
                if not target.empty:
                    # Sort by date desc to get latest
                    target = target.sort_values(by='date', ascending=False)
                    latest_val = float(target.iloc[0]['value'])
                    date_val = target.iloc[0]['date']
                    
                    # print(f"DEBUG: Found Capital for {stock_id} at {date_val}: {latest_val}")
                    
                    # FinMind BalanceSheet Unit Check
                    # It appears to be RAW VALUE (TWD), not Thousands.
                    # Verification showed ~2.86 Billion for 3006.
                    # Shares = Capital / 10
                    
                    return latest_val / 10
            
            # Fallback if '普通股股本' not found but '股本合計' exists?
            # '股本合計' (Total Capital Stock) might include preferred.
            
            return 0
        except Exception as e:
            print(f"FinMind API Error (Info): {e}")
            return 0

    def fetch_minute_price(self, stock_id, date_str):
        """
        Fetch TaiwanStockPriceMinute.
        date_str: YYYY-MM-DD
        Returns DataFrame with date, time, open, high, low, close, volume.
        """
        try:
            # print(f"DEBUG: Fetching Minute {stock_id} {date_str} with token {self.api_token[:10]}...")
            # [Fix] 使用 timeout 包裝
            df = self._get_data_with_timeout(
                timeout_sec=self.api_timeout,
                dataset="TaiwanStockPriceMinute",
                data_id=stock_id,
                start_date=date_str,
                end_date=date_str
            )
            if df is None:
                print(f"FinMind API returned None for {stock_id} {date_str}")
            elif df.empty:
                print(f"FinMind API returned Empty DF for {stock_id} {date_str}")
            return df
        except Exception as e:
            print(f"FinMind API Error (Minute Price) for {stock_id} {date_str}: {e}")
            return None
