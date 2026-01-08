"""
測試抓取上櫃處置股API
"""
import requests

url = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?response=json"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ",
    "Accept": "application/json"
}

try:
    res = requests.get(url, headers=headers, timeout=10)
    print(f"Status: {res.status_code}")
    print(f"Content-Type: {res.headers.get('Content-Type')}")
    
    if res.status_code == 200:
        data = res.json()
        print(f"\nKeys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
        
        if isinstance(data, dict) and 'tables' in data:
            print(f"\nTables count: {len(data['tables'])}")
            if data['tables']:
                table = data['tables'][0]
                print(f"First table keys: {list(table.keys())}")
                if 'data' in table:
                    print(f"Data rows: {len(table['data'])}")
                    if table['data']:
                        print(f"\nFirst row sample: {table['data'][0][:4]}")  # 前4個欄位
except Exception as e:
    print(f"Error: {e}")
