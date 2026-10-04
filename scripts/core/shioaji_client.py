"""
處置股 Shioaji API 統一控制器 (Singleton)
現在已經改為透過 StockGateway (127.0.0.1:8080) 取得資料，不再需要直接登入永豐 API。
"""
import os
import threading
import pandas as pd
from datetime import datetime, date
import requests

class ShioajiClient:
    """Singleton 模式的 Gateway 連線管理器"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._connected = False
        self._login_error = None

    def _ensure_connected(self):
        """改為檢查 Gateway 是否存活"""
        if self._connected:
            return True
        try:
            res = requests.get("http://127.0.0.1:8080/api/health", timeout=2)
            if res.status_code == 200:
                self._connected = True
                self._login_error = None
                return True
            else:
                self._connected = False
                self._login_error = "Gateway 未就緒"
                return False
        except Exception as e:
            self._connected = False
            self._login_error = "無法連線至 Gateway，請確認 StockGateway.exe 是否啟動"
            return False

    @property
    def is_connected(self):
        return self._connected

    @property
    def last_error(self):
        return self._login_error

    # ===== 行情 API (Via Gateway) =====

    def get_kbars(self, code, start, end=None):
        """
        取得歷史日K線，回傳 DataFrame (Date index, Open/High/Low/Close/Volume)
        start/end: "YYYY-MM-DD" 格式
        """
        if not self._ensure_connected():
            return None
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")
        try:
            url = f"http://127.0.0.1:8080/api/data/kbars?code={code}&start={start}&end={end}"
            res = requests.get(url, timeout=30)
            if res.status_code == 200:
                data = res.json()
                if not data: return None
                
                df = pd.DataFrame(data)
                if df.empty: return None
                
                df['ts'] = pd.to_datetime(df['ts'])
                df['Date'] = df['ts'].dt.normalize()
                
                agg_dict = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
                agg_dict = {k: v for k, v in agg_dict.items() if k in df.columns}
                
                df = df.groupby('Date').agg(agg_dict).reset_index()
                df.set_index('Date', inplace=True)
                
                std_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                avail = [c for c in std_cols if c in df.columns]
                return df[avail]
            else:
                print(f"[ShioajiClient] get_kbars Gateway 錯誤: {res.text}")
                return None
        except Exception as e:
            print(f"[ShioajiClient] get_kbars({code}) 錯誤: {e}")
            return None

    def get_snapshots(self, codes):
        """
        取得多檔即時快照
        codes: list of str
        回傳: dict {code: {close, open, high, low, volume, ...}}
        """
        if not self._ensure_connected():
            return {}
        try:
            url = "http://127.0.0.1:8080/api/data/snapshots"
            res = requests.post(url, json={"codes": codes}, timeout=10)
            if res.status_code == 200:
                return res.json()
            return {}
        except Exception as e:
            print(f"[ShioajiClient] get_snapshots 錯誤: {e}")
            return {}

    def get_punish(self):
        """
        取得處置股清單
        回傳: DataFrame with columns [code, start_date, end_date, interval, description, ...]
        若失敗回傳 None
        """
        if not self._ensure_connected():
            return None
        try:
            res = requests.get("http://127.0.0.1:8080/api/data/punish", timeout=30)
            if res.status_code == 200:
                data = res.json()
                if data is None: return None
                return pd.DataFrame(data)
            return None
        except Exception as e:
            print(f"[ShioajiClient] get_punish 錯誤: {e}")
            return None

    def get_notice(self):
        """
        取得注意股清單
        回傳: DataFrame with columns [code, close, reason, announced_date, ...]
        若失敗回傳 None
        """
        if not self._ensure_connected():
            return None
        try:
            res = requests.get("http://127.0.0.1:8080/api/data/notice", timeout=30)
            if res.status_code == 200:
                data = res.json()
                if data is None: return None
                return pd.DataFrame(data)
            return None
        except Exception as e:
            print(f"[ShioajiClient] get_notice 錯誤: {e}")
            return None

    def get_stock_info(self, code):
        """取得股票基本資訊（股本等）"""
        if not self._ensure_connected():
            return None
        try:
            url = f"http://127.0.0.1:8080/api/data/stock_info?code={code}"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                return res.json()
            return None
        except Exception as e:
            print(f"[ShioajiClient] get_stock_info({code}) 錯誤: {e}")
            return None

    def close(self):
        """關閉連線"""
        self._connected = False
        print("[ShioajiClient] 已斷開與 Gateway 的連線")
