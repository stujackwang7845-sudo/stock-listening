"""處置股統計報表產生腳本。"""

from __future__ import annotations

from core.disposal_stats_analytics import (
    SUPPORTED_INTERVALS,
    build_disposal_stats_dataset,
    interval_title,
    render_markdown_report,
)


def main() -> None:
    dataset = build_disposal_stats_dataset(force_refresh=True)
    print(f"統計報表已更新：{dataset.generated_at.strftime('%Y-%m-%d %H:%M')}")

    for interval in SUPPORTED_INTERVALS:
        frame = dataset.frames.get(interval)
        used, total = dataset.event_counts.get(interval, (0, 0))
        print(f"\n===== {interval_title(interval)}（有效事件 {used}/{total}）=====")
        if frame is None or frame.empty:
            print("(無資料)")
        else:
            print(frame.to_string(index=False))

    print("\nMarkdown 報表內容預覽：")
    print(render_markdown_report(dataset))


if __name__ == "__main__":
    main()

