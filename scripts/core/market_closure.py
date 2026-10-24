"""
不定期休市偵測(颱風等天災；2026-10-24 使用者要求。10/23 颱風休市，雲端 18:30 還把當天當交易日推播)。

證交所規則(https://www.twse.com.tw/zh/about/suspended_faq.html)：休市以臺北市為準——
  臺北市長宣布「當日」或「當日上午」全市公教機關停止上班 → 集中市場全日休市(櫃買同)；
  只宣布「下午」停止上班 → 照常開盤(收盤後其他交易停止)。
兩個來源：
  事前：人事行政總處「天然災害停止上班及上課情形」(DGPA_URL) 臺北市那一列，今天/明天停止上班 → 休市。
        前一晚 19:00～22:00 發布；當天凌晨風雨變大可在 04:30 前補發。
  事後：上市、上櫃官方日行情兩邊都沒資料(official_quotes.backfill → record_holiday，已存在)。
記錄到 twse_holidays.json 的 holidays(DateUtils 當休市)與 unscheduled(臨時休市；交易所公告的處置期間
沒扣掉這些天，要順延，見 notify_disposal_golden.postpone_for_closures)。
"""
import html as _html
import json
import os
import re
from datetime import date, datetime, timedelta

import requests

DGPA_URL = "https://www.dgpa.gov.tw/typh/daily/nds.html"


def _row_text(page, city="臺北市"):
    """DGPA 表格中某縣市那一列「是否停止上班上課情形」的文字；沒有那一列回傳 None。"""
    m = re.search(r"<FONT>\s*" + city + r"\s*</FONT>\s*</TD>(.*?)</TR>", page, re.S | re.I)
    if not m:
        return None
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    return re.sub(r"\s+", "", _html.unescape(t))


def _page_date(page):
    m = re.search(r"更新時間：\s*(\d{4})/(\d{1,2})/(\d{1,2})", page)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{2,3})年\s*(\d{1,2})月\s*(\d{1,2})日", page)
    if m:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    return None


def classify(segment):
    """
    一天的文字 → closed(全日或上午停止上班：休市) / open(照常、未達、只停課、只停下午或晚上) / unknown。
    例：「今天已達停止上班及上課標準。」closed；「今天上午照常上班、上課，下午停止上班、停止上課。」open；
    「今天未達停止上班及上課標準。」open；「明天停止上班、停止上課。」closed。
    """
    s = segment
    if not s:
        return "unknown"
    if "未達" in s:
        return "open"
    if "停止上班" not in s:
        return "open" if ("照常上班" in s or "停止上課" in s) else "unknown"
    if "上午停止上班" in s or "全日停止上班" in s or "全天停止上班" in s:
        return "closed"
    if re.search(r"(下午|晚間|晚上|夜間)停止上班", s):
        return "open"
    return "closed"


def parse_dgpa(page, city="臺北市"):
    """回傳 {日期: (closed/open/unknown, 原文)}；頁面日期抓不到回傳 {}。"""
    d0 = _page_date(page)
    if d0 is None:
        return {}
    row = _row_text(page, city)
    if row is None:
        return {d0: ("open", "（人事總處頁面沒有臺北市停班停課訊息）")}
    out = {}
    for word, off in (("今天", 0), ("明天", 1)):
        m = re.search(word + r"(.*?)(?=今天|明天|$)", row)
        if m:
            seg = word + m.group(1)
            out[d0 + timedelta(days=off)] = (classify(seg), seg)
    return out


def fetch_dgpa(timeout=20):
    r = requests.get(DGPA_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    r.encoding = "utf-8"
    return parse_dgpa(r.text)


def record_unscheduled(target, holidays_json):
    """臨時休市日寫進 twse_holidays.json(holidays＋unscheduled)，並更新這個行程裡的 DateUtils。回傳是否新增。"""
    from core.utils import DateUtils
    iso = target.strftime("%Y-%m-%d")
    data = {"holidays": []}
    if os.path.exists(holidays_json):
        with open(holidays_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    hol, uns = set(data.get("holidays", [])), set(data.get("unscheduled", []))
    added = iso not in uns
    hol.add(iso)
    uns.add(iso)
    data["holidays"], data["unscheduled"] = sorted(hol), sorted(uns)
    os.makedirs(os.path.dirname(os.path.abspath(holidays_json)), exist_ok=True)
    tmp = holidays_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, holidays_json)
    DateUtils.add_unscheduled(iso)
    return added


def check_and_record(today, holidays_json, log=print):
    """
    推播前呼叫：查人事總處，臺北市今天/明天(含以後)停止上班 → 記成臨時休市。
    回傳 {日期: (狀態, 原文)}(只含 today 以後的)；網路失敗回傳 None(呼叫端照舊，不當休市)。
    """
    try:
        info = fetch_dgpa()
    except Exception as e:
        log(f"[market_closure] 人事總處停班停課查詢失敗(照常視為交易日)：{e}")
        return None
    out = {}
    for d, (st, seg) in sorted(info.items()):
        if d < today:
            continue
        out[d] = (st, seg)
        if st == "closed" and d.weekday() < 5:
            if holidays_json is None:                     # dry-run：只更新這個行程
                from core.utils import DateUtils
                DateUtils.add_unscheduled(d.strftime("%Y-%m-%d"))
                log(f"[market_closure] 臺北市 {d} 停止上班（{seg}）→ 臨時休市(dry-run 不寫檔)")
            elif record_unscheduled(d, holidays_json):
                log(f"[market_closure] 臺北市 {d} 停止上班（{seg}）→ 記為臨時休市")
        elif st == "unknown":
            log(f"[market_closure] 臺北市 {d} 停班停課文字無法判讀：{seg}")
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for d, v in sorted(fetch_dgpa().items()):
        print(d, *v)
