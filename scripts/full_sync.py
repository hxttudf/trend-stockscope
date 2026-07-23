"""full_sync.py — 全量从trend_picks.db同步所有历史选股到stockscope.db"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREND_DB = "/home/ubuntu/databases/trend_picks.db"
SCOPE_DB = os.path.join(BASE_DIR, "stockscope.db")


def sync_all():
    trend_conn = sqlite3.connect(TREND_DB)
    trend_conn.row_factory = sqlite3.Row
    scope_conn = sqlite3.connect(SCOPE_DB)
    scope_conn.execute("PRAGMA journal_mode=WAL")
    
    cur = trend_conn.cursor()
    rows = cur.execute(
        """SELECT date, strategy_id, symbol, name, close_qfq, ma20, ma60,
                  dist_ma20, vol_ratio, pct_20d, buy_price
           FROM daily_picks ORDER BY date"""
    ).fetchall()
    
    if not rows:
        print("trend_picks.db is empty")
        return
    
    inserted = 0
    skipped = 0
    date_summaries = {}
    
    for r in rows:
        try:
            scope_conn.execute(
                """INSERT OR REPLACE INTO daily_picks 
                   (date, strategy_id, symbol, name, close_qfq, ma20, ma60,
                    dist_ma20, vol_ratio, pct_20d, buy_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (r["date"], r["strategy_id"], r["symbol"], r["name"],
                 r["close_qfq"], r["ma20"], r["ma60"],
                 r["dist_ma20"], r["vol_ratio"], r["pct_20d"],
                 r["buy_price"])
            )
            inserted += 1
            key = r["date"]
            if key not in date_summaries:
                date_summaries[key] = {"count": 0, "strategies": set()}
            date_summaries[key]["count"] += 1
            date_summaries[key]["strategies"].add(r["strategy_id"])
        except Exception as e:
            skipped += 1
            print(f"  skip {r['symbol']} on {r['date']}: {e}")
    
    # Update summaries
    for date, info in sorted(date_summaries.items()):
        scope_conn.execute(
            "INSERT OR REPLACE INTO daily_summary (date, total_picks, strategies) VALUES (?, ?, ?)",
            (date, info["count"], ",".join(sorted(info["strategies"])))
        )
    
    scope_conn.commit()
    scope_conn.close()
    trend_conn.close()
    
    print(f"Synced {inserted} records from {len(date_summaries)} dates")
    print(f"Skipped: {skipped}")
    for date in sorted(date_summaries.keys())[-5:]:
        info = date_summaries[date]
        print(f"  {date}: {info['count']} picks [{','.join(info['strategies'])}]")


if __name__ == "__main__":
    sync_all()
