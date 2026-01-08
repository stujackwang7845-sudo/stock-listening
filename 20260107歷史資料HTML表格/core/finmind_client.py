from FinMind.data import DataLoader
import pandas as pd
import time
from datetime import datetime, timedelta

class FinMindClient:
    def __init__(self, token):
        self.api = DataLoader()
        self.api.login_by_token(api_token=token)
        self.max_retries = 3

    def fetch_daily_price(self, stock_id, start_date=None):
        """
        Fetch TaiwanStockPrice.
        Returns DataFrame with date, open, high, low, close, volume.
        """
        if not start_date:
            # Default to 1 year ago if not specified
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            
        try:
            # FinMind expects YYYY-MM-DD
            df = self.api.taiwan_stock_daily(
                stock_id=stock_id,
                start_date=start_date
            )
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
            df = self.api.get_data(
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
            
            df = self.api.get_data(
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
            df = self.api.get_data(
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
