"""
股票搜尋功能 - 歷史資料抓取器
提供搜尋個股過去N天的注意條款資料
"""
import datetime as dt
from core.fetcher import StockFetcher
from core.parser import StockParser
from core.utils import ClauseParser, DateUtils


class StockSearchHelper:
    """搜尋功能輔助類別"""
    
    @staticmethod
    def fetch_stock_history(stock_code, target_date, days=10):
        """
        抓取個股過去N天的歷史條款資料
        
        Args:
            stock_code: 股票代碼
            target_date: 目標日期（datetime對象）
            days: 往前抓取的天數（預設10天）
            
        Returns:
            dict: {
                "name": "股票名稱",
                "source": "上市/上櫃",
                "clauses": {"mm/dd": "條款內容", ...}
            }
            如果查無資料則返回 None
        """
        fetcher = StockFetcher()
        parser = StockParser()
        
        # 建立過去N天的日期列表
        days_to_fetch = []
        curr = target_date
        count = 0
        
        while count < days:
            if DateUtils.is_trading_day(curr):
                days_to_fetch.append(curr)
                count += 1
            curr = curr - dt.timedelta(days=1)
        
        days_to_fetch.reverse()  # 從舊到新排序
        
        # 抓取每一天的資料
        clauses = {}
        stock_name = ""
        stock_source = ""
        
        for day in days_to_fetch:
            day_str = day.strftime("%Y%m%d")
            display_date = day.strftime("%m/%d")
            
            try:
                # 嘗試從注意股票資料中查找
                twse_att = fetcher.fetch_twse_attention(day_str)
                tpex_att = fetcher.fetch_tpex_attention(day_str)
                
                all_items = []
                if twse_att:
                    all_items.extend(parser.parse_twse_attention(twse_att))
                if tpex_att:
                    all_items.extend(parser.parse_tpex_attention(tpex_att))
                
                # 查找目標個股
                for item in all_items:
                    if item.get('code') == stock_code:
                        raw_reason = str(item.get('reason', ''))
                        clause = ClauseParser.parse_clauses(raw_reason)
                        
                        if not stock_name:
                            stock_name = item.get('name', '')
                            stock_source = "上市" if item.get('source') == 'TWSE' else "上櫃"
                        
                        if clause:  # 只記錄有條款的日期
                            clauses[display_date] = clause
                        break
            
            except Exception as e:
                print(f"DEBUG: 抓取 {day_str} 資料失敗: {e}")
                continue
        
        # 如果找到任何資料，返回結果
        if clauses or stock_name:
            return {
                "name": stock_name or stock_code,
                "source": stock_source or "未知",
                "clauses": clauses
            }
        
        return None
    
    @staticmethod
    def format_search_result(stock_code, stock_data):
        """
        格式化搜尋結果為顯示用的列表
        
        Args:
            stock_code: 股票代碼
            stock_data: fetch_stock_history 返回的資料
            
        Returns:
            list: [(標籤, 值), ...] 用於 InfoBox 顯示
        """
        if not stock_data:
            return []
        
        name = stock_data.get("name", "")
        source = stock_data.get("source", "")
        clauses = stock_data.get("clauses", {})
        
        display_items = []
        display_items.append(("股票代碼", stock_code))
        display_items.append(("股票名稱", name))
        display_items.append(("類別", source))
        
        if clauses:
            # 按日期排序
            sorted_dates = sorted(clauses.keys())
            clause_lines = [f"{date}: {clauses[date]}" for date in sorted_dates]
            clause_str = "\n".join(clause_lines)
            display_items.append(("注意條款歷史", clause_str))
            display_items.append(("資料天數", f"{len(clauses)} 天"))
        else:
            display_items.append(("注意條款歷史", "查無資料"))
        
        return display_items
