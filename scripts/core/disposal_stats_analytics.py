"""處置股統計分析共用模組。

這個模組負責：
- 從 `data/disposal_history.db` 讀取處置事件
- 從日 K parquet 資料對齊交易日並計算各階段報酬
- 產生 5 分盤 / 20 分盤的統計報表資料
- 提供 UI 與批次報表共用的 DataFrame 輸出
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Iterable, Optional
import json
import math
import sqlite3

import pandas as pd
import pyarrow.parquet as pq

from core.measure_parser import MeasureParser
from core.utils import DateUtils

DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[2]
from core.runtime import data_dir_override, get_paths
DEFAULT_DB_PATH = Path(get_paths().disposal_db) if data_dir_override() else DEFAULT_PROJECT_DIR / "data" / "disposal_history.db"
DEFAULT_REPORT_DIR = DEFAULT_PROJECT_DIR / "reports"
DEFAULT_PRICE_ROOT = Path(r"E:\Vibe Coding\Stock\DB\parquet_data\price_daily_raw")
SUPPORTED_INTERVALS = (5, 20, 2)
DEFAULT_POST_DAYS = 1
DEFAULT_MIN_SAMPLE = 15
DEFAULT_MAX_T = 12

# [2026-08-10 修法] 處置新制：撮合頻率統一為約2分鐘，改用「處置天數(5天/7天)」與
# 「初犯/累犯」細分「2分」這個頻率桶，取代舊制用來區分 5分/20分 的撮合頻率分桶
# （新制底下幾乎所有案件都是2分鐘，原本的頻率分桶已經沒有鑑別度）。
# 判定基準與 forecast_page.py 的 ForecastWorker._predict_exact_disposal_frequency()
# 完全一致：以「最近30個營業日內是否已有其他處置紀錄」判斷初犯/累犯。
RULE_CHANGE_DATE = date(2026, 8, 10)
DURATION_OFFENSE_KEYS: tuple[str, ...] = ("5天_初犯", "5天_累犯", "7天_初犯", "7天_累犯")

# frames/event_counts 的 dict key：舊制頻率分鐘數(int，如 5、20、2)或新制天數/
# 初犯累犯分桶(str，DURATION_OFFENSE_KEYS 裡的值)，兩種型別會混用在同一個 dict 裡。
BucketKey = int | str


@dataclass(frozen=True, slots=True)
class DisposalEvent:
    """單筆處置事件。"""

    code: str
    name: str
    period_start: date
    period_end: date
    interval_min: int


@dataclass(frozen=True, slots=True)
class DisposalStatsDataset:
    """統計資料集。

    frames/event_counts 的 key 除了舊制的頻率分鐘數(int，如 5、20、2)之外，
    2026-08-10 新制還會額外產生 DURATION_OFFENSE_KEYS 這幾組字串 key
    （例如 "5天_初犯"），代表在「2分」規則底下依處置天數與初犯/累犯進一步細分的子分桶。
    """

    frames: dict[BucketKey, pd.DataFrame]
    event_counts: dict[BucketKey, tuple[int, int]]
    generated_at: datetime
    source: str
    window_days: Optional[int] = None

    def interval_frame(self, interval_min: BucketKey) -> pd.DataFrame:
        return self.frames.get(interval_min, pd.DataFrame()).copy()


def clean_code(code: object) -> str:
    """標準化股票代號。"""

    text = str(code or "").strip().upper()
    text = text.replace(".TW", "").replace(".TWO", "")
    if text.endswith(".0"):
        text = text[:-2]
    return text


def parse_event_date(value: object) -> Optional[date]:
    """把 DB 內各種日期字串轉成 date。"""

    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime().date()


def classify_interval(measure: object, reason: object) -> Optional[int]:
    """從處置說明判斷 5 分盤 / 20 分盤。"""

    text = f"{measure or ''} {reason or ''}".strip()
    if not text:
        return None

    freq = MeasureParser.parse_frequency(text)
    if not freq.endswith("分"):
        return None

    try:
        value = int(freq[:-1])
    except ValueError:
        return None

    return value if value in SUPPORTED_INTERVALS else None


def load_events(
    db_path: Path | str = DEFAULT_DB_PATH,
    intervals: Iterable[int] = SUPPORTED_INTERVALS,
    window_days: Optional[int] = None,
    as_of: Optional[date] = None,
) -> dict[int, list[DisposalEvent]]:
    """從處置資料庫載入並去重事件。"""

    interval_order = [interval for interval in intervals if interval in SUPPORTED_INTERVALS]
    interval_set = set(interval_order)
    cutoff_date = None
    if window_days is not None:
        cutoff_date = (as_of or datetime.now().date()) - timedelta(days=window_days)
    grouped: dict[int, dict[tuple[str, date, date], DisposalEvent]] = {
        interval: {} for interval in interval_order
    }

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT code, name, period_start, period_end, measure, reason
            FROM disposal_records
            """
        )
        for code, name, period_start, period_end, measure, reason in cursor.fetchall():
            interval = classify_interval(measure, reason)
            if interval not in interval_set:
                continue

            # [Fix 2026-08-25] 排除權證/次順位股等非4碼衍生代碼(如認購權證
            # "039038"、股票"XX一/二/三"次類別"24552"/"49793")。這些代碼會跟
            # 母股同一次處置事件一起出現在 disposal_records，但它們在
            # load_prices() 讀的日K parquet 裡幾乎都沒有自己的價格序列，個別事件
            # 本身就會在 aggregate_interval() 被 continue 跳過，不會拉低任何母股
            # 自己的每階段樣本數(dual-review 已確認排除前後同一批母股事件的
            # used 數不變)。這裡要濾掉的實際理由是：這些代碼會虛增 total_events，
            # 讓「有效事件數 X（總分類 Y）」這行顯示的 used/total 比例失真，也讓
            # load_prices() 白白多查一批注定查不到資料的股票代號。跟專案其他地方
            # (dashboard.py/forecast_page.py 篩選 agg_data 代碼)用同一套「只留
            # 4碼」規則；代價是連帶濾掉少數真的有價格資料的6碼代碼(如DR存託憑證)，
            # 這點跟 dashboard.py 用股票名稱關鍵字明確排除DR的既有慣例一致，
            # 不是這次新引入的取捨。
            clean = clean_code(code)
            if len(clean) != 4:
                continue

            start_dt = parse_event_date(period_start)
            end_dt = parse_event_date(period_end)
            if start_dt is None or end_dt is None or end_dt < start_dt:
                continue
            if cutoff_date is not None and start_dt < cutoff_date:
                continue

            event = DisposalEvent(
                code=clean,
                name=str(name or "").strip(),
                period_start=start_dt,
                period_end=end_dt,
                interval_min=interval,
            )
            key = (event.code, event.period_start, event.period_end)
            grouped.setdefault(interval, {})
            grouped[interval].setdefault(key, event)
    finally:
        conn.close()

    return {interval: list(grouped.get(interval, {}).values()) for interval in interval_order}


def load_all_period_starts_by_code(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, list[date]]:
    """讀取資料庫中每檔股票「所有」處置紀錄的起始日（不限頻率分桶），
    供初犯/累犯判定的30個營業日回顧使用——回顧必須看該股全部處置歷史，
    不能只看已經被分類進某個頻率桶的事件（例如上一次是舊制20分處置，
    這次是新制2分處置，仍然要算累犯）。"""

    result: dict[str, list[date]] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT code, period_start FROM disposal_records")
        for code, period_start in cursor.fetchall():
            start_dt = parse_event_date(period_start)
            if start_dt is None:
                continue
            result.setdefault(clean_code(code), []).append(start_dt)
    finally:
        conn.close()

    for code_key, starts in result.items():
        result[code_key] = sorted(set(starts))
    return result


def compute_duration_days(period_start: date, period_end: date) -> int:
    """計算處置期間的營業日數（含頭尾兩端）。"""

    count = 0
    cursor_date = period_start
    while cursor_date <= period_end:
        if DateUtils.is_trading_day(cursor_date):
            count += 1
        cursor_date += timedelta(days=1)
    return count


def _rolling_30td_window_start(period_start: date) -> date:
    """回傳「以 period_start 為窗口最後一天」往前算，含 period_start 本身在內
    剛好30個營業日的窗口起始日。跟 forecast_page.py 的
    ForecastWorker._predict_exact_disposal_frequency() 用同一套算法：正常情況下
    period_start 本身就是交易日(占窗口第30天)，只需再往前找29天。
    [Fix] 但如果 period_start 因資料異常剛好不是交易日(正常公告不會發生，
    純防禦寫壞資料)，它就不能算進30天裡，這裡改成先判斷 period_start 是否為
    交易日決定起始計數，避免這種情況下窗口少算一天。"""

    trading_days_back = 1 if DateUtils.is_trading_day(period_start) else 0
    check_date = period_start
    while trading_days_back < 30:
        check_date -= timedelta(days=1)
        if DateUtils.is_trading_day(check_date):
            trading_days_back += 1
    return check_date


def classify_offense(code: str, period_start: date, all_periods_by_code: dict[str, list[date]]) -> str:
    """判斷初犯／累犯：最近30個營業日內(不含本次，但含當次視窗最後一天)
    是否已有「其他」處置紀錄。"""

    window_start = _rolling_30td_window_start(period_start)
    prior_periods = all_periods_by_code.get(code, [])
    has_prior = any(window_start <= prior < period_start for prior in prior_periods)
    return "累犯" if has_prior else "初犯"


def bucket_by_duration_offense(
    interval_2_events: list[DisposalEvent],
    all_periods_by_code: dict[str, list[date]],
) -> dict[str, list[DisposalEvent]]:
    """把「2分」規則(2026-08-10新制)的事件，依處置天數(5天/7天)與初犯/累犯
    進一步分成 DURATION_OFFENSE_KEYS 這幾組子分桶。"""

    buckets: dict[str, list[DisposalEvent]] = {key: [] for key in DURATION_OFFENSE_KEYS}
    for event in interval_2_events:
        if event.period_start < RULE_CHANGE_DATE:
            # 理論上不該發生(2分只在新制後才會出現)，保險起見仍排除，
            # 避免舊制資料因巧合天數相同而誤分桶。
            continue

        duration = compute_duration_days(event.period_start, event.period_end)
        if duration == 5:
            duration_label = "5天"
        elif duration == 7:
            duration_label = "7天"
        else:
            continue  # 非標準新制天數(例如假日順延造成的特殊案例)，不納入新分桶

        offense = classify_offense(event.code, event.period_start, all_periods_by_code)
        buckets[f"{duration_label}_{offense}"].append(event)

    return buckets


def load_prices(
    symbols: Iterable[str],
    parquet_root: Path | str = DEFAULT_PRICE_ROOT,
) -> dict[str, pd.DataFrame]:
    """載入指定股票的日 K 資料。"""

    symbol_set = {clean_code(symbol) for symbol in symbols if str(symbol).strip()}
    if not symbol_set:
        return {}

    root = Path(parquet_root)
    if not root.exists():
        raise FileNotFoundError(f"找不到日 K parquet 目錄: {root}")

    frames: list[pd.DataFrame] = []
    for parquet_file in sorted(root.rglob("data.parquet")):
        try:
            table = pq.ParquetFile(parquet_file).read(columns=["date", "symbol", "open", "close"])
            frame = table.to_pandas()
        except Exception:
            continue

        if frame.empty or "symbol" not in frame.columns:
            continue

        frame["symbol"] = frame["symbol"].astype(str).map(clean_code)
        frame = frame[frame["symbol"].isin(symbol_set)]
        if frame.empty:
            continue

        frames.append(frame)

    if not frames:
        return {}

    merged = pd.concat(frames, ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    merged["open"] = pd.to_numeric(merged["open"], errors="coerce")
    merged["close"] = pd.to_numeric(merged["close"], errors="coerce")
    merged = merged.dropna(subset=["date", "open", "close"])
    merged = merged[(merged["open"] > 0) & (merged["close"] > 0)]

    result: dict[str, pd.DataFrame] = {}
    for symbol, group in merged.groupby("symbol"):
        group = group.sort_values("date").drop_duplicates("date", keep="last")
        group = group.set_index("date")[["open", "close"]]
        group.index = pd.to_datetime(group.index)
        result[symbol] = group

    return result


def _build_stage_labels(max_t: int, post_days: int) -> list[str]:
    labels = ["T-1"]
    labels.extend(f"T{i}" for i in range(1, max_t + 1))
    labels.append("Exit")
    labels.extend(f"Exit+{i}" for i in range(1, post_days + 1))
    return labels


def _record_metrics(row: pd.Series) -> Optional[dict[str, float | bool]]:
    open_price = row.get("open")
    close_price = row.get("close")
    prev_close = row.get("prev_close")

    if open_price is None or close_price is None or prev_close is None:
        return None

    try:
        open_value = float(open_price)
        close_value = float(close_price)
        prev_close_value = float(prev_close)
    except (TypeError, ValueError):
        return None

    if open_value <= 0 or close_value <= 0 or prev_close_value <= 0:
        return None

    return {
        "return_pct": (close_value - prev_close_value) / prev_close_value * 100.0,
        "body_pct": (close_value - open_value) / open_value * 100.0,
        "red": close_value > open_value,
        "black": close_value < open_value,
        "flat": math.isclose(close_value, open_value, rel_tol=0.0, abs_tol=1e-9),
    }


def compute_event(
    event: DisposalEvent,
    prices: pd.DataFrame,
    post_days: int = DEFAULT_POST_DAYS,
    max_t: int = DEFAULT_MAX_T,
) -> Optional[dict[str, dict[str, float | bool]]]:
    """計算單一處置事件在各階段的統計值。

    [Fix] T1..Tn 的天數編號改成沿著「實際日曆 + 交易日曆」逐日往前推算，
    不再用 prices 裡的資料列位置差計算。原本用位置差的寫法有個問題：只要
    某檔股票在處置期間「中段」剛好缺一天K棒資料(例如6213在8/11~8/17這段
    處置期間，8/14那天的日K資料整個不存在——不是假日，是資料本身缺漏)，
    後面所有天數就會全部往前錯位一格，把「處置第5天」的資料誤標成
    「處置第4天」，導致T5永遠算不出來、T4的統計還混進了本來屬於T5的資料。
    改成逐日照交易日曆走、依實際日期查價格資料，缺資料的那一天就單純跳過，
    不會影響後面天數的正確編號。
    """

    if prices.empty:
        return None

    dates = pd.DatetimeIndex(prices.index)
    start_ts = pd.Timestamp(event.period_start)

    start_idx = dates.searchsorted(start_ts, side="left")
    if start_idx >= len(dates):
        return None

    # [Fix 2026-08-25] T-1 的目標日期改成跟 T1..Tn/Exit 一樣，先用交易日曆往前推算
    # 實際日曆日期，再依日期查價格(add_by_date)，不要用 start_idx - 1 這種在 prices
    # 裡的位置差。原因跟上面 T1..Tn 那段 [Fix] 完全一樣：如果 period_start 前一個
    # 交易日剛好缺K棒資料(資料源缺漏，不是假日)，start_idx - 1 會直接跳到更早一天
    # 的資料列，把「處置前2天」誤標成「處置前1天(T-1)」。
    t_minus_1_date = event.period_start - timedelta(days=1)
    while not DateUtils.is_trading_day(t_minus_1_date):
        t_minus_1_date -= timedelta(days=1)

    out: dict[str, dict[str, float | bool]] = {}

    def add_by_index(label: str, idx: int) -> None:
        if idx < 1 or idx >= len(dates):
            return

        row = prices.iloc[idx].copy()
        row["prev_close"] = prices.iloc[idx - 1]["close"]
        metrics = _record_metrics(row)
        if metrics is not None:
            out[label] = metrics

    def add_by_date(label: str, target_date: date) -> None:
        """依實際日期(而非在 prices 裡的位置序號)查找該日資料，缺資料就跳過。"""
        ts = pd.Timestamp(target_date)
        idx = dates.searchsorted(ts, side="left")
        if idx >= len(dates) or dates[idx] != ts:
            return
        add_by_index(label, idx)

    add_by_date("T-1", t_minus_1_date)

    cursor_date = event.period_start
    trading_day_count = 0
    while cursor_date <= event.period_end:
        if DateUtils.is_trading_day(cursor_date):
            trading_day_count += 1
            if trading_day_count <= max_t:
                label = "T1" if trading_day_count == 1 else f"T{trading_day_count}"
                add_by_date(label, cursor_date)
        cursor_date += timedelta(days=1)

    exit_date = event.period_end + timedelta(days=1)
    while not DateUtils.is_trading_day(exit_date):
        exit_date += timedelta(days=1)
    add_by_date("Exit", exit_date)

    post_date = exit_date
    for offset in range(1, post_days + 1):
        steps = 0
        while steps < 1:
            post_date += timedelta(days=1)
            if DateUtils.is_trading_day(post_date):
                steps += 1
        add_by_date(f"Exit+{offset}", post_date)

    return out or None


def aggregate_interval(
    events: list[DisposalEvent],
    prices: dict[str, pd.DataFrame],
    *,
    post_days: int = DEFAULT_POST_DAYS,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    max_t: int = DEFAULT_MAX_T,
) -> tuple[pd.DataFrame, int, int]:
    """彙總單一處置頻率的階段統計。"""

    buckets: dict[str, list[dict[str, float | bool]]] = {}
    used_events = 0
    total_events = len(events)

    for event in events:
        price_frame = prices.get(event.code)
        if price_frame is None or price_frame.empty:
            continue

        event_metrics = compute_event(event, price_frame, post_days=post_days, max_t=max_t)
        if not event_metrics:
            continue

        used_events += 1
        for label, metrics in event_metrics.items():
            buckets.setdefault(label, []).append(metrics)

    rows: list[dict[str, object]] = []
    for label in _build_stage_labels(max_t, post_days):
        records = buckets.get(label, [])
        if len(records) < min_sample:
            continue

        # [Fix 2026-09-04] min_sample=0 呼叫端(見 DURATION_OFFENSE_KEYS 子分桶)代表
        # 「不管筆數，有多少顯示多少」，此時 records 可能真的是空的(例如處置才剛開始，
        # +7 那天還沒發生)——不能直接往下算平均，len(records)==0 會除以零。這種情況
        # 顯示「樣本數0、其餘欄位0」，而不是整列消失或當掉。
        if not records:
            rows.append(
                {
                    "時間點": label,
                    "樣本數": 0,
                    "平均漲跌幅%": 0.0,
                    "平均開收差幅%": 0.0,
                    "上漲機率%": 0.0,
                    "紅K機率%": 0.0,
                    "黑K機率%": 0.0,
                    "平K機率%": 0.0,
                }
            )
            continue

        returns = [float(rec["return_pct"]) for rec in records]
        bodies = [float(rec["body_pct"]) for rec in records]
        red_count = sum(1 for rec in records if bool(rec["red"]))
        black_count = sum(1 for rec in records if bool(rec["black"]))
        flat_count = sum(1 for rec in records if bool(rec["flat"]))

        rows.append(
            {
                "時間點": label,
                "樣本數": len(records),
                "平均漲跌幅%": round(sum(returns) / len(returns), 2),
                "平均開收差幅%": round(sum(bodies) / len(bodies), 2),
                "上漲機率%": round(sum(1 for value in returns if value > 0) / len(returns) * 100.0, 1),
                "紅K機率%": round(red_count / len(records) * 100.0, 1),
                "黑K機率%": round(black_count / len(records) * 100.0, 1),
                "平K機率%": round(flat_count / len(records) * 100.0, 1),
            }
        )

    # [Fix] 就算完全沒有任何 stage 達到 min_sample(常發生在新制上路不久、樣本數
    # 還很少的天數/初犯累犯子分桶)，也要保留欄位名稱，不能變成完全沒有欄位的
    # 空 DataFrame——否則存成 CSV 會是零位元組檔案，之後讀取快取時
    # required_columns 檢查會失敗，導致整份快取(含其他有資料的分桶)被判定無效。
    columns = ["時間點", "樣本數", "平均漲跌幅%", "平均開收差幅%", "上漲機率%", "紅K機率%", "黑K機率%", "平K機率%"]
    frame = pd.DataFrame(rows, columns=columns)
    return frame, used_events, total_events


def build_disposal_stats_dataset(
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    parquet_root: Path | str = DEFAULT_PRICE_ROOT,
    post_days: int = DEFAULT_POST_DAYS,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    max_t: int = DEFAULT_MAX_T,
    force_refresh: bool = False,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    window_days: Optional[int] = None,
) -> DisposalStatsDataset:
    """建立處置統計資料集，優先使用快取，必要時重算。"""

    report_dir_path = Path(report_dir)
    cached = load_cached_report_frames(report_dir_path) if window_days is None else None
    if cached is not None and not force_refresh:
        return cached

    events = load_events(db_path=db_path, window_days=window_days)
    symbols = {event.code for bucket in events.values() for event in bucket}

    # [2026-08-10 修法] 把「2分」頻率桶內的事件，再依處置天數(5/7天)與初犯/累犯
    # 細分。初犯/累犯判定要看該股「全部」處置歷史，不受 window_days 篩選影響，
    # 所以另外查一次不設篩選條件的完整起始日清單。
    duration_offense_buckets: dict[str, list[DisposalEvent]] = {}
    interval_2_events = events.get(2, [])
    if interval_2_events:
        all_periods_by_code = load_all_period_starts_by_code(db_path=db_path)
        duration_offense_buckets = bucket_by_duration_offense(interval_2_events, all_periods_by_code)
        symbols |= {event.code for bucket in duration_offense_buckets.values() for event in bucket}

    prices = load_prices(symbols, parquet_root=parquet_root)

    frames: dict[BucketKey, pd.DataFrame] = {}
    counts: dict[BucketKey, tuple[int, int]] = {}
    for interval, interval_events in events.items():
        # [Fix 2026-09-04] 「2分(新制)」母桶混合了5天跟7天兩種處置事件——5天事件
        # 到T5就結束、T6起完全沒有資料，7天事件則一路到T7才結束。母桶要能顯示到
        # 兩者之中較長的T7，但T6/T7的樣本數只由「7天事件」撐著(遠比5天事件少)，
        # 用舊制沿用的 min_sample=15 門檻會被整列砍掉，導致母桶的圖表停在T5，
        # 看起來像是完全沒有更後面的資料，實際上是有、只是被門檻擋掉了。改成
        # 「2分」母桶不設最低樣本數門檻、且只算到T7(T8以後不論哪種事件都不可能
        # 有資料，沒必要顯示恆為0的欄位)。5分/20分(舊制)資料量充足，不受影響，
        # 維持原本傳入的 min_sample 與 max_t。
        if interval == 2:
            bucket_min_sample = 0
            bucket_max_t = 7
        else:
            bucket_min_sample = min_sample
            bucket_max_t = max_t

        frame, used, total = aggregate_interval(
            interval_events,
            prices,
            post_days=post_days,
            min_sample=bucket_min_sample,
            max_t=bucket_max_t,
        )
        frames[interval] = frame
        counts[interval] = (used, total)

    # [Fix 2026-09-04] 使用者明確要求：5天/7天 初犯/累犯這幾個新制子分桶，不管樣本數
    # 多寡都要顯示進統計，天數還沒到的欄位顯示0即可，不要整列因為沒過 min_sample 就
    # 消失(原本 DEFAULT_MIN_SAMPLE_NEW_REGIME=8 的機制正是造成「7天初犯/累犯常常整個
    # 分桶沒資料」的原因——8月10日新制才上路，7天的處置本來就比5天少見，事件數更難
    # 湊到8筆)。改成 min_sample=0，搭配 aggregate_interval() 新增的「records 為空時
    # 輸出0而非整列跳過」邏輯，讓每個時間點(不論筆數，即使是0筆)都會出現在表格裡；
    # 使用者可以直接看「樣本數」欄位判斷這筆統計背後有多少事件撐著，不會被誤導。
    #
    # [Fix 2026-09-04 之二] max_t 改成用每個子分桶自己的天數(bucket_key 開頭的數字，
    # 例如"7天_初犯"→7)，不再統一用外層傳入的 max_t(=12)。5天/7天處置在結構上
    # 就只有那麼多天，T(duration+1)以後不是「筆數不足」而是「這個天數的處置根本
    # 不存在第duration+1天」，永遠是0，顯示出來只會造成「這是不是還沒補齊資料」的
    # 誤解。7天分桶只算到T7，5天分桶只算到T5，沒有T8~T12這些不可能存在的欄位。
    for bucket_key, bucket_events in duration_offense_buckets.items():
        try:
            bucket_duration = int(bucket_key.split("天", 1)[0])
        except (ValueError, IndexError):
            bucket_duration = max_t

        frame, used, total = aggregate_interval(
            bucket_events,
            prices,
            post_days=post_days,
            min_sample=0,
            max_t=bucket_duration,
        )
        frames[bucket_key] = frame
        counts[bucket_key] = (used, total)

    dataset = DisposalStatsDataset(
        frames=frames,
        event_counts=counts,
        generated_at=datetime.now(),
        source="computed",
        window_days=window_days,
    )
    if window_days is None:
        save_disposal_stats_reports(dataset, report_dir=report_dir_path)
    return dataset


def _frame_key_to_str(key: BucketKey) -> str:
    """frames/event_counts 的 dict key 轉成 meta.json / 檔名可用的字串。"""

    return str(key)


def _frame_key_from_str(key_str: str) -> BucketKey:
    """把 meta.json 存的字串 key 還原成原本的 key 型別
    （純數字 -> 舊制頻率分鐘數 int；其他 -> 新制的 DURATION_OFFENSE_KEYS 字串）。"""

    return int(key_str) if key_str.isdigit() else key_str


def _frame_filename(key: BucketKey) -> str:
    """依 key 型別決定 CSV 檔名：舊制頻率分鐘數沿用 disposal_stats_{N}min.csv
    （避免破壞既有快取檔名），新制的天數/初犯累犯分桶則用 disposal_stats_{key}.csv。"""

    if isinstance(key, int):
        return f"disposal_stats_{key}min.csv"
    return f"disposal_stats_{key}.csv"


def load_cached_report_frames(report_dir: Path | str = DEFAULT_REPORT_DIR) -> Optional[DisposalStatsDataset]:
    """讀取已產生的 CSV 報表。以 meta.json 的 event_counts 記錄的 key 集合為準，
    而非寫死 SUPPORTED_INTERVALS，這樣才能同時涵蓋舊制頻率分桶與新制的
    天數/初犯累犯分桶（後者的 key 集合是動態的，取決於資料庫裡有沒有「2分」事件）。"""

    report_dir_path = Path(report_dir)
    meta_path = report_dir_path / "disposal_stats_meta.json"
    if not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None

    raw_counts = meta.get("event_counts", {})
    if not isinstance(raw_counts, dict) or not raw_counts:
        return None

    frame_map: dict[BucketKey, pd.DataFrame] = {}
    counts: dict[BucketKey, tuple[int, int]] = {}
    latest_mtime: float = 0.0

    required_columns = {
        "時間點",
        "樣本數",
        "平均漲跌幅%",
        "平均開收差幅%",
        "上漲機率%",
        "紅K機率%",
        "黑K機率%",
        "平K機率%",
    }

    for key_str, count_pair in raw_counts.items():
        key = _frame_key_from_str(key_str)
        csv_path = report_dir_path / _frame_filename(key)
        if not csv_path.exists():
            return None

        try:
            frame = pd.read_csv(csv_path)
        except Exception:
            return None

        if not required_columns.issubset(set(frame.columns)):
            return None

        frame_map[key] = frame
        if isinstance(count_pair, list) and len(count_pair) == 2:
            try:
                counts[key] = (int(count_pair[0]), int(count_pair[1]))
            except Exception:
                counts[key] = (0, 0)
        else:
            counts[key] = (0, 0)
        latest_mtime = max(latest_mtime, csv_path.stat().st_mtime)

    generated_at = datetime.fromtimestamp(latest_mtime) if latest_mtime else datetime.now()
    meta_generated_at = meta.get("generated_at")
    if isinstance(meta_generated_at, str):
        try:
            generated_at = datetime.fromisoformat(meta_generated_at)
        except Exception:
            pass
    return DisposalStatsDataset(
        frames=frame_map,
        event_counts=counts,
        generated_at=generated_at,
        source=str(meta.get("source", "cache")),
    )


def interval_title(interval_min: BucketKey) -> str:
    """取得顯示標題。舊制頻率分鐘數(int)顯示「N分盤」；
    新制的天數/初犯累犯分桶(str)顯示對應的中文標題。"""

    bucket_titles = {
        "5天_初犯": "2分・5天・初犯",
        "5天_累犯": "2分・5天・累犯",
        "7天_初犯": "2分・7天・初犯",
        "7天_累犯": "2分・7天・累犯",
    }
    if isinstance(interval_min, str) and interval_min in bucket_titles:
        return bucket_titles[interval_min]
    return f"{interval_min}分盤"


def _format_number(value: object, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def render_markdown_report(dataset: DisposalStatsDataset) -> str:
    """把資料集轉成 Markdown 報表。"""

    now_text = dataset.generated_at.strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 處置股各階段漲跌幅、開收差幅與 K 線機率統計",
        "",
        f"產生時間：{now_text}",
        "",
        "## 說明",
        "- 時間軸以個股實際交易日對齊：T-1=處置前 1 日，T1..TN=處置期間，Exit=出關首日，Exit+1=出關次日。",
        "- 平均漲跌幅 = (當日收盤 - 前一交易日收盤) / 前一交易日收盤 × 100。",
        "- 平均開收差幅 = (當日收盤 - 當日開盤) / 當日開盤 × 100。",
        "- 紅K：收盤 > 開盤；黑K：收盤 < 開盤；平K：收盤 = 開盤。",
        "",
    ]

    bucket_order = list(SUPPORTED_INTERVALS) + [
        key for key in DURATION_OFFENSE_KEYS if key in dataset.frames
    ]
    for interval in bucket_order:
        frame = dataset.frames.get(interval, pd.DataFrame())
        used, total = dataset.event_counts.get(interval, (0, 0))
        lines.extend(
            [
                f"## {interval_title(interval)}",
                "",
                f"有效事件數：**{used}**（總分類 {total}）",
                "",
            ]
        )

        if frame.empty:
            lines.append("_（無資料）_")
            lines.append("")
            continue

        columns = list(frame.columns)
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("|" + "|".join(["---"] * len(columns)) + "|")
        for _, row in frame.iterrows():
            values = []
            for col in columns:
                if col == "時間點":
                    values.append(str(row[col]))
                elif col == "樣本數":
                    values.append(str(int(row[col])))
                elif col in {"平均漲跌幅%", "平均開收差幅%"}:
                    values.append(_format_number(row[col], 2))
                else:
                    values.append(_format_number(row[col], 1))
            lines.append("| " + " | ".join(values) + " |")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def save_disposal_stats_reports(
    dataset: DisposalStatsDataset,
    *,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
) -> tuple[Path, ...]:
    """輸出 CSV 與 Markdown 報表。"""

    report_dir_path = Path(report_dir)
    report_dir_path.mkdir(parents=True, exist_ok=True)

    csv_paths: list[Path] = []
    for key, frame in dataset.frames.items():
        csv_path = report_dir_path / _frame_filename(key)
        frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
        csv_paths.append(csv_path)

    meta_path = report_dir_path / "disposal_stats_meta.json"
    meta_payload = {
        "generated_at": dataset.generated_at.isoformat(timespec="seconds"),
        "source": dataset.source,
        "event_counts": {_frame_key_to_str(key): list(counts) for key, counts in dataset.event_counts.items()},
    }
    meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = report_dir_path / "disposal_stats_report.md"
    md_path.write_text(render_markdown_report(dataset), encoding="utf-8")

    return tuple(csv_paths) + (md_path,)
