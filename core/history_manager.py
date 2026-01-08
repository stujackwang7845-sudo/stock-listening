
import json
import os
import requests
from datetime import datetime

class HistoryManager:
    FILE_PATH = "listening_history.json"
    GITHUB_RAW_URL = "https://raw.githubusercontent.com/stujackwang7845-sudo/stock-listening/master/listening_history.json"

    def __init__(self):
        self.history = self._load()
        self._cleanup_bad_records()

    def sync_from_github(self):
        """Fetch remote history and merge into local."""
        try:
            print(f"Syncing from GitHub: {self.GITHUB_RAW_URL}...")
            resp = requests.get(self.GITHUB_RAW_URL, timeout=5)
            if resp.status_code == 200:
                remote_data = resp.json()
                print(f"Downloaded {len(remote_data)} records from GitHub.")
                
                added_count = 0
                for rec in remote_data:
                    # Reuse internal logic to add/update
                    # We check duplicate inside add_record logic manually to avoid redundant saves, 
                    # or just use add_record directly (slower but safer).
                    # Optimization: Batch check
                    
                    if self._merge_record(rec):
                        added_count += 1
                
                if added_count > 0:
                    self.save()
                    print(f"Sync complete. Added/Updated {added_count} records.")
                else:
                    print("Sync complete. No new data.")
                
                return True
            else:
                print(f"GitHub Sync Failed: Status {resp.status_code}")
                return False
        except Exception as e:
            print(f"GitHub Sync Error: {e}")
            return False

    def _merge_record(self, record):
        """Internal helper to merge record without immediate save."""
        # [Filter] Exclude CB
        if len(str(record.get("code", ""))) == 5:
            return False

        # Check duplicate
        for existing in self.history:
            if existing.get("date") == record["date"] and existing.get("code") == record["code"]:
                # Found existing. Update invalid/missing fields?
                # Ideally we respect local changes (tags/comments).
                # But fetcher data (trigger_info) might be better?
                # Let's update trigger_info if widely different?
                return False # Assume local is up to date or same

        # If not found, append
        if "tags" not in record: record["tags"] = []
        if "comment" not in record: record["comment"] = ""
        self.history.append(record)
        return True

    def _cleanup_bad_records(self):
        """Remove 5-digit codes (CBs) from existing history."""
        original_len = len(self.history)
        self.history = [
            x for x in self.history 
            if len(str(x.get("code", ""))) != 5
        ]
        if len(self.history) < original_len:
            print(f"Cleaned up {original_len - len(self.history)} CB records.")
            self.save()

    def _load(self):
        if not os.path.exists(self.FILE_PATH):
            return []
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading history: {e}")
            return []

    def save(self):
        try:
            with open(self.FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving history: {e}")

    def add_record(self, record):
        """
        Add a new record if it doesn't exist for the same date & code.
        record: dict with keys [date, code, name, ...]
        """
        # [Filter] Exclude CB (5 digits)
        if len(str(record.get("code", ""))) == 5:
            return

        # Check duplicate
        for existing in self.history:
            if existing.get("date") == record["date"] and existing.get("code") == record["code"]:
                # Update existing (merge info but keep tags/comments?)
                # Requirement implies we overwrite or update trigger info. 
                # Let's keep existing tags/comments if present.
                if "tags" in existing: record["tags"] = existing["tags"]
                if "comment" in existing: record["comment"] = existing["comment"]
                
                existing.update(record)
                self.save()
                return

        # New record
        if "tags" not in record: record["tags"] = []
        if "comment" not in record: record["comment"] = ""
        
        self.history.append(record)
        self.save()

    def get_all(self):
        # Double check filter
        return sorted([x for x in self.history if len(str(x.get("code", ""))) != 5], key=lambda x: x["date"], reverse=True)

    def update_tags(self, date, code, tags):
        for item in self.history:
            if item["date"] == date and item["code"] == code:
                item["tags"] = tags
                self.save()
                return

    def update_comment(self, date, code, comment):
        for item in self.history:
            if item["date"] == date and item["code"] == code:
                item["comment"] = comment
                self.save()
                return
