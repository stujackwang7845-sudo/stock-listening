import sys
import os
sys.path.append(os.getcwd())
from core.utils import DateUtils
from datetime import datetime

# Mimic the strings we suspect
test_cases = [
    "1141222-1150106",
    "114/12/22 ~ 115/01/06",
    "114/12/22-115/01/06",
    "1141222~1150106",
    "114.12.22-115.01.06" # Possibility?
]

print("=== Testing Parse Start ===")
for t in test_cases:
    res = DateUtils.parse_period_start(t)
    print(f"'{t}' -> {res}")

print("\n=== Testing Parse End ===")
for t in test_cases:
    res = DateUtils.parse_period_end(t)
    print(f"'{t}' -> {res}")

# Logic Simulation
print("\n=== Logic Simulation ===")
disp_end_dt = DateUtils.parse_period_end("1141222-1150106") # Should be 2026-01-06
today_dt = datetime(2026, 1, 2) # Simulate user's screenshot date
print(f"Simulated Today: {today_dt}")
print(f"Disposal End: {disp_end_dt}")

future_dts = [
    datetime(2026, 1, 5), # Future[0] (Simulated next trading day) (Actually 1/6 is next if 1/2 is Fri? Wait 1/2 Fri, 1/5 Mon)
    datetime(2026, 1, 6), # Future[1]
    datetime(2026, 1, 7), # Future[2]
]
# Wait, if Today is 1/2 (Fri). Next trading day is 1/5 (Mon).
# But wait, 1/1 is holiday. 1/2 is Fri.
# Let's check get_market_calendar logic or assumptions.

if disp_end_dt:
    target_idx = -1
    if today_dt and disp_end_dt.date() == today_dt.date():
        target_idx = 0 
        print("Match: End Today")
    elif len(future_dts) > 0 and future_dts[0] and disp_end_dt.date() == future_dts[0].date():
        target_idx = 1
        print("Match: Future[0] (End Tomorrow)")
    elif len(future_dts) > 1 and future_dts[1] and disp_end_dt.date() == future_dts[1].date():
        target_idx = 2 
        print("Match: Future[1] (End Day After)")
    else:
        print("No Match in window")
    
    print(f"Target Index: {target_idx}")
