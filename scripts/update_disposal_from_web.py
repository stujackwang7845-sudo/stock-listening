"""
從網頁自動更新處置資料到資料庫
"""

from core.fetcher import StockFetcher
from core.parser import StockParser
from core.disposal_database import DisposalDatabase
from datetime import datetime, timedelta

def update_disposal_from_web(target_year=None, progress_callback=None):
    """從 TWSE/TPEX 網站抓取處置資料並更新資料庫
    
    Args:
        target_year: 目標年度 (如 "2026")，若為 None 或 "全部" 則只抓今天
        progress_callback: 進度回調函數 callback(message)
    """
    
    def log(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)
    
    log("="*60)
    log("從網頁更新處置資料")
    log("="*60)
    
    fetcher = StockFetcher()
    parser = StockParser()
    db = DisposalDatabase("data/disposal_history.db")
    
    total_imported = 0
    
    # 決定要抓取的日期範圍
    if target_year and target_year != "全部":
        # 抓取整年度資料
        start_date = datetime(int(target_year), 1, 1)
        end_date = datetime.now()
        
        log(f"\n目標年度: {target_year}")
        log(f"抓取範圍: {start_date.strftime('%Y/%m/%d')} ~ {end_date.strftime('%Y/%m/%d')}")
        
        # 計算總天數
        total_days = (end_date - start_date).days + 1
        log(f"預計抓取 {total_days} 天的資料\n")
        
        current_date = start_date
        day_count = 0
        
        while current_date <= end_date:
            day_count += 1
            date_str = current_date.strftime("%Y%m%d")
            
            log(f"[{day_count}/{total_days}] 抓取 {current_date.strftime('%Y/%m/%d')} 的資料...")
            
            # 1. 上市處置
            twse_data = fetcher.fetch_twse_disposition(date_str)
            if twse_data:
                parsed_twse = parser.parse_twse_disposition(twse_data)
                if parsed_twse:
                    log(f"  上市: {len(parsed_twse)} 筆")
                    
                    for item in parsed_twse:
                        try:
                            period_start, period_end = db.parse_period(item.get('period', ''))
                            
                            db.conn.execute("""
                                INSERT OR REPLACE INTO disposal_records
                                (source, announce_date, code, name, period_start, period_end,
                                 period_raw, measure, reason, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """, (
                                "上市",
                                item.get('ann_date') or period_start,
                                item['code'],
                                item['name'],
                                period_start,
                                period_end,
                                item.get('period', ''),
                                item.get('measure', ''),
                                item.get('reason', '')
                            ))
                            total_imported += 1
                        except Exception as e:
                            log(f"  警告: 匯入失敗 - {e}")
                    
                    db.conn.commit()
            
            # 2. 上櫃處置 (每日都有可能公告，需每日抓取)
            # [Fix] Remove "Last Day Only" check. Fetch daily.
            # However, TPEX API often returns "Current Active List" rather than "Announced on Date".
            # But recent observation suggests historical query via Website Portal ("bulletin/disposal_information") might support date range?
            # fetcher.fetch_tpex_disposition uses "bulletin/disposal_information" (Web Portal) or "OpenAPI".
            # If using Web Portal with date range, we should be fine.
            # If using OpenAPI (List all active?), fetching daily is redundant but safe (idempotent).
            
            log(f"  上櫃: 抓取資料...")
            tpex_data = fetcher.fetch_tpex_disposition(date_str)
            if tpex_data:
                parsed_tpex = parser.parse_tpex_disposition(tpex_data)
                if parsed_tpex:
                    log(f"  上櫃: {len(parsed_tpex)} 筆")
                    
                    for item in parsed_tpex:
                        try:
                            # [Fix] TPEX items often miss Announce Date in OpenAPI, try use current date or Period Start?
                            # But parser extracts Date from response.
                            
                            period_start, period_end = db.parse_period(item.get('period', ''))
                            
                            db.conn.execute("""
                                INSERT OR REPLACE INTO disposal_records
                                (source, announce_date, code, name, period_start, period_end,
                                 period_raw, measure, reason, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """, (
                                "上櫃",
                                item.get('ann_date') or period_start,
                                item['code'],
                                item['name'],
                                period_start,
                                period_end,
                                item.get('period', ''),
                                item.get('measure', ''),
                                item.get('reason', '')
                            ))
                            total_imported += 1
                        except Exception as e:
                            log(f"  警告: 匯入失敗 - {e}")
                    
                    db.conn.commit()
            
            current_date += timedelta(days=1)
    
    else:
        # 只抓取今天的資料 (快速模式)
        today = datetime.now().strftime("%Y%m%d")
        log("\n快速模式: 只抓取今天的資料\n")
        
        # 1. 上市處置
        log("正在抓取上市處置資料...")
        twse_data = fetcher.fetch_twse_disposition(None)  # None = 最新
        parsed_twse = []
        if twse_data:
            parsed_twse = parser.parse_twse_disposition(twse_data)
            
        if not parsed_twse:
            try:
                from core.shioaji_client import ShioajiClient
                sj = ShioajiClient()
                punish_df = sj.get_punish()
                if punish_df is not None and not punish_df.empty:
                    log("  TWSE API 無資料，使用 Shioaji 補充上市處置股...")
                    for _, row in punish_df.iterrows():
                        p_code = str(row.get("code", "")).strip()
                        source = fetcher.check_market_type(p_code)
                        if source != "上市": continue
                        
                        s_dt = row.get("start_date")
                        e_dt = row.get("end_date")
                        period = ""
                        if s_dt and e_dt:
                            try:
                                s_obj = datetime.strptime(str(s_dt), "%Y-%m-%d")
                                e_obj = datetime.strptime(str(e_dt), "%Y-%m-%d")
                                period = f"{s_obj.year-1911}/{s_obj.strftime('%m/%d')}~{e_obj.year-1911}/{e_obj.strftime('%m/%d')}"
                            except Exception: pass
                            
                        interval = row.get("interval", "")
                        measure = f"{interval}分盤" if interval else "處置中"
                        
                        parsed_twse.append({
                            'code': p_code,
                            'name': p_code, # 缺名稱
                            'ann_date': s_dt, # 用 start_date 暫時代替 announce_date
                            'period': period,
                            'measure': measure,
                            'reason': "Shioaji 補充"
                        })
            except Exception as e:
                log(f"  Shioaji 補充失敗: {e}")

        if parsed_twse:
            log(f"  抓到 {len(parsed_twse)} 筆上市處置資料")
            
            for item in parsed_twse:
                try:
                    period_start, period_end = db.parse_period(item.get('period', ''))
                    
                    db.conn.execute("""
                        INSERT OR REPLACE INTO disposal_records
                        (source, announce_date, code, name, period_start, period_end,
                         period_raw, measure, reason, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        "上市",
                        item.get('ann_date') or period_start,
                        item['code'],
                        item['name'],
                        period_start,
                        period_end,
                        item.get('period', ''),
                        item.get('measure', ''),
                        item.get('reason', '')
                    ))
                    total_imported += 1
                except Exception as e:
                    log(f"  警告: 匯入失敗 - {e}")
            
            db.conn.commit()
        
        # 2. 上櫃處置
        log("\n正在抓取上櫃處置資料...")
        tpex_data = fetcher.fetch_tpex_disposition(today)
        if tpex_data:
            parsed_tpex = parser.parse_tpex_disposition(tpex_data)
            log(f"  抓到 {len(parsed_tpex)} 筆上櫃處置資料")
            
            for item in parsed_tpex:
                try:
                    period_start, period_end = db.parse_period(item.get('period', ''))
                    
                    db.conn.execute("""
                        INSERT OR REPLACE INTO disposal_records
                        (source, announce_date, code, name, period_start, period_end,
                         period_raw, measure, reason, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        "上櫃",
                        item.get('ann_date') or period_start,
                        item['code'],
                        item['name'],
                        period_start,
                        period_end,
                        item.get('period', ''),
                        item.get('measure', ''),
                        item.get('reason', '')
                    ))
                    total_imported += 1
                except Exception as e:
                    log(f"  警告: 匯入失敗 - {e}")
            
            db.conn.commit()
    
    db.close()
    
    log(f"\n更新完成！共匯入/更新 {total_imported} 筆處置資料")
    return total_imported

if __name__ == "__main__":
    update_disposal_from_web()
