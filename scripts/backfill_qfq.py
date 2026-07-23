#!/usr/bin/env python3
"""回填 stock_daily 中 open_qfq/high_qfq/low_qfq 的空值（前复权 OHLC）"""
import sqlite3, sys

DB = "/home/ubuntu/databases/Sequoia选股.db"
conn = sqlite3.connect(DB)
# 只更新 open_qfq/high_qfq/low_qfq 为 NULL 的行，用 close_qfq/close 比率计算
updated = conn.execute("""
    UPDATE stock_daily
    SET open_qfq = ROUND(open * close_qfq / close, 2),
        high_qfq = ROUND(high * close_qfq / close, 2),
        low_qfq = ROUND(low * close_qfq / close, 2)
    WHERE close_qfq IS NOT NULL AND close > 0
      AND (open_qfq IS NULL OR high_qfq IS NULL OR low_qfq IS NULL)
""").rowcount
conn.commit()
conn.close()
print(f"BACKFILL QFQ: {updated} row(s) updated", file=sys.stderr)
