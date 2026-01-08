
import os
import sys

HISTORY_FILE = "listening_history.json"

if os.path.exists(HISTORY_FILE):
    try:
        os.remove(HISTORY_FILE)
        print("Success: History file deleted.")
        print(f"Removed: {os.path.abspath(HISTORY_FILE)}")
    except Exception as e:
        print(f"Error: Could not delete file. {e}")
else:
    print("Info: History file not found (Already clean).")
