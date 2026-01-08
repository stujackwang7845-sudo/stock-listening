
import requests
import json

url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
print(f"Fetching from {url}...")
try:
    resp = requests.get(url)
    data = resp.json()
    if "data" in data and len(data["data"]) > 0:
        print("Fields:", data.get("fields", []))
        print("First Item:", data["data"][0])
        # Find 1528 if possible to see specific example
        for row in data["data"]:
            if "1528" in str(row):
                print("Found 1528:", row)
                break
    else:
        print("No data found or bad structure")
except Exception as e:
    print(e)
