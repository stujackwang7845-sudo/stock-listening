
import json
import os
from datetime import datetime

class HistoryManager:
    FILE_PATH = "listening_history.json"

    def __init__(self):
        self.history = self._load()

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
        return sorted(self.history, key=lambda x: x["date"], reverse=True)

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
