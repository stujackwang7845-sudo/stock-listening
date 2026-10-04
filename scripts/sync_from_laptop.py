"""筆電外出版 → 家用電腦 資料同步腳本。

用法：把筆電外出版資料夾裡的這些檔案，複製到隨身碟或雲端資料夾的同一層後，帶回家用電腦：
    必要：data/disposal_history.db、stock_prices.db
    建議：data/listening_history.json、data/cache.db、data/market_data.db、data/tags_config.json
然後執行：

    uv run python scripts/sync_from_laptop.py <那個資料夾路徑>

只會把「筆電有、家用電腦沒有」的新資料合併進來，不會刪除或覆蓋家用電腦既有的紀錄
(disposal_records/attention_clauses/stock_prices 都是相同紀錄就跳過，不同紀錄才插入，
用資料庫本身的 UNIQUE 鍵判斷，不看兩邊各自的流水號 id，所以兩台機器各自的 id 不同也沒關係)。
[2026-10-04] 另外合併：cache.db 每天一份的快照(agg_cache/daily_cache/dashboard_summary，
家裡沒有的日期才補)、market_data.db 行情快取、聽牌紀錄在筆電加的標籤/註解、新建的標籤定義。
執行前會自動備份家用電腦目前要寫入的檔案。
路徑走 core.runtime.get_paths()，可用 DISPO_DATA_DIR 指到資料副本測試。
"""
import sys, os, json, sqlite3, shutil
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, os.path.join(PROJ, "scripts"))
from core.runtime import get_paths  # noqa: E402

P = get_paths()

if len(sys.argv) < 2:
    print("用法: python scripts/sync_from_laptop.py <筆電資料複製過來的資料夾路徑>")
    sys.exit(1)

SRC = sys.argv[1]
src_disp = os.path.join(SRC, "disposal_history.db")
src_prices = os.path.join(SRC, "stock_prices.db")
src_listening = os.path.join(SRC, "listening_history.json")
src_cache = os.path.join(SRC, "cache.db")
src_market = os.path.join(SRC, "market_data.db")
src_tags = os.path.join(SRC, "tags_config.json")

missing = [p for p in [src_disp, src_prices] if not os.path.exists(p)]
if missing:
    print("找不到這些檔案，確認資料夾路徑跟複製的檔名：")
    for m in missing:
        print("  -", m)
    sys.exit(1)

NOW = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(path):
    if os.path.exists(path):
        bak = f"{path}.bak_sync_{NOW}"
        shutil.copy2(path, bak)
        print(f"已備份 {path} -> {bak}")


# UNIQUE 鍵用的欄位。SQLite 的 UNIQUE 限制把兩個 NULL 視為互不相同，舊資料裡有大量
# 這幾個欄位是 NULL 的殘留垃圾列，若不排除，合併時會被當成「不重複」整批複製一份。
UNIQUE_KEYS = {
    "disposal_records": ["source", "code", "announce_date", "period_raw"],
    "attention_clauses": ["announce_date", "code", "clause_number"],
    "stock_prices": ["date", "code"],
    "trading_day_cache": ["date"],
}


def merge_table(conn, table, unique_cols=None):
    """從 ATTACH 進來的 laptop 資料庫合併某張表，回傳新增筆數。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    insert_cols = [c for c in cols if c != "id"]  # 讓兩邊各自的流水號不衝突，id 交給 AUTOINCREMENT 重新配
    col_list = ", ".join(insert_cols)
    key_cols = unique_cols or UNIQUE_KEYS[table]
    not_null_clause = " AND ".join(f"{k} IS NOT NULL" for k in key_cols)
    before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({col_list}) "
        f"SELECT {col_list} FROM laptop.{table} WHERE {not_null_clause}"
    )
    after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return after - before


def fill_missing_stats(conn):
    """disposal_records: 家用電腦這筆存在但 calculated_stats 是 NULL、筆電那邊算出來了 → 補上。
    不覆蓋家用電腦已經有值的欄位，只填空白。"""
    rows = conn.execute("""
        SELECT h.id, l.calculated_stats
        FROM disposal_records h
        JOIN laptop.disposal_records l
          ON h.source = l.source AND h.code = l.code
         AND h.announce_date = l.announce_date AND h.period_raw = l.period_raw
        WHERE h.calculated_stats IS NULL AND l.calculated_stats IS NOT NULL
    """).fetchall()
    for rid, stats in rows:
        conn.execute("UPDATE disposal_records SET calculated_stats=? WHERE id=?", (stats, rid))
    return len(rows)


def merge_by_primary_key(conn, table):
    """主鍵相同就保留家用電腦那筆，只補家裡沒有的列(cache.db 每天一份的快照、market_data.db 行情快取)。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    col_list = ", ".join(cols)
    before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.execute(f"INSERT OR IGNORE INTO {table} ({col_list}) SELECT {col_list} FROM laptop.{table}")
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - before


print(f"=== 同步來源: {SRC} ===\n")
backup(P.disposal_db)
backup(P.prices_db)

# ---- disposal_history.db (disposal_records + attention_clauses) ----
conn = sqlite3.connect(P.disposal_db)
conn.execute("ATTACH DATABASE ? AS laptop", (src_disp,))
n_disp = merge_table(conn, "disposal_records", None)
n_clause = merge_table(conn, "attention_clauses", None)
n_filled = fill_missing_stats(conn)
conn.commit()
conn.execute("DETACH DATABASE laptop")
conn.close()
print(f"disposal_records 新增 {n_disp} 筆，補上原本空白的漲跌幅計算 {n_filled} 筆")
print(f"attention_clauses 新增 {n_clause} 筆")

# ---- stock_prices.db (stock_prices + trading_day_cache) ----
conn = sqlite3.connect(P.prices_db)
conn.execute("ATTACH DATABASE ? AS laptop", (src_prices,))
n_price = merge_table(conn, "stock_prices", None)
n_cal = 0
try:
    n_cal = merge_table(conn, "trading_day_cache", None)
except sqlite3.OperationalError:
    pass
conn.commit()
conn.execute("DETACH DATABASE laptop")
conn.close()
print(f"stock_prices 新增 {n_price} 筆")
print(f"trading_day_cache 新增 {n_cal} 筆")

# ---- cache.db：外出期間筆電產生的每日快照(儀表板/總覽讀這裡)，家裡沒有的日期才補 ----
if os.path.exists(src_cache):
    backup(P.cache_db)
    conn = sqlite3.connect(P.cache_db)
    conn.execute("ATTACH DATABASE ? AS laptop", (src_cache,))
    laptop_tables = {r[0] for r in conn.execute("SELECT name FROM laptop.sqlite_master WHERE type='table'")}
    for t in ["agg_cache", "daily_cache", "dashboard_summary"]:
        if t in laptop_tables:
            new_dates = [r[0] for r in conn.execute(
                f"SELECT date_str FROM laptop.{t} WHERE date_str NOT IN (SELECT date_str FROM {t}) ORDER BY date_str")]
            n = merge_by_primary_key(conn, t)
            print(f"cache.db {t} 新增 {n} 天 {new_dates}")
    conn.commit()
    conn.execute("DETACH DATABASE laptop")
    conn.close()
else:
    print("(來源資料夾沒有 cache.db，略過；外出期間的儀表板每日快照不會帶回來)")

# ---- market_data.db：行情快取(沒帶也會在家裡自動重抓，帶回來省時間) ----
if os.path.exists(src_market):
    backup(P.market_db)
    conn = sqlite3.connect(P.market_db)
    conn.execute("ATTACH DATABASE ? AS laptop", (src_market,))
    for t in ["price_history", "ratios", "stock_info"]:
        try:
            print(f"market_data.db {t} 新增 {merge_by_primary_key(conn, t)} 筆")
        except sqlite3.OperationalError as e:
            print(f"market_data.db {t} 略過: {e}")
    conn.commit()
    conn.execute("DETACH DATABASE laptop")
    conn.close()
else:
    print("(來源資料夾沒有 market_data.db，略過；家裡會在需要時自動重抓行情)")

# ---- listening_history.json ----
if os.path.exists(src_listening):
    home_path = P.listening_json
    backup(home_path)
    home_list = json.load(open(home_path, encoding="utf-8")) if os.path.exists(home_path) else []
    laptop_list = json.load(open(src_listening, encoding="utf-8"))
    home_index = {(str(r.get("code")), r.get("date")): r for r in home_list}
    added = []
    n_tags = n_comment = 0
    conflicts = []
    for r in laptop_list:
        key = (str(r.get("code")), r.get("date"))
        h = home_index.get(key)
        if h is None:
            added.append(r)
            continue
        # 同一筆紀錄：筆電加的標籤取聯集；註解只在家裡是空白時補上，兩邊不同就保留家裡的並列出來
        l_tags = r.get("tags") or []
        h_tags = h.get("tags") or []
        extra = [t for t in l_tags if t not in h_tags]
        if extra:
            h["tags"] = h_tags + extra
            n_tags += 1
        l_cm = (r.get("comment") or "").strip()
        h_cm = (h.get("comment") or "").strip()
        if l_cm and not h_cm:
            h["comment"] = r.get("comment")
            n_comment += 1
        elif l_cm and h_cm and l_cm != h_cm:
            conflicts.append((key, h_cm, l_cm))
    if added or n_tags or n_comment:
        home_list.extend(added)
        json.dump(home_list, open(home_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"listening_history.json 新增 {len(added)} 筆，補標籤 {n_tags} 筆，補註解 {n_comment} 筆")
    for key, h_cm, l_cm in conflicts:
        print(f"  註解兩邊不同，保留家裡的：{key} 家裡「{h_cm}」/ 筆電「{l_cm}」")
else:
    print("(來源資料夾沒有 listening_history.json，略過)")

# ---- tags_config.json：筆電新建的標籤定義(家裡沒有的 id 才補) ----
if os.path.exists(src_tags):
    home_tags_path = P.tags_json
    home_cfg = json.load(open(home_tags_path, encoding="utf-8")) if os.path.exists(home_tags_path) else {"tags": {}}
    laptop_cfg = json.load(open(src_tags, encoding="utf-8"))
    new_ids = [tid for tid in laptop_cfg.get("tags", {}) if tid not in home_cfg.setdefault("tags", {})]
    if new_ids:
        backup(home_tags_path)
        for tid in new_ids:
            home_cfg["tags"][tid] = laptop_cfg["tags"][tid]
        json.dump(home_cfg, open(home_tags_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"tags_config.json 新增標籤 {len(new_ids)} 個 {new_ids}")
else:
    print("(來源資料夾沒有 tags_config.json，略過)")

print("\n完成。")
