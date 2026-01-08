
import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
print(f"Fetching {url}...")
try:
    resp = requests.get(url, verify=False)
    data = resp.json()
    print(f"Data Type: {type(data)}")
    if isinstance(data, list) and len(data) > 0:
        print("First Item Keys:", list(data[0].keys()))
        print("First Item Content:", json.dumps(data[0], ensure_ascii=False, indent=2))
    elif isinstance(data, dict):
         print("Data Keys:", data.keys())
except Exception as e:
    print(e)
