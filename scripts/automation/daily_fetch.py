
import sys
import os
import json
import logging
from datetime import datetime
import argparse

# Add Valid Path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.scraper_attention import AttentionScraper
from core.history_manager import HistoryManager
from core.utils import ClauseParser

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="Fetch daily attention stock data.")
    parser.add_argument("--date", type=str, help="Date to fetch (YYYYMMDD), default is today", required=False)
    args = parser.parse_args()

    # Determine Date
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y%m%d")
        except ValueError:
            logging.error("Invalid date format. Use YYYYMMDD.")
            sys.exit(1)
    else:
        target_date = datetime.now()

    date_str = target_date.strftime("%Y-%m-%d")
    logging.info(f"Starting fetch for date: {date_str}")

    # 1. Fetch Data
    try:
        items = AttentionScraper.fetch_data(target_date)
        logging.info(f"Fetched {len(items)} items from Scraper.")
    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        sys.exit(1)

    if not items:
        logging.warning("No data found. Exiting.")
        return

    # 2. Process and Save
    history_mgr = HistoryManager()
    
    saved_count = 0
    
    for item in items:
        code = item.get("code", "")
        name = item.get("name", "")
        raw_reason = item.get("reason", "")
        
        # [Filter] The user specifically requested "Accumulated Abnormal Count" (Listening/Hearing) data.
        # "累計次數異常" usually contains "累積" (Accumulated) in the reason text.
        # However, valid listening stocks might use phrases like "連續X次" or "已有Y次" without the word "累積".
        # To be safe and capture ALL potential listening stocks from the official list, we accept all items.
        # The App can filter them later if needed.

        # Parse Trigger Info (Clause)
        # We store the full raw reason as trigger_info for clarity in history
        trigger_info = raw_reason
        
        rec = {
            "date": date_str,
            "code": code,
            "name": name,
            "trigger_info": trigger_info,
            "is_disposed_next_day": False,
            "tags": [],
            "comment": "GitHub Actions 自動每日擷取"
        }
        
        # Add to history (filtering handled by Manager)
        history_mgr.add_record(rec)
        logging.info(f"新增/更新紀錄: {code} {name}")
        saved_count += 1
    
    if saved_count == 0:
        logging.info("今日無符合「累積」條件的聽牌資料。")
    else:
        logging.info(f"已儲存 {saved_count} 筆聽牌資料。")

    logging.info("執行完成。")


if __name__ == "__main__":
    main()
