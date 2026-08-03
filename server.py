"""StockScope Backend — K线数据 + 自选股 + 每日选股 API"""
import os
import sqlite3
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = BASE_DIR
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
    
    conn = db_conn(SEQUOIA_DB)
    cur = conn.cursor()
    
    if use_qfq:
        rows = cur.execute(
            """SELECT date, open, high, low, close, open_qfq, high_qfq, low_qfq, close_qfq, volume, turnover
               FROM stock_daily 
               WHERE symbol = ? AND close_qfq IS NOT NULL AND close > 0
               ORDER BY date""",
            (symbol,)
        ).fetchall()
    else:
        rows = cur.execute(
            """SELECT date, open, high, low, close, close_qfq, volume, turnover
               FROM stock_daily 
               WHERE symbol = ? 
               ORDER BY date""",
            (symbol,)
        ).fetchall()
    
    conn.close()
    
    kline = []
    for r in rows:
        if use_qfq and r["close_qfq"] and r["close_qfq"] > 0:
            # 前复权 OHLC：优先用预计算值；若为 NULL 则实时计算（新数据可能未回填）
            ratio = r["close_qfq"] / r["close"] if r["close"] and r["close"] > 0 else 1.0
            o_qfq = r["open_qfq"] if r["open_qfq"] else (round(r["open"] * ratio, 2) if r["open"] else 0)
            h_qfq = r["high_qfq"] if r["high_qfq"] else (round(r["high"] * ratio, 2) if r["high"] else 0)
            l_qfq = r["low_qfq"] if r["low_qfq"] else (round(r["low"] * ratio, 2) if r["low"] else 0)
            c_qfq = round(r["close_qfq"], 2)
            kline.append({
                "time": r["date"],
                "open": o_qfq,
                "high": h_qfq,
                "low": l_qfq,
                "close": c_qfq,
                "volume": r["volume"],
                "turnover": r["turnover"] if r["turnover"] else 0,
            })
        else:
            kline.append({
                "time": r["date"],
                "open": round(r["open"], 2),
                "high": round(r["high"], 2),
                "low": round(r["low"], 2),
                "close": round(r["close"], 2),
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
    # 底部确认策略信号(买入+观察)
    bc_rows = cur.execute(
        "SELECT date, name, status FROM bottom_confirm_picks "
        "WHERE symbol = ? AND status IN ('worth', 'watch') ORDER BY date DESC LIMIT 50",
        (symbol,)
    ).fetchall()
    conn.close()
    for r in rows:
        signals.append({"date": r["date"], "type": r["strategy_id"], "name": r["name"]})
    for r in bc_rows:
        t = "bottom_confirm" if r["status"] == "worth" else "bottom_confirm_watch"
        signals.append({"date": r["date"], "type": t, "name": r["name"]})
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
        where.append("dp.date = ?")
        params.append(date)
    
    # Strategy filter: find all symbols that have the target strategy on that date,
    # then return their full multi-strategy grouped rows
    if strategy:
        where.append("dp.symbol IN (SELECT symbol FROM daily_picks WHERE date = ? AND strategy_id = ?)")
        params.append(date if date else "")
        params.append(strategy)
    
    rows = cur.execute(
        f"""SELECT dp.date, dp.symbol, dp.name,
                   GROUP_CONCAT(DISTINCT dp.strategy_id) as strategies,
                   MAX(dp.close_qfq) as close_qfq,
                   MAX(dp.ma20) as ma20, MAX(dp.ma60) as ma60,
                   MAX(dp.dist_ma20) as dist_ma20,
                   MAX(dp.vol_ratio) as vol_ratio,
                   MAX(dp.pct_20d) as pct_20d,
                   MAX(dp.buy_price) as buy_price
            FROM daily_picks dp
            WHERE {' AND '.join(where)}
            GROUP BY dp.date, dp.symbol
            ORDER BY dist_ma20 DESC""",
        params
    ).fetchall()
    conn.close()
    
    result = []
    # 用实时名称替换 daily_picks 中可能过时的名字
    conn_seq = db_conn(SEQUOIA_DB)
    for r in rows:
        d = dict(r)
        real_name = conn_seq.execute(
            "SELECT name FROM stock_basics WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (d["symbol"],)
        ).fetchone()
        if real_name and real_name["name"]:
            d["name"] = real_name["name"]
        d["strategy_id"] = d.pop("strategies")
        result.append(d)
    conn_seq.close()
    return jsonify(result)


@app.route("/api/picks/dates")
def get_pick_dates():
    strategy = request.args.get("strategy")
    conn = db_conn(SCOPE_DB)
    cur = conn.cursor()
    if strategy:
        rows = cur.execute(
            """SELECT dp.date, 
                      COUNT(DISTINCT dp.symbol) as total_picks,
                      GROUP_CONCAT(DISTINCT dp.strategy_id) as strategies
               FROM daily_picks dp
               WHERE dp.strategy_id = ?
               GROUP BY dp.date
               ORDER BY dp.date DESC LIMIT 200""",
            (strategy,)
        ).fetchall()
    else:
        rows = cur.execute(
            "SELECT date, total_picks, strategies FROM daily_summary ORDER BY date DESC LIMIT 200"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Watchlist ────────────────────────────────────────────────
@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    conn = db_conn(SCOPE_DB)
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM watchlist ORDER BY sort_order ASC, added_at DESC").fetchall()
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


@app.route("/api/watchlist/reorder", methods=["PUT"])
def reorder_watchlist():
    data = request.get_json()
    symbols = data.get("symbols", [])
    conn = db_conn(SCOPE_DB)
    for i, sym in enumerate(symbols):
        conn.execute("UPDATE watchlist SET sort_order = ? WHERE symbol = ?", (i, sym))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


# ── 缠论信号(静态路由必须先于 /api/chanlun/<symbol>, 否则被当作symbol) ──
@app.route("/api/chanlun/dates")
def api_chanlun_dates():
    """缠论信号日期列表: ?type=三买 时只统计该类型"""
    conn = db_conn(TREND_DB)
    typ = request.args.get("type", "")
    try:
        if typ:
            rows = conn.execute(
                "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE signal_type=? "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60", (typ,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT signal_date, COUNT(*) FROM chanlun_signals GROUP BY signal_date "
                "ORDER BY signal_date DESC LIMIT 60").fetchall()
    except Exception:
        return json.dumps([])
    return json.dumps([{"date": r[0], "total": r[1]} for r in rows], ensure_ascii=False)


@app.route("/api/chanlun/signals")
def api_chanlun_signals():
    """缠论信号列表: ?date=2026-07-30&type=三买 | type=二三买 时返回同股同日二买+三买重合"""
    date = request.args.get("date", "")
    typ = request.args.get("type", "")
    conn = db_conn(TREND_DB)
    try:
        if typ == "二三买":
            rows = conn.execute(
                "SELECT a.symbol, a.name, '二买+三买', a.signal_date, a.price, a.ref_zd, a.ref_zg "
                "FROM chanlun_signals a JOIN chanlun_signals b "
                "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.signal_date=? AND a.signal_type='二买' AND b.signal_type='三买' "
                "ORDER BY a.symbol", (date,)).fetchall()
        else:
            q = "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg FROM chanlun_signals WHERE signal_date=?"
            args = [date]
            if typ:
                q += " AND signal_type=?"
                args.append(typ)
            q += " ORDER BY signal_type, symbol"
            rows = conn.execute(q, args).fetchall()
    except Exception:
        return json.dumps([])
    return json.dumps([{"symbol": r[0], "name": r[1], "type": r[2], "date": r[3],
                        "price": r[4], "zd": r[5], "zg": r[6]} for r in rows], ensure_ascii=False)


# ── Stock basic info ──────────────────────────────────────────
@app.route("/api/chanlun/<symbol>")
def api_chanlun(symbol):
    """缠论分析(完整版): 笔/线段/中枢/走势类型/背驰/买卖点"""
    import chanlun_full
    return json.dumps(chanlun_full.analyze(symbol), ensure_ascii=False)


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


# ── 底部确认策略 ────────────────────────────────────────────
@app.route("/api/bottom-confirm/picks")
def get_bc_picks():
    date = request.args.get("date")
    conn = db_conn(TREND_DB)
    rows = conn.execute(
        "SELECT date, symbol, name, status, score, stage, drop_pct, bottom_days, "
        "vol_shrink, streak, close_qfq, ma20, ma60 "
        "FROM bottom_confirm_picks WHERE (? IS NULL OR date = ?) "
        "ORDER BY CASE status WHEN 'worth' THEN 0 ELSE 1 END, score DESC",
        (date, date)
    ).fetchall()
    conn.close()
    # 实时名称
    conn_seq = db_conn(SEQUOIA_DB)
    result = []
    for r in rows:
        d = dict(r)
        real_name = conn_seq.execute(
            "SELECT name FROM stock_basics WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (d["symbol"],)
        ).fetchone()
        if real_name and real_name["name"]:
            d["name"] = real_name["name"]
        result.append(d)
    conn_seq.close()
    return jsonify(result)


@app.route("/api/bottom-confirm/dates")
def get_bc_dates():
    conn = db_conn(TREND_DB)
    rows = conn.execute(
        "SELECT date, COUNT(*) as total, "
        "SUM(CASE WHEN status='worth' THEN 1 ELSE 0 END) as worth_cnt "
        "FROM bottom_confirm_picks GROUP BY date ORDER BY date DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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
