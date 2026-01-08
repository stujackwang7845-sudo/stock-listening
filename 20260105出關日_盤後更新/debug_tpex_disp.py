
import requests
import json

url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
print(f"Fetching from {url}...")
try:
    resp = requests.get(url, verify=False)
    data = resp.json()
    print(f"Items: {len(data)}")
    if len(data) > 0:
        print("First Item Keys:", data[0].keys())
        print("First Item Content:", json.dumps(data[0], ensure_ascii=False, indent=2))
except Exception as e:
    print(e)
