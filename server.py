"""StockScope Backend — K线数据 + 自选股 + 每日选股 API"""
import os
import sqlite3
import json
from datetime import datetime, timedelta
import time
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


class PrefixMiddleware:
    """兼容路径前缀: 直接访问 :8004 时 /stockscope/xxx → /xxx (nginx已去前缀, 不受影响)"""

    def __init__(self, app, prefix="/stockscope"):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith(self.prefix):
            environ["PATH_INFO"] = path[len(self.prefix):] or "/"
        return self.app(environ, start_response)


app.wsgi_app = PrefixMiddleware(app.wsgi_app)
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
        "SELECT symbol, name FROM stock_basics WHERE symbol LIKE ? OR name LIKE ? GROUP BY symbol LIMIT 20",
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
        "SELECT date, strategy_id, name FROM daily_picks WHERE symbol = ? ORDER BY date DESC",
        (symbol,)
    ).fetchall()
    # 底部确认策略信号(买入+观察)
    bc_rows = cur.execute(
        "SELECT date, name, status FROM bottom_confirm_picks "
        "WHERE symbol = ? AND status IN ('worth', 'watch') ORDER BY date DESC",
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
    result = _add_ret_pct(result)
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
            "SELECT date, total_picks, strategies FROM daily_summary WHERE total_picks > 0 ORDER BY date DESC LIMIT 200"
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


_d3_kline_cache = {}  # symbol -> (closes, vols, d2i) 模块级缓存(K线)


def _d3_fetch(symbol):
    """查K线(带缓存)"""
    if symbol in _d3_kline_cache:
        return _d3_kline_cache[symbol]
    conn = db_conn(SEQUOIA_DB)
    rows = conn.execute(
        "SELECT date, close, close_qfq, volume FROM stock_daily "
        "WHERE symbol=? AND close_qfq>0 ORDER BY date", (symbol,)).fetchall()
    conn.close()
    if len(rows) < 120:
        _d3_kline_cache[symbol] = None
        return None
    pc = ([r[2] for r in rows], [r[3] for r in rows], {r[0]: i for i, r in enumerate(rows)})
    _d3_kline_cache[symbol] = pc
    return pc


def _d3_check_data(closes, vols, d2i, sdate):
    """D3老高5条件(纯内存): 均线多头+回踩2-15%+底部≥120天+均线上翘+放量启动"""
    idx = d2i.get(sdate)
    if idx is None or idx < 60:
        return False
    i = idx
    cur = closes[i]
    ma20 = sum(closes[i - 19:i + 1]) / 20
    ma60 = sum(closes[i - 59:i + 1]) / 60
    ma20_5 = sum(closes[i - 24:i - 4]) / 20
    if ma60 <= 0 or not (cur > ma20 > ma60):
        return False
    dist = (cur - ma20) / ma20 * 100
    if not (2 <= dist <= 15):
        return False
    lo = min(closes[max(0, i - 249):i + 1])
    li = closes[max(0, i - 249):i + 1].index(lo) + max(0, i - 249)
    if (i - li) < 120:
        return False
    if not (ma20 > ma20_5):
        return False
    for k in range(1, 11):
        if i - k < 1:
            break
        chg = (closes[i - k] / closes[i - k - 1] - 1) * 100
        a = sum(vols[max(0, i - k - 20):i - k]) / min(20, i - k) if i - k > 0 else 1
        if chg >= 3 and (vols[i - k] / a if a else 0) >= 1.5:
            return True
    return False


def _d3_check(symbol, sdate):
    pc = _d3_fetch(symbol)
    if pc is None:
        return False
    return _d3_check_data(pc[0], pc[1], pc[2], sdate)


def _d3_warmup(conn, dates):
    """批量预热K线缓存: 一次SQL拿全部所需股票K线"""
    syms = set()
    for (d,) in dates:
        for r in conn.execute(
                "SELECT symbol FROM chanlun_signals WHERE signal_date=? AND status='ok' AND signal_type='二买' AND status='ok'",
                (d,)).fetchall():
            syms.add(r[0])
    missing = [s for s in syms if s not in _d3_kline_cache]
    if not missing:
        return
    seq = db_conn(SEQUOIA_DB)
    for i in range(0, len(missing), 300):
        batch = missing[i:i + 300]
        rows = seq.execute(
            f"SELECT symbol, date, close_qfq, volume FROM stock_daily "
            f"WHERE symbol IN ({','.join('?' * len(batch))}) AND close_qfq>0 ORDER BY symbol, date",
            batch).fetchall()
        per = {}
        for r in rows:
            per.setdefault(r[0], []).append(r)
        for s in batch:
            k = per.get(s, [])
            if len(k) < 120:
                _d3_kline_cache[s] = None
            else:
                # r[1]=date, r[2]=close_qfq, r[3]=volume
                _d3_kline_cache[s] = ([r[2] for r in k], [r[3] for r in k], {r[1]: i for i, r in enumerate(k)})
    seq.close()


def _d3_list(conn, date):
    """date当日符合D3(标记列)的二买信号"""
    return conn.execute(
        "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM chanlun_signals "
        "WHERE signal_date=? AND d3=1 ORDER BY symbol", (date,)).fetchall()


_worth_map = None


def _worth_load(conn):
    """worth确认日期映射: {symbol: [W日...]}"""
    global _worth_map
    if _worth_map is None:
        _worth_map = {}
        for r in conn.execute(
                "SELECT date, symbol FROM bottom_confirm_picks WHERE status='worth'").fetchall():
            _worth_map.setdefault(r[1], []).append(r[0])
    return _worth_map


def _w30_check(conn, symbol, sdate):
    """缠论买点信号日 是否在 worth确认后30天内(含同日)"""
    import datetime
    wm = _worth_load(conn)
    for w in wm.get(symbol, []):
        d0 = datetime.date.fromisoformat(w)
        d1 = datetime.date.fromisoformat(sdate)
        if 0 <= (d1 - d0).days <= 30:
            return True
    return False


def _w30_list(conn, date):
    """date当日 符合'worth确认后30天内'(标记列)的缠论买点"""
    return conn.execute(
        "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM chanlun_signals "
        "WHERE signal_date=? AND w30=1 ORDER BY symbol", (date,)).fetchall()


# ── 缠论信号(静态路由必须先于 /api/chanlun/<symbol>, 否则被当作symbol) ──
@app.route("/api/chanlun/dates")
def api_chanlun_dates():
    """缠论信号日期列表: ?type=三买 时只统计该类型; type=二三买 统计重合信号"""
    conn = db_conn(TREND_DB)
    typ = request.args.get("type", "")
    preview = request.args.get("preview", "") == "1"
    try:
        if preview:
            # 盘中预览: 正式表(已确认) + 未确认增量(preview_signals的preview状态日期)
            pv_dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT signal_date FROM preview_signals WHERE (batch_date, batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND status='preview'").fetchall()]
            if typ in ("二三买", "二三卖"):
                # 重合类: 正式表已确认重合 + preview表未确认重合 合并(修复: 原逻辑只查正式表, 预览日期丢失)
                bt = "二买" if typ == "二三买" else "二卖"
                st = "三买" if typ == "二三买" else "三卖"
                rows = conn.execute(
                    "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                    "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                    "WHERE a.status='ok' AND b.status='ok' AND a.signal_type=? AND b.signal_type=? "
                    "GROUP BY a.signal_date", (bt, st)).fetchall()
                pv_rows = conn.execute(
                    "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM preview_signals a "
                    "JOIN preview_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                    "WHERE (a.batch_date, a.batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND a.status='preview' AND a.signal_type=? AND b.signal_type=? "
                    "GROUP BY a.signal_date", (bt, st)).fetchall()
                merged = {}
                for d, n in rows + pv_rows:
                    merged[d] = max(merged.get(d, 0), n)
                out = sorted(merged.items(), key=lambda x: x[0], reverse=True)[:60]
                return json.dumps([{"date": d, "total": n} for d, n in out], ensure_ascii=False)
            if typ == "二三买":
                rows = conn.execute(
                    "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                    "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                    "WHERE a.status='ok' AND b.status='ok' AND a.signal_type='二买' AND b.signal_type='三买' "
                    "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60").fetchall()
            elif typ.lower() == "d3":
                rows = conn.execute(
                    "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE d3=1 AND status='ok' "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
            elif typ.lower() == "w30":
                rows = conn.execute(
                    "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE w30=1 AND status='ok' "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
            elif typ:
                rows = conn.execute(
                    "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE signal_type=? AND status='ok' "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60", (typ,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE status='ok' "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
            # 用预览数量覆盖未确认日期(8/3、8/4)
            if pv_dates:
                out = []
                pv_map = {}
                for r in conn.execute(
                        "SELECT signal_date, signal_type, COUNT(*) FROM preview_signals WHERE (batch_date, batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND status='preview' "
                        "GROUP BY signal_date, signal_type").fetchall():
                    pv_map.setdefault(r[0], {})[r[1]] = r[2]
                for d, n in rows:
                    if d in pv_dates:
                        cnt = 0
                        for t, c in pv_map.get(d, {}).items():
                            if typ and t != typ:
                                continue
                            cnt += c
                        if cnt:
                            out.append((d, cnt))
                    else:
                        out.append((d, n))
                # 预览独有日期(正式表没有的, 如8/4)
                have = {r[0] for r in out}
                for d in pv_dates:
                    if d not in have:
                        cnt = sum(c for t, c in pv_map.get(d, {}).items() if not typ or t == typ)
                        if cnt:
                            out.append((d, cnt))
                out.sort(key=lambda x: x[0], reverse=True)
                rows = out[:60]
        elif typ == "二三买":
            rows = conn.execute(
                "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.status='ok' AND b.status='ok' AND a.signal_type='二买' AND b.signal_type='三买' "
                "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60").fetchall()
        elif typ == "二三卖":
            rows = conn.execute(
                "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.status='ok' AND b.status='ok' AND a.signal_type='二卖' AND b.signal_type='三卖' "
                "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60").fetchall()
        elif typ.lower() == "w30":
            # W30: worth确认后30天内的缠论买点(标记列) — 最近60个日期
            rows = conn.execute(
                "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE w30=1 AND status='ok' "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        elif typ.lower() == "d3":
            # D3: 二买+老高5条件(标记列) — 最近60个日期
            rows = conn.execute(
                "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE d3=1 AND status='ok' "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        elif typ:
            rows = conn.execute(
                "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE signal_type=? AND status='ok' "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60", (typ,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE status='ok' GROUP BY signal_date "
                "ORDER BY signal_date DESC LIMIT 60").fetchall()
    except Exception:
        return json.dumps([])
    return json.dumps([{"date": r[0], "total": r[1]} for r in rows], ensure_ascii=False)


_sig_cache = {}  # (date,type,preview) -> (timestamp, json) 300s TTL


def _add_ret_pct(items):
    """给信号列表加'信号后涨跌幅'(信号日收盘→最新K线收盘, 前复权)
    最新价: 更新日志表最新交易日一次范围查询(停牌fallback); 信号日价: 索引直查"""
    try:
        seq = db_conn(SEQUOIA_DB)
        syms = list({it["symbol"] for it in items if it.get("date")})
        latest = {}
        if syms:
            ph = ",".join("?" * len(syms))
            ld = seq.execute("SELECT latest_date FROM kline_update_log ORDER BY id DESC LIMIT 1").fetchone()
            latest_date = ld[0] if ld else None
            # 兜底: stock_daily实际最新日期可能比日志表新(日线拉取未写日志), 取较大者
            sd_max = seq.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
            if latest_date is None or sd_max > latest_date:
                latest_date = sd_max
            for s, c in seq.execute(
                    "SELECT symbol, close_qfq FROM stock_daily WHERE date=? AND symbol IN (%s) AND close_qfq>0" % ph,
                    [latest_date] + syms).fetchall():
                latest[s] = c
            missing = [s for s in syms if s not in latest]
            if missing:
                mph = ",".join("?" * len(missing))
                for s, c in seq.execute(
                        "SELECT d.symbol, d.close_qfq FROM stock_daily d JOIN ("
                        "SELECT symbol, MAX(date) m FROM stock_daily WHERE symbol IN (%s) AND close_qfq>0 GROUP BY symbol"
                        ") x ON d.symbol=x.symbol AND d.date=x.m" % mph, missing).fetchall():
                    latest[s] = c
        sig_close = {}
        if syms:
            ph = ",".join("?" * len(syms))
            dates = sorted({it["date"] for it in items if it.get("date")})
            dph = ",".join("?" * len(dates)) if dates else "''"
            for s, d, c in seq.execute(
                    "SELECT symbol, date, close_qfq FROM stock_daily "
                    "WHERE symbol IN (%s) AND date IN (%s) AND close_qfq>0" % (ph, dph),
                    syms + dates).fetchall():
                sig_close[(s, d)] = c
        for it in items:
            sc_ = sig_close.get((it.get("symbol"), it.get("date")))
            lc = latest.get(it.get("symbol"))
            it["ret_pct"] = round((lc / sc_ - 1) * 100, 1) if sc_ and lc else None
        seq.close()
    except Exception:
        for it in items:
            it["ret_pct"] = None
    return items

@app.route("/api/chanlun/signals")
def api_chanlun_signals():
    """缠论信号列表: ?date=2026-07-30&type=三买 | type=二三买 时返回同股同日二买+三买重合"""
    date = request.args.get("date", "")
    typ = request.args.get("type", "")
    conn = db_conn(TREND_DB)
    preview = request.args.get("preview", "") == "1"
    ck = (date, typ, preview)
    hit = _sig_cache.get(ck)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        if preview:
            # 盘中预览: 未确认日期(preview状态)→预览表; 已确认日期→正式表
            pv_dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT signal_date FROM preview_signals WHERE (batch_date, batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND status='preview'").fetchall()]
            if date in pv_dates:
                if typ.lower() == "d3":
                    rows = conn.execute(
                        "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM preview_signals "
                        "WHERE signal_date=? AND d3=1 ORDER BY symbol", (date,)).fetchall()
                elif typ.lower() == "w30":
                    rows = conn.execute(
                        "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM preview_signals "
                        "WHERE signal_date=? AND w30=1 ORDER BY symbol", (date,)).fetchall()
                elif typ == "二三买":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score "
                        "FROM preview_signals a JOIN preview_signals b "
                        "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                        "WHERE (a.batch_date, a.batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND a.signal_date=? AND ((a.signal_type='二买' AND b.signal_type='三买') "
                        "OR (a.signal_type='三买' AND b.signal_type='二买')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                elif typ == "二三卖":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score "
                        "FROM preview_signals a JOIN preview_signals b "
                        "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                        "WHERE (a.batch_date, a.batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND a.signal_date=? AND ((a.signal_type='二卖' AND b.signal_type='三卖') "
                        "OR (a.signal_type='三卖' AND b.signal_type='二卖')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                else:
                    q = "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM preview_signals WHERE signal_date=?"
                    args = [date]
                    if typ:
                        q += " AND signal_type=?"
                        args.append(typ)
                    q += " ORDER BY signal_type, symbol"
                    rows = conn.execute(q, args).fetchall()
            else:
                # 已确认日期 → 正式表(与正常模式完全一致)
                if typ.lower() == "d3":
                    rows = _d3_list(conn, date)
                elif typ.lower() == "w30":
                    rows = _w30_list(conn, date)
                elif typ == "二三买":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score "
                        "FROM chanlun_signals a JOIN chanlun_signals b "
                        "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                        "WHERE a.signal_date=? AND ((a.signal_type='二买' AND b.signal_type='三买') "
                        "OR (a.signal_type='三买' AND b.signal_type='二买')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                elif typ == "二三卖":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score "
                        "FROM chanlun_signals a JOIN chanlun_signals b "
                        "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                        "WHERE a.signal_date=? AND ((a.signal_type='二卖' AND b.signal_type='三卖') "
                        "OR (a.signal_type='三卖' AND b.signal_type='二卖')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                else:
                    q = "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM chanlun_signals WHERE signal_date=?"
                    args = [date]
                    if typ:
                        q += " AND signal_type=?"
                        args.append(typ)
                    q += " ORDER BY signal_type, symbol"
                    rows = conn.execute(q, args).fetchall()
        elif typ.lower() == "w30":
            rows = _w30_list(conn, date)
        elif typ.lower() == "d3":
            rows = _d3_list(conn, date)
        elif typ == "二三买":
            # 返回双行(二买+三买各自带status), 前端合并逻辑自动逐类型标✗
            rows = conn.execute(
                "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score "
                "FROM chanlun_signals a JOIN chanlun_signals b "
                "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.signal_date=? AND ((a.signal_type='二买' AND b.signal_type='三买') "
                "OR (a.signal_type='三买' AND b.signal_type='二买')) "
                "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
        elif typ == "二三卖":
            rows = conn.execute(
                "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score "
                "FROM chanlun_signals a JOIN chanlun_signals b "
                "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.signal_date=? AND ((a.signal_type='二卖' AND b.signal_type='三卖') "
                "OR (a.signal_type='三卖' AND b.signal_type='二卖')) "
                "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
        else:
            q = "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score FROM chanlun_signals WHERE signal_date=?"
            args = [date]
            if typ:
                q += " AND signal_type=?"
                args.append(typ)
            q += " ORDER BY signal_type, symbol"
            rows = conn.execute(q, args).fetchall()
    except Exception:
        return json.dumps([])
    items = [{"symbol": r[0], "name": r[1], "type": r[2], "date": r[3],
              "price": r[4], "zd": r[5], "zg": r[6],
              "status": r[7] if len(r) > 7 else "ok",
              "strength": r[8] if len(r) > 8 else "neutral",
              "score": r[9] if len(r) > 9 else 50} for r in rows]
    items = _add_ret_pct(items)
    order = {"strong": 0, "neutral": 1, "weak": 2}
    items.sort(key=lambda x: (order.get(x["strength"], 1), -(x.get("score") or 50), x["type"], x["symbol"]))
    out = json.dumps(items, ensure_ascii=False)
    _sig_cache[ck] = (time.time(), out)
    return out


# ── Stock basic info ──────────────────────────────────────────
@app.route("/api/chanlun/<symbol>")
def api_chanlun(symbol):
    """缠论分析(完整版): 笔/线段/中枢/走势类型/背驰/买卖点
    附加: DB中被推翻的信号(status='error')以灰色✗标记追加到buy_sell
    可选: ?as_of=YYYY-MM-DD 回放该日期的结构(动态中枢, light模式只返回中枢)"""
    import chanlun_full
    as_of = request.args.get("as_of")
    d = chanlun_full.analyze(symbol, as_of=as_of, light=bool(as_of))
    if as_of:
        # 动态中枢请求: 只返回中枢+基础信息, 不追加errs
        return json.dumps(d, ensure_ascii=False)
    try:
        mp = db_conn(TREND_DB)
        errs = mp.execute(
            "SELECT signal_type, signal_date, price, confirmed_date, confirmed_later FROM chanlun_signals "
            "WHERE symbol=? AND status='error' AND confirmed_date IS NOT NULL", (symbol,)).fetchall()
        # 全历史信号(DB): K线markers数据源 — 每信号带status/confirmed_later/延迟天数
        dbs = mp.execute(
            "SELECT signal_type, signal_date, price, status, confirmed_date, confirmed_later, overturned_date FROM chanlun_signals "
            "WHERE symbol=? ORDER BY signal_date", (symbol,)).fetchall()
        # 延迟天数(交易日差): confirm_days=确认延迟, ov_days=推翻延迟 — K线标注用
        confirm_days = {}
        ov_days = {}
        try:
            sq = db_conn("/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db")
            tds = [r[0] for r in sq.execute(
                "SELECT DISTINCT date FROM stock_daily WHERE symbol=? AND close_qfq>0 ORDER BY date", (symbol,)).fetchall()]
            sq.close()
            td_idx = {d: i for i, d in enumerate(tds)}
            for ty, sd, p, st, cd, cl, od in dbs:
                si = td_idx.get(sd, -1)
                if si >= 0:
                    ci = td_idx.get(cd, -1)
                    oi = td_idx.get(od, -1)
                    if ci > si:
                        confirm_days[(ty, sd)] = ci - si
                    if oi > si:
                        ov_days[(ty, sd)] = oi - si
        except Exception:
            pass
        d["db_signals"] = [{"time": t, "type": ty, "price": round(p, 2) if p else 0,
                            "status": st, "confirmed_date": cd, "confirmed_later": cl,
                            "confirm_days": confirm_days.get((ty, t)), "ov_days": ov_days.get((ty, t))}
                           for ty, t, p, st, cd, cl, od in dbs]
        mp.close()
        for t, sd, p, cd, cl in errs:
            d.setdefault("buy_sell", []).append(
                {"time": sd, "type": f"✗{t}", "price": round(p, 2) if p else 0, "status": "error",
                 "confirmed_date": cd, "confirmed_later": cl})
    except Exception:
        pass
    # 实时信号确认信息: 优先用DB回放判定的confirmed_later(只有真正延迟确认的才标"后"), 无记录=当时确认(0)
    try:
        mp2 = db_conn(TREND_DB)
        later_rows = mp2.execute(
            "SELECT signal_type, signal_date FROM chanlun_signals WHERE symbol=? AND confirmed_later=1", (symbol,)).fetchall()
        mp2.close()
        later_set = {(t, d) for t, d in later_rows}
    except Exception:
        later_set = set()
    for bs in d.get("buy_sell", []):
        if bs.get("status") == "error":
            continue
        bs["confirmed_later"] = 1 if (bs.get("type", ""), bs.get("time", "")) in later_set else 0
    for bs in d.get("chain", []):
        bs["confirmed_later"] = 1 if (bs.get("type", ""), bs.get("time", "")) in later_set else 0
    for bs in d.get("sell_chain", []):
        bs["confirmed_later"] = 1 if (bs.get("type", ""), bs.get("time", "")) in later_set else 0
    return json.dumps(d, ensure_ascii=False)


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
    result = _add_ret_pct(result)
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
