"""
處置預測器

提供四大觸發規則的分析：
1. 連續 3 日第一款
2. 連續 5 日一至八款
3. 10 日內 6 次一至八款
4. 30 日內 12 次一至八款

重要：進入最近一次處置區塊之前的歷史紀錄會被截斷歸零重算(見各函式的 cutoff_idx 邏輯)。
但截斷之後，處置期間內若再被公布注意交易資訊，一樣要計入下一次觸發的累計次數——
依證交所「公布或通知注意交易資訊暨處置作業要點」第六條，只有依第三/四項(監視業務督導
會報或共同責任制交割結算基金特別管理委員會「決議」的特殊處置)，處置期間的注意次數才
不列入統計；依第一項四條標準規則(本檔案的1~4項)觸發的一般處置，官方並未排除。
[2026-08-20 修正] 先前這裡誤寫「處置期間的注意次數不計入計算」，導致 analyze()／
get_status_counts() 的 Rule 3/4 曾經多濾掉一層處置期間的天數，跟 get_trigger_progress()
算出不同答案，已修正為三個函式行為一致。
"""


class DispositionPredictor:
    @staticmethod
    def _compute_live_state(H):
        """
        [Fix 2026-08-27] 逐日模擬 H(已用 cutoff_idx 排除進入最近一次連續處置區塊
        前的舊資料)，任何一條規則(連3日第一款/連5日1-8款/10日內6次/30日內12次)
        只要在某一天被觸發，隔天起「所有」規則的累計都歸零重新計算。

        這才是「進入處置後，所有次數歸零重新計算」的完整語意：如果同一段連續處置
        期間內因為公告不斷升級(例如6225從10分->25分->25分，中間從未真正出關)，
        代表這段期間其實發生了好幾次各自獨立的觸發事件，每次觸發都要各自歸零，
        不能只在第一次進入這個連續區塊時歸零一次、讓後面的累計一路往上疊加
        超過 target(例如6225連續8天都中第一款，色塊會顯示成 8/3 這種分子比分母
        還大、又長得像日期的畸形結果——實際上中間已經因為連3日觸發過2次、各自
        歸零，「目前」只是重新累積到第2天而已，正確答案是2/3)。

        Returns: dict，streak_c1/streak_any 是最近一次歸零後的連續天數；
                 window_any 是最近一次歸零後累積的 'any' 命中序列(bool list)，
                 供呼叫端接著用 _simulate_window_needed 繼續往前模擬滑動視窗汰舊。
        """
        streak_c1 = 0
        streak_any = 0
        window_any: list[bool] = []
        pending_reset = False  # 前一天已觸發，要在「今天」開始前先歸零

        for day in H:
            if pending_reset:
                # [Fix] 歸零要從觸發的「隔天」才開始生效，不能在觸發當天自己就把
                # 剛達成的數字蓋掉——否則同一天既符合「已達標」又顯示成歸零後的0，
                # 使用者會看到當天明明中了第3款注意卻顯示「0/3」安全色。
                streak_c1 = 0
                streak_any = 0
                window_any = []
                pending_reset = False

            c1 = day["c1"]
            any_c = day["any"]

            streak_c1 = streak_c1 + 1 if c1 else 0
            streak_any = streak_any + 1 if any_c else 0
            window_any.append(any_c)

            count_10 = sum(window_any[-10:])
            count_30 = sum(window_any[-30:])

            if streak_c1 >= 3 or streak_any >= 5 or count_10 >= 6 or count_30 >= 12:
                pending_reset = True

        return {"streak_c1": streak_c1, "streak_any": streak_any, "window_any": window_any}

    @staticmethod
    def _simulate_window_needed(history, window_size, threshold, key, max_days):
        """
        逐日模擬「視窗內累積 N 次」規則還差幾天達標，正確處理視窗滑動汰舊。
        單純用 max(0, threshold-count) 相減，沒考慮視窗往前滑動時最舊一天也會
        被移出——如果被移出的剛好是命中日，新舊命中互相抵銷，累積次數不會真的
        增加。[2026-08-20 修正，見 BUGREPORT_一進聽_window_slide.md 的反例]

        history: 由舊到新，最後一筆最新
        Returns: (needed_days, current_count)
                 needed_days=0 表示已達標；None 表示 max_days 天內都不會達標。
        """
        current_window = history[-window_size:] if len(history) >= window_size else history
        current_count = sum(1 for x in current_window if x[key])
        if current_count >= threshold:
            return 0, current_count

        sim = list(history)
        for day in range(1, max_days + 1):
            sim.append({key: True})
            window = sim[-window_size:] if len(sim) >= window_size else sim
            count = sum(1 for x in window if x[key])
            if count >= threshold:
                return day, current_count
        return None, current_count

    @staticmethod
    def analyze(history_items, future_days=5):
        """
        history_items: list of dict [{"is_clause1": bool, "is_any": bool, "is_disposed": bool}, ...]
                       Current day is LAST item.
                       is_disposed=True 表示該天在處置期間內，不計入觸發計算。
        future_days: number of future days to simulate (e.g. 5)
        
        Returns: (Warning string, Probability %, Min Days Needed)
        """
        if not history_items: 
            return ("", 0, 999)
        
        # === 正規化資料與截斷歷史 ===
        H = []
        has_any_hit = False
        for item in history_items:
            disposed = item.get("is_disposed", False)
            c1 = item.get("is_clause1", False)
            any_c = item.get("is_any", False)
            any_all = item.get("is_any_all", any_c) # 預設 fallback 為 any_c
            if c1 or any_c: has_any_hit = True
            # [Fix 2026-09-03] rule2(連續5天1-8款)/rule3(10日內6次)/rule4(30日內12次)
            # 依證交所「公布或通知注意交易資訊暨處置作業要點」第六條原文，這三條規則
            # 明確限定「依第四條第一項第一款至第八款發布注意交易資訊者」，跟 rule1
            # 一樣只認第1~8款，不含第9款以上(如DR溢價、高價股價差)或「聽牌(官方)」
            # 彙總清單裡「等X個營業日已有Y次」這種多日累計摘要文字(非當天單日觸發，
            # 解析不出具體第幾款才會落回泛稱「注意」)。之前這裡誤用 any_all，會把
            # 這些不該算的日子也當成觸發，導致某些股票(如4991)被多算一次而顯示成
            # 已達標、或某些股票(如2426)因為誤觸發了模擬歸零而顯示過低。
            H.append({"c1": c1, "any": any_c, "any_all": any_all, "disposed": disposed})
            
        if not has_any_hit:
            return ("", 0, 999)

        # 進入處置後，所有次數歸零重新計算 (截斷歷史)
        cutoff_idx = 0
        in_block = False
        for i in range(len(H)-1, -1, -1):
            if H[i]["disposed"]:
                in_block = True
            elif in_block:
                cutoff_idx = i + 1
                break
        
        H = H[cutoff_idx:]

        # [Fix 2026-08-27] 改用 _compute_live_state 逐日模擬，任何規則觸發都會讓
        # 「所有」規則歸零重新計算，不是只在進入這段連續處置區塊時歸零一次
        # (見該函式註解，修正連續多次升級沒真正出關、累計次數超過門檻的 bug)。
        state = DispositionPredictor._compute_live_state(H)
        streak_c1 = state["streak_c1"]
        streak_any = state["streak_any"]
        window_any = [{"any": v} for v in state["window_any"]]

        candidates = []
        min_needed = 999

        # Rule 1: 連續 3 天第一款（包含處置期間）
        needed_c1 = max(0, 3 - streak_c1)
        if needed_c1 <= 0:
            candidates.append({"days": 0, "msg": "已達第一款連續3天 -> 進處置", "type": "C1", "prob": 100, "needed": 0})
            min_needed = 0
        elif needed_c1 < 3 and needed_c1 <= future_days:
             if needed_c1 == 1:
                 msg = "明天第一款則進處置"
             else:
                 msg = f"接下來連續{needed_c1}天第一款則進處置"
             p = int(((3 - needed_c1) / 3) * 100)
             candidates.append({"days": needed_c1, "msg": msg, "type": "C1", "prob": p, "needed": needed_c1})
             min_needed = min(min_needed, needed_c1)

        # Rule 2: 連續 5 天任一款（包含處置期間）
        needed_any = max(0, 5 - streak_any)
        if needed_any <= 0:
             candidates.append({"days": 0, "msg": "已達連續5天注意 -> 進處置", "type": "Any", "prob": 100, "needed": 0})
             min_needed = 0
        elif needed_any < 5 and needed_any <= future_days:
             if needed_any == 1:
                 msg = "明天一至八款則進處置"
             else:
                 msg = f"接下來連續{needed_any}天一至八款則進處置"
             p = int(((5 - needed_any) / 5) * 100)
             candidates.append({"days": needed_any, "msg": msg, "type": "Any", "prob": p, "needed": needed_any})
             min_needed = min(min_needed, needed_any)

        # [Fix] 原本這裡把「處置期間內的天數」濾掉不算(H_non_disposed)，但依證交所
        # 官方公告：只有依「公布或通知注意交易資訊暨處置作業要點」第六條第三/四項
        # (監視業務督導會報或共同責任制委員會「決議」的特殊處置)，處置期間的注意次數
        # 才不列入統計；依第一項四條標準規則(連3日/連5日/10日6次/30日12次)觸發的一般
        # 處置，官方並未排除——處置期間內若再被注意，一樣要算進下一次觸發的累計次數
        # (使用者提供的官方原文與實例已確認)。這裡跟 Rule 1/2 一樣改用未過濾的 H，
        # 也才會跟 get_trigger_progress() 算出一致的答案。

        # Rule 3: 10 日內 6 次 (一至八款)
        # [Fix 2026-08-20] 改用逐日模擬，正確處理視窗滑動汰舊(見 _simulate_window_needed)
        # [Fix 2026-08-27] 用 window_any(歸零後的子序列)取代整個 H，理由同上面的
        # _compute_live_state 呼叫。
        needed_10_6, count_10 = DispositionPredictor._simulate_window_needed(window_any, 10, 6, "any", future_days)

        if needed_10_6 == 0:
            candidates.append({"days": 0, "msg": "已達10日內6次 -> 進處置", "type": "Any", "prob": 100, "needed": 0})
            min_needed = 0
        elif needed_10_6 is not None and needed_10_6 <= future_days:
            candidates.append({"days": needed_10_6, "msg": f"接下來{needed_10_6}天注意則進處置(10日6次)",
                             "type": "Any", "prob": int((count_10/6)*100), "needed": needed_10_6})
            min_needed = min(min_needed, needed_10_6)

        # Rule 4: 30 日內 12 次 (一至八款)
        # [Fix] 原本用 any_all(不限款次，含第9款以上)，但官方「公布或通知注意交易
        # 資訊暨處置作業要點」第六條原文明確寫「...依第四條第一項第一款至第八款
        # 發布交易資訊者」——連5日、10日6次、30日12次三條規則都限定1~8款，
        # 跟 Rule 3(10日內6次)用的 any 應該一致，之前用 any_all 是誤把「不限款次」
        # 當成正確判斷依據，會把第9款以上(如DR溢價、高價股價差)也算進去，導致
        # 跟 get_trigger_progress() 算出不同答案。
        # [Fix 2026-08-20] 同 Rule 3，改用逐日模擬
        # [Fix 2026-08-27] 同上，用 window_any 取代整個 H
        needed_30_12, count_30 = DispositionPredictor._simulate_window_needed(window_any, 30, 12, "any", future_days)

        if needed_30_12 == 0:
            candidates.append({"days": 0, "msg": "已達30日內12次 -> 進處置", "type": "Any30", "prob": 100, "needed": 0})
            min_needed = 0
        elif needed_30_12 is not None and needed_30_12 <= future_days:
            candidates.append({"days": needed_30_12, "msg": f"接下來{needed_30_12}天注意則進處置(30日12次)",
                             "type": "Any30", "prob": int((count_30/12)*100), "needed": needed_30_12})
            min_needed = min(min_needed, needed_30_12)

        if not candidates:
             return ("", 0, 999)
             
        # 過濾：只顯示 needed <= 2
        urgent_candidates = [c for c in candidates if c['needed'] <= 2]
        
        if not urgent_candidates:
            return ("", 0, 999)
            
        # 選最緊急的
        urgent_candidates.sort(key=lambda x: (-x["prob"], x["days"]))
        best = urgent_candidates[0]
        
        return (best["msg"], best["prob"], min_needed)

    @staticmethod
    def get_trigger_progress(history_items):
        """
        計算四大觸發規則的進度，用於色塊渲染。
        
        history_items: list of dict [{"is_clause1": bool, "is_any": bool, "is_disposed": bool}, ...]
                       最後一筆是最新一天。
                       is_disposed=True 表示該天在處置期間內。
        
        Returns: dict with rule1~rule4 progress + min_needed
        """
        if not history_items:
            return {
                "rule1": {"current": 0, "target": 3, "needed": 3, "blocks": [False, False, False]},
                "rule2": {"current": 0, "target": 5, "needed": 5, "blocks": [False]*5},
                "rule3": {"current": 0, "target": 6, "needed": 6, "blocks": [False]*10},
                "rule4": {"current": 0, "target": 12, "needed": 12, "blocks": [False]*30},
                "min_needed": 999,
            }

        H = []
        for item in history_items:
            disposed = item.get("is_disposed", False)
            c1 = item.get("is_clause1", False)
            any_c = item.get("is_any", False)
            any_all = item.get("is_any_all", any_c)
            # [Fix 2026-09-03] 同 analyze()，rule2/3/4 限定第1~8款，見下方詳細說明。
            H.append({"c1": c1, "any": any_c, "any_all": any_all, "disposed": disposed})

        # === 尋找最近一次的處置起點 (period_start) 並截斷歷史 ===
        # 使用者確認：「進入處置之後，累積注意次數就會重新計算，這是沒問題的」
        # 所以我們必須把進入最近一次處置以前的紀錄截斷歸零！
        cutoff_idx = 0
        in_block = False
        for i in range(len(H)-1, -1, -1):
            if H[i]["disposed"]:
                in_block = True
            elif in_block:
                # 剛離開最近一次的處置區塊，i+1 就是 period_start
                cutoff_idx = i + 1
                break
        
        # 截斷 H，只保留最近一次處置起點以後的紀錄 (也就是處置期間 + 出關後的紀錄)
        H = H[cutoff_idx:]

        # [Fix 2026-08-27] 改用 _compute_live_state 逐日模擬，任何規則觸發都會讓
        # 「所有」規則歸零重新計算，不是只在進入這段連續處置區塊時歸零一次
        # (見該函式註解，修正 6225 連續多次升級沒真正出關、色塊分子超過分母的 bug)。
        state = DispositionPredictor._compute_live_state(H)
        streak_c1 = state["streak_c1"]
        streak_any = state["streak_any"]
        window_any = [{"any": v} for v in state["window_any"]]

        # === Rule 1: 連3日第1款 (包含處置期間) ===
        needed_c1 = max(0, 3 - streak_c1)

        # === Rule 2: 連5日1-8款 (包含處置期間) ===
        needed_any_streak = max(0, 5 - streak_any)

        # === Rule 3: 10日內6次 (拔除處置期間排除，與儀表板一致) ===
        # 不再過濾處置期間日子，直接看最近10天
        # [Fix 2026-08-20] 改用逐日模擬，正確處理視窗滑動汰舊(見 _simulate_window_needed)。
        # max_days=window_size 保證一定會有解(全數命中時視窗必達門檻)，不會回傳 None。
        needed_10_6, count_10 = DispositionPredictor._simulate_window_needed(window_any, 10, 6, "any", 10)

        # === Rule 4: 30日內12次 (拔除處置期間排除，與儀表板一致) ===
        # 不再過濾處置期間日子，直接看最近30天
        needed_30_12, count_30 = DispositionPredictor._simulate_window_needed(window_any, 30, 12, "any", 30)

        min_needed = min(needed_c1, needed_any_streak, needed_10_6, needed_30_12)

        return {
            "rule1": {"current": streak_c1, "target": 3, "needed": needed_c1},
            "rule2": {"current": streak_any, "target": 5, "needed": needed_any_streak},
            "rule3": {"current": count_10, "target": 6, "needed": needed_10_6},
            "rule4": {"current": count_30, "target": 12, "needed": needed_30_12},
            "min_needed": min_needed,
        }

    @staticmethod
    def _escape_window_option(seq, window_size, threshold):
        """
        seq: 歸零後的 'any' 命中序列，最後一筆是「明天(假設沒被注意)」。
        找出明天之後，最早能達標的滑動視窗，以及「同樣次數就夠」能撐到哪一天。
        existing(j) = 視窗結尾落在明天後第 j 個交易日時，視窗內既有(已發生)的命中數；
        j 越大視窗越往後滑、舊命中被汰出，existing 只會減少不會增加，所以最早可達標的
        j 同時也是「需要注意次數最少」的那個，m 一定等於 j。之後只要 existing 沒再減少
        (被汰出的舊日子本來就沒命中)，同樣 m 次注意都還夠，k 可以往後延伸。
        Returns {"days": k, "hits": m}；視窗內不可能達標時回傳 None。
        """
        def existing(j):
            keep = window_size - j
            if keep <= 0:
                return 0
            return sum(1 for v in seq[-keep:] if v)

        j_min = None
        for j in range(1, window_size):
            if existing(j) + j >= threshold:
                j_min = j
                break
        if j_min is None:
            return None

        base = existing(j_min)
        hits = threshold - base
        days = j_min
        # 視窗要同時容納「明天」和後面 k 天，所以 k+1 <= window_size
        while days + 2 <= window_size and existing(days + 1) == base:
            days += 1
        return {"days": days, "hits": hits}

    @staticmethod
    def escape_tomorrow_requirement(history_items):
        """
        假設「明天」沒被公布注意(逃過處置)，之後最容易進處置的條件。
        四條規則都算，挑需要注意次數最少的；次數相同時，不限款次的優先於限第一款的，
        再來天數越長(越寬鬆)越優先。

        Returns {"days": k, "hits": m, "clause1": bool} 或 None。
                days: 從後天起算的交易日數；hits: 這段期間內需要被注意的次數；
                clause1: True 表示必須是第一款(連3日第一款規則)。
        """
        if not history_items:
            return None

        H = []
        for item in history_items:
            H.append({
                "c1": item.get("is_clause1", False),
                "any": item.get("is_any", False),
                "disposed": item.get("is_disposed", False),
            })

        # 與 analyze()/get_trigger_progress() 相同的截斷邏輯
        cutoff_idx = 0
        in_block = False
        for i in range(len(H) - 1, -1, -1):
            if H[i]["disposed"]:
                in_block = True
            elif in_block:
                cutoff_idx = i + 1
                break
        H = H[cutoff_idx:]

        state = DispositionPredictor._compute_live_state(H)
        seq = list(state["window_any"]) + [False]  # 明天沒被注意

        # 明天沒被注意，連續天數兩條規則都歸零，要從後天重新連續累積
        options = [
            {"days": 3, "hits": 3, "clause1": True},
            {"days": 5, "hits": 5, "clause1": False},
        ]
        for window_size, threshold in ((10, 6), (30, 12)):
            opt = DispositionPredictor._escape_window_option(seq, window_size, threshold)
            if opt:
                opt["clause1"] = False
                options.append(opt)

        options.sort(key=lambda o: (o["hits"], o["clause1"], -o["days"]))
        return options[0]

    @staticmethod
    def get_status_counts(history_items):
        """
        Returns (needed_c1, needed_any)
        使用與 analyze() / get_trigger_progress() 一致的截斷邏輯。
        """
        if not history_items:
            return (3, 5)
            
        H = []
        for item in history_items:
            disposed = item.get("is_disposed", False)
            c1 = item.get("is_clause1", False)
            any_c = item.get("is_any", False)
            any_all = item.get("is_any_all", any_c)
            # [Fix 2026-09-03] 同 analyze()/get_trigger_progress()，rule2/3/4 限定第1~8款。
            H.append({"c1": c1, "any": any_c, "any_all": any_all, "disposed": disposed})

        # === 進入處置後，所有次數歸零重新計算 (截斷歷史) ===
        # 與 analyze() 和 get_trigger_progress() 使用相同的 cutoff_idx 邏輯
        cutoff_idx = 0
        in_block = False
        for i in range(len(H)-1, -1, -1):
            if H[i]["disposed"]:
                in_block = True
            elif in_block:
                cutoff_idx = i + 1
                break
        
        H = H[cutoff_idx:]

        # [Fix 2026-08-27] 改用 _compute_live_state 逐日模擬，任何規則觸發都會讓
        # 「所有」規則歸零重新計算，不是只在進入這段連續處置區塊時歸零一次
        # (見該函式註解與 get_trigger_progress()/analyze() 的相同修正)。
        state = DispositionPredictor._compute_live_state(H)
        streak_c1 = state["streak_c1"]
        streak_any = state["streak_any"]
        window_any = [{"any": v} for v in state["window_any"]]

        # 1. C1 連續天數
        needed_c1 = max(0, 3 - streak_c1)

        # 2. 連5日任一款
        needed_any_streak = max(0, 5 - streak_any)

        # 3. 10日內6次 (一至八款)
        # [Fix] 處置期間內的注意天數不該排除，理由同 analyze()：官方只有第六條第三/四項
        # (委員會決議之特殊處置)才排除處置期間次數，一般四條標準規則觸發的處置不排除。
        # [Fix 2026-08-20] 改用逐日模擬，正確處理視窗滑動汰舊(見 _simulate_window_needed)。
        # 沿用原本語意：即使已達標也至少回報還差1天(此函式不負責回報「已觸發」訊號，
        # 那是 analyze()/get_trigger_progress() 的責任)。
        sim_10, _ = DispositionPredictor._simulate_window_needed(window_any, 10, 6, "any", 10)
        needed_accum_10 = max(1, sim_10)

        # 4. 30日內12次 (一至八款)
        sim_30, _ = DispositionPredictor._simulate_window_needed(window_any, 30, 12, "any", 30)
        needed_accum_30 = max(1, sim_30)
        
        needed_any = min(needed_any_streak, needed_accum_10, needed_accum_30)
        
        return (needed_c1, needed_any)
