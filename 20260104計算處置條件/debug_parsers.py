
import requests
import json
from datetime import datetime

# Mimic Fetcher behavior
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def fetch_twse_disp(date_str):
    # TWSE API
    url = f"https://www.twse.com.tw/rwd/zh/announcement/punish?date={date_str}&startDate={date_str}&endDate={date_str}&response=json"
    print(f"Fetching TWSE: {url}")
    res = requests.get(url, headers=HEADERS)
    return res.json()

def fetch_tpex_disp(date_str):
    # TPEX API: https://www.tpex.org.tw/web/bulletin/disposition/disposition_stk.php?l=zh-tw&f=json&d=114/12/31
    # Convert 20241231 to 113/12/31, 20251231 to 114/12/31
    y = int(date_str[:4]) - 1911
    m = date_str[4:6]
    d = date_str[6:]
    roc_date = f"{y}/{m}/{d}"
    
    url = f"https://www.tpex.org.tw/web/bulletin/disposition/disposition_stk.php?l=zh-tw&f=json&d={roc_date}"
    print(f"Fetching TPEX: {url}")
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        print(f"TPEX Status: {res.status_code}")
        
        try:
           data = res.json()
           print("TPEX JSON Success")
           return data
        except:
           print("TPEX JSON Fail. Raw Text:")
           print(res.text[:500])
           return []
    except Exception as e:
        print(f"TPEX Request Error: {e}")
        return []

def run():
    date_str = "20251231" # User context year 2025? (Current real time 2026, date 2025/12/31)
    
    # TPEX
    tpex_data = fetch_tpex_disp(date_str)
    print("\n--- TPEX DATA SAMPLE ---")
    if isinstance(tpex_data, dict):
        print(f"Dict Keys: {tpex_data.keys()}")
        if "aaData" in tpex_data:
            print("Found 'aaData'")
            if len(tpex_data["aaData"]) > 0:
                 print(f"Row 0: {tpex_data['aaData'][0]}")
    elif isinstance(tpex_data, list):
        print("Is List")
        if len(tpex_data) > 0:
            print(f"Row 0: {tpex_data[0]}")
    else:
        print(f"Type: {type(tpex_data)}")

if __name__ == "__main__":
    run()
