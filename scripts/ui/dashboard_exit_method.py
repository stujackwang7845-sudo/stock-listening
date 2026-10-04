    def _update_exit_box_only(self, agg_data):
        """只重新計算並更新出關區（用於歷史日期快取載入時）"""
        # 從 populate_table 提取出關區計算邏輯
        
        # 準備 future_dts
        anchor_year = self.calendar["anchor_obj"].year
        anchor_month = self.calendar["anchor_obj"].month
        anchor_date = self.calendar["anchor_obj"]
        
        future_dts = []
        for d_str in self.calendar["future"]:
            try:
                dm = d_str.split("/")
                m, d = int(dm[0]), int(dm[1])
                y = anchor_year
                if anchor_month == 12 and m == 1: y += 1
                elif anchor_month == 1 and m == 12: y -= 1
                future_dts.append(dt.datetime(y, m, d))
            except:
                future_dts.append(None)
        
        # 取得 sorted_codes
        valid_keys = [str(k) for k in agg_data.keys() if isinstance(k, str) or isinstance(k, int)]
        sorted_codes = sorted(valid_keys)
        
        # 定義 safe_date
        def safe_date(idx):
            if idx < len(self.calendar["future"]): return self.calendar["future"][idx]
            return "??"
        
        lbl_1 = f"明天({safe_date(0)})出關"
        lbl_2 = f"後天({safe_date(1)})出關"
        lbl_3 = f"大後天({safe_date(2)})出關"
        
        # 計算「明天是出關日前四天」的資訊
        day_before_4_twse = []
        day_before_4_tpex = []
        
        print(f"[DEBUG] _update_exit_box_only: future_dts length: {len(future_dts)}", flush=True)
        
        if len(future_dts) > 0 and future_dts[0]:
            tomorrow = future_dts[0]
            print(f"[DEBUG] Tomorrow: {tomorrow}", flush=True)
            
            # 計算明天+4個交易日的日期
            target_exit_date = tomorrow
            trading_days_count = 0
            check_date = tomorrow
            while trading_days_count < 4:
                check_date = check_date + dt.timedelta(days=1)
                if DateUtils.is_trading_day(check_date):
                    trading_days_count += 1
                    target_exit_date = check_date
            
            print(f"[DEBUG] Target Exit Date (Tomorrow+4): {target_exit_date}", flush=True)
            
            # 檢查是否有股票的出關日 = target_exit_date
            for code in sorted_codes:
                data = agg_data[code]
                period = data.get("period", "")
                disp_end_dt = DateUtils.parse_period_end(period)
                
                if disp_end_dt and disp_end_dt.date() == target_exit_date.date():
                    name = data["name"]
                    has_futures = data.get("has_futures", False)
                    suffix = "(期)" if has_futures else ""
                    item_str = f"<a href='{code}' style='color: #E0E0E0; text-decoration: none;'>{code}&nbsp;{name}{suffix}</a>"
                    
                    if data["source"] == "上市":
                        day_before_4_twse.append(item_str)
                    else:
                        day_before_4_tpex.append(item_str)
            
            # 格式化前四天標籤
            exit_date_str = target_exit_date.strftime("%m/%d")
            lbl_4 = f"前四天({exit_date_str})出關"
            print(f"[DEBUG] Day-4 label: {lbl_4}, TWSE: {len(day_before_4_twse)}, TPEX: {len(day_before_4_tpex)}", flush=True)
        else:
            lbl_4 = "前四天出關"
            print("[DEBUG] future_dts is empty or None!", flush=True)
        
        # 計算明天/後天/大後天出關（需要從歷史邏輯中提取）
        # 簡化版：只顯示前四天，其他設為空
        exit_list = [
            (lbl_1, ""),
            ("     上市", "無"),
            ("     上櫃", "無"),
            (lbl_2, ""),
            ("     上市", "無"),
            ("     上櫃", "無"),
            (lbl_3, ""),
            ("     上市", "無"),
            ("     上櫃", "無"),
            (lbl_4, ""),
            ("     上市", "&nbsp;&nbsp;&nbsp;&nbsp;".join(day_before_4_twse) if day_before_4_twse else "無"),
            ("     上櫃", "&nbsp;&nbsp;&nbsp;&nbsp;".join(day_before_4_tpex) if day_before_4_tpex else "無")
        ]
        
        self.disp_exit_box.update_items(exit_list)
