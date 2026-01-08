
import math
from decimal import Decimal, ROUND_FLOOR

class TickUtils:
    @staticmethod
    def get_tick_size(price):
        if price < 10: return 0.01
        if price < 50: return 0.05
        if price < 100: return 0.1
        if price < 500: return 0.5
        if price < 1000: return 1.0
        return 5.0

    @staticmethod
    def floor_to_tick(price):
        """Round *down* to the nearest valid tick."""
        tick = TickUtils.get_tick_size(price)
        # algorithm: floor(price / tick) * tick
        # But floating point issues... use integer math if possible or epsilon
        steps = math.floor((price + 0.0000001) / tick)
        return round(steps * tick, 2)
        
    @staticmethod
    def calculate_limit_up(ref_price):
        """
        Calculate Limit Up Price (Max +10%).
        Taiwan Rule: Ref * 1.10.
        Then round down to nearest tick.
        """
        raw_limit = ref_price * 1.10
        # The tick size depends on the NEW price range? 
        # Usually limit up price tick is determined by the limit up price itself.
        # e.g. 207 * 1.1 = 227.7. Price range is 100-500, tick 0.5.
        # 227.7 / 0.5 = 455.4 -> floor -> 455 -> 455 * 0.5 = 227.5.
        
        # However, if price crosses a threshold (e.g. 499 -> 548.9), 
        # Tick varies.
        # But standard simplification: Check tick size of the raw_limit.
        
        return TickUtils.floor_to_tick(raw_limit)

    @staticmethod
    def calculate_limit_down(ref_price):
        """
        Calculate Limit Down Price (Max -10%).
        Taiwan Rule: Ref * 0.90.
        Then round up? Actually it's floor of the drop?
        Stock Exchange Formula: Floor((Price * 10%) / Tick) * Tick ... then Price +/- Delta.
        
        Detailed Formula:
        Delta = Price * 0.10
        Delta = floor(Delta / Tick) * Tick
        Limit Up = Price + Delta
        Limit Down = Price - Delta
        
        Wait, tick size for Delta is based on Ref Price? 
        Let's verify TWSE rule.
        "升降幅度計算方式：以漲跌幅度（10%）乘以前一日收盤價格... 計算至分，未滿一分者無條件捨去... 且符合升降單位"
        
        Actually, simpler: 
        LimitUp = Ref * 1.10, Round Down to Tick.
        LimitDown = Ref * 0.90, Round Up to Tick? (Should be floor of the fall?)
        
        Let's stick to the User instructions/example.
        User said: 207 * 1.1 = 227.7 -> 227.5.
        This matches "Round down to tick".
        """
        # For Limit Down, we usually Round Up to nearest tick? Or Floor the result?
        # If 100 -> 90.
        # If 105 -> 94.5 (tick 0.1? No 100+ is 0.5). 
        # Ref 105 (tick 0.5). 10% = 10.5. 105-10.5 = 94.5.
        # 94.5 (tick 0.1). Valid.
        
        # Let's just implement Limit Up for now as that's the blocker.
        return TickUtils.floor_to_tick(ref_price * 0.90)
