
import shutil
import os
from datetime import datetime

SOURCE_DIR = r"e:\Vibe Coding\Stock\處置股"
DEST_PARENT = r"e:\Vibe Coding\Stock"
BACKUP_NAME = "20260104計算處置條件"
DEST_DIR = os.path.join(DEST_PARENT, BACKUP_NAME)

IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__", 
    "*.pyc", 
    ".git", 
    ".venv", 
    "temp", 
    ".vscode",
    "debug_*.log"
)

def backup_project():
    if os.path.exists(DEST_DIR):
        print(f"Warning: Destination {DEST_DIR} already exists.")
        # Optional: shutil.rmtree(DEST_DIR) if you want to overwrite
        # For safety, let's not auto-delete.
    
    print(f"Backing up from: {SOURCE_DIR}")
    print(f"To directory:    {DEST_DIR}")
    
    try:
        shutil.copytree(SOURCE_DIR, DEST_DIR, ignore=IGNORE_PATTERNS, dirs_exist_ok=True)
        print("Backup completed successfully!")
    except Exception as e:
        print(f"Backup failed: {e}")

if __name__ == "__main__":
    backup_project()
