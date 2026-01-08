
from core.utils import DateUtils
from datetime import datetime

def test_parsing():
    test_cases = [
        ("1141222~1150106", datetime(2026, 1, 6)),
        ("114/12/22 ~ 115/01/06", datetime(2026, 1, 6)),
        ("114.12.22-115.01.06", datetime(2026, 1, 6)),
        ("114.12.22 - 115.01.06", datetime(2026, 1, 6)),
        ("1141222–1150106", datetime(2026, 1, 6)), # en-dash
        ("1141222—1150106", datetime(2026, 1, 6)), # em-dash
        ("114/12/22～115/01/06", datetime(2026, 1, 6)), # Fullwidth tilde
    ]
    
    print("Testing DateUtils.parse_period_end...")
    for inp, expected in test_cases:
        result = DateUtils.parse_period_end(inp)
        status = "PASS" if result == expected else f"FAIL (Got {result})"
        print(f"Input: '{inp}' -> Expected: {expected} -> {status}")

if __name__ == "__main__":
    test_parsing()
