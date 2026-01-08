from core.tick_utils import TickUtils
import math

class DispositionCalculator:
    @staticmethod
    def calculate_conditions(history_df, source=None, shares_outstanding=0, needed_c1=1, needed_any=1):
        """
        Analyze history (OHLC) and return list of potentially triggered conditions.
        Target must be reachable tomorrow (<= Limit Up).
        needed_c1: Days left for Clause 1 to trigger disp (1 means hitting tomorrow triggers).
        needed_any: Days left for Any Clause to trigger disp (1 means hitting tomorrow triggers).
        """
        if history_df is None or len(history_df) < 5:
            return ["資料不足 (Need > 5 days)"]
            
        # Ensure descending for index convenience (Latest is Last)
        closes = history_df['Close'].tolist()
        last_close = closes[-1]
        
        # Helper to get source label
        source_label = f"({source})" if source else ""
        
        # Volume Data
        volumes = history_df['Volume'].tolist()
        
        # Calculate Limit Up using precise Tick Rules
        limit_up_price = TickUtils.calculate_limit_up(last_close)
        
        results_lines = []
        
        def get_target_and_ref(period_days, threshold_pct):
            days_ago = period_days - 2
            if len(closes) < days_ago + 1: return None, None
            ref_idx = -1 - days_ago
            if abs(ref_idx) > len(closes): return None, None
            ref_price = closes[ref_idx]
            raw_target = ref_price * (1 + (threshold_pct / 100.0))
            tick = TickUtils.get_tick_size(raw_target)
            steps = math.ceil((raw_target - 0.0000001) / tick)
            target = round(steps * tick, 2)
            return target, ref_price

        def get_target_sum_roc(period_days, threshold_pct):
            needed_rocs = period_days - 1
            if len(closes) < needed_rocs + 1: return None, 0
            
            current_sum_roc = 0.0
            rocs = []
            
            for i in range(needed_rocs):
                curr = closes[-(i+1)]
                prev = closes[-(i+2)]
                roc = ((curr - prev) / prev) * 100
                current_sum_roc += roc
                rocs.append(roc)
            
            required_next_roc = threshold_pct - current_sum_roc
            raw_target = last_close * (1 + (required_next_roc / 100.0))
            tick = TickUtils.get_tick_size(raw_target)
            steps = math.ceil((raw_target - 0.0000001) / tick)
            target = round(steps * tick, 2)
            
            # Safety Check: Round to 2 decimals comparison
            # User Requirement: 25.0048% -> 25.00% which is NOT > 25%
            # If failing, iterate to next tick
            for _ in range(10): # Limit iterations
                new_roc = ((target - last_close) / last_close) * 100
                total_roc = current_sum_roc + new_roc
                if round(total_roc, 2) > threshold_pct:
                    break
                # Bump to next tick
                tick = TickUtils.get_tick_size(target)
                target = round(target + tick, 2)
                
            return target, current_sum_roc

        # Separate lists for formatting
        disposition_lines = []
        listening_lines = []
        must_enter = False

        # [1] 6日累積 > 32% (Sum of ROCs)
        limit_down_price = TickUtils.calculate_limit_down(last_close)
        
        roc_thresh_1 = 32
        roc_thresh_1_1 = 25
        diff_thresh = 50
        
        if source == "上櫃":
            roc_thresh_1 = 30
            roc_thresh_1_1 = 23
            diff_thresh = 40
        
        # Condition A: Standard ROC
        t1_main, _ = get_target_sum_roc(6, roc_thresh_1)
        
        # Condition B: Lower ROC + Price Diff
        t1_sub = None
        t1_sub_roc, _ = get_target_sum_roc(6, roc_thresh_1_1)
        
        # Reference Check for Diff (Close T-6)
        # Period 6 days includes Next Day. ROcs needed: 5 past + 1 next.
        # closes[-1] is T. closes[-2] is T-1. closes[-6] is T-5.
        # Wait, get_target_sum_roc iterates 5 times.
        # T-1 vs T-2 ... T-5 vs T-6.
        # So reference is closes[-6].
        ref_price_diff = 0
        if len(closes) >= 6:
             ref_price_diff = closes[-6]
             target_diff = ref_price_diff + diff_thresh
             if t1_sub_roc:
                 t1_sub = max(t1_sub_roc, target_diff)
        
        # Determine Effective Target
        final_t1 = t1_main
        active_clause_label = f"[1] 6日漲幅 > {roc_thresh_1}%"
        
        if t1_sub and (not final_t1 or t1_sub < final_t1):
            final_t1 = t1_sub
            active_clause_label = f"[1-1] 6日漲幅 > {roc_thresh_1_1}% + 差價 >={diff_thresh}"
            
        if final_t1 and final_t1 <= limit_up_price:
             gap_pct = ((final_t1 - last_close) / last_close) * 100
             status_target = f"目標{final_t1:.2f}"
             status_gap = f"(需漲{gap_pct:.2f}%)"
             
             if final_t1 < limit_down_price:
                 status_target = f"目標 {final_t1:.2f} 跌停價 {limit_down_price:.2f} 必進處置"
                 status_gap = ""
                 must_enter = True
             elif last_close >= final_t1:
                 gap_pct = ((final_t1 - last_close) / last_close) * 100
                 status_target = f"目標 {final_t1:.2f}"
                 status_gap = f"(可跌{gap_pct:.2f}%)"
                 
             msg = (
                 f"<b>{active_clause_label} {source_label}</b>"
                 f"<br>{status_target} {status_gap}<br>"
             )
             
             if needed_c1 <= 1:
                 disposition_lines.append(msg)
             elif needed_c1 <= 2:
                 listening_lines.append(msg)

        # [2] 30日100% / 60日130% / 90日160%
        # ... (Clause 2 logic matches existing file content) ...
        # Since I cannot use "..." in replacement, I must include the logic.
        tick_size = TickUtils.get_tick_size(last_close)
        next_tick_price = round(last_close + tick_size, 2)
        
        c2_candidates = []
        t30, r30 = get_target_and_ref(30, 100)
        t60, r60 = get_target_and_ref(60, 130)
        t90, r90 = get_target_and_ref(90, 160)
        
        if t30: 
            t30 = max(t30, next_tick_price)
            if t30 <= limit_up_price:
                c2_candidates.append((t30, f"[30日>100%]"))
        if t60: 
            t60 = max(t60, next_tick_price)
            if t60 <= limit_up_price:
                c2_candidates.append((t60, f"[60日>130%]"))
        if t90: 
            t90 = max(t90, next_tick_price)
            if t90 <= limit_up_price:
                c2_candidates.append((t90, f"[90日>160%]"))
             
        if c2_candidates:
            min_t = min(c[0] for c in c2_candidates)
            matched_clauses = [c[1] for c in c2_candidates if c[0] == min_t]
            clause_str = " ".join(matched_clauses)
            
            gap_pct = ((min_t - last_close) / last_close) * 100
            status_part = f"(需漲{gap_pct:.2f}%)"
            
            if last_close >= min_t:
                gap_pct = ((min_t - last_close) / last_close) * 100
                status_part = f"(可跌{gap_pct:.2f}%)"
            
            msg = (
                f"<b>[2] 長期漲幅條款 {source_label}</b>"
                f"<br>{min_t} 進處置 {status_part} {clause_str}<br>"
            )
            
            if needed_any <= 1:
                disposition_lines.append(msg)
            elif needed_any <= 2:
                listening_lines.append(msg)

        # [3] 6日累積 > 25% (OTC 27%)
        roc_thresh_34 = 25
        if source == "上櫃":
            roc_thresh_34 = 27
            
        t3, _ = get_target_sum_roc(6, roc_thresh_34)
        if t3 and t3 <= limit_up_price:
             gap_pct = ((t3 - last_close) / last_close) * 100
             status_target = f"目標{t3:.2f}"
             status_gap = f"(需漲{gap_pct:.2f}%)"
             
             if last_close >= t3:
                 gap_pct = ((t3 - last_close) / last_close) * 100
                 status_target = f"目標 {t3:.2f}"
                 status_gap = f"(可跌{gap_pct:.2f}%)"
                 
             vol_clause_text = "60均量5倍"
             vol_target_text = ""
             
             if len(volumes) >= 59:
                 sum_vol_59 = sum(volumes[-59:])
                 target_vol_shares = math.ceil(sum_vol_59 / 11)
                 target_vol_zhang = math.ceil(target_vol_shares / 1000)
                 
                 curr_vol = history_df['Volume'].iloc[-1]
                 if curr_vol > target_vol_zhang:
                      vol_target_text = f" + 成交量 > {target_vol_zhang}張"
                 else:
                      vol_target_text = f" + (需成交量 > {target_vol_zhang}張)"
                 # Note: Currently assumes user manually checks Volume or it's displayed
                 
             msg = (
                 f"<b>[3] 6日漲幅 > {roc_thresh_34}% {source_label} + {vol_clause_text}</b>"
                 f"<br>{status_target} {status_gap}{vol_target_text}<br>"
             )
             
             if needed_any <= 1:
                 disposition_lines.append(msg)
             elif needed_any <= 2:
                 listening_lines.append(msg)


        # [4] 6日累積 > 25% (OTC 27%) + Turnover
        t4, _ = get_target_sum_roc(6, roc_thresh_34)
        if t4 and t4 <= limit_up_price:
             status_target = f"目標{t4:.2f}"
             gap_pct = ((t4 - last_close) / last_close) * 100
             status_gap = f"(需漲{gap_pct:.2f}%)"
             
             if last_close >= t4:
                 gap_pct = ((t4 - last_close) / last_close) * 100
                 status_target = f"目標 {t4:.2f}"
                 status_gap = f"(可跌{gap_pct:.2f}%)"

             rate_threshold = 10.0
             if source == "上櫃":
                 rate_threshold = 5.0
                 
             turnover_clause_text = f"周轉率{int(rate_threshold)}% {source_label}"
             turnover_target_text = ""
             
             if shares_outstanding and shares_outstanding > 0:
                 req_vol_shares = math.ceil(shares_outstanding * (rate_threshold / 100.0))
                 req_vol_zhang = math.ceil(req_vol_shares / 1000)
                 turnover_target_text = f" + 成交量 > {req_vol_zhang}張"
             else:
                 turnover_target_text = f" + (需周轉率 > {rate_threshold}%)"
                 
             msg = (
                 f"<b>[4] 6日漲幅 > {roc_thresh_34}% {source_label} + {turnover_clause_text}</b>"
                 f"<br>{status_target} {status_gap}{turnover_target_text}<br>"
             )
             
             if needed_any <= 1:
                 disposition_lines.append(msg)
             elif needed_any <= 2:
                 listening_lines.append(msg)

        # [5] 6日累積 > 25% (OTC 27%) + Broker Volume
        # User requested: Limit calculation to Price only, just list Broker condition text.
        # Thresholds: Listed: Broker > 25%, OTC: Broker > 20%
        broker_thresh = 25
        if source == "上櫃":
            broker_thresh = 20
            
        t5, _ = get_target_sum_roc(6, roc_thresh_34)
        if t5 and t5 <= limit_up_price:
             status_target5 = f"目標{t5:.2f}"
             gap_pct5 = ((t5 - last_close) / last_close) * 100
             status_gap5 = f"(需漲{gap_pct5:.2f}%)"
             
             if last_close >= t5:
                 gap_pct5 = ((t5 - last_close) / last_close) * 100
                 status_target5 = f"目標 {t5:.2f}"
                 status_gap5 = f"(可跌{gap_pct5:.2f}%)"
             
             # Static info for broker volume
             broker_text = f" + 券商交易量% > {broker_thresh}% ({source_label})"
             
             msg = (
                 f"<b>[5] 6日漲幅 > {roc_thresh_34}% {source_label}{broker_text}</b>"
                 f"<br>{status_target5} {status_gap5} + (無法計算券商集中度)<br>"
             )
             
             if needed_any <= 1:
                 disposition_lines.append(msg)
             elif needed_any <= 2:
                 listening_lines.append(msg)

        # [6] PER/PBR Clause
        # Listed: PER < 0 or >= 60, PBR >= 6, TO >= 5%, Vol > 3000
        # OTC:    PER < 0 or >= 65, PBR >= 4, TO >= 5%, Vol > 2000
        
        # Check if PER/PBR exists
        has_ratios = 'PER' in history_df.columns and 'PBR' in history_df.columns
        if has_ratios:
            curr_per = history_df['PER'].iloc[-1]
            curr_pbr = history_df['PBR'].iloc[-1]
            
            # Defaults for Listed
            thresh_per = 60
            thresh_pbr = 6
            thresh_to = 5 # 5%
            thresh_vol_z = 3000
            
            if source == "上櫃":
                thresh_per = 65
                thresh_pbr = 4
                thresh_vol_z = 2000
                
            # 1. PER Target
            # If PER < 0, condition met immediately (Target = 0)
            target_price_per = 0
            if curr_per > 0:
                # Need Price / EPS >= 60
                # Current Price / EPS = curr_per -> EPS = Price/curr_per
                # Target / EPS >= 60 -> Target >= 60 * EPS
                target_price_per = last_close * (thresh_per / curr_per)
                
            # 2. PBR Target
            target_price_pbr = 0
            if curr_pbr > 0:
                target_price_pbr = last_close * (thresh_pbr / curr_pbr)
                
            # Both must be met? User requests LOWER price (Min).
            # If PER < 0, target_price_per is 0. Min(0, PBR) = 0?
            # If PER < 0, Condition is Met. So effectively we only check PBR.
            # If PER > 0 and PBR > 0. User says take LOWER.
            
            check_list = []
            if target_price_per > 0: check_list.append(target_price_per)
            if target_price_pbr > 0: check_list.append(target_price_pbr)
            
            target_6 = 0
            if check_list:
                target_6 = min(check_list)
            
            # Round to Tick
            if target_6 > 0:
                tick = TickUtils.get_tick_size(target_6)
                steps = math.ceil((target_6 - 0.0000001) / tick)
                target_6 = round(steps * tick, 2)
                # Removed: target_6 = max(target_6, round(last_close + tick, 2)) 
                # User wants to see the actual threshold even if lower.
            else:
                target_6 = 0 
            
            status_target = f"目標{target_6:.2f}"
            gap_pct = 0
            status_gap = ""
            
            if target_6 > 0:
                 if last_close < target_6:
                     # Not met
                     if target_6 <= limit_up_price:
                         gap_pct = ((target_6 - last_close) / last_close) * 100
                         status_gap = f"(需漲{gap_pct:.2f}%)"
                 else:
                     # Met - Show allowable drop
                     gap_pct = ((target_6 - last_close) / last_close) * 100
                     status_target = f"目標 {target_6:.2f}"
                     status_gap = f"(可跌{gap_pct:.2f}%)"
            
            # Show if:
            # 1. Not met (Price < Target)
            # 2. Met (Price >= Target)
            
            # Condition to display line:
            if target_6 <= limit_up_price or last_close >= target_6:
                 # Volume Logic
                 vol_desc = ""
                 if shares_outstanding and shares_outstanding > 0:
                     req_vol_shares = math.ceil(shares_outstanding * (thresh_to / 100.0))
                     req_vol_zhang = math.ceil(req_vol_shares / 1000)
                     # req_vol_final = max(thresh_vol_z, req_vol_zhang) # Wait, is it Max? 
                     # Rule: "Limit: TO >= 10% AND Vol > 3000?" Usually AND. 
                     # But here we display "TO% AND Vol".
                     # Actually display text: "量>X張 & 周轉率Y%"
                     # Or "量>Max(A,B)張"?
                     # Usually it matches both.
                     term1 = f"周轉率{thresh_to}%"
                     # Logic for display:
                     vol_desc = f" + (量>{req_vol_zhang}張 & 量>{thresh_vol_z}張)"
                     # Simplification: Amount matching TO%
                     vol_desc = f" + (需量>{req_vol_zhang}張)"

                 else:
                     vol_desc = f" + 量>{thresh_vol_z}張 & 周轉率{thresh_to}%"
                     
                 # Format: [6] 本益比>=60, 股淨比>=6, 周轉率 >= 5%, 成交量 > 3000
                 clause_6_desc = f"本益比>={thresh_per}, 股淨比>={thresh_pbr}, 周轉率 >= {thresh_to}%, 成交量 > {thresh_vol_z}"
                 
                 msg = (
                     f"<b>[6] {clause_6_desc} {source_label}</b>"
                     f"<br>{status_target} {status_gap}{vol_desc}<br>"
                 )
                 
                 if needed_any <= 1:
                     disposition_lines.append(msg)
                 elif needed_any <= 2:
                     listening_lines.append(msg)

        # User Request: If Must Enter (via Clause 1 Logic), suppress listening.
        if must_enter:
            listening_lines = []

        # Assemble Final Output
        results_lines.append(f"最新收盤: {last_close}  漲停價: {limit_up_price:.2f}")

        if disposition_lines:
            results_lines.append("<br><b>進處置:</b>")
            results_lines.extend(disposition_lines)
            
        if listening_lines:
            results_lines.append("<br><b>達以下任一則聽牌:</b>")
            results_lines.extend(listening_lines)
            
        if not disposition_lines and not listening_lines:
            results_lines.append("無 (全部條件漲停皆無法達成)")
        
        # Determine if Clause 2 is the MAIN risk for Disposition (Entering Tomorrow)
        # Check if any msg in disposition_lines contains "[2]" (Our label for Clause 2)
        # And make sure it's not overridden by specific "Must Enter" Clause 1 logic (though "Must Enter" usually implies Clause 1 Limit Down)
        # User Logic: "Show Exclusion ... only if chance to reach Clause 2 and enter disposition"
        
        is_clause2_risk = False
        for msg in disposition_lines:
            if "[2]" in msg:
                is_clause2_risk = True
                break
                
        return results_lines, is_clause2_risk
