
import requests

def download_html():
    url = "https://www.tpex.org.tw/zh-tw/announce/market/attention.html"
    params = {"startDate": "20260130", "endDate": "20260130", "type": "code"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
    
    print(f"Downloading {url}...")
    r = requests.get(url, params=params, headers=headers, verify=False, timeout=10)
    print(f"Status: {r.status_code}")
    
    with open("tpex_attention.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    
    print("Saved to tpex_attention.html")

if __name__ == "__main__":
    download_html()
