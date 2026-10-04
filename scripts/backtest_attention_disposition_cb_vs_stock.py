"""
可轉債 (CB) vs 現股：首次注意與首次處置之正期望值回測系統

研究主題：
有發行 CB 的股票，在該檔 CB 存續期間，如果「第一次」因「漲幅的原因」：
  1. 被列為「注意股票」
  2. 被列為「處置股票」

隔天分別使用「CB 投資」跟「股票現股投資」，對比：
  - 進場時序：隔天開盤進場 (Next Open) vs 隔天收盤進場 (Next Close)
  - 持有時間與出場：
      * 短線固定天數：T+1, T+3, T+5, T+10 (處置出關), T+20, T+60
      * 趨勢生命線：跌破 5MA, 10MA, 20MA (月線) 出場
      * 超長線終極持有：持有至 CB 賣回日 / 下市日
  - 停損停利機制：
      * 無停損利
      * 停損 -5% / 停利 +15%
      * 停損 -8% / 停利 +20%
      * 停損 -10% / 停利 +30%
      * 移動停利 (Trailing Stop: 自最高點回檔 8% 出場)
      * 出場時序：盤中觸價 vs 收盤確認 vs 次日開盤
  - 扣除真實手續費與稅費（現股單趟 0.36%，CB 單趟 0.16%）

以量化期望值 (Expected Value %)、勝率、賺賠比、最大虧損評估最優正期望值策略。
"""

import sqlite3
import pandas as pd
import numpy as np
import math
from datetime import datetime
from collections import defaultdict

# -------------------------------------------------------------
# 1. 載入資料
# -------------------------------------------------------------
print("=== 1. 載入歷史資料庫 ===")
conn = sqlite3.connect(r"E:\Vibe Coding\Stock\DB\data\cb_enterprise.db")

# 1.1 讀取 CB Master
cb_master_df = pd.read_sql_query("""
    SELECT cb_id, stock_id, cb_name, listing_date, maturity_date, delisting_date, put_date
    FROM cb_master
    WHERE status != '停用' OR status IS NULL
""", conn)

# 1.2 讀取母股日K
stock_df = pd.read_sql_query("""
    SELECT date, stock_id, open, high, low, close, volume
    FROM stock_price_history
    ORDER BY stock_id, date ASC
""", conn)

# 1.3 讀取 CB 日K
cb_price_df = pd.read_sql_query("""
    SELECT date, cb_id, open, high, low, close, volume
    FROM cb_price_history
    ORDER BY cb_id, date ASC
""", conn)

conn.close()

print(f"CB Master: {len(cb_master_df)} 檔")
print(f"母股日K: {len(stock_df)} 筆, 涵蓋 {stock_df['stock_id'].nunique()} 檔股票")
print(f"CB 日K: {len(cb_price_df)} 筆, 涵蓋 {cb_price_df['cb_id'].nunique()} 檔 CB")

# 建立快速查找字典
# stock_bars: {stock_id: df_sorted_by_date}
stock_bars_dict = {}
for sid, g in stock_df.groupby('stock_id'):
    g = g.sort_values('date').reset_index(drop=True)
    # 預先計算 MA
    g['ma5'] = g['close'].rolling(5).mean()
    g['ma10'] = g['close'].rolling(10).mean()
    g['ma20'] = g['close'].rolling(20).mean()
    g['vol_ma60'] = g['volume'].rolling(60).mean()
    stock_bars_dict[sid] = g

# cb_bars: {cb_id: df_sorted_by_date}
cb_bars_dict = {}
for cid, g in cb_price_df.groupby('cb_id'):
    g = g.sort_values('date').reset_index(drop=True)
    g['ma5'] = g['close'].rolling(5).mean()
    g['ma10'] = g['close'].rolling(10).mean()
    g['ma20'] = g['close'].rolling(20).mean()
    cb_bars_dict[cid] = g

# 建立 date 到 index 的查找表以加快檢索
stock_date_idx = {sid: {d: idx for idx, d in enumerate(df['date'])} for sid, df in stock_bars_dict.items()}
cb_date_idx = {cid: {d: idx for idx, d in enumerate(df['date'])} for cid, df in cb_bars_dict.items()}

# -------------------------------------------------------------
# 2. 判斷母股之「因漲幅注意」與「因漲幅處置」
# -------------------------------------------------------------
print("\n=== 2. 計算母股注意與處置事件標記 ===")

# 官方注意與處置判斷核心
# 條款四：6日累積漲幅 > 32% (上市) / > 30% (上櫃)，或 6日 > 25%(23%) 且差價 > 50(40) 元
# 條款六：30日 > 100%, 60日 > 130%(140%), 90日 > 160%
# 條款三：6日 > 25%(27%) 且 當日量 > 60日均量之 5 倍

def detect_stock_attention_and_disp(sid, df):
    """
    對單一股票的歷史日K，標記每日是否符合「因漲幅注意」以及是否觸發「因漲幅處置」
    回傳字典：date -> {'is_attention': bool, 'is_disp_trigger': bool}
    """
    n = len(df)
    is_otc = (len(sid) >= 4 and sid.startswith(('3', '4', '5', '6', '8'))) # 簡化市場識別，上櫃多以3/4/5/6/8開頭
    
    thresh_6d_1 = 30.0 if is_otc else 32.0
    thresh_6d_2 = 23.0 if is_otc else 25.0
    diff_thresh = 40.0 if is_otc else 50.0
    thresh_60d = 140.0 if is_otc else 130.0
    thresh_vol_roc = 27.0 if is_otc else 25.0

    attention_flags = [False] * n
    disp_flags = [False] * n

    closes = df['close'].values
    volumes = df['volume'].values
    dates = df['date'].values

    # 計算每日注意狀態
    for i in range(5, n):
        c_curr = closes[i]
        c_prev5 = closes[i-5]
        if c_prev5 <= 0 or np.isnan(c_curr) or np.isnan(c_prev5):
            continue

        # 1. 6日漲幅 (Sum of 5 ROCs 或 總變動率)
        roc_6d = ((c_curr - c_prev5) / c_prev5) * 100.0
        diff_6d = c_curr - c_prev5

        hit_clause1 = (roc_6d >= thresh_6d_1) or (roc_6d >= thresh_6d_2 and diff_6d >= diff_thresh)

        # 2. 長期漲幅 (30日>100%, 60日>130/140%, 90日>160%)
        hit_clause2 = False
        if i >= 29:
            c_prev29 = closes[i-29]
            if c_prev29 > 0 and ((c_curr - c_prev29) / c_prev29) * 100.0 >= 100.0:
                hit_clause2 = True
        if not hit_clause2 and i >= 59:
            c_prev59 = closes[i-59]
            if c_prev59 > 0 and ((c_curr - c_prev59) / c_prev59) * 100.0 >= thresh_60d:
                hit_clause2 = True
        if not hit_clause2 and i >= 89:
            c_prev89 = closes[i-89]
            if c_prev89 > 0 and ((c_curr - c_prev89) / c_prev89) * 100.0 >= 160.0:
                hit_clause2 = True

        # 3. 6日漲幅 + 量爆量 (60日均量5倍)
        hit_clause3 = False
        if i >= 59:
            vol_60_mean = np.mean(volumes[i-59:i+1])
            if vol_60_mean > 0 and roc_6d >= thresh_vol_roc and volumes[i] >= 5.0 * vol_60_mean:
                hit_clause3 = True

        if hit_clause1 or hit_clause2 or hit_clause3:
            attention_flags[i] = True

    # 計算處置觸發狀態 (滑動視窗)
    # 處置標準：連續3日注意，或近5日達4日注意，或近10日達6日注意，或近30日達12日注意
    for i in range(n):
        if not attention_flags[i]:
            continue

        # 檢查連續 3 日
        if i >= 2 and all(attention_flags[i-2:i+1]):
            disp_flags[i] = True
            continue

        # 檢查近 5 日達 4 日
        if i >= 4 and sum(attention_flags[i-4:i+1]) >= 4:
            disp_flags[i] = True
            continue

        # 檢查近 10 日達 6 日
        if i >= 9 and sum(attention_flags[i-9:i+1]) >= 6:
            disp_flags[i] = True
            continue

        # 檢查近 30 日達 12 日
        if i >= 29 and sum(attention_flags[i-29:i+1]) >= 12:
            disp_flags[i] = True
            continue

    res = {}
    for i in range(n):
        res[dates[i]] = {
            'is_attention': attention_flags[i],
            'is_disp_trigger': disp_flags[i]
        }
    return res

stock_events_dict = {}
for sid, df in stock_bars_dict.items():
    stock_events_dict[sid] = detect_stock_attention_and_disp(sid, df)

print("母股注意/處置事件標記完成。")

# -------------------------------------------------------------
# 3. 萃取「每檔 CB 存續期間之首次注意與首次處置」
# -------------------------------------------------------------
print("\n=== 3. 萃取 CB 存續期間之首次事件 ===")

target_cases = [] # 儲存所有可回測案例

for _, cb_row in cb_master_df.iterrows():
    cb_id = str(cb_row['cb_id'])
    stock_id = str(cb_row['stock_id'])
    cb_name = cb_row['cb_name']
    listing_d = cb_row['listing_date']
    delisting_d = cb_row['delisting_date'] or cb_row['maturity_date'] or '2026-12-31'
    put_d = cb_row['put_date']

    if stock_id not in stock_events_dict or cb_id not in cb_bars_dict:
        continue

    events = stock_events_dict[stock_id]
    s_df = stock_bars_dict[stock_id]
    c_df = cb_bars_dict[cb_id]

    # 找出該 CB 流通期間內發生的事件
    # 篩選在 [listing_d, delisting_d] 之間的交易日
    valid_dates = [d for d in s_df['date'] if listing_d <= d <= delisting_d and d in c_df['date'].values]
    if not valid_dates:
        continue

    first_attention_date = None
    first_disp_date = None

    for d in valid_dates:
        ev = events.get(d, {})
        if ev.get('is_attention') and first_attention_date is None:
            first_attention_date = d
        if ev.get('is_disp_trigger') and first_disp_date is None:
            first_disp_date = d

    if first_attention_date:
        target_cases.append({
            'type': 'FIRST_ATTENTION',
            'cb_id': cb_id,
            'stock_id': stock_id,
            'cb_name': cb_name,
            'event_date': first_attention_date,
            'listing_date': listing_d,
            'delisting_date': delisting_d,
            'put_date': put_d
        })

    if first_disp_date:
        target_cases.append({
            'type': 'FIRST_DISPOSITION',
            'cb_id': cb_id,
            'stock_id': stock_id,
            'cb_name': cb_name,
            'event_date': first_disp_date,
            'listing_date': listing_d,
            'delisting_date': delisting_d,
            'put_date': put_d
        })

cases_df = pd.DataFrame(target_cases)
print(f"總共萃取出 {len(cases_df)} 個有效事件案例：")
print(cases_df['type'].value_counts())
print(cases_df.head())

# -------------------------------------------------------------
# 4. 雙標的 (現股 vs CB) 多時序模擬回測引擎
# -------------------------------------------------------------
print("\n=== 4. 執行多時序、多持有期、多停損利全維度模擬 ===")

# 定義手續費與稅費
STOCK_BUY_FEE = 0.0003
STOCK_SELL_FEE = 0.0003 + 0.0030 # 手續費 + 證交稅 0.3%
CB_BUY_FEE = 0.0003
CB_SELL_FEE = 0.0003 + 0.0010   # 手續費 + 證交稅 0.1%

def simulate_trade(df, entry_idx, entry_timing, exit_rule, exit_param, is_cb=False):
    """
    通用單筆交易回測模擬器
    entry_timing: 'NEXT_OPEN' (次日開盤) 或 'NEXT_CLOSE' (次日收盤)
    exit_rule:
      - 'FIXED_DAYS': 持有 N 天收盤出場 (exit_param=N)
      - 'BREAK_MA': 跌破均線出場 (exit_param=5/10/20)
      - 'HOLD_TO_END': 持有到最後一天收盤出場
      - 'SL_TP': 固定停損停利 (exit_param={'sl': -0.08, 'tp': 0.20})
      - 'TRAILING_STOP': 移動停利 (exit_param={'sl': -0.08, 'trail_pct': 0.08})
    """
    n = len(df)
    # T+1 日是進場日
    if entry_timing == 'NEXT_OPEN':
        # 在 entry_idx + 1 開盤進場
        buy_bar_idx = entry_idx + 1
        if buy_bar_idx >= n:
            return None
        buy_price = df['open'].iloc[buy_bar_idx]
    elif entry_timing == 'NEXT_CLOSE':
        # 在 entry_idx + 1 收盤進場
        buy_bar_idx = entry_idx + 1
        if buy_bar_idx >= n:
            return None
        buy_price = df['close'].iloc[buy_bar_idx]
    else:
        return None

    if buy_price <= 0 or np.isnan(buy_price):
        return None

    buy_fee = CB_BUY_FEE if is_cb else STOCK_BUY_FEE
    sell_fee = CB_SELL_FEE if is_cb else STOCK_SELL_FEE

    # 模擬出場
    exit_price = None
    exit_bar_idx = None
    exit_reason = ""
    holding_days = 0

    # 追蹤最高價 (供 Trailing Stop 使用)
    peak_price = buy_price

    # 遍歷持股期間的每一天
    # 若在 NEXT_OPEN 進場，當天 (buy_bar_idx) 盤中就已在場內
    # 若在 NEXT_CLOSE 進場，從 buy_bar_idx + 1 開始檢查
    start_sim_idx = buy_bar_idx if entry_timing == 'NEXT_OPEN' else (buy_bar_idx + 1)

    if start_sim_idx >= n:
        return None

    for i in range(start_sim_idx, n):
        curr_bar = df.iloc[i]
        curr_o = curr_bar['open']
        curr_h = curr_bar['high']
        curr_l = curr_bar['low']
        curr_c = curr_bar['close']
        cur_days = (i - buy_bar_idx) + (1 if entry_timing == 'NEXT_OPEN' else 0)

        # 更新波段高點
        if curr_h > peak_price:
            peak_price = curr_h

        # 檢查出場規則
        # 1. 固定天數
        if exit_rule == 'FIXED_DAYS':
            target_days = exit_param
            if cur_days >= target_days:
                exit_price = curr_c
                exit_bar_idx = i
                exit_reason = f"FIXED_{target_days}D"
                holding_days = cur_days
                break

        # 2. 均線跌破出場 (BREAK_MA)
        elif exit_rule == 'BREAK_MA':
            ma_col = f"ma{exit_param}"
            ma_val = curr_bar[ma_col]
            if not np.isnan(ma_val) and curr_c < ma_val:
                exit_price = curr_c
                exit_bar_idx = i
                exit_reason = f"BREAK_{exit_param}MA"
                holding_days = cur_days
                break

        # 3. 停損停利 (SL_TP)
        elif exit_rule == 'SL_TP':
            sl_ratio = exit_param['sl'] # e.g. -0.08
            tp_ratio = exit_param['tp'] # e.g. 0.20
            sl_price = buy_price * (1 + sl_ratio)
            tp_price = buy_price * (1 + tp_ratio)

            # 盤中觸價優先判定
            # 先檢查停損 (防禦優先)
            if curr_l <= sl_price:
                exit_price = sl_price
                exit_bar_idx = i
                exit_reason = "STOP_LOSS"
                holding_days = cur_days
                break
            elif curr_h >= tp_price:
                exit_price = tp_price
                exit_bar_idx = i
                exit_reason = "TAKE_PROFIT"
                holding_days = cur_days
                break

        # 4. 移動停利 (TRAILING_STOP)
        elif exit_rule == 'TRAILING_STOP':
            sl_ratio = exit_param.get('sl', -0.08)
            trail_pct = exit_param.get('trail_pct', 0.08)
            sl_price = buy_price * (1 + sl_ratio)
            trail_price = peak_price * (1 - trail_pct)

            if curr_l <= sl_price:
                exit_price = sl_price
                exit_bar_idx = i
                exit_reason = "STOP_LOSS"
                holding_days = cur_days
                break
            elif peak_price > buy_price * 1.05 and curr_l <= trail_price:
                exit_price = trail_price
                exit_bar_idx = i
                exit_reason = "TRAILING_STOP"
                holding_days = cur_days
                break

        # 5. 持有至資料終點 / 下市
        elif exit_rule == 'HOLD_TO_END':
            pass # 繼續走到最後一筆

    # 若直到資料結束都未觸發出場，以最後一日收盤出場
    if exit_price is None:
        exit_price = df['close'].iloc[-1]
        exit_bar_idx = n - 1
        exit_reason = "END_OF_DATA"
        holding_days = (n - 1 - buy_bar_idx)

    # 計算扣除成本後之淨報酬率
    raw_ret = (exit_price - buy_price) / buy_price
    net_ret = ((exit_price * (1 - sell_fee)) - (buy_price * (1 + buy_fee))) / (buy_price * (1 + buy_fee))

    return {
        'buy_price': buy_price,
        'exit_price': exit_price,
        'raw_ret_pct': raw_ret * 100.0,
        'net_ret_pct': net_ret * 100.0,
        'holding_days': max(1, holding_days),
        'exit_reason': exit_reason
    }

# -------------------------------------------------------------
# 5. 運行全矩陣回測
# -------------------------------------------------------------

# 定義要測試的出場規則列表
strategies = [
    ('T+1 (隔日沖)', 'FIXED_DAYS', 1),
    ('T+3 (短波段)', 'FIXED_DAYS', 3),
    ('T+5 (1週)', 'FIXED_DAYS', 5),
    ('T+10 (處置2週出關)', 'FIXED_DAYS', 10),
    ('T+20 (1個月)', 'FIXED_DAYS', 20),
    ('T+60 (1季波段)', 'FIXED_DAYS', 60),
    ('跌破 5MA 出場', 'BREAK_MA', 5),
    ('跌破 10MA 出場', 'BREAK_MA', 10),
    ('跌破 20MA (月線)', 'BREAK_MA', 20),
    ('停損-5%/停利+15%', 'SL_TP', {'sl': -0.05, 'tp': 0.15}),
    ('停損-8%/停利+20%', 'SL_TP', {'sl': -0.08, 'tp': 0.20}),
    ('停損-10%/停利+30%', 'SL_TP', {'sl': -0.10, 'tp': 0.30}),
    ('停損-10%/停利+50%', 'SL_TP', {'sl': -0.10, 'tp': 0.50}),
    ('移動停利(高檔回檔8%)', 'TRAILING_STOP', {'sl': -0.08, 'trail_pct': 0.08}),
    ('持有至下市/資料結束', 'HOLD_TO_END', None),
]

all_results = []

for idx, case in cases_df.iterrows():
    ev_type = case['type']
    cid = case['cb_id']
    sid = case['stock_id']
    ev_date = case['event_date']

    s_df = stock_bars_dict[sid]
    c_df = cb_bars_dict[cid]

    # 取得 event_date 在母股與 CB 的 index
    s_idx = stock_date_idx[sid].get(ev_date)
    c_idx = cb_date_idx[cid].get(ev_date)

    if s_idx is None or c_idx is None:
        continue

    # 測試進場時序：NEXT_OPEN 與 NEXT_CLOSE
    for entry_timing in ['NEXT_OPEN', 'NEXT_CLOSE']:
        for strat_name, rule, param in strategies:
            # 1. 股票投資
            s_res = simulate_trade(s_df, s_idx, entry_timing, rule, param, is_cb=False)
            if s_res:
                all_results.append({
                    'event_type': ev_type,
                    'cb_id': cid,
                    'stock_id': sid,
                    'asset_type': 'STOCK (現股)',
                    'entry_timing': entry_timing,
                    'strategy': strat_name,
                    'net_ret_pct': s_res['net_ret_pct'],
                    'holding_days': s_res['holding_days'],
                    'exit_reason': s_res['exit_reason']
                })

            # 2. CB 投資
            c_res = simulate_trade(c_df, c_idx, entry_timing, rule, param, is_cb=True)
            if c_res:
                all_results.append({
                    'event_type': ev_type,
                    'cb_id': cid,
                    'stock_id': sid,
                    'asset_type': 'CB (可轉債)',
                    'entry_timing': entry_timing,
                    'strategy': strat_name,
                    'net_ret_pct': c_res['net_ret_pct'],
                    'holding_days': c_res['holding_days'],
                    'exit_reason': c_res['exit_reason']
                })

df_res = pd.DataFrame(all_results)
print(f"\n全矩陣模擬完成！產生 {len(df_res)} 筆交易模擬記錄。")

# -------------------------------------------------------------
# 6. 計算期望值與量化指標
# -------------------------------------------------------------
print("\n=== 5. 彙整統計各組合之期望值與勝率 ===")

summary_records = []

# 分組維度：(event_type, asset_type, entry_timing, strategy)
grouped = df_res.groupby(['event_type', 'asset_type', 'entry_timing', 'strategy'])

for (ev_type, asset, timing, strat), g in grouped:
    n_trades = len(g)
    if n_trades < 10:
        continue

    rets = g['net_ret_pct'].values
    wins = rets[rets > 0]
    losses = rets[rets < 0]

    win_rate = (len(wins) / n_trades) * 100.0
    avg_ret = np.mean(rets)
    median_ret = np.median(rets)
    avg_win = np.mean(wins) if len(wins) > 0 else 0.0
    avg_loss = np.mean(losses) if len(losses) > 0 else 0.0 # 負值
    payoff_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    
    # 期望值 (Expected Value per trade, %)
    # EV = (WinRate * AvgWin) + (LossRate * AvgLoss) (注意 AvgLoss 是負數)
    ev = (len(wins)/n_trades * avg_win) + (len(losses)/n_trades * avg_loss)

    max_win = np.max(rets) if len(rets) > 0 else 0.0
    max_loss = np.min(rets) if len(rets) > 0 else 0.0
    avg_days = np.mean(g['holding_days'].values)

    summary_records.append({
        '事件類型': ev_type,
        '投資標的': asset,
        '進場時點': timing,
        '出場策略': strat,
        '交易筆數': n_trades,
        '勝率%': round(win_rate, 1),
        '平均報酬%': round(avg_ret, 2),
        '中位數%': round(median_ret, 2),
        '平均獲利%': round(avg_win, 2),
        '平均虧損%': round(avg_loss, 2),
        '賺賠比': round(payoff_ratio, 2),
        '期望值EV%': round(ev, 2),
        '最大獲利%': round(max_win, 1),
        '最大虧損%': round(max_loss, 1),
        '平均持有天': round(avg_days, 1)
    })

df_summary = pd.DataFrame(summary_records)

# 儲存結果為 CSV 供後續分析
output_csv = r"E:\Vibe Coding\Stock\處置股\scratch\backtest_attention_disp_results.csv"
df_summary.to_csv(output_csv, index=False, encoding='utf-8-sig')
print(f"統計摘要已成功輸出至：{output_csv}")

# 印出最高期望值的前 15 名策略
print("\n--- 🏆 全市場正期望值排行榜 TOP 15 ---")
top15 = df_summary.sort_values('期望值EV%', ascending=False).head(15)
print(top15[['事件類型', '投資標的', '進場時點', '出場策略', '勝率%', '平均報酬%', '賺賠比', '期望值EV%', '最大虧損%']].to_string(index=False))

# 分類印出：
# 1. 首次注意事件中最優
print("\n--- 📌 首次注意 (FIRST_ATTENTION) 最優策略 TOP 5 ---")
print(df_summary[df_summary['事件類型']=='FIRST_ATTENTION'].sort_values('期望值EV%', ascending=False).head(5)[['投資標的', '進場時點', '出場策略', '勝率%', '平均報酬%', '賺賠比', '期望值EV%']].to_string(index=False))

# 2. 首次處置事件中最優
print("\n--- 📌 首次處置 (FIRST_DISPOSITION) 最優策略 TOP 5 ---")
print(df_summary[df_summary['事件類型']=='FIRST_DISPOSITION'].sort_values('期望值EV%', ascending=False).head(5)[['投資標的', '進場時點', '出場策略', '勝率%', '平均報酬%', '賺賠比', '期望值EV%']].to_string(index=False))
