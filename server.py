"""StockScope Backend — K线数据 + 自选股 + 每日选股 API"""
import os
import re
import sqlite3
import json
from datetime import datetime, timedelta
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# 拼音首字母映射(启动时加载一次, 用于首字母搜索; 文件不存在则空dict, 不影响其他功能)
try:
    _pinyin_map = json.load(open(os.path.join(os.path.dirname(__file__), 'pinyin_map.json'), encoding='utf-8'))
except Exception:
    _pinyin_map = {}

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
    # 拼音搜索: 纯字母输入走内存映射(启动时加载一次, 线性扫描5544条<2ms, 不影响DB)
    # 匹配: 首字母(wsxx) / 全拼(weishengxinxi) / 全拼前缀(weisheng)
    if re.fullmatch(r"[a-zA-Z]+", q):
        ql = q.lower()
        hits = [(s, v["name"]) for s, v in _pinyin_map.items()
                if ql in v["initials"] or ql in v.get("full", "")
                or v["initials"].startswith(ql) or v.get("full", "").startswith(ql)]
        hits.sort(key=lambda x: (x[1].startswith(ql) is False, x[0]))
        return jsonify([{"symbol": s, "name": n} for s, n in hits[:20]])
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
    
    # 市值(总市值/流通市值, 万元): 最新一条 basics (须在conn.close前查询)
    basic = cur.execute(
        "SELECT mktcap, nmc FROM stock_basics WHERE symbol=? ORDER BY date DESC LIMIT 1", (symbol,)
    ).fetchone()
    # 盘中预K线: 正式K(stock_daily)尚缺最新一天时, 用 preview_daily 最新批次补最后1根(盘后update_daily写入正式K后自动不补)
    try:
        pv = cur.execute(
            """SELECT date, open, high, low, close, close_qfq, volume, amount
               FROM preview_daily WHERE symbol = ?
               ORDER BY batch_date DESC, batch_seq DESC, date DESC LIMIT 1""",
            (symbol,)
        ).fetchone()
    except Exception:
        pv = None
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
    
    # 盘中预K线: 正式K缺最新一天时追加(仅1根, 盘后正式K入库后 date<=最后正式K 自动不再补)
    if pv and pv["date"] and (not kline or pv["date"] > kline[-1]["time"]):
        _r = {"date": pv["date"], "open": pv["open"], "high": pv["high"], "low": pv["low"],
              "close": pv["close"], "close_qfq": pv["close_qfq"], "volume": pv["volume"],
              "turnover": pv["amount"], "open_qfq": None, "high_qfq": None, "low_qfq": None}
        if use_qfq and _r["close_qfq"] and _r["close_qfq"] > 0:
            ratio = _r["close_qfq"] / _r["close"] if _r["close"] and _r["close"] > 0 else 1.0
            kline.append({
                "time": _r["date"],
                "open": round(_r["open"] * ratio, 2) if _r["open"] else 0,
                "high": round(_r["high"] * ratio, 2) if _r["high"] else 0,
                "low": round(_r["low"] * ratio, 2) if _r["low"] else 0,
                "close": round(_r["close_qfq"], 2),
                "volume": _r["volume"],
                "turnover": _r["turnover"] if _r["turnover"] else 0,
            })
        else:
            kline.append({
                "time": _r["date"],
                "open": round(_r["open"], 2) if _r["open"] else 0,
                "high": round(_r["high"], 2) if _r["high"] else 0,
                "low": round(_r["low"], 2) if _r["low"] else 0,
                "close": round(_r["close"], 2) if _r["close"] else 0,
                "volume": _r["volume"],
                "turnover": _r["turnover"] if _r["turnover"] else 0,
            })

    # Get signal markers from daily_picks (both local and trend_picks)
    signals = _get_stock_signals(symbol)
    
    return jsonify({"symbol": symbol, "kline": kline, "signals": signals,
                    "mktcap": basic[0] if basic else None, "nmc": basic[1] if basic else None})


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
    result = _add_ret_pct(result, buy_mode='same_day')
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
def _etf_sym_cond(col="symbol"):
    """ETF symbol SQL条件: 5/15/16开头"""
    return f"({col} LIKE '5%' OR {col} LIKE '15%' OR {col} LIKE '16%')"


@app.route("/api/chanlun/dates")
def api_chanlun_dates():
    """缠论信号日期列表: ?type=三买 时只统计该类型; type=二三买 统计重合信号; etf=1只看ETF日期"""
    conn = db_conn(TREND_DB)
    typ = request.args.get("type", "")
    preview = request.args.get("preview", "") == "1"
    etf = request.args.get("etf", "0") == "1"
    cat = request.args.get("category", "")
    # 指数维度(category=index): 实时COUNT(信号量小, 无需缓存表)
    if cat == "index" and not preview:
        if typ in ("二三买", "二三卖"):
            bt = "二买" if typ == "二三买" else "二卖"
            st = "三买" if typ == "二三买" else "三卖"
            rows = conn.execute(
                "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.status='ok' AND b.status='ok' AND a.signal_type=? AND b.signal_type=? AND a.category='index' "
                "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60", (bt, st)).fetchall()
        elif typ.lower() == "d3":
            rows = conn.execute("SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE category='index' AND d3=1 AND status='ok' GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        elif typ.lower() == "w30":
            rows = conn.execute("SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE category='index' AND w30=1 AND status='ok' GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        elif typ:
            rows = conn.execute("SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE category='index' AND status='ok' AND signal_type=? GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60", (typ,)).fetchall()
        else:
            rows = conn.execute("SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE category='index' AND status='ok' GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        conn.close()
        return json.dumps([{"date": r[0], "total": r[1]} for r in rows], ensure_ascii=False)
    # ETF过滤在SQL层(日期和数量都按ETF口径), etf=1只看ETF; 默认排除ETF只看股票
    ETC = _etf_sym_cond() if etf else "NOT " + _etf_sym_cond()
    # 读持久化缓存(chanlun_dates_cache, 每日计算后重建; 空表/无数据则现算兜底)
    if not preview:
        try:
            cached = conn.execute(
                "SELECT signal_date, total FROM chanlun_dates_cache WHERE typ=? AND etf=? "
                "ORDER BY signal_date DESC LIMIT 60", (typ, 1 if etf else 0)).fetchall()
            if cached:
                conn.close()
                return json.dumps([{"date": r[0], "total": r[1]} for r in cached], ensure_ascii=False)
        except Exception:
            pass
    try:
        if preview:
            # 盘中预览: 正式表(已确认) + 未确认增量(preview_signals的preview状态日期)
            # 盘中批次统一挂批次日(batch_date=今天): 缠论signal_date(可能=prev_day)不暴露到日期列表, 避免与正式表同日混淆
            # 类别过滤: 指数/ETF/股票各看各的盘中批次
            PVC = "category='" + ("index" if cat == "index" else ("etf" if etf else "stock")) + "'"
            CAT = PVC  # 正式表(两表同构category列)同口径
            pv_dates = [r[0] for r in conn.execute(
                f"SELECT DISTINCT batch_date FROM preview_signals WHERE (batch_date, batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND status='preview' AND {PVC}").fetchall()]
            if typ in ("二三买", "二三卖"):
                # 重合类: 正式表已确认重合 + preview表未确认重合 合并(修复: 原逻辑只查正式表, 预览日期丢失)
                bt = "二买" if typ == "二三买" else "二卖"
                st = "三买" if typ == "二三买" else "三卖"
                rows = conn.execute(
                    "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                    "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                    f"WHERE a.status='ok' AND b.status='ok' AND a.signal_type=? AND b.signal_type=? AND {CAT} "
                    "GROUP BY a.signal_date", (bt, st)).fetchall()
                pv_rows = conn.execute(
                    "SELECT a.batch_date, COUNT(DISTINCT a.symbol) FROM preview_signals a "
                    "JOIN preview_signals b ON a.symbol=b.symbol AND a.batch_date=b.batch_date AND a.category=b.category "
                    f"WHERE (a.batch_date, a.batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND a.status='preview' AND {PVC} AND a.signal_type=? AND b.signal_type=? "
                    "GROUP BY a.batch_date", (bt, st)).fetchall()
                merged = {}
                for d, n in rows + pv_rows:
                    merged[d] = max(merged.get(d, 0), n)
                out = sorted(merged.items(), key=lambda x: x[0], reverse=True)[:60]
                # 注: 原此处引用未定义的etf_dates/stock_dates(会NameError), ETC条件已在SQL里过滤, 无需重复过滤
                return json.dumps([{"date": d, "total": n} for d, n in out], ensure_ascii=False)
            if typ == "二三买":
                rows = conn.execute(
                    "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                    "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                    f"WHERE a.status='ok' AND b.status='ok' AND a.signal_type='二买' AND b.signal_type='三买' AND {CAT} "
                    "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60").fetchall()
            elif typ.lower() == "d3":
                rows = conn.execute(
                    f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE d3=1 AND status='ok' AND {CAT} "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
            elif typ.lower() == "w30":
                rows = conn.execute(
                    f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE w30=1 AND status='ok' AND {CAT} "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
            elif typ:
                rows = conn.execute(
                    f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE signal_type=? AND status='ok' AND {CAT} "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60", (typ,)).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE status='ok' AND {CAT} "
                    "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
            # 盘中批次统一挂批次日(batch_date=今天)展示; 正式表各日期计数原样保留(不再被preview同日期覆盖)
            if pv_dates:
                out = list(rows)
                pv_map = {}
                for r in conn.execute(
                        f"SELECT batch_date, signal_type, COUNT(*) FROM preview_signals WHERE (batch_date, batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND status='preview' AND {PVC} "
                        "GROUP BY batch_date, signal_type").fetchall():
                    pv_map.setdefault(r[0], {})[r[1]] = r[2]
                # 盘中批次日期(今天): 追加进日期列表
                have = {r[0] for r in out}
                for d in pv_dates:
                    cnt = sum(c for t, c in pv_map.get(d, {}).items() if not typ or t == typ)
                    if d in have:
                        out = [(dd, cnt if dd == d else n) for dd, n in out]
                    elif cnt:
                        out.append((d, cnt))
                out.sort(key=lambda x: x[0], reverse=True)
                rows = out[:60]
        elif typ == "二三买":
            rows = conn.execute(
                "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                f"WHERE a.status='ok' AND b.status='ok' AND a.signal_type='二买' AND b.signal_type='三买' AND {ETC} "
                "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60").fetchall()
        elif typ == "二三卖":
            rows = conn.execute(
                "SELECT a.signal_date, COUNT(DISTINCT a.symbol) FROM chanlun_signals a "
                "JOIN chanlun_signals b ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                f"WHERE a.status='ok' AND b.status='ok' AND a.signal_type='二卖' AND b.signal_type='三卖' AND {ETC} "
                "GROUP BY a.signal_date ORDER BY a.signal_date DESC LIMIT 60").fetchall()
        elif typ.lower() == "w30":
            # W30: worth确认后30天内的缠论买点(标记列) — 最近60个日期
            rows = conn.execute(
                f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE w30=1 AND status='ok' AND {ETC} "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        elif typ.lower() == "d3":
            # D3: 二买+老高5条件(标记列) — 最近60个日期
            rows = conn.execute(
                f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE d3=1 AND status='ok' AND {ETC} "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60").fetchall()
        elif typ:
            rows = conn.execute(
                f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE signal_type=? AND status='ok' AND {ETC} "
                "GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60", (typ,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT signal_date, COUNT(*) FROM chanlun_signals WHERE status='ok' AND {ETC} GROUP BY signal_date "
                "ORDER BY signal_date DESC LIMIT 60").fetchall()
    except Exception:
        return json.dumps([])
    return json.dumps([{"date": r[0], "total": r[1]} for r in rows], ensure_ascii=False)


_sig_cache = {}  # (date,type,preview,etf) -> (timestamp, json) 300s TTL


def _add_ret_pct(items, buy_mode='t1'):
    """给信号列表加'信号后涨跌幅'(前复权)
    buy_mode='t1': 买入基准=信号日T+1收盘(缠论, T+1确认→次日价); 'same_day': 基准=信号日当日收盘(选股/底部确认, 当日出信号)
    最新价: 更新日志表最新交易日一次范围查询(停牌fallback); 信号日价: 索引直查"""
    try:
        seq = db_conn(SEQUOIA_DB)
        syms = list({it["symbol"] for it in items if it.get("date")})
        latest = {}
        if syms:
            ph = ",".join("?" * len(syms))
            ld = seq.execute("SELECT latest_date FROM kline_update_log ORDER BY id DESC LIMIT 1").fetchone()
            latest_date = ld[0] if ld else None
            # 性能优化: 正常情况下直接用日志表最新交易日(避免全表MAX)
            # 仅当"信号日 > 日志日期"(日志stale/盘中新信号)时才查stock_daily实际MAX兜底
            max_sig_date = max(it["date"] for it in items if it.get("date")) if any(it.get("date") for it in items) else ""
            if latest_date is None or (max_sig_date and max_sig_date > latest_date):
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
        t1_price = {}
        if syms:
            ph = ",".join("?" * len(syms))
            dates = sorted({it["date"] for it in items if it.get("date")})
            dph = ",".join("?" * len(dates)) if dates else "''"
            # LEAD: 每根K线的下一交易日收盘(信号日T+1买入价用)
            # 对每只股票取 signal_date 之后的第一根K线close_qfq 作为 T+1 买入价
            for s, d, c in seq.execute(
                    "SELECT symbol, date, close_qfq FROM stock_daily "
                    "WHERE symbol IN (%s) AND date IN (%s) AND close_qfq>0" % (ph, dph),
                    syms + dates).fetchall():
                sig_close[(s, d)] = c
            # T+1买入价: 信号日之后(>date)第一根有收盘的K线, 一次范围查询按(symbol,date)索引取
            t1_price = {}
            if buy_mode == 't1' and syms and dates:
                min_d = min(dates)
                for s, d, c in seq.execute(
                        "WITH t AS (SELECT symbol, date, close_qfq, ROW_NUMBER() OVER "
                        "(PARTITION BY symbol ORDER BY date) rn FROM stock_daily "
                        "WHERE symbol IN (%s) AND date>=? AND close_qfq>0) "
                        "SELECT t1.symbol, t0.date, t1.close_qfq FROM t t0 JOIN t t1 "
                        "ON t0.symbol=t1.symbol AND t1.rn=t0.rn+1 "
                        "WHERE t0.symbol IN (%s)" % (ph, ph),
                        syms + [min_d] + syms).fetchall():
                    t1_price[(s, d)] = c
        for it in items:
            sc_ = (t1_price.get((it.get("symbol"), it.get("date"))) or sig_close.get((it.get("symbol"), it.get("date")))) if buy_mode == 't1' \
                else sig_close.get((it.get("symbol"), it.get("date")))
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
    ck = (date, typ, preview, request.args.get("etf", "0"), request.args.get("category", ""))
    hit = _sig_cache.get(ck)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        if preview:
            # 盘中预览: 未确认日期(preview状态)→预览表; 已确认日期→正式表
            # 盘中批次统一挂批次日(batch_date=今天): 缠论signal_date(可能=prev_day)不暴露到日期列表, 避免与正式表同日混淆
            # 类别过滤: category=index→指数 / etf=1→ETF / 默认→股票(preview表category列: stock/etf/index)
            _cat = "index" if request.args.get("category") == "index" else ("etf" if request.args.get("etf") == "1" else "stock")
            PVC = f"category='{_cat}'"
            pv_dates = [r[0] for r in conn.execute(
                f"SELECT DISTINCT batch_date FROM preview_signals WHERE (batch_date, batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND status='preview' AND {PVC}").fetchall()]
            if date in pv_dates:
                if typ.lower() == "d3":
                    rows = conn.execute(
                        "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score, category FROM preview_signals "
                        "WHERE batch_date=? AND d3=1 AND {PVC} ORDER BY symbol", (date,)).fetchall()
                elif typ.lower() == "w30":
                    rows = conn.execute(
                        "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score, category FROM preview_signals "
                        "WHERE batch_date=? AND w30=1 AND {PVC} ORDER BY symbol", (date,)).fetchall()
                elif typ == "二三买":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score, a.category "
                        "FROM preview_signals a JOIN preview_signals b "
                        "ON a.symbol=b.symbol AND a.batch_date=b.batch_date AND a.category=b.category "
                        "WHERE (a.batch_date, a.batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND a.batch_date=? AND {PVC} AND ((a.signal_type='二买' AND b.signal_type='三买') "
                        "OR (a.signal_type='三买' AND b.signal_type='二买')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                elif typ == "二三卖":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score, a.category "
                        "FROM preview_signals a JOIN preview_signals b "
                        "ON a.symbol=b.symbol AND a.batch_date=b.batch_date AND a.category=b.category "
                        "WHERE (a.batch_date, a.batch_seq) IN (SELECT batch_date, batch_seq FROM preview_signals ORDER BY batch_date DESC, batch_seq DESC LIMIT 1) AND a.batch_date=? AND {PVC} AND ((a.signal_type='二卖' AND b.signal_type='三卖') "
                        "OR (a.signal_type='三卖' AND b.signal_type='二卖')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                else:
                    q = f"SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score, category FROM preview_signals WHERE batch_date=? AND {PVC}"
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
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score, a.category "
                        "FROM chanlun_signals a JOIN chanlun_signals b "
                        "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                        "WHERE a.signal_date=? AND ((a.signal_type='二买' AND b.signal_type='三买') "
                        "OR (a.signal_type='三买' AND b.signal_type='二买')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                elif typ == "二三卖":
                    rows = conn.execute(
                        "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score, a.category "
                        "FROM chanlun_signals a JOIN chanlun_signals b "
                        "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                        "WHERE a.signal_date=? AND ((a.signal_type='二卖' AND b.signal_type='三卖') "
                        "OR (a.signal_type='三卖' AND b.signal_type='二卖')) "
                        "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
                else:
                    q = "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score, category FROM chanlun_signals WHERE signal_date=?"
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
                "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score, a.category "
                "FROM chanlun_signals a JOIN chanlun_signals b "
                "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.signal_date=? AND ((a.signal_type='二买' AND b.signal_type='三买') "
                "OR (a.signal_type='三买' AND b.signal_type='二买')) "
                "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
        elif typ == "二三卖":
            rows = conn.execute(
                "SELECT a.symbol, a.name, a.signal_type, a.signal_date, a.price, a.ref_zd, a.ref_zg, a.status, a.strength, a.strength_score, a.category "
                "FROM chanlun_signals a JOIN chanlun_signals b "
                "ON a.symbol=b.symbol AND a.signal_date=b.signal_date "
                "WHERE a.signal_date=? AND ((a.signal_type='二卖' AND b.signal_type='三卖') "
                "OR (a.signal_type='三卖' AND b.signal_type='二卖')) "
                "ORDER BY a.symbol, a.signal_type", (date,)).fetchall()
        else:
            q = "SELECT symbol, name, signal_type, signal_date, price, ref_zd, ref_zg, status, strength, strength_score, category FROM chanlun_signals WHERE signal_date=?"
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
              "score": r[9] if len(r) > 9 else 50,
              "category": r[10] if len(r) > 10 and r[10] else ("index" if "." in r[0] else "stock")} for r in rows]
    # 类别过滤: category参数优先(index/etf/stock, 用DB列); 向后兼容etf=1(按symbol前缀)
    # 默认(无category参数): 排除指数和ETF, 保持股票视图
    etf = request.args.get("etf", "0") == "1"
    cat = request.args.get("category", "")
    if cat in ("index", "etf", "stock"):
        items = [it for it in items if it.get("category") == cat]
    elif etf:
        items = [it for it in items if it["symbol"][:2] in ("51", "15", "16", "56", "58") or it["symbol"].startswith("5")]
    else:
        items = [it for it in items if not (it["symbol"][:2] in ("51", "15", "16", "56", "58") or it["symbol"].startswith("5"))
                 and "." not in it["symbol"]]
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
    result = _add_ret_pct(result, buy_mode='same_day')
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


# ── 缠论板块共振 (热力矩阵 + 板块信号) ────────────────────────
CONCEPT_DB = "/home/ubuntu/databases/concept_map.db"
BOARD_BLACKLIST = {'昨日涨停', '昨日涨停_含一字', '昨日首板', '昨日连板', '东方财富热股',
                   '融资融券', '转融券标的', '机构重仓', '基金重仓', '深股通', '沪股通',
                   'MSCI中国', '标准普尔', '富时罗素', 'AH股', '破净股', '低价股', '高送转',
                   '预盈预增', '预亏预减', '区块链', '次新股', '壳资源', 'ST股', '百元股',
                   '微利股', '创业成份', '大盘价值', '价值股', '参股保险', '参股券商', '参股新三板',
                   '中字头', '央企改革', '国企改革', '地方国资改革', '上证180', '上证50_', '沪深300_', '中证500',
                   '上证380', '中盘股', '深成500', '红利股', '小盘股', '大盘股', '巨潮100',
                   '上证A股', '深证A股', '创业板综', '科创50_', '融资标的', '深股通标的',
                   'QFII重仓', '社保重仓', '信托重仓', '券商重仓', '保险重仓', '标普道琼斯A股',
                   '央视50', '沪股通标的', '富时A股', '深证100', '沪深300成份', '上证50成份'}
BUY_TYPES = {'一买', '二买', '三买'}

@app.route("/api/board/matrix")
def api_board_matrix():
    """热力矩阵: 近N日 板块×日期 买/卖信号数. 参数 days=N(默认15)&dimension=concept|industry|region"""
    try:
        days = int(request.args.get("days", 15))
    except Exception:
        days = 15
    days = min(max(days, 5), 60)
    dimension = request.args.get("dimension", "concept")
    if dimension not in ("concept", "industry", "region"):
        dimension = "concept"
    conn = db_conn(TREND_DB)
    ccon = db_conn(CONCEPT_DB)
    try:
        # 最近N个有信号的交易日
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT signal_date FROM chanlun_signals WHERE status='ok' "
            "ORDER BY signal_date DESC LIMIT ?", (days,)).fetchall()]
        dates = list(reversed(dates))  # 旧→新
        if not dates:
            return jsonify({"dates": [], "boards": [], "signals": []})
        ph = ",".join("?" * len(dates))
        # 概念映射(按维度)
        try:
            mem = ccon.execute(
                "SELECT code, concept FROM concept_members WHERE dimension=?", (dimension,)).fetchall()
        except Exception:
            mem = []
        cmap = {}
        for code, concept in mem:
            cmap.setdefault(code.zfill(6), set()).add(concept)
        # 聚合 (concept, date) -> [buy, sell, strong, total]
        sigs2 = conn.execute(
            f"SELECT symbol, signal_type, strength, signal_date FROM chanlun_signals "
            f"WHERE signal_date IN ({ph}) AND status='ok' AND category!='index'",
            dates).fetchall()
        agg = {}
        for sym, stype, strength, sdate in sigs2:
            sym6 = sym.zfill(6)
            for concept in cmap.get(sym6, []):
                if concept in BOARD_BLACKLIST:
                    continue
                key = (concept, sdate)
                a = agg.setdefault(key, [0, 0, 0, 0])  # buy, sell, strong, total
                if stype in BUY_TYPES:
                    a[0] += 1
                else:
                    a[1] += 1
                if strength == 'strong':
                    a[2] += 1
                a[3] += 1
        # 板块列表 + 共振分(近3日买信号数×2 + 总信号数, 降序)
        board_stats = {}
        for (concept, sdate), (b, s, st, t) in agg.items():
            st0 = board_stats.setdefault(concept, [0, 0, 0])  # buy3d, total3d, maxbuy
            if sdate >= dates[-3] if len(dates) >= 3 else True:
                st0[0] += b
                st0[1] += t
            st0[2] = max(st0[2], b)
        boards = sorted(board_stats.items(),
                        key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:40]
        # 矩阵数据: 每个板块每日期 (buy, sell, strong)
        board_names = [c for c, _ in boards]
        rows = []
        for concept in board_names:
            row = {"name": concept, "cells": []}
            for d in dates:
                b, s, st, t = agg.get((concept, d), [0, 0, 0, 0])
                row["cells"].append({"date": d, "buy": b, "sell": s, "strong": st, "total": t})
            rows.append(row)
        return jsonify({"dates": dates, "boards": rows, "dimension": dimension})
    finally:
        conn.close()
        ccon.close()


@app.route("/api/board/signals")
def api_board_signals():
    """某日某板块的信号明细. 参数 date=YYYY-MM-DD&concept=板块名&dimension=concept|industry|region"""
    date = request.args.get("date", "")
    concept = request.args.get("concept", "")
    dimension = request.args.get("dimension", "concept")
    if not date or not concept:
        return jsonify({"items": [], "error": "date/concept required"})
    conn = db_conn(TREND_DB)
    ccon = db_conn(CONCEPT_DB)
    try:
        try:
            members = [r[0] for r in ccon.execute(
                "SELECT code FROM concept_members WHERE concept=? AND dimension=?",
                (concept, dimension)).fetchall()]
        except Exception:
            members = []
        if not members:
            return jsonify({"items": [], "error": "板块无成分"})
        ph = ",".join("?" * len(members))
        # 该板块当日信号(含名称/分数/强度) — 与缠论tab同字段
        items = conn.execute(
            f"SELECT symbol, name, signal_type, strength, strength_score, price, status "
            f"FROM chanlun_signals WHERE signal_date=? AND status='ok' "
            f"AND symbol IN ({ph}) AND category!='index' ORDER BY "
            f"CASE signal_type WHEN '一买' THEN 1 WHEN '二买' THEN 2 WHEN '三买' THEN 3 "
            f"WHEN '一卖' THEN 4 WHEN '二卖' THEN 5 WHEN '三卖' THEN 6 ELSE 7 END",
            [date] + members).fetchall()
        # 合并同股多信号: 同一 symbol 多条 → 单条, types 拼进 type 字段
        merged = {}
        for r in items:
            sym = r[0]
            if sym not in merged:
                merged[sym] = {"symbol": r[0], "name": r[1], "type": r[2], "strength": r[3],
                               "score": r[4], "price": r[5], "status": r[6], "types": [r[2]]}
            else:
                merged[sym]["types"].append(r[2])
                # 类型排序: 按买卖类型顺序
                order = {'一买': 1, '二买': 2, '三买': 3, '一卖': 4, '二卖': 5, '三卖': 6}
                merged[sym]["types"].sort(key=lambda t: order.get(t, 9))
                merged[sym]["type"] = "+".join(merged[sym]["types"])
                # 强度取更强
                prio = {'strong': 0, 'neutral': 1, 'weak': 2}
                if prio.get(r[3], 1) < prio.get(merged[sym]["strength"], 1):
                    merged[sym]["strength"] = r[3]
        out = list(merged.values())
        return jsonify({"items": out, "date": date, "concept": concept, "dimension": dimension})
    finally:
        conn.close()
        ccon.close()


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
