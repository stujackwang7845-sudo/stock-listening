from core.utils import DateUtils
from datetime import datetime

d = datetime(2026, 1, 2)
print(f"Checking 2026-01-02 (Weekday {d.weekday()})")
is_trading = DateUtils.is_trading_day(d)
print(f"is_trading_day(2026-01-02): {is_trading}")

last = DateUtils.get_last_trading_day()
print(f"get_last_trading_day() [Current time {datetime.now()}]: {last}")

# Check Holidays
print(f"HOLIDAYS: {DateUtils.HOLIDAYS}")
