"""sync_picks.py — 15:22 cron: 从trend_picks.db同步今日选股到stockscope.db"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREND_DB = "/home/ubuntu/databases/trend_picks.db"
SCOPE_DB = os.path.join(BASE_DIR, "stockscope.db")

# 北京时间
TODAY = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")


def sync():
    """从trend_picks.db daily_picks复制今日数据到stockscope.db"""
    trend_conn = sqlite3.connect(TREND_DB)
    trend_conn.row_factory = sqlite3.Row
    
    scope_conn = sqlite3.connect(SCOPE_DB)
    scope_conn.execute("PRAGMA journal_mode=WAL")
    
    # 获取今天的选股
    cur = trend_conn.cursor()
    rows = cur.execute(
        """SELECT date, strategy_id, symbol, name, close_qfq, ma20, ma60,
                  dist_ma20, vol_ratio, pct_20d, volume, avg_vol_20d, buy_price
           FROM daily_picks WHERE date = ?""",
        (TODAY,)
    ).fetchall()
    
    if not rows:
        print(f"{TODAY} — no picks found in trend_picks.db")
        # 今天没数据，记录空摘要
        scope_conn.execute(
            "INSERT OR REPLACE INTO daily_summary (date, total_picks, strategies) VALUES (?, 0, '')",
            (TODAY,)
        )
        scope_conn.commit()
        scope_conn.close()
        trend_conn.close()
        return
    
    # 写入stockscope.db
    strategies = set()
    inserted = 0
    for r in rows:
        try:
            scope_conn.execute(
                """INSERT OR REPLACE INTO daily_picks 
                   (date, strategy_id, symbol, name, close_qfq, ma20, ma60,
                    dist_ma20, vol_ratio, pct_20d, volume, avg_vol_20d, buy_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["date"], r["strategy_id"], r["symbol"], r["name"],
                 r["close_qfq"], r["ma20"], r["ma60"],
                 r["dist_ma20"], r["vol_ratio"], r["pct_20d"],
                 r["volume"], r["avg_vol_20d"], r["buy_price"])
            )
            strategies.add(r["strategy_id"])
            inserted += 1
        except Exception as e:
            print(f"  skip {r['symbol']}: {e}")
    
    # 更新摘要
    scope_conn.execute(
        "INSERT OR REPLACE INTO daily_summary (date, total_picks, strategies) VALUES (?, ?, ?)",
        (TODAY, inserted, ",".join(sorted(strategies)))
    )
    
    scope_conn.commit()
    scope_conn.close()
    trend_conn.close()
    
    print(f"{TODAY} — synced {inserted} picks, strategies: {','.join(sorted(strategies))}")


if __name__ == "__main__":
    sync()
