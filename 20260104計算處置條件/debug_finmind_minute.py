

import requests
import json

# User's Token
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMS0wMyAwNzo0Njo0MyIsInVzZXJfaWQiOiJzdHVyYWluYm93c3Blcm0iLCJpcCI6IjIwMy4yMTEuMTA1LjIwNCJ9.JUyqymsQdWt4aQ4SgkPcSRv4mub13CaTUpYnPrdlKbU"

def debug_minute_data():
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPriceMinute",
        "data_id": "2330",
        "start_date": "2026-01-02",
        "token": TOKEN
    }
    
    print(f"Fetching raw data from {url}...")
    try:
        res = requests.get(url, params=params)
        print(f"Status Code: {res.status_code}")
        
        if res.status_code == 200:
            data = res.json()
            print("Response Keys:", data.keys())
            if "data" in data and len(data["data"]) > 0:
                print("First item:", data["data"][0])
            else:
                print("Data list is empty:", data)
        else:
            print("Error Response:", res.text)
            
    except Exception as e:
        print(f"Request Error: {e}")

if __name__ == "__main__":
    debug_minute_data()

