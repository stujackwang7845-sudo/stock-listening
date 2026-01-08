from core.cache import CacheManager
import os
print(f"CWD: {os.getcwd()}")
try:
    cm = CacheManager()
    print(f"DB Path: {cm.db_path}")
    print(f"Exists? {os.path.exists(cm.db_path)}")
    print(f"Dir Exists? {os.path.exists('data')}")
except Exception as e:
    print(f"Error: {e}")
