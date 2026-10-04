"""筆電外出版 → 家用電腦 資料同步腳本。

用法：把筆電的 data/disposal_history.db、stock_prices.db、data/listening_history.json
複製到隨身碟或雲端資料夾後，帶回家用電腦，執行：

    uv run python scripts/sync_from_laptop.py <那個資料夾路徑>

只會把「筆電有、家用電腦沒有」的新資料合併進來，不會刪除或覆蓋家用電腦既有的紀錄
(disposal_records/attention_clauses/stock_prices 都是相同紀錄就跳過，不同紀錄才插入，
用資料庫本身的 UNIQUE 鍵判斷，不看兩邊各自的流水號 id，所以兩台機器各自的 id 不同也沒關係)。
執行前會自動備份家用電腦目前的兩個資料庫。
"""
import sys, os, json, sqlite3, shutil
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)

if len(sys.argv) < 2:
    print("用法: python scripts/sync_from_laptop.py <筆電資料複製過來的資料夾路徑>")
    sys.exit(1)

SRC = sys.argv[1]
src_disp = os.path.join(SRC, "disposal_history.db")
src_prices = os.path.join(SRC, "stock_prices.db")
src_listening = os.path.join(SRC, "listening_history.json")

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


print(f"=== 同步來源: {SRC} ===\n")
backup("data/disposal_history.db")
backup("stock_prices.db")

# ---- disposal_history.db (disposal_records + attention_clauses) ----
conn = sqlite3.connect("data/disposal_history.db")
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
conn = sqlite3.connect("stock_prices.db")
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

# ---- listening_history.json ----
if os.path.exists(src_listening):
    home_path = "data/listening_history.json"
    backup(home_path)
    home_list = json.load(open(home_path, encoding="utf-8")) if os.path.exists(home_path) else []
    laptop_list = json.load(open(src_listening, encoding="utf-8"))
    seen = {(str(r.get("code")), r.get("date")) for r in home_list}
    added = [r for r in laptop_list if (str(r.get("code")), r.get("date")) not in seen]
    if added:
        home_list.extend(added)
        json.dump(home_list, open(home_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"listening_history.json 新增 {len(added)} 筆")
else:
    print("(來源資料夾沒有 listening_history.json，略過)")

print("\n完成。")
