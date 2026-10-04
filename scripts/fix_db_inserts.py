import re

def fix_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # update_disposal_from_web.py
    content = content.replace('"上市",\n                                item.get(\'ann_date\'),',
                              '"上市",\n                                item.get(\'ann_date\') or period_start,')
    content = content.replace('"上櫃",\n                                item.get(\'ann_date\'),',
                              '"上櫃",\n                                item.get(\'ann_date\') or period_start,')

    # dashboard.py
    content = content.replace('announce_str,\n                                    p_code,',
                              'announce_str or start_str,\n                                    p_code,')
    content = content.replace('"上市", item.get("date"), item.get("code"), item.get("name"),',
                              '"上市", item.get("date") or start, item.get("code"), item.get("name"),')
    content = content.replace('"上櫃", item.get("date"), item.get("code"), item.get("name"),',
                              '"上櫃", item.get("date") or start, item.get("code"), item.get("name"),')
    content = content.replace('"上市",\n                                    item.get("date"), # Announce Date',
                              '"上市",\n                                    item.get("date") or start, # Announce Date')
    content = content.replace('r.get(\'source\', \'\'),\n                            announce_date_val,\n                            r.get(\'code\', \'\'),',
                              'r.get(\'source\', \'\'),\n                            announce_date_val or p_start,\n                            r.get(\'code\', \'\'),')

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file(r'e:\Vibe Coding\Stock\處置股\scripts\update_disposal_from_web.py')
fix_file(r'e:\Vibe Coding\Stock\處置股\scripts\ui\dashboard.py')
print("Fixed DB inserts!")
