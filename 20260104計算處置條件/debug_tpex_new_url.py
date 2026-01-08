
import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?startDate=&endDate=&code=&cate=&type=all&reason=-1&measure=-1&order=date&id=&response=json"
print(f"Fetching {url}...")
try:
    resp = requests.get(url, verify=False)
    data = resp.json()
    print(f"Data Type: {type(data)}")
    
    if "data" in data and len(data["data"]) > 0:
        print("First Item Keys:", data["data"][0].keys())
        print("First Item Content:", json.dumps(data["data"][0], ensure_ascii=False, indent=2))
        
        # Check for measure info in fields
        for item in data["data"]:
            if "五分鐘" in str(item) or "二十分鐘" in str(item):
                print("Found item with measure info:", item)
                break
    else:
        print("No data found")

except Exception as e:
    print(e)
