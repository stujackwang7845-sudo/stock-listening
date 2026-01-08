
import sys
import datetime as dt
from datetime import datetime
from core.utils import DateUtils

# Mock Data simulating 4971
# Based on previous debug output: 'period': '1141222~1150106'
mock_data_4971 = {
    "code": "4971",
    "name": "IET-KY",
    "source": "上櫃",
    "is_disposed": True,
    "period": "1141222~1150106",
    # "period": "114/12/22 ~ 115/01/06", # Variant to test if strictly this matters
    "measure": "約每5分鐘撮合一次"
}

def simulate_dashboard_logic():
    print("--- Simulating Dashboard Exit Logic ---")
    
    # 1. Setup Anchor Date (Simulate Today = 2026-01-05)
    anchor_date = datetime(2026, 1, 5) # Monday
    print(f"Anchor Date (Today): {anchor_date.strftime('%Y-%m-%d')}")
    
    # 2. Get Calendar
    calendar = DateUtils.get_market_calendar(anchor_date, past_days=2, future_days=5)
    print(f"Calendar Future: {calendar['future']}")
    
    # 3. Parse Future Dts (Replicating dashboard.py logic exactly)
    anchor_year = anchor_date.year
    anchor_month = anchor_date.month
    
    future_dts = []
    for d_str in calendar["future"]:
        try:
            dm = d_str.split("/")
            m, d = int(dm[0]), int(dm[1])
            y = anchor_year
            if anchor_month == 12 and m == 1: y += 1
            elif anchor_month == 1 and m == 12: y -= 1
            future_dts.append(datetime(y, m, d))
        except Exception as e:
            print(f"Error parsing future date {d_str}: {e}")
            future_dts.append(None)
            
    print(f"Parsed Future Dts: {future_dts}")
    
    # 4. Check Stock 4971
    period = mock_data_4971.get("period", "")
    disp_end_dt = DateUtils.parse_period_end(period)
    
    print(f"Stock 4971 Period: '{period}'")
    print(f"Parsed End Date: {disp_end_dt}")
    
    target_idx = -1
    today_dt = anchor_date
    
    if mock_data_4971.get("is_disposed", False) and disp_end_dt:
        if today_dt and disp_end_dt.date() == today_dt.date():
             print("Match: End Today -> Free Tomorrow")
             target_idx = 0 
        elif len(future_dts) > 0 and future_dts[0] and disp_end_dt.date() == future_dts[0].date():
             print(f"Match: End Tomorrow ({future_dts[0].date()}) -> Free Day After")
             target_idx = 1
        elif len(future_dts) > 1 and future_dts[1] and disp_end_dt.date() == future_dts[1].date():
             print(f"Match: End Day After ({future_dts[1].date()}) -> Free 3rd Day")
             target_idx = 2
        else:
             print("No Match in [Today, Tomorrow, Day After]")
             
    print(f"Result Target Index: {target_idx}")
    
    if target_idx == 0:
        print("Should appear in: 明天出關")
    elif target_idx == 1:
        print("Should appear in: 後天出關")
    elif target_idx == 2:
        print("Should appear in: 大後天出關")
    else:
        print("Will NOT appear in Exit Box.")

if __name__ == "__main__":
    simulate_dashboard_logic()
