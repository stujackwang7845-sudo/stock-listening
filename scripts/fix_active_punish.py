import codecs

def patch_forecast_page():
    with codecs.open('scripts/ui/forecast_page.py', 'r', 'utf-8') as f:
        code = f.read()

    target = '''            if in_active_punish:
                # 優先使用官方 API 處置資訊，強迫覆蓋並將之列入處置中
                actually_disposed = True
                disp_start_dt = datetime.combine(active_punish_map[str(code)]["start"], datetime.min.time())
                disp_end_dt = datetime.combine(active_punish_map[str(code)]["end"], datetime.min.time())
                freq = active_punish_map[str(code)]["freq"]
                reason = active_punish_map[str(code)]["reason"] or self._guess_reason(data)
            else:'''
    
    # We will do a generic replacement matching the structure since encoding might have broken the comments in standard out
    target_fallback = '''            if in_active_punish:
                # uϥΥ API BmTAjN˴Q෽ӪѦCJBm
                actually_disposed = True
                disp_start_dt = datetime.combine(active_punish_map[str(code)]["start"], datetime.min.time())
                disp_end_dt = datetime.combine(active_punish_map[str(code)]["end"], datetime.min.time())
                freq = active_punish_map[str(code)]["freq"]
                reason = active_punish_map[str(code)]["reason"] or self._guess_reason(data)
            else:'''

    replacement = '''            # Check if there is a newer future disposal that overrides the active one
            override_active_punish = False
            if in_active_punish and future_period:
                f_start = DateUtils.parse_period_start(future_period)
                if f_start and f_start.date() > active_punish_map[str(code)]["start"]:
                    override_active_punish = True

            if in_active_punish and not override_active_punish:
                # 優先使用官方 API 處置資訊，強迫覆蓋並將之列入處置中
                actually_disposed = True
                disp_start_dt = datetime.combine(active_punish_map[str(code)]["start"], datetime.min.time())
                disp_end_dt = datetime.combine(active_punish_map[str(code)]["end"], datetime.min.time())
                freq = active_punish_map[str(code)]["freq"]
                reason = active_punish_map[str(code)]["reason"] or self._guess_reason(data)
            else:'''

    if target in code:
        code = code.replace(target, replacement)
    elif target_fallback in code:
        code = code.replace(target_fallback, replacement)
    else:
        # manual replace
        lines = code.split('\\n')
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if 'if in_active_punish:' in line and 'actually_disposed = True' in lines[i+2] and 'disp_start_dt = datetime.combine' in lines[i+3]:
                # replace it
                new_lines.append('            # Check if there is a newer future disposal that overrides the active one')
                new_lines.append('            override_active_punish = False')
                new_lines.append('            if in_active_punish and future_period:')
                new_lines.append('                f_start = DateUtils.parse_period_start(future_period)')
                new_lines.append('                if f_start and f_start.date() > active_punish_map[str(code)]["start"]:')
                new_lines.append('                    override_active_punish = True')
                new_lines.append('')
                new_lines.append('            if in_active_punish and not override_active_punish:')
            else:
                new_lines.append(line)
            i += 1
        code = '\\n'.join(new_lines)
            
    with codecs.open('scripts/ui/forecast_page.py', 'w', 'utf-8') as f:
        f.write(code)

if __name__ == '__main__':
    patch_forecast_page()
