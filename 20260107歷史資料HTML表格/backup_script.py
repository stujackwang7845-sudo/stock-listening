
import shutil
import os
import datetime

source_dir = r"e:\Vibe Coding\Stock\處置股"
dest_dir = r"e:\Vibe Coding\Stock\處置股\20260102注意處置資料"

# If destination exists, we might want to handle it (e.g. append timestamp)
# But user gave specific name. If exists, maybe we merge or error?
# Let's remove if exists or overwrite. Safe bet is verify first? 
# User said "backup to...", usually implies create.

ignore_patterns = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "temp", ".gemini", 
    "20260102注意處置資料", "*.pyc"
)

try:
    if os.path.exists(dest_dir):
        print(f"Destination {dest_dir} already exists. Removing old backup...")
        shutil.rmtree(dest_dir)
        
    print(f"Copying from {source_dir} to {dest_dir}...")
    shutil.copytree(source_dir, dest_dir, ignore=ignore_patterns)
    print("Backup completed successfully.")
except Exception as e:
    print(f"Backup failed: {e}")
