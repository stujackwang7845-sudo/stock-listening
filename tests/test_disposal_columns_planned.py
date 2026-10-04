
import unittest
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# Mocking the logic I intend to implement in DisposalStatsManager
class MockDisposalStatsManager:
    def calculate_price_changes(
        self, 
        code: str, 
        disposal_start: datetime, 
        disposal_end: datetime, 
        days_after: int = 5
    ):
        # Determine actual trading days (mocking continuous days for simplicity in test)
        # Real logic uses get_trading_days_in_range
        
        # Scenario: Start=Day 10, End=Day 20. Exit=Day 21. Exit+5=Day 26.
        # Range needed: Day 9 to Day 26.
        
        # Let's say Day 0 is some base date.
        base_date = disposal_start - timedelta(days=5)
        
        # Mock finding indices
        # In real code: idx of start, idx of end.
        
        headers = []
        changes = {}
        
        # Mock logic
        # -1
        headers.append("-1")
        
        # Start to End
        # If Start is idx S, End is idx E.
        # Length = E - S + 1
        
        # Let's say duration is 10 days. S=0, E=9.
        # i=0 -> "處置日" (Start)
        # i=9 -> "處置結束" (End)
        # i=1..8 -> "+1".."+8"
        
        duration_days = (disposal_end - disposal_start).days + 1
        
        for i in range(duration_days):
            if i == 0:
                label = "處置日"
            elif i == duration_days - 1:
                label = "處置結束"
            else:
                label = f"+{i}"
            headers.append(label)
        
        # Exit to Exit+5
        # Exit day is End + 1
        for i in range(1, days_after + 2): # 1 to 6
            # i=1: Exit Day (End+1)
            # i=2: Exit+1
            if i == 1:
                label = "出關日"
            else:
                label = f"出+{i-1}"
            headers.append(label)
            
        return headers, changes

class TestDisposalColumns(unittest.TestCase):
    def test_headers_generation(self):
        manager = MockDisposalStatsManager()
        start = datetime(2023, 1, 1)
        # Simulate a 12-day disposal period (Start, +1..+10, End)
        end = datetime(2023, 1, 12) 
        
        headers, _ = manager.calculate_price_changes("1101", start, end, days_after=5)
        
        print(f"Generated Headers: {headers}")
        
        expected = [
            "-1", 
            "處置日", # Day 1
            "+1",    # Day 2
            "+2",    # Day 3
            "+3",    # Day 4
            "+4",
            "+5",
            "+6",
            "+7",
            "+8",
            "+9",
            "+10",   # Day 11
            "處置結束", # Day 12
            "出關日",   # End+1
            "出+1",     
            "出+2",
            "出+3",
            "出+4",
            "出+5"
        ]
        
        self.assertEqual(headers, expected)

if __name__ == "__main__":
    unittest.main()
