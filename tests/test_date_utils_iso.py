import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import unittest
from datetime import datetime
from core.utils import DateUtils

class TestDateNormalization(unittest.TestCase):
    def test_western_iso(self):
        self.assertEqual(DateUtils.to_iso_date_str("2023-01-01"), "2023-01-01")
        
    def test_western_slash(self):
        self.assertEqual(DateUtils.to_iso_date_str("2023/01/01"), "2023-01-01")
        
    def test_western_compact(self):
        self.assertEqual(DateUtils.to_iso_date_str("20230101"), "2023-01-01")
        
    def test_roc_slash(self):
        self.assertEqual(DateUtils.to_iso_date_str("112/01/01"), "2023-01-01")
        
    def test_roc_dot(self):
        self.assertEqual(DateUtils.to_iso_date_str("112.01.01"), "2023-01-01")
        
    def test_roc_compact(self):
        self.assertEqual(DateUtils.to_iso_date_str("1120101"), "2023-01-01")
        
    def test_empty(self):
        self.assertEqual(DateUtils.to_iso_date_str(""), "")
        self.assertEqual(DateUtils.to_iso_date_str(None), "")
        
    def test_datetime_obj(self):
        dt = datetime(2023, 1, 1)
        self.assertEqual(DateUtils.to_iso_date_str(dt), "2023-01-01")

if __name__ == "__main__":
    unittest.main()
