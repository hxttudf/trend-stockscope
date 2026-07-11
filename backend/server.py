"""StockScope Backend — K线数据 + 自选股 + 每日选股 API"""
import os
import sqlite3
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")

# DB paths
TREND_DB = "/home/ubuntu/databases/trend_picks.db"
SEQUOIA_DB = "/home/ubuntu/databases/Sequoia选股.db"
SCOPE_DB = os.path.join(BASE_DIR, "stockscope.db")

app = Flask(__name__, static_folder=FRONTEND_DIST, static_url_path="")
CORS(app)


def db_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── Stock Search ──────────────────────────────────────────────
@app.route("/api/search")
def search_stocks():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        return jsonify([])
    conn = db_conn(SEQUOIA_DB)
    cur = conn.cursor()
    like = f"%{q}%"
    rows = cur.execute(
        "SELECT DISTINCT symbol, name FROM stock_basics WHERE symbol LIKE ? OR name LIKE ? LIMIT 20",
        (like, like)
    ).fetchall()
    conn.close()
    return jsonify([{"symbol": r["symbol"], "name": r["name"]} for r in rows])


# ── K-line Data ──────────────────────────────────────────────
@app.route("/api/kline/<symbol>")
def get_kline(symbol):
    use_qfq = request.args.get("qfq", "1") == "1"
    limit = int(request.args.get("limit", "500"))
    
    conn = db_conn(SEQUOIA_DB)
    cur = conn.cursor()
    
    if use_qfq:
        rows = cur.execute(
            """SELECT date, open, high, low, 
                      close_qfq as close, volume, turnover
               FROM stock_daily 
               WHERE symbol = ? 
               ORDER BY date DESC LIMIT ?""",
            (symbol, limit)
        ).fetchall()
    else:
        rows = cur.execute(
            """SELECT date, open, high, low, close, volume, turnover
               FROM stock_daily 
               WHERE symbol = ? 
               ORDER BY date DESC LIMIT ?""",
            (symbol, limit)
        ).fetchall()
    
    conn.close()
    
    kline = []
    for r in reversed(rows):
        kline.append({
            "time": r["date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
            "turnover": r["turnover"] if r["turnover"] else 0,
        })
    
    # Get signal markers from daily_picks (both local and trend_picks)
    signals = _get_stock_signals(symbol)
    
    return jsonify({"symbol": symbol, "kline": kline, "signals": signals})


def _get_stock_signals(symbol):
    signals = []
    conn = db_conn(TREND_DB)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT date, strategy_id, name FROM daily_picks WHERE symbol = ? ORDER BY date DESC LIMIT 50",
        (symbol,)
    ).fetchall()
    conn.close()
    for r in rows:
        signals.append({"date": r["date"], "type": r["strategy_id"], "name": r["name"]})
    return signals


# ── Daily Picks ──────────────────────────────────────────────
@app.route("/api/picks")
def get_picks():
    date = request.args.get("date")
    strategy = request.args.get("strategy")
    
    conn = db_conn(SCOPE_DB)
    cur = conn.cursor()
    
    where = ["1=1"]
    params = []
    if date:
        where.append("date = ?")
        params.append(date)
    if strategy:
        where.append("strategy_id = ?")
        params.append(strategy)
    
    rows = cur.execute(
        f"SELECT * FROM daily_picks WHERE {' AND '.join(where)} ORDER BY dist_ma20 DESC",
        params
    ).fetchall()
    conn.close()
    
    return jsonify([dict(r) for r in rows])


@app.route("/api/picks/dates")
def get_pick_dates():
    conn = db_conn(SCOPE_DB)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT date, total_picks, strategies FROM daily_summary ORDER BY date DESC LIMIT 60"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Watchlist ────────────────────────────────────────────────
@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    conn = db_conn(SCOPE_DB)
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watchlist", methods=["POST"])
def add_watchlist():
    data = request.get_json()
    symbol = data.get("symbol", "").strip()
    name = data.get("name", "")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    
    conn = db_conn(SCOPE_DB)
    try:
        conn.execute("INSERT INTO watchlist (symbol, name) VALUES (?, ?)", (symbol, name))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        # Also return the existing one
        conn2 = db_conn(SCOPE_DB)
        row = conn2.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
        conn2.close()
        return jsonify(dict(row) if row else {"symbol": symbol})
    
    row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/watchlist/<symbol>", methods=["DELETE"])
def remove_watchlist(symbol):
    conn = db_conn(SCOPE_DB)
    conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/watchlist/<symbol>/note", methods=["PUT"])
def update_watchlist_note(symbol):
    data = request.get_json()
    note = data.get("note", "")
    conn = db_conn(SCOPE_DB)
    conn.execute("UPDATE watchlist SET note = ? WHERE symbol = ?", (note, symbol))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


# ── Stock basic info ──────────────────────────────────────────
@app.route("/api/stock/<symbol>")
def get_stock_info(symbol):
    conn = db_conn(SEQUOIA_DB)
    cur = conn.cursor()
    row = cur.execute(
        "SELECT DISTINCT symbol, name FROM stock_basics WHERE symbol = ? LIMIT 1",
        (symbol,)
    ).fetchone()
    conn.close()
    if row:
        return jsonify({"symbol": row["symbol"], "name": row["name"]})
    return jsonify({"symbol": symbol, "name": ""})


# ── Serve static frontend ────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.route("/assets/<path:path>")
def serve_assets(path):
    return send_from_directory(os.path.join(FRONTEND_DIST, "assets"), path)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8004
    print(f"StockScope backend running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
