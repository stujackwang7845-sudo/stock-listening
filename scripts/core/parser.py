
import pandas as pd
from datetime import datetime
import re

class StockParser:
    def __init__(self):
        pass
        
    def parse_twse_attention(self, raw_data):
        """
        TWSE Attention Data Structure:
        {
          "data": [
            [ "1", "0050", "元大台灣50", "...", ... ]
          ],
          "fields": ["序號", "證券代號", "證券名稱", ...]
        }
        """
        if not raw_data or "data" not in raw_data or "fields" not in raw_data:
            return []
            
        columns = raw_data["fields"]
        data = raw_data["data"]
        
        parsed_list = []
        for row in data:
            # TWSE fields usually: 序號, 證券代號, 證券名稱, 累積次數, 注意交易資訊內容...
            try:
                item = {
                    "source": "TWSE",
                    "status": "注意股",
                    "code": str(row[1]) if len(row) > 1 else "",
                    "name": row[2] if len(row) > 2 else "",
                    "reason": row[4] if len(row) > 4 else "", # Assuming 5th column is content
                    "raw": row
                }
                parsed_list.append(item)
            except IndexError:
                continue
        return parsed_list

    def _parse_roc_date(self, date_str):
        """1150108 or 115/01/08 or 115.01.08 or 115/2/2 -> 2026/01/08"""
        if not date_str: return ""
        
        # Method 1: Split by separator
        if "/" in date_str or "." in date_str:
            sep = "/" if "/" in date_str else "."
            parts = date_str.split(sep)
            if len(parts) == 3:
                try:
                    y = int(parts[0]) + 1911
                    m = int(parts[1])
                    d = int(parts[2])
                    return f"{y:04d}/{m:02d}/{d:02d}"
                except: pass

        # Method 2: Fixed Length (No separator)
        s = date_str.replace("/", "").replace(".", "").strip()
        
        # Format: 1150108 (7 chars)
        if len(s) == 7:
            try:
                y = int(s[:3]) + 1911
                return f"{y}/{s[3:5]}/{s[5:]}" # YYYY/MM/DD
            except:
                return ""
        # Format: 2026/01/08 (Already AD, 8 chars)
        if len(s) == 8:
            try:
                # Check if starts with 20 or 19
                if s.startswith("20") or s.startswith("19"):
                    return f"{s[:4]}/{s[4:6]}/{s[6:]}"
                else: 
                     # Might be ROC 1151212 (7 chars) but wait, 8 chars implies AD or invalid ROC
                     pass
            except:
                return ""
                
        return ""

    def parse_twse_disposition(self, raw_data):
        """
        TWSE Disposition Data Structure similar to Attention
        """
        if not raw_data or "data" not in raw_data or "fields" not in raw_data:
            return []
            
        columns = raw_data["fields"]
        data = raw_data["data"]
        
        parsed_list = []
        for row in data:
            # TWSE fields: 序號, 證券代號, 證券名稱, 處置期間, 處置措施...
            try:
                # TWSE indices: 0:Serial, 1:Date (Announce), 2:Code, 3:Name, 4:Count, 5:Condition, 6:Period, 7:Type, 8:Content
                ann_date_roc = str(row[1]).strip() if len(row) > 1 else ""
                ann_date = self._parse_roc_date(ann_date_roc)
                
                period = str(row[6]).strip() if len(row) > 6 else ""
                # row[8] 是完整的「處置內容」全文，其中包含撮合頻率
                measure_full = str(row[8]).strip() if len(row) > 8 else (str(row[7]).strip() if len(row) > 7 else "")
                
                # Simplify Measure
                from core.measure_parser import MeasureParser
                measure = MeasureParser.parse_frequency(measure_full)

                item = {
                    "source": "上市",
                    "status": "處置股",
                    "code": str(row[2]).strip() if len(row) > 2 else "",
                    "name": str(row[3]).strip() if len(row) > 3 else "",
                    # [Fix] 原本這裡丟掉了 measure_full 全文只留 "{measure} {period}"，
                    # 導致「變更交易方法」「分盤方式交易」等關鍵字連同 measure_full
                    # 一起被丟掉。這些關鍵字是判斷新制「例外情形不套用2分鐘」的唯一依據
                    # （見 MeasureParser.get_effective_frequency），reason 被截斷後
                    # 該股撮合頻率就會被誤判成2分鐘（曾發生在6225，官方原文明明是
                    # 「變更交易方法為全額交割者...約每十分鐘撮合一次」）。改存完整原文。
                    "reason": measure_full or f"{measure} {period}",
                    "period": period,
                    "measure": measure,
                    "ann_date": ann_date,
                    "raw": row
                }
                parsed_list.append(item)
            except IndexError:
                continue
        return parsed_list

    # ... (skipping tpex_attention) ...

    def parse_tpex_disposition(self, raw_data):
        """
        Parses TPEX Disposition Data.
        Supports:
        1. JSON List (OpenAPI)
        2. HTML String (Web Portal) [New]
        """
        if not raw_data:
            return []
            
        parsed_list = []
        
        # OpenAPI usually returns a list directly
        data_list = []
        is_portal = False
        if isinstance(raw_data, list):
             data_list = raw_data
        elif isinstance(raw_data, dict):
             if "tables" in raw_data and len(raw_data["tables"]) > 0 and "data" in raw_data["tables"][0]:
                 data_list = raw_data["tables"][0]["data"]
                 is_portal = True
             else:
                 # Fallback if wrapped
                 data_list = raw_data.get("aaData", [])
             
        for row in data_list:
            if is_portal:
                if len(row) < 8: continue
                code = str(row[2]).strip()
                name_html = str(row[3])
                name_match = re.match(r'^([^\(]+)', name_html) # Get text before '(' or HTML
                name = name_match.group(1).strip() if name_match else name_html
                name = re.sub(r'<[^>]+>', '', name).strip()
                
                period = str(row[5]).strip()
                measure_full = str(row[7]).strip()
                ann_date_roc = str(row[1]).strip()
                ann_date = self._parse_roc_date(ann_date_roc)
            else:
                # OpenAPI fields
                code = str(row.get("SecuritiesCompanyCode", row.get("StkNo", ""))).strip()
                name = row.get("CompanyName", row.get("SecuritiesCompanyName", row.get("StkName", ""))).strip()
                
                period = row.get("DispositionPeriod", "").strip()
                measure_full = row.get("DisposalCondition", "").strip()
                ann_date_roc = row.get("Date", "").strip()
                ann_date = self._parse_roc_date(ann_date_roc)
            
            # Simplify Measure (Handle Chinese numerals, number parsing, and garbled text)
            from core.measure_parser import MeasureParser
            measure = MeasureParser.parse_frequency(measure_full)
            
            if code:
                parsed_list.append({
                    "code": code,
                    "name": name,
                    # [Fix] 原本硬寫死 "處置"，完整原文(measure_full)直接被丟棄，
                    # 理由同 TWSE 那邊的修正：關鍵字全部遺失會導致新制頻率誤判。
                    "reason": measure_full or "處置",
                    "is_disposed": True,
                    "period": period,
                    "measure": measure,
                    "ann_date": ann_date,
                    "source": "上櫃"
                })
                
        return parsed_list
    def parse_twse_margin(self, raw_data):
        """
        Return set of viable margin codes.
        TWSE MI_MARGN structure:
        "tables": [ ..., { "data": [[Code, Name, ..., Note], ...], "fields": [..., "備註"] } ]
        Note: 'O' = Stop Margin Buy, 'X' = Stop Short Sell.
        """
        valid_codes = set()
        if not raw_data: return valid_codes
        
        data = []
        note_idx = -1
        
        if isinstance(raw_data, dict):
            if "tables" in raw_data:
                for table in raw_data["tables"]:
                     if "data" in table:
                         # Heuristic: Check fields
                         fields = table.get("fields", [])
                         if "股票代號" in fields or "代號" in fields:
                             data = table["data"]
                             # Find Note index
                             for i, f in enumerate(fields):
                                 if "備註" in f:
                                     note_idx = i
                                     break
                             break
        
        for row in data:
            if len(row) > 0:
                code = str(row[0])
                # Check Note
                if note_idx != -1 and len(row) > note_idx:
                    note = str(row[note_idx])
                    if "O" in note or "X" in note:
                        continue # Skip if limited
                
                valid_codes.add(code)
        return valid_codes

    def parse_tpex_margin(self, raw_data):
        """
        TPEX Balance structure:
        { "tables": [ { "data": [[Code, Name, ..., Note], ...], "fields": [..., "註記"] } ] }
        """
        valid_codes = set()
        if not raw_data: return valid_codes
        
        data_list = []
        note_idx = -1
        
        if isinstance(raw_data, dict) and "tables" in raw_data:
            tables = raw_data["tables"]
            if tables and len(tables) > 0:
                t0 = tables[0]
                data_list = t0.get("data", [])
                fields = t0.get("fields", [])
                for i, f in enumerate(fields):
                    if "註記" in f or "備註" in f:
                        note_idx = i
                        break
                        
        for row in data_list:
            if not isinstance(row, list) or len(row) < 1: continue
            
            code = str(row[0])
            
            # Check Note
            if note_idx != -1 and len(row) > note_idx:
                note = str(row[note_idx])
                if "O" in note or "X" in note:
                    continue
            
            valid_codes.add(code)
        return valid_codes

    def parse_tpex_attention(self, raw_data):
        import datetime
        import os
        log_path = r"E:\Vibe Coding\Stock\處置股\real_time_debug.log"
        print(f"DEBUG_CONSOLE: Parser called with {type(raw_data)}")
        
        if not raw_data: 
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] Parser received empty raw_data\n")
            return []
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] Parser processing raw_data. Keys: {raw_data.keys() if isinstance(raw_data, dict) else 'List'}\n")

        data = []
        try:
            if isinstance(raw_data, list):
                 data = raw_data
            elif isinstance(raw_data, dict):
                 if "aaData" in raw_data:
                     data = raw_data["aaData"]
                 elif "tables" in raw_data and raw_data["tables"]:
                     # New API format
                     data = raw_data["tables"][0].get("data", [])
                     with open(log_path, "a", encoding="utf-8") as f:
                         f.write(f"[{datetime.datetime.now()}] Found 'tables' format. Rows: {len(data)}\n")
                 elif "iTotalRecords" in raw_data: # Datatable
                     data = raw_data.get("aaData", [])
                     
            if not data:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now()}] No data rows found in raw_data.\n")

            parsed_list = []
            for row in data:
                try:
                    code = ""
                    name = ""
                    reason = ""
                    
                    if isinstance(row, dict):
                        code = row.get("StkNo", "")
                        name = row.get("StkName", "")
                        pass
                    elif isinstance(row, list):
                        # Array based structure from API: [Serial, Code, Name, Count, Reason, Date, ...]
                        code = str(row[1]) if len(row) > 1 else ""
                        name = str(row[2]) if len(row) > 2 else ""
                        # Reason is typically column 4
                        reason = str(row[4]) if len(row) > 4 else ""
                        
                    if not code: continue
                    
                    parsed_list.append({
                        "source": "TPEX",
                        "status": "注意股",
                        "code": code,
                        "name": name,
                        "reason": reason, # Raw reason
                        "raw": row
                    })
                except Exception as e:
                     with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now()}] Row Parse Error: {e}\n")
                     continue
            
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] Parsed {len(parsed_list)} items.\n")
                
            print(f"DEBUG_CONSOLE: Parser finished. Items: {len(parsed_list)}")
            return parsed_list
            
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] Top Level Parse Error: {e}\n")
            print(f"DEBUG_CONSOLE: Parser Error: {e}")
            return []

    def parse_taifex_futures_list(self, raw_data):
        """
        Parsing TAIFEX SSFLists.
        Expected sample: {'Contract': 'CAF', 'StockCode': '1303', ...} (Based on debug output)
        Return set of StockCode.
        """
        valid_codes = set()
        if not raw_data or not isinstance(raw_data, list): return valid_codes
        
        for item in raw_data:
            # Debug output showed 'StockCode'
            uid = item.get("StockCode", item.get("UnderlyingID", ""))
            if uid:
                valid_codes.add(str(uid).strip())
        return valid_codes
