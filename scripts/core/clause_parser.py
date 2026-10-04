"""
條款解析工具

提供注意條款的解析功能，支援中文數字和阿拉伯數字格式
"""

import re
from typing import List


class ClauseNumberParser:
    """解析注意條款編號"""

    # 中文數字對照表
    # [Fix 2026-09-07] 原本只到十二，導致官方公告原文裡明明白白寫著「第十三款」
    # （當日沖銷成交量占比過高，是判斷處置5天/7天的關鍵依據）跟「第十四款」
    # 全部被靜默丟棄，從來沒有進過 attention_clauses 表。查證：臺灣證券交易所
    # 「公布或通知注意交易資訊暨處置作業要點」第四條第一項共列至第十四款，
    # 不是只有到十二款；且第十三款的公告文字在 TWSE/TPEX 官方 notice API 裡
    # 本來就一直存在(NoticeFetcher 抓下來的 reason 原文早就有「﹝第十三款﹞」，
    # 問題只出在這支解析器的正則跟對照表沒有涵蓋到，不是抓取端漏抓)。
    CHINESE_NUM = {
        '一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8,
        '九': 9, '十': 10, '十一': 11, '十二': 12,
        '十三': 13, '十四': 14
    }

    @staticmethod
    def parse(reason: str) -> List[int]:
        """
        從 reason 字串中解析條款編號（1-14 款）

        支援格式：
        - 中文數字：第一款、第二款...第十四款
        - 阿拉伯數字：第1款、第2款...第14款
        - 混合：第二款及第4款

        Args:
            reason: 原始 reason 字串

        Returns:
            條款編號列表（已排序且去重），例如 [2, 4, 7]
        """
        if not reason:
            return []

        clauses = set()

        # Pattern 1: 第X款（中文數字，支援一~十四）
        # [Fix] 雙字中文數字(十一~十四)要排在單字的「十」前面比對，避免「十三」被
        # 貪婪比對成「十」就提早結束、漏看後面的「三」。
        pattern1 = r'第(十一|十二|十三|十四|[一二三四五六七八九十])款'
        for match in re.finditer(pattern1, reason):
            chinese = match.group(1)
            if chinese in ClauseNumberParser.CHINESE_NUM:
                clauses.add(ClauseNumberParser.CHINESE_NUM[chinese])

        # Pattern 2: 第N款（阿拉伯數字）
        pattern2 = r'第(\d+)款'
        for match in re.finditer(pattern2, reason):
            num = int(match.group(1))
            if 1 <= num <= 14:
                clauses.add(num)

        return sorted(list(clauses))


if __name__ == "__main__":
    # 測試
    test_cases = [
        "第一款",
        "第2款",
        "第二款及第4款",
        "第9款",  # 應該被過濾
        "第1款、第3款、第5款",
        "第一款、第二款、第三款、第四款、第五款、第六款、第七款、第八款",
        "第9款及第10款",  # 應該都被過濾
        "",  # 空字串
        "無條款",  # 無法解析
    ]
    
    print("="*60)
    print("條款解析測試")
    print("="*60)
    
    for test in test_cases:
        result = ClauseNumberParser.parse(test)
        print(f"輸入: {test!r:50s} => 輸出: {result}")
