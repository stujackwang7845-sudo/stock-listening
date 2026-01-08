
import requests
import pandas as pd
import urllib3
import json
from datetime import datetime

urllib3.disable_warnings()

def debug_twse():
    print("--- DEBUG TWSE (20260102) ---")
    url = "https://www.twse.com.tw/rwd/zh/announcement/notetrans?date=20260102&response=json"
    try:
        r = requests.get(url, verify=False, timeout=10)
        data = r.json()
        print(f"Status: {data.get('stat')}")
        print(f"Title: {data.get('title')}")
        fields = data.get('fields')
        print(f"Fields: {fields}")
        
        rows = data.get('data', [])
        print(f"Total Rows: {len(rows)}")
        for i, row in enumerate(rows):
            print(f"Row {i}: {row}")
    except Exception as e:
        print(f"Error: {e}")

def debug_tpex():
    print("\n--- DEBUG TPEX TARGETED (20260102) ---")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Referer": "https://www.tpex.org.tw/"
    }

    urls = [
        "https://www.tpex.org.tw/www/zh-tw/bulletin/warning?response=json",
        "https://www.tpex.org.tw/www/zh-tw/bulletin/warning?startDate=115/01/02&endDate=115/01/02&response=json",
        # Check attention with type param again
        "https://www.tpex.org.tw/www/zh-tw/bulletin/attention?type=abnormal&response=json"
    ]
    
    for url in urls:
        try:
            print(f"Testing: {url}")
            r = requests.get(url, headers=headers, verify=False, timeout=5)
            if r.status_code == 200:
                try:
                    data = r.json()
                    # Print full structure summary
                    if 'tables' in data:
                        print(f"  Tables: {len(data['tables'])}")
                        for i, t in enumerate(data['tables']):
                            print(f"    Table {i} Title: {t.get('title')}")
                            print(f"    Table {i} Data Count: {len(t.get('data', []))}")
                            if len(t.get('data', [])) > 0:
                                print(f"    Sample Row: {t['data'][0]}")
                    elif 'data' in data:
                        print(f"  Items: {len(data['data'])}")
                        if len(data['data']) > 0:
                            print(f"  Sample: {data['data'][0]}")
                    else:
                        print(f"  Keys: {data.keys()}")
                except:
                    print("  [200 OK] Not JSON")
            else:
                 print(f"  [{r.status_code}]")
        except Exception as e:
            print(f"  Error: {e}")
            
    print("-" * 20)

if __name__ == "__main__":
    debug_twse()
    debug_tpex()
