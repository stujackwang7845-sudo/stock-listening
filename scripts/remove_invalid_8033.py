import json

file_path = 'listening_history.json'
dates_to_remove = ["2025-12-12", "2025-12-23", "2025-12-26", "2025-12-29"]
target_code = "8033"

with open(file_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

initial_count = len(data)
new_data = [
    r for r in data 
    if not (r['code'] == target_code and r['date'] in dates_to_remove)
]
final_count = len(new_data)

print(f"Removed {initial_count - final_count} records for {target_code}.")

with open(file_path, 'w', encoding='utf-8') as f:
    json.dump(new_data, f, ensure_ascii=False, indent=4)
