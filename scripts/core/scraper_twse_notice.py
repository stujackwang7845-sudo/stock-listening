"""
新的 TWSE Notice 抓取方法（帶 querytype=1 支援日期並有款次）
"""
import requests
from datetime import datetime

class NoticeFetcher:
    
    @staticmethod
    def fetch_notice(date_obj: datetime):
        """
        抓取指定日期的公布注意交易資訊 (含第X款) - TWSE + TPEX
        """
        twse_data = NoticeFetcher._fetch_twse_notice(date_obj)
        tpex_data = NoticeFetcher._fetch_tpex_notice(date_obj)
        return twse_data + tpex_data

    @staticmethod
    def _fetch_twse_notice(date_obj: datetime):
        """
        抓取 TWSE 指定日期的公布注意交易資訊 (含第X款)
        URL: https://www.twse.com.tw/rwd/zh/announcement/notice
        Params: querytype=1, startDate=YYYYMMDD, endDate=YYYYMMDD, response=json
        """
        date_str = date_obj.strftime("%Y%m%d")
        url = "https://www.twse.com.tw/rwd/zh/announcement/notice"
        params = {
            "querytype": "1",
            "startDate": date_str,
            "endDate": date_str,
            "response": "json"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        results = []
        try:
            r = requests.get(url, params=params, headers=headers, verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get('stat', '').lower() == 'ok':
                    rows = data.get('data', [])
                    for row in rows:
                        if len(row) >= 6:
                            # [1] 代號, [2] 名稱, [4] 注意交易資訊(條款)
                            code = str(row[1]).strip()
                            name = str(row[2]).strip()
                            reason = str(row[4]).strip()
                            
                            results.append({
                                'code': code,
                                'name': name,
                                'reason': reason,
                                'source': 'TWSE'
                            })
        except Exception as e:
            print(f"[NoticeFetcher] TWSE Error for {date_str}: {e}")
            
        return results

    @staticmethod
    def _fetch_tpex_notice(date_obj: datetime):
        """
        抓取 TPEX 指定日期的公布注意交易資訊 (含第X款)
        URL: https://www.tpex.org.tw/www/zh-tw/bulletin/attention
        Params: startDate=YYYY/MM/DD, endDate=YYYY/MM/DD, response=json
        """
        date_str = date_obj.strftime("%Y/%m/%d")
        url = "https://www.tpex.org.tw/www/zh-tw/bulletin/attention"
        params = {
            "startDate": date_str,
            "endDate": date_str,
            "code": "",
            "cate": "",
            "type": "all",
            "order": "date",
            "id": "",
            "response": "json"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        results = []
        try:
            r = requests.get(url, params=params, headers=headers, verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get('stat', '').lower() == 'ok' and 'tables' in data and data['tables']:
                    # TPEX returns a list of tables, usually the first one is what we need
                    table = data['tables'][0]
                    rows = table.get('data', [])
                    for row in rows:
                        if len(row) >= 5:
                            # row[1] 證券代號, row[2] 證券名稱, row[4] 注意交易資訊(條款原因)
                            code = str(row[1]).strip()
                            name = str(row[2]).strip()
                            reason = str(row[4]).strip()
                            
                            results.append({
                                'code': code,
                                'name': name,
                                'reason': reason,
                                'source': 'TPEX'
                            })
        except Exception as e:
            print(f"[NoticeFetcher] TPEX Error for {date_str}: {e}")
            
        return results

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings()
    res = NoticeFetcher.fetch_notice(datetime(2026, 2, 11))
    print(f"找到 {len(res)} 筆 (TWSE+TPEX)")
    for r in res[:3]:
        print(f"  {r['code']} {r['name']}: {r['reason'][:30]}...")
