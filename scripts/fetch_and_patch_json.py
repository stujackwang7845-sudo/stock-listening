import json
from datetime import datetime
import sys
import os

sys.path.append(os.getcwd())
from core.scraper_attention import AttentionScraper

def patch_json():
    target_dt = datetime(2026, 2, 23)
    print(f"Fetching official attention data for {target_dt.date()}...")
    try:
        att_list = AttentionScraper.fetch_data(target_dt)
    except Exception as e:
        print("Scraper error:", e)
        return
        
    if not att_list:
        print("No data fetched.")
        return
        
    print("Sample fetched:", att_list[0] if att_list else "Empty")
    
    code_to_clauses = {}
    for item in att_list:
        code = str(item.get('code'))
        # Usually scraper returns 'clauses' or 'trigger_info' 
        # or we might need to look at 'reason'
        trigger = item.get('trigger_info', item.get('clauses', ''))
        code_to_clauses[code] = trigger
    
    print(f"Fetched {len(code_to_clauses)} stocks. 5386 clauses: {code_to_clauses.get('5386')}")
    
    json_path = 'listening_history.json'
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    fixed = 0
    for row in data:
        code = str(row.get('code'))
        date_str = str(row.get('date'))
        if date_str == '2026-02-23':
            current_trigger = row.get('trigger_info', {})
            if isinstance(current_trigger, str):
                try: current_trigger = json.loads(current_trigger)
                except: current_trigger = {}
                
            if not current_trigger and code in code_to_clauses:
                trigger = code_to_clauses.get(code)
                if trigger:
                    # Write dict format { '2026-02-23': '一,二' }
                    row['trigger_info'] = {date_str: trigger}
                    fixed += 1
                    print(f"Patched {code} with {trigger}")
                    
    if fixed > 0:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"Successfully patched {fixed} records in JSON.")

if __name__ == "__main__":
    patch_json()
