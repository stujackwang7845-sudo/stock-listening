from core.market_cache import MarketDataCache
from core.predictor import DispositionPredictor
from core.finmind_client import FinMindClient

def debug_3006():
    cache = MarketDataCache()
    # Need to load data. The cache might be empty if main isn't running or db is locked?
    # Actually cache loads from sqlite.
    
    # We need history items for 3006.
    # predictor.analyze needs history_items.
    # history_items are stored in 'predictions' table? No, 'analysis_results'? 
    # Or we can fetch raw data and recalculate?
    # It's better to peek at what the UI sees.
    # The UI calls calculator -> which calls predictor.
    
    # But locally I don't have the full cache state easily without running the full flow.
    # I'll try to fetch fresh data for 3006 and verify what the cache *likely* has if I can access the DB.
    # The DB is at 'market_data.db'.
    
    # Let's inspect the 'disposition_history' or similar if it exists. 
    # Wait, `core/predictor.py` takes `history_items` which are list of dicts.
    # These usually come from `core/calculator.py` parsing the "reason" field.
    
    print("Checking 3006 status...")
    rows = cache.get_disposition_history('3006')
    print(f"Db History Rows for 3006: {len(rows)}")
    for r in rows:
        print(r)
        
    # We can try to reconstruct the params for Predictor.get_status_counts
    # But better, let's just see what the db has.
    
    # Also check if it's in the 'monitored_stocks' or if we can simulate the calculator check.
    
if __name__ == "__main__":
    debug_3006()
