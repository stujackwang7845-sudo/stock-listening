"""
從官方 API 下載每日股價並儲存到資料庫

支援:
- TWSE (上市) 股票
- TPEX (上櫃) 股票
"""

import requests
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from core.price_database import PriceDatabase


class StockPriceDownloader:
    """股價下載器"""
    
    def __init__(self, db_path="stock_prices.db"):
        """初始化"""
        self.db = PriceDatabase(db_path)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        # 關閉 SSL 驗證（官方 API 證書問題）
        self.session.verify = False
        
        # 禁用 SSL 警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    def download_twse_daily(self, date: str = None) -> int:
        """
        下載上市股票每日價格
        
        Args:
            date: 日期 (YYYY-MM-DD)，預設為今天
        
        Returns:
            成功下載的筆數
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        print(f"\n下載上市股票 {date} 價格...")
        
        try:
            # TWSE API: /exchangeReport/STOCK_DAY_ALL
            url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if not data:
                print("  無資料")
                return 0
            
            # 解析並轉換為資料庫格式
            price_records = []
            for item in data:
                try:
                    # TWSE 欄位: Code, Name, TradeVolume, TradeValue, OpeningPrice, 
                    #            HighestPrice, LowestPrice, ClosingPrice, Change, Transaction
                    
                    code = str(item.get('Code', '')).strip()
                    if not code or len(code) > 4:  # 跳過非標準股票代號
                        continue
                    
                    # 價格處理（移除逗號）
                    def parse_price(val):
                        if not val or val == '--' or val == 'N/A':
                            return None
                        try:
                            return float(str(val).replace(',', ''))
                        except:
                            return None
                    
                    # 成交量處理
                    def parse_volume(val):
                        if not val or val == '--':
                            return None
                        try:
                            return int(str(val).replace(',', ''))
                        except:
                            return None
                    
                    price_records.append({
                        'date': date,
                        'code': code,
                        'name': item.get('Name', ''),
                        'open': parse_price(item.get('OpeningPrice')),
                        'high': parse_price(item.get('HighestPrice')),
                        'low': parse_price(item.get('LowestPrice')),
                        'close': parse_price(item.get('ClosingPrice')),
                        'volume': parse_volume(item.get('TradeVolume')),
                        'source': '上市'
                    })
                
                except Exception as e:
                    print(f"  解析失敗: {item.get('Code')} - {e}")
            
            # 批次插入資料庫
            if price_records:
                count = self.db.insert_daily_prices(price_records)
                print(f"  ✓ 成功下載 {count} 筆上市股票")
                return count
            
            return 0
        
        except Exception as e:
            print(f"  ✗ 下載失敗: {e}")
            return 0
    
    def download_tpex_daily(self, date: str = None) -> int:
        """
        下載上櫃股票每日價格
        
        Args:
            date: 日期 (YYYY-MM-DD)，預設為今天
        
        Returns:
            成功下載的筆數
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        print(f"\n下載上櫃股票 {date} 價格...")
        
        try:
            # TPEX API: /tpex_mainboard_daily_close_quotes
            url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if not data:
                print("  無資料")
                return 0
            
            # 解析並轉換為資料庫格式
            price_records = []
            for item in data:
                try:
                    # TPEX 欄位: SecuritiesCompanyCode, CompanyName, Close, Change, 
                    #            Open, High, Low, Volume
                    
                    code = str(item.get('SecuritiesCompanyCode', '')).strip()
                    if not code:
                        continue
                    
                    # 價格處理
                    def parse_price(val):
                        if not val or val == '--' or val == 'N/A':
                            return None
                        try:
                            return float(str(val).replace(',', ''))
                        except:
                            return None
                    
                    # 成交量處理
                    def parse_volume(val):
                        if not val or val == '--':
                            return None
                        try:
                            return int(str(val).replace(',', ''))
                        except:
                            return None
                    
                    price_records.append({
                        'date': date,
                        'code': code,
                        'name': item.get('CompanyName', ''),
                        'open': parse_price(item.get('Open')),
                        'high': parse_price(item.get('High')),
                        'low': parse_price(item.get('Low')),
                        'close': parse_price(item.get('Close')),
                        'volume': parse_volume(item.get('Volume')),
                        'source': '上櫃'
                    })
                
                except Exception as e:
                    print(f"  解析失敗: {item.get('SecuritiesCompanyCode')} - {e}")
            
            # 批次插入資料庫
            if price_records:
                count = self.db.insert_daily_prices(price_records)
                print(f"  ✓ 成功下載 {count} 筆上櫃股票")
                return count
            
            return 0
        
        except Exception as e:
            print(f"  ✗ 下載失敗: {e}")
            return 0
    
    def download_all_today(self) -> Dict:
        """下載今天的所有股票價格"""
        today = datetime.now().strftime("%Y-%m-%d")
        
        print(f"="*60)
        print(f"下載 {today} 股價數據")
        print(f"="*60)
        
        results = {
            'date': today,
            'twse_count': 0,
            'tpex_count': 0,
            'total_count': 0
        }
        
        # 下載上市
        results['twse_count'] = self.download_twse_daily(today)
        time.sleep(2)  # 避免請求過快
        
        # 下載上櫃
        results['tpex_count'] = self.download_tpex_daily(today)
        
        results['total_count'] = results['twse_count'] + results['tpex_count']
        
        print(f"\n{'='*60}")
        print(f"下載完成")
        print(f"{'='*60}")
        print(f"上市: {results['twse_count']} 筆")
        print(f"上櫃: {results['tpex_count']} 筆")
        print(f"總計: {results['total_count']} 筆")
        
        return results
    
    def download_date_range(self, start_date: str, end_date: str) -> Dict:
        """
        下載日期範圍的股價（逐日下載）
        
        注意: 官方 API 只提供最新數據，歷史數據需要每日執行累積
        
        Args:
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
        """
        print(f"\n警告: 官方 API 只提供當日最新數據")
        print(f"無法直接下載歷史區間 {start_date} ~ {end_date}")
        print(f"建議: 設定每日自動執行此腳本以累積歷史數據\n")
        
        return self.download_all_today()
    
    def close(self):
        """關閉資料庫連線"""
        self.db.close()


if __name__ == "__main__":
    import sys
    
    downloader = StockPriceDownloader("stock_prices.db")
    
    try:
        if len(sys.argv) == 1:
            # 無參數：下載今天
            downloader.download_all_today()
        elif len(sys.argv) == 2:
            # 一個參數：下載指定日期
            date = sys.argv[1]
            downloader.download_twse_daily(date)
            time.sleep(2)
            downloader.download_tpex_daily(date)
        elif len(sys.argv) == 3:
            # 兩個參數：下載日期範圍（實際只會下載今天）
            start_date = sys.argv[1]
            end_date = sys.argv[2]
            downloader.download_date_range(start_date, end_date)
        
        # 顯示資料庫統計
        print(f"\n{'='*60}")
        print("資料庫統計")
        print(f"{'='*60}")
        stats = downloader.db.get_statistics()
        print(f"總筆數: {stats['total_records']:,}")
        print(f"各來源: {stats['by_source']}")
        print(f"不同股票: {stats['unique_stocks']}")
        print(f"日期範圍: {stats['date_range']['start']} ~ {stats['date_range']['end']}")
    
    finally:
        downloader.close()
