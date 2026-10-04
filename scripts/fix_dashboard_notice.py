import codecs

def fix_dashboard():
    with codecs.open('scripts/ui/dashboard.py', 'r', 'utf-8') as f:
        lines = f.readlines()
        
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Add back the notice appending logic
        if 'is_future_start = True' in line and 'if f_start and f_start.date() > display_dt_only:' in lines[i-1]:
            new_lines.append(line)
            # Add the logic right after
            logic = """
            if is_future_start:
                 suffix = ""
                 if data.get("has_futures") or (hasattr(self, 'mf_db') and self.mf_db and self.mf_db.has_futures(code)): suffix += "(期)"
                 if hasattr(self, 'cb_db') and self.cb_db and self.cb_db.has_cb_now(code): suffix += "(CB)"
                 item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                 src_notice = data.get("source", "")
                 if src_notice in ("TWSE", "tse", "上市"): src_notice = "上市"
                 elif src_notice in ("TPEX", "otc", "OTC", "上櫃", " TPEX"): src_notice = "上櫃"
                 if src_notice == "上市": notice_twse.append(item_str)
                 else: notice_tpex.append(item_str)
"""
            new_lines.append(logic)
            i += 1
            continue
            
        # 2. Remove the "明天處置第一天" from active_list
        if 'active_lbl_new =' in line and '處置第一天' in line:
            # We don't append this line
            i += 1
            continue
            
        if 'active_list = [' in line:
            # Check if next lines are the ones we want to remove
            if '(active_lbl_new, ""),' in lines[i+1]:
                new_lines.append(line)
                i += 4 # skip the next 3 lines (active_lbl_new, twse, tpex)
                continue

        new_lines.append(line)
        i += 1

    with codecs.open('scripts/ui/dashboard.py', 'w', 'utf-8') as f:
        f.writelines(new_lines)
        
if __name__ == '__main__':
    fix_dashboard()
