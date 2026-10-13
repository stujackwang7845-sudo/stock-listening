"""
處置策略 Telegram 推播的雲端總控(2026-10-14，使用者要求電腦沒開也要推播)。由 GitHub Actions(私有倉庫)呼叫。

  post：決定盤後日 D(最近一個已收盤的交易日) → run_daily(D) 更新狀態資料夾(官方行情、注意條款、處置紀錄、預測快取)
        → 盤後推播(notify_disposal_golden.py --date D)
  pre ：盤前提醒(notify_disposal_golden.py --pre-close)，用狀態資料夾裡前一晚更新好的資料

狀態資料夾(--state)就是 DISPO_DATA_DIR：disposal_history.db、cache.db、market_data.db、margin_futures.db、
listening_history.json、tags_config.json、twse_holidays.json、disposal_golden_signals.json…(由 workflow 從私有倉庫
Release 下載、跑完再上傳)。雲端不連 FinMind/Shioaji；Telegram 用環境變數 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID。

  uv run python scripts/cloud/dispo_cloud_job.py --mode post --state state [--date YYYYMMDD] [--dry-run]
  uv run python scripts/cloud/dispo_cloud_job.py --mode pre  --state state [--dry-run]
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.dirname(SCRIPTS)


def last_closed_trading_day(now=None):
    """最近一個已收盤(14:00 後)的交易日。"""
    sys.path.insert(0, SCRIPTS)
    from core.utils import DateUtils
    now = now or datetime.now()
    ref = now if now.hour >= 14 else now - timedelta(days=1)
    return DateUtils.get_last_trading_day(datetime.combine(ref.date(), datetime.min.time()))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["post", "pre"], required=True)
    ap.add_argument("--state", required=True, help="狀態資料夾")
    ap.add_argument("--date", help="盤後日 YYYYMMDD(預設最近一個已收盤交易日)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-update", action="store_true", help="post 模式不跑 run_daily(測試用)")
    a = ap.parse_args(argv)

    state = os.path.abspath(a.state)
    env = dict(os.environ, DISPO_DATA_DIR=state, DISPO_NO_FINMIND="1", DISPO_NO_SHIOAJI="1", PYTHONIOENCODING="utf-8")
    notify = [sys.executable, os.path.join(SCRIPTS, "automation", "notify_disposal_golden.py")]
    if a.dry_run:
        notify.append("--dry-run")

    if a.mode == "pre":
        return subprocess.run(notify + ["--pre-close"], cwd=PROJ, env=env).returncode

    d = a.date or last_closed_trading_day().strftime("%Y%m%d")
    print(f"[dispo_cloud_job] 盤後日 {d}，狀態資料夾 {state}", flush=True)
    if not a.skip_update:
        # 網頁統計(處置統計/統計圖表)不是推播需要的，跳過以縮短時間
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "cloud", "run_daily.py"), "--date", d,
                            "--data-dir", state, "--out", os.path.join(PROJ, "temp", "cloud_site"), "--skip-stats"],
                           cwd=PROJ, env=env)
        if r.returncode != 0:
            print(f"[dispo_cloud_job] run_daily 結束碼 {r.returncode}，仍繼續推播(推播本身會用官方公告與行情)", flush=True)
    return subprocess.run(notify + ["--date", d], cwd=PROJ, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
