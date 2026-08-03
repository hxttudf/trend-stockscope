#!/usr/bin/env python3
"""缠论分析API模块: K线合并→分型→笔→中枢→背驰→一二三类买卖点
供 server.py 的 /api/chanlun/<symbol> 使用
"""
import sqlite3

SEQUOIA_DB = "/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db"


def merge_inclusion(k):
    """k: [[date, h, l, close], ...] 前复权高低点 → 合并后 [[date, h, l]]"""
    merged = []
    for r in k:
        h, l = r[1], r[2]
        if not merged:
            merged.append([r[0], h, l])
            continue
        ph, pl = merged[-1][1], merged[-1][2]
        if (h >= ph and l <= pl) or (h <= ph and l >= pl):
            if len(merged) >= 2:
                dir_up = merged[-1][1] > merged[-2][1]
            else:
                dir_up = h >= ph
            if dir_up:
                merged[-1][1] = max(ph, h)
                merged[-1][2] = max(pl, l)
            else:
                merged[-1][1] = min(ph, h)
                merged[-1][2] = min(pl, l)
        else:
            merged.append([r[0], h, l])
    return merged


def analyze(symbol):
    """返回缠论分析结果dict"""
    conn = sqlite3.connect(SEQUOIA_DB)
    rows = conn.execute(
        "SELECT date, open, high, low, close, close_qfq, volume FROM stock_daily "
        "WHERE symbol=? AND close_qfq>0 ORDER BY date", (symbol,)).fetchall()
    conn.close()
    if len(rows) < 120:
        return {"error": "数据不足"}

    # 前复权修正 high/low
    qf_rows = []
    for r in rows:
        ratio = r[5] / r[4] if r[4] else 1
        qf_rows.append([r[0], r[2] * ratio, r[3] * ratio, r[5]])

    merged = merge_inclusion(qf_rows)

    # 分型
    fractals = []
    for i in range(1, len(merged) - 1):
        h0, l0 = merged[i-1][1], merged[i-1][2]
        h1, l1 = merged[i][1], merged[i][2]
        h2, l2 = merged[i+1][1], merged[i+1][2]
        if h1 > h0 and h1 > h2 and l1 > l0 and l1 > l2:
            fractals.append((i, 'top', h1))
        elif l1 < l0 and l1 < l2 and h1 < h0 and h1 < h2:
            fractals.append((i, 'bottom', l1))

    # 笔
    bi = []
    for f in fractals:
        if not bi:
            bi.append(f)
            continue
        if f[1] == bi[-1][1]:
            if (f[1] == 'top' and f[2] > bi[-1][2]) or (f[1] == 'bottom' and f[2] < bi[-1][2]):
                bi[-1] = f
        else:
            if f[0] - bi[-1][0] >= 4:
                bi.append(f)
            else:
                if (f[1] == 'top' and f[2] > bi[-1][2]) or (f[1] == 'bottom' and f[2] < bi[-1][2]):
                    bi[-1] = f

    # 中枢: 连续3笔重叠
    zs_list = []
    for i in range(len(bi) - 2):
        a, b, c = bi[i], bi[i+1], bi[i+2]
        segs = []
        for x, y in [(a, b), (b, c)]:
            lo, hi = min(x[2], y[2]), max(x[2], y[2])
            segs.append((lo, hi))
        zs_hi = min(s[1] for s in segs)
        zs_lo = max(s[0] for s in segs)
        if zs_hi > zs_lo:
            zs_list.append({"bi_idx": i, "lo": zs_lo, "hi": zs_hi,
                            "start": merged[a[0]][0], "end": merged[c[0]][0]})

    # 背驰: 最近两段同向笔力度比较
    beichi = {"type": None, "desc": ""}
    if len(bi) >= 4:
        def seg_force(b1, b2):
            return abs(b2[2] / b1[2] - 1) * 100 if b1[2] else 0
        d1 = seg_force(bi[-4], bi[-3])
        d2 = seg_force(bi[-2], bi[-1])
        if bi[-1][1] == 'bottom' and d2 < d1:
            beichi = {"type": "底背驰", "desc": f"下跌力度 {d1:.1f}%→{d2:.1f}%, 第二段更小"}
        elif bi[-1][1] == 'top' and d2 < d1:
            beichi = {"type": "顶背驰", "desc": f"上涨力度 {d1:.1f}%→{d2:.1f}%, 第二段更小"}

    # 一二三类买卖点 (在最后15笔内找)
    buy_sell = []
    if len(bi) >= 5:
        recent = bi[-15:]
        # 一买: 下跌趋势底背驰后的bottom
        if beichi["type"] == "底背驰" and bi[-1][1] == 'bottom':
            buy_sell.append({"time": merged[bi[-1][0]][0], "type": "一买",
                             "price": round(bi[-1][2], 2)})
            # 二买: 一买之后第一个bottom(这里即一买本身之后, 回踩不创新低) — 简化: 若倒数第3笔是bottom且高于一买
            if len(recent) >= 5 and recent[-3][1] == 'bottom' and recent[-3][2] > recent[-1][2]:
                buy_sell.append({"time": merged[recent[-3][0]][0], "type": "二买",
                                 "price": round(recent[-3][2], 2)})
        # 顶背驰对称 → 一卖/二卖
        if beichi["type"] == "顶背驰" and bi[-1][1] == 'top':
            buy_sell.append({"time": merged[bi[-1][0]][0], "type": "一卖",
                             "price": round(bi[-1][2], 2)})
        # 三买: 最近中枢之后的bottom笔, 低点>中枢上沿
        if zs_list:
            last_zs = zs_list[-1]
            for b in recent:
                if b[1] == 'bottom' and b[2] > last_zs["hi"] and b[0] > last_zs.get("bi_idx", 0) + 2:
                    buy_sell.append({"time": merged[b[0]][0], "type": "三买",
                                     "price": round(b[2], 2)})
                    break
            # 三卖: 最近中枢之后的top笔, 高点<中枢下沿
            for b in recent:
                if b[1] == 'top' and b[2] < last_zs["lo"] and b[0] > last_zs.get("bi_idx", 0) + 2:
                    buy_sell.append({"time": merged[b[0]][0], "type": "三卖",
                                     "price": round(b[2], 2)})
                    break

    # 输出: 最近30笔(含日期/价格), 中枢最近5个, 买卖点(去重, 按时间)
    biz = [{"time": merged[b[0]][0], "type": b[1], "price": round(b[2], 2)} for b in bi[-30:]]
    # 买卖点去重
    seen = set()
    bs_uniq = []
    for x in buy_sell:
        key = (x["time"], x["type"])
        if key not in seen:
            seen.add(key)
            bs_uniq.append(x)

    cur_price = qf_rows[-1][3]
    last_zs = zs_list[-1] if zs_list else None
    return {
        "symbol": symbol,
        "bars": len(rows),
        "cur_price": round(cur_price, 2),
        "cur_date": rows[-1][0],
        "bi": biz,
        "zhongshu": zs_list[-5:],
        "last_zhongshu": {"lo": round(last_zs["lo"], 2), "hi": round(last_zs["hi"], 2)} if last_zs else None,
        "beichi": beichi,
        "buy_sell": bs_uniq,
    }


if __name__ == "__main__":
    import json
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "002920"
    print(json.dumps(analyze(sym), ensure_ascii=False, indent=1))
