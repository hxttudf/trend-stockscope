"""Initialize stockscope.db schema."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stockscope.db")

def init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()
    
    # 自选股
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT DEFAULT '',
            note TEXT DEFAULT '',
            added_at TEXT DEFAULT (datetime('now','+8 hours'))
        )
    """)
    
    # 每日选股缓存（从trend_picks.db同步过来）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            close_qfq REAL,
            ma20 REAL,
            ma60 REAL,
            dist_ma20 REAL,
            vol_ratio REAL,
            pct_20d REAL,
            volume REAL,
            avg_vol_20d REAL,
            buy_price REAL,
            created_at TEXT DEFAULT (datetime('now','+8 hours')),
            UNIQUE(date, strategy_id, symbol)
        )
    """)
    
    # 每日摘要
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_picks INTEGER DEFAULT 0,
            strategies TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','+8 hours'))
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"Initialized: {DB_PATH}")

if __name__ == "__main__":
    init()
