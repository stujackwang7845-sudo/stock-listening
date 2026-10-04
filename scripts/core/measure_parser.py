import re

class MeasureParser:
    @staticmethod
    def parse_frequency(measure_str: str) -> str:
        """
        統一解析處置頻率，支援：
        1. 正常中文字串: "約每20分鐘撮合一次"
        2. 亂碼含特定格式: "C20X@" -> "20分"
        3. Shioaji API 格式: "5", "5分盤" -> "5分"
        """
        if not measure_str:
            return ""
            
        s = str(measure_str)
        # 移除不可見字元 (處理部分 \xef\xbf\xbd 等控制字元)
        s = "".join(c for c in s if c.isprintable()).strip()
        
        # 1. 處理亂碼英文代碼格式 (如 C10X@, C20X@, C25X@, C5X@, C60X@ 等)
        # 應對 \ufffdC20\ufffdX\ufffd@\ufffd 的情況
        m_code = re.search(r'[Cc][^\d]*(\d+)[^Xx]*[Xx][^@]*@', s)
        if m_code:
            return f"{m_code.group(1)}分"
            
        # 2. 處理直接傳入數字 (如 "5", "20")
        if s.isdigit():
            return f"{s}分"
            
        # 3. 處理 "5分盤" 或 "5分鐘" 等短格式
        m_short = re.search(r'^(\d+)分', s)
        if m_short:
            return f"{m_short.group(1)}分"
            
        # 4. 處理完整敘述: "約每20分鐘撮合一次" 或 "二十分鐘"
        # 轉換國字數字為阿拉伯數字以利統一判斷
        mapping = {
            "六十": "60", "四十五": "45", "二十五": "25", "二十": "20",
            "十五": "15", "十": "10", "五": "5", "二": "2"
        }
        for k, v in mapping.items():
            s = s.replace(k, v)

        # 尋找 "每XX分鐘" 或 "XX分鐘"
        m_full = re.search(r'(\d+)分', s)
        if m_full:
            val = m_full.group(1)
            # 確保提取的數字是合理的處置頻率
            # [2026-08-10 修法] 新增 "2" (約每2分鐘撮合一次，新制標準頻率)
            if val in ["2", "5", "10", "15", "20", "25", "45", "60"]:
                return f"{val}分"

        return ""

    # [2026-08-10 修法] 交易所「調整處置期間與撮合時間之原則」第(三)點：
    # 除變更交易方法、分盤方式交易之有價證券及管理股票外，其餘受處置有價證券之撮合時間，
    # 自8/10起「一律」改採約每2分鐘撮合一次——這是撮合引擎的即時系統參數變更，
    # 不分該處置案是何時公告的（不同於處置「期間」本身不會回溯縮短，見 disposal_database.py）。
    # 官方公告的歷史文字不會因此被改寫（仍停留在公告當下的舊頻率字樣），所以顯示層需要
    # 對「目前正在處置中」的股票，在參考日期 >= 8/10 時，把撮合頻率正規化成 2分。
    RULE_CHANGE_DATE = "2026-08-10"
    MATCHING_TIME_EXCLUDE_KEYWORDS = ("變更交易方法", "分盤", "管理股票")
    # 新制底下，「一般案件」只會是2分鐘；10/25/45/60分鐘這幾個值依規定只會出現在
    # 變更交易方法／分盤方式交易的例外情形（見交易所第六條修正對照表）。
    # 所以就算公告原文被截斷、抓不到關鍵字(曾發生在6225：DB裡的 measure 只剩"10分"，
    # reason 也只剩模板字串，關鍵字「變更交易方法」整段消失)，只要已經解析出來的頻率
    # 剛好是這幾個特殊值之一，也不該被覆寫成2分鐘——當作防呆的第二道防線。
    VARIANT_TRADING_METHOD_FREQUENCIES = ("10分", "25分", "45分", "60分")

    @staticmethod
    def get_effective_frequency(measure_str: str, ref_date_str: str) -> str:
        """回傳「依新制正規化後」實際應顯示的撮合頻率。"""
        parsed = MeasureParser.parse_frequency(measure_str)
        if not measure_str or not ref_date_str:
            return parsed
        if ref_date_str < MeasureParser.RULE_CHANGE_DATE:
            return parsed
        if parsed in MeasureParser.VARIANT_TRADING_METHOD_FREQUENCIES:
            return parsed
        s = str(measure_str)
        if any(k in s for k in MeasureParser.MATCHING_TIME_EXCLUDE_KEYWORDS):
            return parsed
        return "2分"

