"""
監控儀表板的資料組裝核心(原 ui/dashboard.py 的 HistoryWorker.run 與 Dashboard 的
_merge_disposal_status_from_db / _build_local_agg_data / _merge_clauses_from_listening_history /
_merge_clauses_from_db，2026-10-04 P2 原樣搬出，不改邏輯)。本模組不依賴 PyQt6。

HistoryWorker.run 依原本的段落拆成：
  sync_listening_and_disposals → fetch_market_flags → fetch_disposition_map →
  collect_daily_attention → apply_disposition_map → merge_listening_history，
由 build_agg_data() 依序呼叫(順序與原本完全相同)。
"""
import json
import time
import datetime as dt
from datetime import datetime

from core.utils import DateUtils, ClauseParser
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.cache import CacheManager
from core.scraper_attention import AttentionScraper
from core.disposal_database import DisposalDatabase
from core.price_database import PriceDatabase


def sync_listening_and_disposals(history_manager, target_dt, target_date_str, fetcher, parser, progress):
    """當天官方注意股寫進 listening_history、從 GitHub 同步聽牌、更新處置公告(Shioaji 優先，失敗用官方爬蟲)。"""
    # 啟動時一併同步聽牌資料庫 + 處置公告 (依時間決定來源)
    if history_manager:
        try:
            from datetime import datetime
            now = datetime.now()
            # 無論何時啟動，一律優先進行雙軌聯集抓取，確保最新資料正確無誤
            if True:
                progress("正在下載今日最新注意股票...")
                merged_records = {} # code -> record_dict
                
                # === 1. (已移除) 嘗試從永豐 API 抓取注意股 ===
                # 依據使用者規則：聽牌區清單永遠來自官方跟GITHUB，不用自己計算。
                # Shioaji 的 notice_df 會包含大量非官方聽牌的標的，導致 listening_history.json 被污染。
                
                # === 2. 執行傳統網頁爬蟲 (雙軌聯集備援) ===
                try:
                    att_list = AttentionScraper.fetch_data(target_dt)
                    if att_list:
                        for item in att_list:
                            code = item['code'].strip()
                            if len(code) > 4 or '.' in code:
                                continue
                            reason_str = item.get('reason', '')
                            date_key = target_dt.strftime("%m/%d")
                            parsed_clause = ClauseParser.parse_clauses(reason_str)
                            
                            # 取得正確的市場來源
                            raw_source = item.get('source', '')
                            if raw_source in ("TWSE", "tse", "上市"):
                                market_src = "上市"
                            elif raw_source in ("TPEX", "otc", "OTC", "上櫃"):
                                market_src = "上櫃"
                            else:
                                market_src = fetcher.check_market_type(code) or "上市"
                            
                            # 合併：如果爬蟲有更豐富的資訊 (如名稱)，以此為主
                            if code in merged_records:
                                merged_records[code]["name"] = item['name']
                                if not merged_records[code]["reason"] and reason_str:
                                    merged_records[code]["reason"] = reason_str
                                    merged_records[code]["trigger_info"] = json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}"
                            else:
                                merged_records[code] = {
                                    "date": target_dt.strftime("%Y-%m-%d"),
                                    "code": code,
                                    "name": item['name'],
                                    "reason": reason_str,
                                    "source": market_src,
                                    "trigger_info": json.dumps({date_key: parsed_clause}, ensure_ascii=False) if parsed_clause else "{}",
                                    "is_disposed_next_day": False,
                                    "tags": [],
                                    "comment": ""
                                }
                        print(f"[HistoryWorker] 爬蟲: 已抓取 {len(att_list)} 筆注意股")
                except Exception as e:
                    print(f"[HistoryWorker] 爬蟲注意股抓取失敗: {e}")
                
                # === 3. 寫入本地歷史資料庫 ===
                if merged_records:
                    for record in merged_records.values():
                        history_manager.add_record(record)
                    print(f"[HistoryWorker] 聯集成功，共存入/更新 {len(merged_records)} 筆官方注意股")
            
            # 聯集抓取完畢後，統一從 GitHub 同步歷史，補齊其他天數的缺失資料
            progress("正在同步官方聽牌資料庫 (GitHub)...")
            history_manager.sync_from_github()
            
            # === 自動下載處置公告（所有時段都執行）===
            try:
                progress("正在自動更新處置公告...")
                # 優先嘗試 Shioaji api.punish()
                punish_saved = False
                try:
                    from core.shioaji_client import ShioajiClient
                    sj_client = ShioajiClient()
                    punish_df = sj_client.get_punish()
                    if punish_df is not None and not punish_df.empty:
                        from core.disposal_database import DisposalDatabase
                        disp_db = DisposalDatabase()
                        cursor = disp_db.conn.cursor()
                        count = 0
                        for _, p_row in punish_df.iterrows():
                            try:
                                p_code = str(p_row.get('code', '')).strip()
                                p_start = p_row.get('start_date')
                                p_end = p_row.get('end_date')
                                p_interval = str(p_row.get('interval', ''))
                                p_desc = str(p_row.get('description', ''))
                                p_announced = p_row.get('announced_date')
                                
                                start_str = p_start.strftime("%Y-%m-%d") if p_start else ""
                                end_str = p_end.strftime("%Y-%m-%d") if p_end else ""
                                announce_str = p_announced.strftime("%Y-%m-%d") if p_announced else ""
                                
                                # 動態識別上市/上櫃
                                market_src = fetcher.check_market_type(p_code)
                                if market_src not in ("上市", "上櫃"):
                                    market_src = "上市" # 預設安全值
                                
                                cursor.execute("""
                                    INSERT OR REPLACE INTO disposal_records
                                    (source, announce_date, code, name, period_start, period_end, 
                                     period_raw, measure, reason, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                """, (
                                    market_src,
                                    announce_str,
                                    p_code,
                                    "",  # Shioaji 不提供名稱
                                    start_str,
                                    end_str,
                                    f"{start_str}~{end_str}",
                                    p_interval,
                                    p_desc
                                ))
                                count += 1
                            except Exception:
                                pass
                        disp_db.conn.commit()
                        disp_db.close()
                        punish_saved = True
                        print(f"[HistoryWorker] Shioaji punish: 已存入 {count} 筆處置記錄")
                except Exception as e:
                    print(f"[HistoryWorker] Shioaji punish 失敗: {e}")
                
                # 備援：若 Shioaji 失敗，用傳統爬蟲
                if not punish_saved:
                    from core.disposal_database import DisposalDatabase
                    disp_db = DisposalDatabase()
                    cursor = disp_db.conn.cursor()
                    
                    # 2.1 上市爬取
                    twse_disp = fetcher.fetch_twse_disposition(target_date_str)
                    if twse_disp:
                        parsed_tse = parser.parse_twse_disposition(twse_disp)
                        if parsed_tse:
                            for item in parsed_tse:
                                try:
                                    start, end = disp_db.parse_period(item.get("period"))
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO disposal_records
                                        (source, announce_date, code, name, period_start, period_end,
                                         period_raw, measure, reason, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                    """, (
                                        "上市", item.get("date") or start, item.get("code"), item.get("name"),
                                        start, end, item.get("period"), item.get("measure"), item.get("reason")
                                    ))
                                except:
                                    pass
                            print(f"[HistoryWorker] 爬蟲: 已存入 {len(parsed_tse)} 筆上市處置記錄")
                            
                    # 2.2 上櫃爬取
                    tpex_disp = fetcher.fetch_tpex_disposition(target_date_str)
                    if tpex_disp:
                        parsed_otc = parser.parse_tpex_disposition(tpex_disp)
                        if parsed_otc:
                            for item in parsed_otc:
                                try:
                                    start, end = disp_db.parse_period(item.get("period"))
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO disposal_records
                                        (source, announce_date, code, name, period_start, period_end,
                                         period_raw, measure, reason, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                    """, (
                                        "上櫃", item.get("date") or start, item.get("code"), item.get("name"),
                                        start, end, item.get("period"), item.get("measure"), item.get("reason")
                                    ))
                                except:
                                    pass
                            print(f"[HistoryWorker] 爬蟲: 已存入 {len(parsed_otc)} 筆上櫃處置記錄")
                    
                    disp_db.conn.commit()
                    disp_db.close()
            except Exception as e:
                print(f"[HistoryWorker] 自動更新處置公告失敗: {e}")
                
        except Exception as e:
            print(f"[HistoryWorker] Sync error: {e}")


def fetch_market_flags(fetcher, parser, target_date_str, progress):
    """融券(上市+上櫃)與股票期貨清單 → (margin_codes, futures_codes)。"""
    # 0. Fetch Margin/Short Lists (Use Target Date)
    progress("正在抓取融券/信用交易名單...")
    margin_codes = set()
    
    # Check if DateUtils supports historical margin lookup or if fetcher does
    # Currently fetcher methods take date_str
    margin_date_str = target_date_str
    
    # TWSE
    twse_margin = fetcher.fetch_twse_margin_list(margin_date_str)
    if twse_margin:
        margin_codes.update(parser.parse_twse_margin(twse_margin))
        
    # TPEX
    tpex_margin = fetcher.fetch_tpex_margin_list(margin_date_str)
    if tpex_margin:
        margin_codes.update(parser.parse_tpex_margin(tpex_margin))
        
    # Futures List
    progress("正在抓取股票期貨清單...")
    futures_codes = set()
    taifex_futures = fetcher.fetch_taifex_futures_list()
    if taifex_futures:
        futures_codes.update(parser.parse_taifex_futures_list(taifex_futures))

    return margin_codes, futures_codes


def fetch_disposition_map(fetcher, parser, target_date_str, progress):
    """當天官方處置名單 → {code: [{name, source, period, measure}]}。"""
    # 1. Fetch Current Disposition List (For Pink Highlight)
    progress(f"正在抓取 {target_date_str} 處置股名單...")
    disposition_map = {} # code -> {name, source}
    
    # TWSE Disposition
    twse_disp = fetcher.fetch_twse_disposition(target_date_str)
    if twse_disp:
        parsed = parser.parse_twse_disposition(twse_disp)
        print(f"DEBUG: Parsed TWSE Disposition Items: {len(parsed)}")
        for item in parsed:
            code = item["code"].strip()
            if code not in disposition_map:
                disposition_map[code] = []
            disposition_map[code].append({
                "name": item["name"], 
                "source": "上市",
                "period": item.get("period", ""),
                "measure": item.get("measure", "")
            })

    # TPEX Disposition
    progress("正在抓取上櫃處置股...")
    tpex_disp = fetcher.fetch_tpex_disposition(target_date_str)
    if tpex_disp:
        parsed_otc = parser.parse_tpex_disposition(tpex_disp)
        print(f"DEBUG: Parsed TPEX Disposition Items: {len(parsed_otc)}")
        for item in parsed_otc:
            code = item["code"].strip()
            if code not in disposition_map:
                disposition_map[code] = []
            disposition_map[code].append({
                "name": item["name"], 
                "source": "上櫃",
                "period": item.get("period", ""),
                "measure": item.get("measure", "")
            })
    
    print(f"DEBUG: Final Disposition Map Keys: {list(disposition_map.keys())}") 

    return disposition_map


def collect_daily_attention(agg_data, fetcher, parser, cache_mgr, target_date, margin_codes, futures_codes, progress):
    """最近 11 個交易日的逐日注意股條款寫進 agg_data(過去日期讀 daily_cache)，回傳 last_trading_day。"""
    # Determine Fetch Dates (10 days history for prediction)
    last_trading_day = DateUtils.get_last_trading_day()
    
    days_to_fetch = []
    days_to_fetch.append(last_trading_day)
    
    count = 0
    curr = last_trading_day
    while count < 10:
        curr = curr - dt.timedelta(days=1)
        if DateUtils.is_trading_day(curr):
            days_to_fetch.append(curr)
            count += 1
    days_to_fetch.sort()
    
    progress(f"準備抓取 {len(days_to_fetch)} 天資料...")
    
    for day in days_to_fetch:
        day_str = day.strftime("%Y%m%d")
        display_date = day.strftime("%m/%d")
        
        is_latest = (day == last_trading_day)
        cached_data = cache_mgr.get_daily_data(day_str)
        
        daily_items = []
        
        if cached_data is not None and not is_latest:
            progress(f"讀取快取 {display_date} 資料...")
            daily_items = cached_data
        else:
            progress(f"正在下載 {display_date} 資料...")
            
            # TWSE
            twse_data = fetcher.fetch_twse_attention(day_str)
            parsed_twse = parser.parse_twse_attention(twse_data)

            # TPEX
            tpex_data = fetcher.fetch_tpex_attention(day_str)
            parsed_tpex = parser.parse_tpex_attention(tpex_data)

            # Merge
            daily_items = parsed_twse + parsed_tpex
            print(f"DEBUG: Fetched {day_str} -> TWSE: {len(parsed_twse)}, TPEX: {len(parsed_tpex)}, Total: {len(daily_items)}", flush=True)

            # [Fix] fetch_twse_attention/fetch_tpex_attention 連線失敗時回傳 None（區別於「當天真的沒有注意股」
            # 的合法空清單）。若任一來源是 None，代表這天的資料不完整，不可快取，否則之後只讀快取、
            # 永遠不會重抓，會永久遺漏那一天某個市場的注意股條款（曾發生 TWSE 失敗、TPEX 成功，
            # 混出來的 19 筆被當成當天完整資料快取，導致該日某檔股票的第一款紀錄永久消失）。
            fetch_incomplete = (twse_data is None) or (tpex_data is None)

            # Save to Cache (Only if data found or not latest to avoid caching empty pending data)
            if fetch_incomplete:
                print(f"DEBUG: Skipping cache save for {day_str} (Incomplete fetch: TWSE={'OK' if twse_data is not None else 'FAILED'}, TPEX={'OK' if tpex_data is not None else 'FAILED'})", flush=True)
            elif daily_items:
                cache_mgr.save_daily_data(day_str, daily_items)
            elif not is_latest:
                 # If not latest and empty, maybe it's a holiday or really empty? Save to avoid repeated fetch.
                 cache_mgr.save_daily_data(day_str, daily_items)
            else:
                 print(f"DEBUG: Skipping cache save for {day_str} (Latest & Empty)", flush=True)
            
            time.sleep(0.5)
        
        print(f"DEBUG: Day {day_str} Items: {len(daily_items)}", flush=True)
        
        for item in daily_items:
            if item.get('code') in ('6230', '6442'):
                 print(f"Found {item.get('code')} in daily_items for {day_str}!", flush=True)
            code = item.get('code', '').strip()
            if not code: continue
            
            name = item.get('name', '')
            source = "上市" if item.get('source', '') == 'TWSE' else "上櫃"
            raw_reason = str(item.get('reason', ''))
            
            # DEBUG 8046 Raw Reason
            if code == '8046':
                print(f"DEBUG 8046 Raw Reason: '{raw_reason}'", flush=True)

            clause = ClauseParser.parse_clauses(raw_reason)
            
            # [Fallback] Parse Future Disposal from Reason
            # Pattern: 115年01月27日至115年02月09日
            future_pd_found = None
            future_meas_found = "處置(詳見注意資訊)"
            try:
                import re
                # Match dates: 115年01月27日 ... 115年02月09日 OR 115/01/27 ... 115/02/09
                # Regex handles "至" or "~" or "～" details
                # Capture groups: Y1, M1, D1 ... Y2, M2, D2
                # New Robust Regex: (\d{3})[年/](\d{1,2})[月/](\d{1,2})[日\s]*?[~至\-\—]+?[ ]*?(\d{3})[年/](\d{1,2})[月/](\d{1,2})[日\s]*?
                date_pat = r"(\d{3})[年/](\d{1,2})[月/](\d{1,2})[日]?.*?[~至\-\—].*?(\d{3})[年/](\d{1,2})[月/](\d{1,2})[日]?"
                m = re.search(date_pat, raw_reason)
                if m:
                    y1, m1, d1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    y2, m2, d2 = int(m.group(4)), int(m.group(5)), int(m.group(6))
                    
                    start_dt = datetime(y1 + 1911, m1, d1)
                    end_dt = datetime(y2 + 1911, m2, d2)
                    
                    # Only consider if Start Date > Target Date (Future)
                    # We use self.target_date or last_trading_day
                    tgt = target_date if target_date else DateUtils.get_last_trading_day()
                    tgt_dt = datetime(tgt.year, tgt.month, tgt.day)
                    
                    if start_dt > tgt_dt:
                         p_str = f"{y1}/{m1:02d}/{d1:02d}~{y2}/{m2:02d}/{d2:02d}"
                         future_pd_found = p_str
            except Exception as e:
                pass # Regex fail safe

            
            if code not in agg_data:
                # Only add if it has recent hits
                agg_data[code] = {
                    "name": name,
                    "source": source,
                    "clauses": {},
                    "disposition": None,
                    "can_short": (code in margin_codes),
                    "has_futures": (code in futures_codes)
                }
                if code == '2408':
                     pass # print(f"DEBUG: 2408 Added to agg_data.", flush=True)

            # Update Clause
            agg_data[code]["clauses"][display_date] = clause
            if code in ('6230', '6442'):
                print(f'UPDATE CLAUSE INSIDE HISTORY WORKER: {code} -> {display_date} = {clause}')
            if code in ('6230', '6442'):
                print(f'UPDATE CLAUSE INSIDE HISTORY WORKER: {code} -> {display_date} = {clause}')
            
            # Update Future Period from Attention (Fallback)
            if future_pd_found:
                # Don't overwrite if already has better info (unlikely here)
                if not agg_data[code].get("future_period"):
                    agg_data[code]["future_period"] = future_pd_found
                    agg_data[code]["future_measure"] = future_meas_found
            
        # Log all codes found for this day
        found_codes_list = [item.get('code','').strip() for item in daily_items]
        # print(f"DEBUG: Codes found in {day_str}: {found_codes_list}", flush=True)

    return last_trading_day


def apply_disposition_map(agg_data, disposition_map, target_date, margin_codes, futures_codes):
    """處置名單套進 agg_data：現行處置 → is_disposed/period/measure，未來處置 → future_period/future_measure。"""
    # --- Post-Processing: Apply Disposition Status & Add Missing Disposed Stocks ---
    # Modified Logic: Handle Multiple Dispositions (Active vs Future)
    if '8046' in disposition_map:
         pass 
    else:
         pass

    for code, info_list in disposition_map.items():
        # 1. Separate Active and Future
        active_info = None
        future_info = None
        
        # Use specific target date for comparison
        compare_date = target_date if target_date else DateUtils.get_last_trading_day()
        compare_date_dt = datetime(compare_date.year, compare_date.month, compare_date.day)
        
        # Sort info_list by period start to handle logic deterministically?
        # Actually, just find the one that covers Today (Active) and the one starting After Today (Future)
        
        for info in info_list:
            p_start = DateUtils.parse_period_start(info["period"])
            p_end = DateUtils.parse_period_end(info["period"])
            
            if p_start and p_end:
                if p_start <= compare_date_dt <= p_end:
                    # Prioritize newer active disposition
                    if not active_info:
                        active_info = info
                    else:
                        curr_start = DateUtils.parse_period_start(active_info["period"])
                        if p_start > curr_start:
                            active_info = info
                elif p_start > compare_date_dt:
                    # Prioritize earlier future disposition (next to come)
                    if not future_info:
                        future_info = info
                    else:
                        curr_future_start = DateUtils.parse_period_start(future_info["period"])
                        if p_start < curr_future_start:
                            future_info = info
        
        # Fallback: If only one record and it creates no active/future hit (maybe data error), 
        # assume it's active or future based on logic? 
        # Current logic previously just took the last one.
        # If we didn't match anything perfectly, maybe dates are slightly off or parser failed.
        # Let's trust parser dates.
        
        # If NO active found, but we have records, maybe the "Active" one ended yesterday? 
        # Or maybe it starts tomorrow?
        if not active_info and not future_info and info_list:
            # Fallback: Just take the first one?
            # Let's try to match loosely.
            pass

        # Update agg_data
        if code not in agg_data:
            # Determine base info from Active, or Future, or first available
            base = active_info if active_info else (future_info if future_info else info_list[0])
            
            agg_data[code] = {
                "name": base["name"],
                "source": base["source"],
                "can_short": (code in margin_codes),
                "has_futures": (code in futures_codes),
                "clauses": {}, # No clauses history
                "is_disposed": False, # Will set below
                "period": "",
                "measure": ""
            }
        
        # Apply Active
        if active_info:
            agg_data[code]["is_disposed"] = True
            agg_data[code]["period"] = active_info["period"]
            agg_data[code]["measure"] = active_info["measure"]
        
        # Apply Future
        if future_info:
            agg_data[code]["future_period"] = future_info["period"]
            agg_data[code]["future_measure"] = future_info["measure"]
            # If NOT active (e.g. only future), we might want to flag it?
            # agg_data[code]["is_future_disposed"] = True # Optional


def merge_listening_history(agg_data, history_manager, last_trading_day):
    """listening_history 最近 45 天的條款補進 agg_data(只補空、過濾未來日期)。"""
    # --- IMPORTANT: 從 listening_history.json 補充歷史條款 ---
    # 這樣快速更新和日期更新會有一致的行為
    if history_manager:
        from datetime import timedelta
        import datetime as _dt_worker

        # 取得最近 45 天的資料（覆蓋 30 個交易日，供 Rule 4: 30日內12次 使用）
        lookback_days = 45
        start_date = last_trading_day - timedelta(days=lookback_days)
        current = start_date
        
        t_year = last_trading_day.year
        t_month = last_trading_day.month
        
        while current <= last_trading_day:
            records = history_manager.get_listening_data(current)
            
            if records:
                for r in records:
                    code = r["code"]
                    # 過濾權證
                    if len(str(code)) == 5:
                        continue
                    
                    trigger_info = r.get("trigger_info", {})
                    if isinstance(trigger_info, str):
                        try:
                            trigger_info = json.loads(trigger_info)
                        except:
                            trigger_info = {}
                    
                    # [Fix] 如果股票不在 agg_data 中，必須將它加入，否則無法進行「一進聽」預測
                    if code not in agg_data:
                        # 加入基本的 agg_data 結構
                        agg_data[code] = {
                            "name": r.get("name", ""),
                            "source": r.get("source", "上市"),
                            "clauses": {},
                            "disposition": None,
                            "is_disposed": False,
                            "period": "",
                            "can_short": False, # 這邊預設 false，稍後需要可再查
                            "has_futures": r.get("has_futures", False)
                        }
                    
                    if trigger_info:
                        for date_key, clause_val in trigger_info.items():
                            # [Fix] 只填入 ≤ last_trading_day 的條款，避免未來日期污染預測
                            try:
                                parts = date_key.split("/")
                                if len(parts) == 2:
                                    m, d = int(parts[0]), int(parts[1])
                                    y = t_year
                                    if t_month == 1 and m == 12:
                                        y = t_year - 1
                                    elif t_month == 12 and m == 1:
                                        y = t_year + 1
                                    clause_dt = _dt_worker.datetime(y, m, d)
                                    if clause_dt.date() > last_trading_day.date():
                                        continue
                            except:
                                pass  # 日期解析失敗時不過濾
                            
                            if date_key not in agg_data[code]["clauses"] or not agg_data[code]["clauses"][date_key]:
                                agg_data[code]["clauses"][date_key] = clause_val
            
            current += timedelta(days=1)
        
        print(f"[HistoryWorker] 已從 listening_history 補充歷史條款（已過濾未來日期）")


def build_agg_data(history_manager, target_date=None, cache_mgr=None, progress=None):
    """
    原 HistoryWorker.run：抓官方資料、組出儀表板/總覽共用的 agg_data。
    target_date=None 代表最新交易日；progress(msg) 給桌面版狀態列用。
    """
    progress = progress or (lambda msg: None)
    if cache_mgr is None:
        cache_mgr = CacheManager()
    fetcher = StockFetcher()
    parser = StockParser()

    agg_data = {}

    # Determine Date Context
    if target_date:
        target_dt = target_date
        progress(f"正在抓取 {target_dt.strftime('%Y/%m/%d')} 資料...")
    else:
        target_dt = DateUtils.get_last_trading_day()
        
    target_date_str = target_dt.strftime("%Y%m%d")

    sync_listening_and_disposals(history_manager, target_dt, target_date_str, fetcher, parser, progress)
    margin_codes, futures_codes = fetch_market_flags(fetcher, parser, target_date_str, progress)
    disposition_map = fetch_disposition_map(fetcher, parser, target_date_str, progress)
    last_trading_day = collect_daily_attention(agg_data, fetcher, parser, cache_mgr, target_date,
                                               margin_codes, futures_codes, progress)
    apply_disposition_map(agg_data, disposition_map, target_date, margin_codes, futures_codes)
    merge_listening_history(agg_data, history_manager, last_trading_day)
    return agg_data


def merge_disposal_status_from_db(agg_data, target_date, history_manager):
    """
    將 disposal_records 的公告(announce_date)與現行(active)處置狀態，
    即時合併進 agg_data(就地修改)。

    [Fix 2026-08-31] 原本這段邏輯只在 _build_local_agg_data() 的「無快取」
    回退路徑才會執行；但只要當天的 agg_cache 曾經被建立過(即使是在該股票
    公告要處置「之前」建的舊底稿)，就會走 cached_agg 分支直接沿用舊資料，
    永遠不會重新從 disposal_records 同步 is_disposed/future_period，導致
    當天公告的新處置股(例：3163/3441/4188/6103 於 2026-08-28 公告)在總覽
    與儀表板都顯示不出來。抽成獨立方法，讓 cached_agg 分支也能呼叫，確保
    不論是否有快取，處置狀態都以 disposal_records(即時資料庫)為準。
    """
    date_str = target_date.strftime("%Y-%m-%d")
    try:
        db = DisposalDatabase()

        # A. Fetch Announcements (Notices for today)
        # [Fix 2026-09-05 撤回] 原本加了 get_upcoming_disposals() 把「公告日不是
        # 今天、但生效日還沒到」的個股也算進來，這是誤解需求——使用者明確要求
        # 「盤後處置公告」只顯示「今天」盤後公告的個股，隔天(即使該個股生效日
        # 還沒到)就不該再顯示，要換成隔天自己當天公告的個股。維持單純只查
        # announce_date == 今天，不額外擴大範圍。
        ann_recs = db.get_disposal_by_announce_date(date_str)

        # B. Fetch Active Disposals (For Table/Exit Zone)
        act_recs = db.get_active_disposals(date_str)

        all_disposals = []
        # [Fix] 使用 (code, period_start) 做記錄去重，避免 ann_recs 和 act_recs 重複
        ann_keys = set()
        act_keys = set()
        if ann_recs:
            for r in ann_recs:
                key = (r['code'], r.get('period_start', ''))
                ann_keys.add(key)
                all_disposals.append(r)
        if act_recs:
            for r in act_recs:
                key = (r['code'], r.get('period_start', ''))
                act_keys.add(key)
                if key not in ann_keys:  # 避免重複加入
                    all_disposals.append(r)

        # Sort all collected records by announce_date DESC, then period_start DESC
        # So the FIRST record we see for a code is the NEWEST (most recently announced/started)
        def safe_date(d): return d if d else "0000-00-00"
        all_disposals.sort(key=lambda x: (safe_date(x.get('announce_date')), safe_date(x.get('period_start'))), reverse=True)

        seen_codes = set()
        for r in all_disposals:
            code = r['code']
            r_key = (code, r.get('period_start', ''))
            is_announcement = r_key in ann_keys
            is_active = r_key in act_keys

            # Setup code entry if missing
            if code not in agg_data:
                # [Fix] 從 futures_stocks 模組檢查期貨
                has_futures = False
                try:
                    from core.futures_stocks import has_futures as check_futures
                    has_futures = check_futures(code)
                except: pass

                can_short = False

                agg_data[code] = {
                    "code": code,
                    "name": r['name'],
                    "source": r['source'],
                    "clauses": {},
                    "trigger_info": {},
                    # [Fix 2026-08-31] 公告當天(即使處置期間尚未開始)也視為已處置，
                    # 統一以粉紅色 + 處置頻率顯示，不再區分「已生效/剛公告」兩種顏色
                    # (使用者要求：顏色直接顯示粉紅即可，但要有處置頻率)。
                    "is_disposed": is_active or is_announcement,
                    "has_futures": has_futures,
                    "can_short": can_short
                }
                seen_codes.add(code)

                # Fill primary data from the NEWEST record
                agg_data[code]["measure"] = r.get("measure", "")
                agg_data[code]["source"] = r["source"]

                if is_announcement:
                    agg_data[code]["future_period"] = r.get("period_raw", r.get("period", ""))
                    agg_data[code]["future_measure"] = r.get("measure", "")
                    agg_data[code]["period_start"] = r.get("period_start")
                    agg_data[code]["period_end"] = r.get("period_end")
                    if not is_active:
                        agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                if is_active:
                    agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
            else:
                # If this is the FIRST time we process this code in this loop
                if code not in seen_codes:
                    seen_codes.add(code)
                    agg_data[code]["is_disposed"] = is_active or is_announcement
                    agg_data[code]["measure"] = r.get("measure", "")
                    agg_data[code]["source"] = r["source"]

                    if is_announcement:
                        agg_data[code]["future_period"] = r.get("period_raw", r.get("period", ""))
                        agg_data[code]["future_measure"] = r.get("measure", "")
                        agg_data[code]["period_start"] = r.get("period_start")
                        agg_data[code]["period_end"] = r.get("period_end")
                        if not is_active:
                            agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                    if is_active:
                        agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))
                else:
                    # Existing Code, seen_codes ALREADY HAS IT meaning we already filled the NEWEST measure.
                    # Do not overwrite measure. Just supplement Missing status.
                    if (is_active or is_announcement) and not agg_data[code].get("is_disposed"):
                        agg_data[code]["is_disposed"] = True
                        if not agg_data[code].get("period"):
                            agg_data[code]["period"] = r.get("period_raw", r.get("period", ""))

                    if is_announcement and not agg_data[code].get("period_start"):
                         agg_data[code]["future_period"] = r.get("period_raw", r.get("period", ""))
                         agg_data[code]["period_start"] = r.get("period_start")
                         agg_data[code]["period_end"] = r.get("period_end")

            # Ensure checking Short and Futures from history or module update here too if desired
            # [Fix] 即使已存在，也要檢查並更新期貨與融券資訊
            if "has_futures" not in agg_data[code] or not agg_data[code]["has_futures"]:
                try:
                    from core.futures_stocks import has_futures as check_futures
                    agg_data[code]["has_futures"] = check_futures(code)
                except: pass

            # 融券檢測稍後統一處理

        db.close()

        # [Fix] Restore Clauses from History for Active Disposals
        # (Because Disposal DB doesn't have daily clause details, but History does)
        active_codes = set(k for k, v in agg_data.items() if v.get("is_disposed"))

        if active_codes:
             last_recs = {}
             for h in history_manager.history:
                 c = str(h.get("code"))
                 if c in active_codes:
                     d_str = h.get("date", "")
                     # Find latest record before or on target date
                     if d_str <= date_str:
                         curr_best = last_recs.get(c)
                         if not curr_best or d_str > curr_best["date"]:
                             last_recs[c] = h

             for c, h in last_recs.items():
                 t_info = {}
                 try:
                     raw = h.get("trigger_info", {})
                     if isinstance(raw, str): t_info = json.loads(raw)
                     else: t_info = raw
                 except: pass

                 if t_info:
                     # Merge clauses into active disposal data
                     current_clauses = agg_data[c].get("clauses", {})
                     # If current is empty, just take it. If not, merge?
                     if not current_clauses:
                          agg_data[c]["clauses"] = t_info
                          agg_data[c]["trigger_info"] = t_info # Keep sync

    except Exception as e:
        print(f"Error loading disposal DB for {target_date}: {e}")

    return agg_data


def build_local_agg_data(target_date, history_manager):
    """
    從本地檔案 (History + Disposal DB) 重建 agg_data
    用於當快取被清除時的回退機制，確保資料不消失。
    """
    agg_data = {}
    date_str = target_date.strftime("%Y-%m-%d") # Format for DB query (flexible)
    
    # 1. Load Listening History in Range (Past 15 days)
    # This ensures the table shows all stocks noticed recently, not just today's.
    try:
        from datetime import timedelta
        start_dt = target_date - timedelta(days=15)
        start_str = start_dt.strftime("%Y-%m-%d")
        target_str = target_date.strftime("%Y-%m-%d")
        
        # Group by code, AGGREGATE records in range
        # (Solves Missing Clauses by merging dates, Solves Futures by scanning all history)
        map_agg = {} 
        
        if history_manager is not None:
             # Sort by date
             sorted_hist = sorted(history_manager.history, key=lambda x: x.get("date", ""))
             
             for h in sorted_hist:
                 d = h.get("date", "")
                 c = str(h.get("code"))
                 
                 if start_str <= d <= target_str:
                     if c not in map_agg:
                         map_agg[c] = {
                             "code": c,
                             "name": h.get("name"),
                             "source": h.get("source"),
                             "has_futures": False,
                             "clauses": {},
                             "tags": [],
                             "comment": "",
                             "last_date": ""
                         }
                     
                     entry = map_agg[c]
                     
                     # 1. Accumulate Futures (If any record says True, it's True)
                     if h.get("has_futures"):
                         entry["has_futures"] = True
                         
                     # 2. Accumulate Clauses (Trigger Info)
                     raw = h.get("trigger_info", {})
                     t_dict = {}
                     if isinstance(raw, str):
                         try:
                             t_dict = json.loads(raw)
                         except:
                             # [Fix] Fallback: 從純文字描述中萌取「第一至八款」
                             # 例: 「……（第六款）」 → key=「02/04」 val=「六」
                             import re as _re
                             CLAUSE_MAP = {
                                 "一": "一", "二": "二", "三": "三", "四": "四",
                                 "五": "五", "六": "六", "七": "七", "八": "八"
                             }
                             # 找出所有「第X款」，X 限一至八
                             clause_matches = _re.findall(r'第([一二三四五六七八])款', raw)
                             if clause_matches and d and len(d) >= 10:
                                 k = f"{d[5:7]}/{d[8:10]}"
                                 clause_val = ",".join(CLAUSE_MAP[c] for c in clause_matches)
                                 t_dict[k] = clause_val
                             # [Fix] 純文字 trigger_info fallback：
                             # 「連續X次」= 當天一定是注意日 → 標記為「注」
                             # 「已有X次」= 可能只是窗口滑動 → 不做 fallback
                             elif "連續" in raw and d and len(d) >= 10:
                                 k = f"{d[5:7]}/{d[8:10]}"
                                 t_dict[k] = "注"
                     else:
                         t_dict = raw
                     
                     if t_dict:
                         for k, v in t_dict.items():
                             # Normalize Key: 20260120 -> 01/20
                             norm_k = k
                             if len(k) == 8 and k.isdigit(): # YYYYMMDD
                                 norm_k = f"{k[4:6]}/{k[6:]}"
                             elif "-" in k and len(k)==10: # YYYY-MM-DD
                                  norm_k = f"{k[5:7]}/{k[8:10]}"
                             
                             # Normalize Value
                             val_str = str(v)
                             # Map "第一款" -> "一"
                             val_str = val_str.replace("第一款", "一").replace("第二款", "二") \
                                              .replace("第三款", "三").replace("第四款", "四") \
                                              .replace("第五款", "五").replace("第六款", "六") \
                                              .replace("第七款", "七").replace("第八款", "八") \
                                              .replace("第九款", "九").replace("第十款", "十") \
                                              .replace("第十一款", "十一").replace("第十二款", "十二")
                             
                             # [User Request] Remove "連續" / "注意" - showing nothing is better than showing noise
                             if "連續" in val_str: val_str = ""
                             if val_str == "詳見紀錄": val_str = ""
                             # [Fix] 「注意」是不精確的泛稱，不存入 clauses
                             # 只有第 1~8 款才有意義
                             if val_str == "注意": val_str = ""
                             
                             if val_str:
                                 entry["clauses"][norm_k] = val_str
                             
                     # 3. Update Metadata if Newer
                     if d >= entry["last_date"]:
                         entry["last_date"] = d
                         entry["name"] = h.get("name")
                         if h.get("source"): entry["source"] = h.get("source") # Prefer latest source
                         entry["tags"] = h.get("tags")
                         entry["comment"] = h.get("comment")
                         # [Simple Fix] 直接從 history 讀取期貨與融券資訊
                         if "has_futures" in h:
                             entry["has_futures"] = h.get("has_futures", False)
                         if "can_short" in h:
                             entry["can_short"] = h.get("can_short", False)

        # [Simple Fix] 設定預設值
        for code in map_agg:
            if "has_futures" not in map_agg[code]:
                map_agg[code]["has_futures"] = False
            if "can_short" not in map_agg[code]:
                map_agg[code]["can_short"] = False

        for code, data in map_agg.items():
            t_info = data["clauses"]
            
            # Resolve Source (prefer Agg > Search > Default)
            # [Fix] FinMind is not a displayable source, resolve to 上市/上櫃
            src = data.get("source")
            if not src or src == "FinMind":
                 # 1. Try PriceDatabase (Local Cache)
                 try:
                     pdb = PriceDatabase()
                     src = pdb.get_latest_source(code)
                     pdb.close()
                 except: pass
                 
                 # 2. [效能優化] 移除同步 check_market_type() 網路請求
                 # 每支股票約 500ms，20 支就要 10 秒以上，導致日期切換嚴重卡頓
                 # 改為直接使用預設值，確保日期切換即時響應
                 if not src or src == "FinMind":
                     src = "上市"

            agg_data[code] = {
                "code": code,
                "name": data['name'],
                "source": src,
                "trigger_info": t_info,
                "clauses": t_info, # [Fix] Use aggregated clauses
                "tags": data.get("tags", []),
                "comment": data.get("comment", ""),
                "is_disposed": False, 
                "has_futures": data.get("has_futures", False), # Accumulate True
                "can_short": data.get("can_short", False)
            }
    except Exception as e:
        print(f"Error loading history for {target_date}: {e}")

    # 2. Load Disposal Announcements (DB) & Active
    merge_disposal_status_from_db(agg_data, target_date, history_manager)

    return agg_data


def merge_clauses_from_listening_history(agg_data, target_date, history_manager):
    if not history_manager:
        return
        
    import json
    import datetime as _dt
    from datetime import timedelta
    
    # [Fix] 擴展到 45 天以覆蓋 30 日內 12 次規則所需的完整範圍
    lookback_days = 45
    start_date = target_date - timedelta(days=lookback_days)
    current = start_date
    
    # 計算 target_date 的年份與月份，用於解析 MM/DD 格式的日期鍵
    t_year = target_date.year
    t_month = target_date.month
    
    while current <= target_date:
        records = history_manager.get_listening_data(current)
        if records:
            for r in records:
                code = r["code"]
                if len(str(code)) == 5: continue
                
                trigger_info = r.get("trigger_info", {})
                rec_date_str = r.get("date", "")
                if isinstance(trigger_info, str):
                    try: trigger_info = json.loads(trigger_info)
                    except:
                        trigger_info = {}
                    
                if code in agg_data and trigger_info:
                    for date_key, clause_val in trigger_info.items():
                        # 只補充精確的條款（1~8款），跳過「注」「注意」
                        if clause_val in ("注", "注意"):
                            continue
                        
                        # [Fix] 只填入 ≤ target_date 的條款，避免未來日期污染預測結果
                        # 例如 GitHub 資料裡 "2026-02-03" 的記錄含有 "02/04" 的條款
                        # 這種「預告」條款不應填入今天的預測
                        try:
                            parts = date_key.split("/")
                            if len(parts) == 2:
                                m, d = int(parts[0]), int(parts[1])
                                # 處理跨年邊界
                                y = t_year
                                if t_month == 1 and m == 12:
                                    y = t_year - 1
                                elif t_month == 12 and m == 1:
                                    y = t_year + 1
                                clause_dt = _dt.datetime(y, m, d)
                                # 只允許 ≤ target_date 的條款
                                if clause_dt.date() > target_date.date():
                                    continue
                        except:
                            pass  # 日期解析失敗時不過濾（容錯）
                        
                        if date_key not in agg_data[code]["clauses"] or not agg_data[code]["clauses"][date_key]:
                            agg_data[code]["clauses"][date_key] = clause_val
        current += timedelta(days=1)


def merge_clauses_from_db(agg_data, target_date):
    """
    從資料庫查詢注意條款並合併到 agg_data 的 clauses 字典中
    查詢過去 10 個交易日的所有條款
    
    Args:
        agg_data: 聚合資料字典
        target_date: datetime 物件
    """
    try:
        import sqlite3
        from core import database_clause_ext
        from core.utils import DateUtils
        import datetime as dt
        
        # 連接資料庫
        from core.runtime import get_paths
        conn = sqlite3.connect(get_paths().disposal_db)
        
        # 計算過去 30 個交易日 (支援 Rule 4: 30日內12次)
        past_dates = []
        curr = target_date
        count = 0
        while count < 30:
            if DateUtils.is_trading_day(curr):
                past_dates.append(curr)
                count += 1
            curr = curr - dt.timedelta(days=1)
        
        # 查詢每個交易日的條款
        total_loaded = 0
        for date_obj in past_dates:
            date_str_mmdd = date_obj.strftime("%m/%d")
            clauses_from_db = database_clause_ext.get_clauses_for_date(conn, date_str_mmdd)
            
            if not clauses_from_db:
                continue
            # 將從資料庫取得的條款加入 agg_data
            for code, db_data in clauses_from_db.items():
                clause_val = db_data.get("clauses", "")
                # [Fix] 如果該股票不在 agg_data 中，將其加入以便「一進聽」預測可以執行
                if code not in agg_data:
                    agg_data[code] = {
                        "name": db_data.get("name", ""),
                        "source": db_data.get("source", "上市"), 
                        "clauses": {},
                        "disposition": None,
                        "is_disposed": False,
                        "period": "",
                        "can_short": False,
                        "has_futures": False
                    }
                
                data = agg_data[code]
                if not isinstance(data, dict):
                    continue
                    
                # [Fix] 如果快取中的名稱是空的，用資料庫的名稱補上
                if not data.get("name") and db_data.get("name"):
                    data["name"] = db_data.get("name")
                    
                if "clauses" not in data:
                    data["clauses"] = {}
                    
                # 覆蓋或加入該日期的條款
                data["clauses"][date_str_mmdd] = clause_val
                total_loaded += 1
        
        conn.close()
        
        if total_loaded > 0:
            print(f"[Clauses] 已從資料庫載入過去 30 個交易日共 {total_loaded} 筆條款資料")
        
    except Exception as e:
        print(f"[Clauses] 從資料庫載入條款失敗: {e}")
        import traceback
        traceback.print_exc()
