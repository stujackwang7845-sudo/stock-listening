import os
import sys

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.disposal_stats_analytics import (  # noqa: E402
    DisposalEvent,
    aggregate_interval,
    classify_interval,
    compute_event,
)


def _make_price_frame() -> pd.DataFrame:
    dates = pd.to_datetime(
        [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
        ]
    )
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 103.0, 100.0, 102.0],
            "close": [100.0, 103.0, 101.0, 102.0, 104.0],
        },
        index=dates,
    )
    return frame


def test_classify_interval_handles_five_and_twenty_minutes() -> None:
    assert classify_interval("約每五分鐘撮合一次", None) == 5
    assert classify_interval(None, "約每二十分鐘撮合一次") == 20
    assert classify_interval("每十分鐘", None) is None


def test_compute_event_returns_expected_stages() -> None:
    frame = _make_price_frame()
    event = DisposalEvent(
        code="2330",
        name="台積電",
        period_start=pd.Timestamp("2024-01-03").date(),
        period_end=pd.Timestamp("2024-01-04").date(),
        interval_min=5,
    )

    result = compute_event(event, frame, post_days=1, max_t=12)
    assert result is not None
    assert set(result) == {"T-1", "T1", "T2", "Exit", "Exit+1"}
    assert result["T1"]["red"] is True
    assert result["T2"]["black"] is True
    assert result["T-1"]["flat"] is True


def test_aggregate_interval_keeps_sample_count_and_average() -> None:
    frame = _make_price_frame()
    prices = {"2330": frame}
    event = DisposalEvent(
        code="2330",
        name="台積電",
        period_start=pd.Timestamp("2024-01-03").date(),
        period_end=pd.Timestamp("2024-01-04").date(),
        interval_min=5,
    )

    summary, used, total = aggregate_interval(
        [event, event],
        prices,
        post_days=1,
        min_sample=1,
        max_t=12,
    )

    assert used == 2
    assert total == 2
    t1 = summary.loc[summary["時間點"] == "T1"].iloc[0]
    assert int(t1["樣本數"]) == 2
    assert round(float(t1["平均漲跌幅%"]), 2) == 3.00
    assert round(float(t1["紅K機率%"]), 1) == 100.0
