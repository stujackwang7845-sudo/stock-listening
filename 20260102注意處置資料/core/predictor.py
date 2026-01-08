
class DispositionPredictor:
    @staticmethod
    def analyze(history_items, future_days=5):
        """
        history_items: list of dict [{"is_clause1": bool, "is_any": bool}, ...] 
                       Current day is LAST item.
        future_days: number of future days to simulate (e.g. 5)
        
        Returns: (Warning string, Probability %, Min Days Needed)
        """
        if not history_items: 
            return ("", 0, 999)
        
        # Normalize Data
        H = []
        has_any_hit = False
        for item in history_items:
            c1 = item.get("is_clause1", False)
            any_c = item.get("is_any", False)
            if c1 or any_c: has_any_hit = True
            H.append({ "c1": c1, "any": any_c })
            
        if not has_any_hit:
            return ("", 0, 999)

        candidates = [] # List of {"days": int, "msg": str, "type": "C1", "prob": int, "needed": int}
        min_needed = 999
        
        # Rule 1: Consecutive 3 days Clause 1
        streak_c1 = 0
        for i in range(len(H)-1, -1, -1):
            if H[i]["c1"]: streak_c1 += 1
            else: break
            
        needed_c1 = 3 - streak_c1
        if needed_c1 <= 0:
            candidates.append({"days": 0, "msg": "已達第一款連續3天 -> 進處置", "type": "C1", "prob": 100, "needed": 0})
            min_needed = 0
        elif needed_c1 < 3 and needed_c1 <= future_days:
             # Construct message
             if needed_c1 == 1:
                 msg = "明天第一款則進處置"
             else:
                 msg = f"接下來連續{needed_c1}天第一款則進處置"
             # Prob
             p = int(((3 - needed_c1) / 3) * 100)
             candidates.append({"days": needed_c1, "msg": msg, "type": "C1", "prob": p, "needed": needed_c1})
             min_needed = min(min_needed, needed_c1)
                 
        # Rule 2: Consecutive 5 days Any Clause
        streak_any = 0
        for i in range(len(H)-1, -1, -1):
            if H[i]["any"]: streak_any += 1
            else: break
            
        needed_any = 5 - streak_any
        if needed_any <= 0:
             candidates.append({"days": 0, "msg": "已達連續5天注意 -> 進處置", "type": "Any", "prob": 100, "needed": 0})
             min_needed = 0
        elif needed_any < 5 and needed_any <= future_days:
             if needed_any == 1:
                 msg = "明天一至八款則進處置"
             else:
                 msg = f"接下來連續{needed_any}天一至八款則進處置"
             # Prob
             p = int(((5 - needed_any) / 5) * 100)
             candidates.append({"days": needed_any, "msg": msg, "type": "Any", "prob": p, "needed": needed_any})
             min_needed = min(min_needed, needed_any)

        # Rule 3: 6 days in 10 days (Any Clause)
        # Scan future days 1..x
        for x in range(1, future_days + 1):
             slice_start = -10 + x
             if slice_start >= 0:
                 hist_slice = H[slice_start:] 
             else:
                 hist_slice = H[slice_start:]
                 
             base_count = sum(1 for item in hist_slice if item["any"])
             needed = 6 - base_count
             
             if needed <= x:
                 hits_req = max(1, needed)
                 
                 # Phrasing
                 if hits_req == x:
                     if x == 1:
                         msg = "明天一至八款則進處置"
                     else:
                         msg = f"接下來連續{x}天一至八款則進處置"
                 else:
                     msg = f"接下來{x}天 有{hits_req}天一至八款則進處置"
                 
                 p = int(((x - hits_req) / x) * 100)
                 
                 candidates.append({"days": x, "msg": msg, "type": "Any", "prob": p, "needed": needed})
                 min_needed = min(min_needed, needed)

        if not candidates:
             return ("", 0, 999)
             
        # Pick best candidate (Highest Prob, then Shortest Days)
        candidates.sort(key=lambda x: (-x["prob"], x["days"]))
        best = candidates[0]
        
        return (best["msg"], best["prob"], min_needed)
