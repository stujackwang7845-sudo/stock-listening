from core.tick_utils import TickUtils
import math

class DispositionCalculator:
    @staticmethod
    def calculate_conditions(history_df, source=None, shares_outstanding=0, needed_c1=1, needed_any=1, stock_name=None, **kwargs):
        """
        Analyze history (OHLC) and return list of potentially triggered conditions.
        Target must be reachable tomorrow (<= Limit Up).
        needed_c1: Days left for Clause 1 to trigger disp (1 means hitting tomorrow triggers).
        needed_any: Days left for Any Clause to trigger disp (1 means hitting tomorrow triggers).
        stock_name: 股票名稱,用於檢查特殊股票標記
        """
        # 檢查股票名稱是否有 * 標記(減資股、全額交割股等面額非10元股票)
        # 用戶要求：正常計算，因此移除此阻擋。
        # if stock_name and "*" in stock_name:
        #     return ["面額不為10,無法計算"], False
        
        if history_df is None or len(history_df) < 5:
            return ["資料不足 (Need > 5 days)"], False
        
        # 過濾掉收盤價為 NaN 的資料列（停牌、無成交等）
        history_df = history_df.dropna(subset=['Close'])
        if len(history_df) < 5:
            return ["資料不足 (有效收盤價不足 5 天)"], False
            
        # Ensure descending for index convenience (Latest is Last)
        closes = history_df['Close'].tolist()
        last_close = closes[-1]
        
        # NaN 防護：檢查 last_close 是否有效
        if last_close is None or (isinstance(last_close, float) and math.isnan(last_close)) or last_close <= 0:
            return ["收盤價異常 (NaN 或 ≤0)"], False
        
        # Helper to get source label
        source_label = f"({source})" if source else ""
        
        # Volume Data
        volumes = history_df['Volume'].tolist()
        
        # Calculate Limit Up using precise Tick Rules
        limit_up_price = TickUtils.calculate_limit_up(last_close)
        
        results_lines = []
        
        def get_target_and_ref(period_days, threshold_pct):
            # 用戶定義：預測 "明天 (T+1)" 是否滿足長天期漲幅。
            # "今天盤後的資料永遠都是29日/59日/89日" -> 明天才是第 30/60/90 天。
            # 目標：比較 Day 30 (T+1) 與 Day 1 (基準日)。
            # Day 30 = T + 1
            # Day 1 = (T + 1) - (period_days - 1) = T - (period_days - 2)
            # 
            # 在 closes 列表 (最後一筆是 T, index -1) 中：
            # T 的 index = -1
            # Day 1 (T - (period_days - 2)) 的 index = -1 - (period_days - 2)
            # 所以 days_ago 應該設為 period_days - 2
            
            days_ago = period_days - 2
            
            if len(closes) < days_ago + 1: return None, None
            ref_idx = -1 - days_ago
            if abs(ref_idx) > len(closes): return None, None
            ref_price = closes[ref_idx]
            
            # Check for NaN or invalid data
            if ref_price is None or (isinstance(ref_price, float) and math.isnan(ref_price)):
                return None, None
                
            raw_target = ref_price * (1 + (threshold_pct / 100.0))
            
            # Additional safety check for raw_target
            if math.isnan(raw_target):
                return None, None
                
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
                
                # Check for NaN values
                if (curr is None or (isinstance(curr, float) and math.isnan(curr)) or
                    prev is None or (isinstance(prev, float) and math.isnan(prev)) or
                    prev == 0):
                    return None, 0
                    
                roc = ((curr - prev) / prev) * 100
                current_sum_roc += roc
                rocs.append(roc)
            
            required_next_roc = threshold_pct - current_sum_roc
            raw_target = last_close * (1 + (required_next_roc / 100.0))
            
            # Check for NaN in raw_target
            if math.isnan(raw_target):
                return None, 0
                
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

        def get_target_sum_roc_drop(period_days, threshold_pct):
            # 跌方向鏡像：找出明天收盤價最高可以是多少，仍能讓 period_days 累積跌幅超過 threshold_pct%
            # 官方原文用語為「漲跌百分比」，雙向適用同一數字門檻
            needed_rocs = period_days - 1
            if len(closes) < needed_rocs + 1: return None, 0

            current_sum_roc = 0.0
            for i in range(needed_rocs):
                curr = closes[-(i+1)]
                prev = closes[-(i+2)]

                if (curr is None or (isinstance(curr, float) and math.isnan(curr)) or
                    prev is None or (isinstance(prev, float) and math.isnan(prev)) or
                    prev == 0):
                    return None, 0

                roc = ((curr - prev) / prev) * 100
                current_sum_roc += roc

            required_next_roc = -threshold_pct - current_sum_roc
            raw_target = last_close * (1 + (required_next_roc / 100.0))

            # 股價不可能 <= 0，超過此界線代表這個門檻對目前股價已無法達成
            if math.isnan(raw_target) or raw_target <= 0:
                return None, 0

            tick = TickUtils.get_tick_size(raw_target)
            steps = math.floor((raw_target + 0.0000001) / tick)
            if steps < 1:
                return None, 0
            target = round(steps * tick, 2)
            if target <= 0:
                return None, 0

            # Safety Check: 確保四捨五入後仍嚴格超過門檻，否則往下一檔位推移
            for _ in range(10):
                new_roc = ((target - last_close) / last_close) * 100
                total_roc = current_sum_roc + new_roc
                if round(total_roc, 2) < -threshold_pct:
                    break
                tick = TickUtils.get_tick_size(target)
                target = round(target - tick, 2)
                if target <= 0:
                    return None, 0
            else:
                # 10 次都沒能推到嚴格超過門檻(理論上極端邊界才會發生)，代表這個 target 不可信，不回傳
                return None, 0

            return target, current_sum_roc

        def _eval_direction(target, is_drop, limit_up, limit_down, ref_close, allow_must_enter=True):
            # 評估單一方向(漲或跌)的目標價是否可達成(明日限度內)、是否已觸發、以及顯示文字。
            # allow_must_enter=False 用於 [3][4][5] 款，維持這些款項原本就沒有「必進處置」鎖死判斷的既有行為。
            if not target:
                return None
            if is_drop:
                if target < limit_down:
                    return None
            else:
                if target > limit_up:
                    return None

            must_enter_local = False
            if allow_must_enter:
                if is_drop and target > limit_up:
                    must_enter_local = True
                elif not is_drop and target < limit_down:
                    must_enter_local = True

            already = (ref_close <= target) if is_drop else (ref_close >= target)
            gap_pct = ((target - ref_close) / ref_close) * 100
            gap_abs = abs(gap_pct)

            if must_enter_local:
                bound_price = limit_up if is_drop else limit_down
                bound_label = "漲停價" if is_drop else "跌停價"
                status_target = f"目標 {target:.2f} {bound_label} {bound_price:.2f} 必進處置"
                status_gap = ""
            elif already:
                verb = "可漲" if is_drop else "可跌"
                status_target = f"目標 {target:.2f}"
                status_gap = f"({verb}{gap_abs:.2f}%)"
            else:
                verb = "需跌" if is_drop else "需漲"
                status_target = f"目標{target:.2f}"
                status_gap = f"({verb}{gap_abs:.2f}%)"

            return {
                "already": already,
                "gap_abs": gap_abs,
                "must_enter": must_enter_local,
                "status_target": status_target,
                "status_gap": status_gap,
            }

        def _choose_directions(rise_eval, drop_eval):
            # 挑選要顯示的方向：已觸發者優先顯示；都未觸發則顯示較接近觸發(所需幅度較小)的那個。
            chosen = []
            if rise_eval and rise_eval["already"]:
                chosen.append(("rise", rise_eval))
            if drop_eval and drop_eval["already"]:
                chosen.append(("drop", drop_eval))
            if chosen:
                # 理論上同一款漲跌方向的「已觸發」不會同時成立(共用同一個帶正負號的累積漲跌%)，
                # 但仍加防呆：萬一兩者都已觸發，只顯示較接近邊界(幅度較小)的那一個，不兩個都顯示
                if len(chosen) > 1:
                    chosen.sort(key=lambda c: c[1]["gap_abs"])
                    return [chosen[0]]
                return chosen
            candidates = [c for c in [("rise", rise_eval), ("drop", drop_eval)] if c[1]]
            if not candidates:
                return []
            candidates.sort(key=lambda c: c[1]["gap_abs"])
            return [candidates[0]]

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
        
        # Reference Check for Diff (前六個營業日收盤價差價)
        # 依據台股規定，若計算明天 (T+1) 的條件，其「前六個營業日」其實是指包含明天在內的6日區間的「第1天」
        # 也就是明天往前推5個交易日：T-4。
        # closes[-1] 是 T，所以 T-4 是 closes[-5]
        ref_price_diff = 0
        if len(closes) >= 5:
             ref_price_diff = closes[-5]
             # User Request: Default is >= 50. 
             # New Request: Strictly > 50. Means Price - Ref > 50.
             # So Price > Ref + 50.
             # Target must be at least (Ref + 50) + 1 Tick.
             base_target = ref_price_diff + diff_thresh
             tick = TickUtils.get_tick_size(base_target)
             # To become strictly greater, we step up one tick from the exact differnce
             # But wait, if base_target is exactly on a tick?
             # e.g. 318 + 50 = 368. 368 is a valid price.
             # If Price is 368: 368 - 318 = 50. This is NOT > 50.
             # So we need 368.5 (next tick).
             # Logic: Calculate Next Tick Price after (Ref + Thresh)
             
             # But first, ensure base_target aligns to tick? Usually integer addition is fine.
             # Let's get next tick.
             target_diff = round(base_target + tick, 2)
             
             if t1_sub_roc:
                 t1_sub = max(t1_sub_roc, target_diff)
        
        # Determine Effective Target
        final_t1 = t1_main
        active_clause_label = f"[1] 6日漲幅 > {roc_thresh_1}%"
        
        if t1_sub and (not final_t1 or t1_sub < final_t1):
            final_t1 = t1_sub
            # Update label to show > instead of >=
            active_clause_label = f"[1-1] 6日漲幅 > {roc_thresh_1_1}% + 差價 > {diff_thresh}"

        # --- 跌方向 (2026-09-20 新增；官方"漲跌百分比"用語雙向適用同一數字門檻) ---
        t1_main_drop, _ = get_target_sum_roc_drop(6, roc_thresh_1)

        t1_sub_drop = None
        t1_sub_roc_drop, _ = get_target_sum_roc_drop(6, roc_thresh_1_1)

        target_diff_drop = None
        if len(closes) >= 5:
             base_target_drop = ref_price_diff - diff_thresh
             if base_target_drop > 0:
                 tick = TickUtils.get_tick_size(base_target_drop)
                 # 鏡像漲方向的 "+tick" 做法：直接退一檔，確保「跌價差 > diff_thresh」是嚴格大於，
                 # 不能只是 floor 到 base_target_drop 本身(那樣剛好等於門檻，法規要求嚴格大於不算數)
                 target_diff_drop = round(base_target_drop - tick, 2)
                 if target_diff_drop <= 0:
                     target_diff_drop = None

        if t1_sub_roc_drop and target_diff_drop:
             # 跌方向要同時滿足「跌幅」與「差價」兩個子條件，需取較極端(較低)的價格才會兩者皆成立
             t1_sub_drop = min(t1_sub_roc_drop, target_diff_drop)
        # 若價差子條件因股價過低而不可能成立(target_diff_drop is None)，[1-1]跌方向視為不可能觸發，
        # 不 fallback 成純跌幅目標，否則會誤報一個實際上不可能同時滿足的門檻

        final_t1_drop = t1_main_drop
        active_clause_label_drop = f"[1] 6日跌幅 > {roc_thresh_1}%"

        if t1_sub_drop and (not final_t1_drop or t1_sub_drop > final_t1_drop):
            final_t1_drop = t1_sub_drop
            active_clause_label_drop = f"[1-1] 6日跌幅 > {roc_thresh_1_1}% + 差價 > {diff_thresh}"

        # --- 選擇顯示方向：已觸發者優先；都未觸發則顯示較接近觸發(所需幅度較小)的方向 ---
        rise_eval1 = _eval_direction(final_t1, False, limit_up_price, limit_down_price, last_close)
        drop_eval1 = _eval_direction(final_t1_drop, True, limit_up_price, limit_down_price, last_close)

        # [Fix 2026-10-04] 第一款本身也算「一至八款任一款」，所以第一款明天觸發會不會進處置，
        # 要看「連3日第一款」與「連5日/10日6次/30日12次」哪條比較急，取較小的那個。
        # 例：10日內已5次但第一款沒連續(needed_c1=3, needed_any=1)，明天中第一款一樣湊滿6次進處置。
        needed_c1_eff = min(needed_c1, needed_any)

        for _direction1, _ev1 in _choose_directions(rise_eval1, drop_eval1):
             _label1 = active_clause_label if _direction1 == "rise" else active_clause_label_drop
             _status_target1 = _ev1['status_target']
             if _ev1["must_enter"]:
                 if needed_c1_eff <= 1:
                     must_enter = True
                 else:
                     # 明天必定達到第一款，但還差2次才進處置 → 只是必定進聽牌
                     _status_target1 = _status_target1.replace("必進處置", "必聽牌")
             msg = (
                 f"<b>{_label1} {source_label}</b>"
                 f"<br>{_status_target1} {_ev1['status_gap']}<br>"
             )

             if needed_c1_eff <= 1:
                 disposition_lines.append(msg)
             elif needed_c1_eff <= 2:
                 listening_lines.append(msg)

        # [2] 長期漲幅條款
        # 上市：30日>100% / 60日>130% / 90日>160%
        # 上櫃：30日>100% / 60日>140% / 90日>160%
        tick_size = TickUtils.get_tick_size(last_close)
        next_tick_price = round(last_close + tick_size, 2)
        
        # 根據上市/上櫃設定不同門檻
        threshold_60d = 130  # 上市預設
        if source == "上櫃":
            threshold_60d = 140  # 上櫃
        
        c2_candidates = []
        t30, r30 = get_target_and_ref(30, 100)
        t60, r60 = get_target_and_ref(60, threshold_60d)  # 使用動態門檻
        t90, r90 = get_target_and_ref(90, 160)
        
        if t30: 
            t30 = max(t30, next_tick_price)
            if t30 <= limit_up_price:
                c2_candidates.append((t30, f"[30日>100%]"))
        if t60: 
            t60 = max(t60, next_tick_price)
            if t60 <= limit_up_price:
                c2_candidates.append((t60, f"[60日>{threshold_60d}%]"))  # 顯示實際門檻
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
        t3_drop, _ = get_target_sum_roc_drop(6, roc_thresh_34)

        rise_eval3 = _eval_direction(t3, False, limit_up_price, limit_down_price, last_close, allow_must_enter=False)
        drop_eval3 = _eval_direction(t3_drop, True, limit_up_price, limit_down_price, last_close, allow_must_enter=False)

        for _direction3, _ev3 in _choose_directions(rise_eval3, drop_eval3):
             vol_clause_text = "60均量5倍"
             vol_target_text = ""

             if len(volumes) >= 59:
                 sum_vol_59 = sum(volumes[-59:])
                 target_vol_shares = math.ceil(sum_vol_59 / 11) if not math.isnan(sum_vol_59) else 0
                 target_vol_zhang = math.ceil(target_vol_shares / 1000)

                 curr_vol = history_df['Volume'].iloc[-1]
                 if curr_vol > target_vol_zhang:
                      vol_target_text = f" + 成交量 > {target_vol_zhang}張"
                 else:
                      vol_target_text = f" + (需成交量 > {target_vol_zhang}張)"
                 # Note: Currently assumes user manually checks Volume or it's displayed

             _direction_label3 = "漲幅" if _direction3 == "rise" else "跌幅"
             msg = (
                 f"<b>[3] 6日{_direction_label3} > {roc_thresh_34}% {source_label} + {vol_clause_text}</b>"
                 f"<br>{_ev3['status_target']} {_ev3['status_gap']}{vol_target_text}<br>"
             )

             if needed_any <= 1:
                 disposition_lines.append(msg)
             elif needed_any <= 2:
                 listening_lines.append(msg)


        # [4] 6日累積 > 25% (OTC 27%) + Turnover
        t4, _ = get_target_sum_roc(6, roc_thresh_34)
        t4_drop, _ = get_target_sum_roc_drop(6, roc_thresh_34)

        rise_eval4 = _eval_direction(t4, False, limit_up_price, limit_down_price, last_close, allow_must_enter=False)
        drop_eval4 = _eval_direction(t4_drop, True, limit_up_price, limit_down_price, last_close, allow_must_enter=False)

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

        for _direction4, _ev4 in _choose_directions(rise_eval4, drop_eval4):
             _direction_label4 = "漲幅" if _direction4 == "rise" else "跌幅"
             msg = (
                 f"<b>[4] 6日{_direction_label4} > {roc_thresh_34}% {source_label} + {turnover_clause_text}</b>"
                 f"<br>{_ev4['status_target']} {_ev4['status_gap']}{turnover_target_text}<br>"
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
        t5_drop, _ = get_target_sum_roc_drop(6, roc_thresh_34)

        rise_eval5 = _eval_direction(t5, False, limit_up_price, limit_down_price, last_close, allow_must_enter=False)
        drop_eval5 = _eval_direction(t5_drop, True, limit_up_price, limit_down_price, last_close, allow_must_enter=False)

        # Static info for broker volume
        broker_text = f" + 券商交易量% > {broker_thresh}% ({source_label})"

        for _direction5, _ev5 in _choose_directions(rise_eval5, drop_eval5):
             _direction_label5 = "漲幅" if _direction5 == "rise" else "跌幅"
             msg = (
                 f"<b>[5] 6日{_direction_label5} > {roc_thresh_34}% {source_label}{broker_text}</b>"
                 f"<br>{_ev5['status_target']} {_ev5['status_gap']} + (無法計算券商集中度)<br>"
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
            
            # Sanitize (Handle None/NaN)
            if curr_per is None or (isinstance(curr_per, float) and math.isnan(curr_per)):
                curr_per = 0.0
            if curr_pbr is None or (isinstance(curr_pbr, float) and math.isnan(curr_pbr)):
                curr_pbr = 0.0
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
                
            # User requirement: Take HIGHER valuation (Max) as target
            # Both PER and PBR must be met, so we use the HIGHER threshold
            # to ensure both conditions are satisfied when that price is reached.
            
            check_list = []
            if target_price_per > 0: check_list.append(target_price_per)
            if target_price_pbr > 0: check_list.append(target_price_pbr)
            
            target_6 = 0
            if check_list:
                target_6 = max(check_list)  # Changed from min to max - use higher valuation
            
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
                 # User Request: Show Current PER/PBR
                 
                 curr_vals_text = f"(目前本益比:{curr_per:.2f}, 股淨比:{curr_pbr:.2f})"
                 
                 clause_6_desc = f"本益比>={thresh_per}, 股淨比>={thresh_pbr}, 周轉率 >= {thresh_to}%, 成交量 > {thresh_vol_z}"
                 
                 msg = (
                     f"<b>[6] {clause_6_desc} {source_label}</b>"
                     f"<br>{curr_vals_text}<br>{status_target} {status_gap}{vol_desc}<br>"
                 )
                 
                 if needed_any <= 1:
                     disposition_lines.append(msg)
                 elif needed_any <= 2:
                     listening_lines.append(msg)

        # User Request: If Must Enter (via Clause 1 Logic), suppress listening.
        if must_enter:
            listening_lines = []

        # Assemble Final Output
        results_lines.append(f"最新收盤: {last_close}  漲停價: {limit_up_price:.2f}  跌停價: {limit_down_price:.2f}")

        if disposition_lines:
            # [2026-10-04] 標題講清楚是「明天達到」的後果，避免把一進聽股誤讀成已經聽牌
            results_lines.append("<br><b>明日達以下任一 → 進處置:</b>")
            results_lines.extend(disposition_lines)

        if listening_lines:
            results_lines.append("<br><b>明日達以下任一 → 變聽牌（還不會進處置）:</b>")
            results_lines.extend(listening_lines)
            
        if not disposition_lines and not listening_lines:
            results_lines.append("無 (全部條件明日漲跌停皆無法達成)")
        
        # 判斷第二款排除條款風險
        is_clause2_risk = False
        exclusion_lines = []
        for msg in (disposition_lines + listening_lines):
            if "[2]" in msg:
                is_clause2_risk = True
                break
        
        # 排除條款詳細內容 (對齊 Dashboard 邏輯)
        if is_clause2_risk:
            # Indices: T(0) ... T-6(-7). Total 7 rows needed.
            if history_df is not None and len(history_df) >= 7:
                today_row = history_df.iloc[-1]
                price_t = today_row['Close']
                price_t_1 = history_df.iloc[-2]['Close']
                
                # Calculate Sum ROC 5 (Include T: T, T-1, T-2, T-3, T-4)
                sum_roc = 0.0
                for i in range(0, 5):
                    curr_idx = -(1 + i)
                    prev_idx = -(2 + i)
                    p_curr = history_df.iloc[curr_idx]['Close']
                    p_prev = history_df.iloc[prev_idx]['Close']
                    sum_roc += (p_curr / p_prev - 1) * 100
                
                # Rule A
                limit_rate_a = 25.0 if source == "上市" else 27.0
                remaining_a = limit_rate_a - sum_roc
                price_limit_a = price_t * (1 + remaining_a/100)
                
                # Rule B
                limit_rate_b = 10.0
                remaining_b = limit_rate_b - sum_roc
                price_limit_b = price_t * (1 + remaining_b/100)
                
                # Format Output
                exclusion_lines.append("<div style='color: #FF4444; font-weight: bold; margin-top: 10px; margin-bottom: 5px; font-size: 14px;'>排除條件:</div>")
                exclusion_lines.append("<div style='color: #DDDDDD; font-size: 12px;'>[第二款]<br>")
                
                # 1. 前30日曾發生第一款
                # 這裡的 has_c1_30 和 has_c2_disp_60 先預設為未知，由外部傳入或後續補齊
                hist_a_str = "<span style='color: #FF4444;'>是</span>" if kwargs.get('has_c1_30', False) else "否"
                exclusion_lines.append("1.")
                exclusion_lines.append(f"前30日曾發生第一款: {hist_a_str}")
                exclusion_lines.append(f"且6日漲幅&lt;{int(limit_rate_a)}% 股價 &lt; {price_limit_a:.2f} 則排除<br>")
                
                # 2. 前60日曾因第二款進處置
                hist_b_str = "<span style='color: #FF4444;'>是</span>" if kwargs.get('has_c2_disp_60', False) else "否"
                exclusion_lines.append("2.")
                exclusion_lines.append(f"前60日曾因第二款進處置: {hist_b_str}")
                exclusion_lines.append(f"且6日漲幅&lt;10% 股價 &lt; {price_limit_b:.2f} 則排除")
                exclusion_lines.append("或股價下跌則排除")
                
                exclusion_lines.append("</div>")
            else:
                exclusion_lines = ["<span style='color:#999'>資料不足無法計算排除條件</span>"]
                
        return results_lines, is_clause2_risk, exclusion_lines

