
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
                # TWSE indices: 0:Serial, 1:Date, 2:Code, 3:Name, 4:Count, 5:Condition, 6:Period, 7:Type, 8:Content
                period = str(row[6]).strip() if len(row) > 6 else ""
                measure_full = str(row[8]).strip() if len(row) > 8 else (str(row[7]).strip() if len(row) > 7 else "")
                
                # Simplify Measure (Handle Chinese numerals like 五, 二十)
                match = re.search(r'(約每\S+分鐘撮合一次)', measure_full)
                measure = match.group(1) if match else (measure_full[:20] + "..." if len(measure_full)>20 else measure_full)
                
                # 公告日期 (第2欄, 格式: YYYYMMDD)
                announce_date = str(row[1]).strip() if len(row) > 1 else ""
                
                item = {
                    "source": "TWSE",
                    "status": "處置股",
                    "code": str(row[2]).strip() if len(row) > 2 else "",
                    "name": str(row[3]).strip() if len(row) > 3 else "",
                    "reason": f"{measure} {period}",
                    "period": period,
                    "measure": measure,
                    "announce_date": announce_date,  # 新增
                    "raw": row
                }
                parsed_list.append(item)
            except IndexError:
                continue
        return parsed_list

    def parse_tpex_attention(self, raw_data):
        """
        TPEX New Portal often returns:
        { "tables": [ { "data": [ [col1, col2...], ... ] } ] }
        or simple list.
        """
        if not raw_data:
            return []
            
        data_list = []
        
        # 1. New Portal Structure
        if isinstance(raw_data, dict) and "tables" in raw_data:
            tables = raw_data["tables"]
            if tables and isinstance(tables, list) and len(tables) > 0:
                data_list = tables[0].get("data", [])
        # 2. Old/Fallback Structure
        elif isinstance(raw_data, dict) and "aaData" in raw_data:
            data_list = raw_data["aaData"]
        elif isinstance(raw_data, list):
            data_list = raw_data
            
        parsed_list = []
        for row in data_list:
            code = ""
            name = ""
            reason = ""
            
            if isinstance(row, list):
                 # Debug showed: ['1', '3081', '聯亞', ...]
                 # 0: Serial, 1: Code, 2: Name
                 if len(row) > 2: 
                     code = str(row[1])
                     name = row[2]
                 elif len(row) > 1:
                     # Fallback if no serial
                     code = str(row[0])
                     name = row[1]
                 
                 for col in row:
                    if isinstance(col, str) and ("款" in col or "以" in col):
                        reason = col
                        break
            elif isinstance(row, dict):
                 code = str(row.get("SecuritiesCompanyCode", row.get("StkNo", "")))
                 name = row.get("CompanyName", row.get("SecuritiesCompanyName", row.get("StkName", "")))
                 reason = row.get("TradingInformation", row.get("TransactionInformationContent", row.get("Reason", "")))

            item = {
                "source": "TPEX",
                "status": "注意股",
                "code": code,
                "name": name,
                "reason": reason,
                "raw": row
            }
            parsed_list.append(item)
        return parsed_list

    def parse_tpex_disposition(self, raw_data):
        """
        OpenAPI returns a list of dicts:
        [
          {"Date":"1131231","SecuritiesCompanyCode":"30922","CompanyName":"鴻碩二",...},
          ...
        ]
        """
        if not raw_data:
            return []
            
        # OpenAPI usually returns a list directly
        if isinstance(raw_data, list):
             data_list = raw_data
        else:
             # Fallback if wrapped
             data_list = raw_data.get("aaData", [])
             
        parsed_list = []
        for row in data_list:
            # OpenAPI fields: SecuritiesCompanyCode, CompanyName
            code = str(row.get("SecuritiesCompanyCode", row.get("StkNo", ""))).strip()
            name = row.get("CompanyName", row.get("SecuritiesCompanyName", row.get("StkName", ""))).strip()
            
            period = row.get("DispositionPeriod", "").strip()
            measure_full = row.get("DisposalCondition", "").strip()
            
            # Simplify Measure (Handle Chinese numerals)
            match = re.search(r'(約每\S+分鐘撮合一次)', measure_full)
            measure = match.group(1) if match else (measure_full[:20] + "..." if len(measure_full)>20 else measure_full)
            
            # 公告日期 (Date欄位)
            announce_date = str(row.get("Date", "")).strip()
            
            if code:
                parsed_list.append({
                    "code": code, 
                    "name": name, 
                    "reason": "處置", 
                    "is_disposed": True,
                    "period": period,
                    "measure": measure,
                    "announce_date": announce_date
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
