
import json
import os

FILE = "core/listening_history.json"
if not os.path.exists(FILE):
    FILE = "listening_history.json"

if os.path.exists(FILE):
    with open(FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    targets = ["6205", "8068", "6265", "4768"]
    print(f"Checking {len(data)} records for {targets}...")
    
    for r in data:
        if r["code"] in targets:
            print(f"--- {r['code']} ({r['date']}) ---")
            t_info = r.get("trigger_info", "")
            print(f"Raw Trigger Info: {repr(t_info)}")
            
            # Simulate Filter Logic
            parts = t_info.split(" ")
            real = 0
            for p in parts:
                if "/" in p: continue # date
                if "[" in p: continue # status
                if not p: continue
                
                clean_p = p.strip()
                is_dash = clean_p in ["-", "—", "–"]
                print(f"  Part: '{p}' | Clean: '{clean_p}' | Is Dash? {is_dash}")
                
                if not is_dash:
                    real += 1
            print(f"  Real Count: {real}")
else:
    print("History file not found.")
