
from core.fetcher import StockFetcher
from core.parser import StockParser
import json

def check_2408_parsing():
    fetcher = StockFetcher()
    parser = StockParser()
    
    date_str = "20260102"
    twse_data = fetcher.fetch_twse_attention(date_str)
    
    if twse_data:
        # Filter for 2408 row to simulate what parser sees
        raw_rows = twse_data.get("data", [])
        target_row = None
        for row in raw_rows:
             if "2408" in str(row):
                 target_row = row
                 break
        
        if target_row:
            print(f"Raw Row: {target_row}")
            # Try parsing the whole data structure
            parsed_items = parser.parse_twse_attention(twse_data)
            
            # Find 2408 in parsed items
            parsed_2408 = next((item for item in parsed_items if item["code"] == "2408"), None)
            
            if parsed_2408:
                print(f"Parsed 2408: {parsed_2408}")
                reason = parsed_2408.get("reason", "")
                print(f"Reason: '{reason}'")
                
                # Check Clause Parsing
                from core.utils import ClauseParser
                clauses = ClauseParser.parse_clauses(reason)
                print(f"Clauses: {clauses}")
            else:
                print("FAILED to parse 2408 from data!")
        else:
            print("2408 not in data rows?")

if __name__ == "__main__":
    check_2408_parsing()
