#!/usr/bin/env python3
"""完整缠论算法 V2 (按缠论原文标准设计)
级别递推: K线合并 → 分型 → 笔 → 线段(特征序列法) → 中枢(延伸/扩展) → 走势类型 → 趋势背驰 → 买卖点
关键修正:
  1. 中枢: 线段级, ZD/ZG重叠区间 + DD/GG波动区间, 9段延伸升级
  2. 走势类型: 上涨/下跌趋势(≥2中枢不重叠) vs 盘整(单中枢)
  3. 背驰: 仅趋势末端判定 (创新低+DIF黄白线不新低 或 MACD面积衰竭)
  4. 买卖点: 一买=趋势末中枢后背驰 | 二买=一买后次级别回调不创新低 | 三买=突破中枢后回抽不进中枢
"""
import sqlite3

DB = "/home/ubuntu/Sequoia-X-a/data/sequoia_v2.db"


# ═══ 第1层: K线包含合并 ═══
def merge_inclusion(k):
    """k: [[date, h, l, close]] → [[date, h, l]] 向上取高高/向下取低低"""
    merged = []
    for r in k:
        h, l = r[1], r[2]
        if not merged:
            merged.append([r[0], h, l])
            continue
        ph, pl = merged[-1][1], merged[-1][2]
        if (h >= ph and l <= pl) or (h <= ph and l >= pl):
            # 方向由前一根决定
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


# ═══ 第2层: 分型 ═══
def calc_fractals(merged):
    """顶分型(中间高点最高且低点最高) / 底分型(中间低点最低且高点最低)"""
    fr = []
    for i in range(1, len(merged) - 1):
        h0, l0 = merged[i-1][1], merged[i-1][2]
        h1, l1 = merged[i][1], merged[i][2]
        h2, l2 = merged[i+1][1], merged[i+1][2]
        if h1 > h0 and h1 > h2 and l1 > l0 and l1 > l2:
            fr.append((i, 'top', h1))
        elif l1 < l0 and l1 < l2 and h1 < h0 and h1 < h2:
            fr.append((i, 'bottom', l1))
    return fr


# ═══ 第3层: 笔 (严格) ═══
def calc_bi(merged):
    """顶底严格交替; 相邻分型(合并K线)间隔>=4; 同类型间隔够且更极端才替换; 相反类型间隔不够忽略"""
    fr = calc_fractals(merged)
    bi = []
    for f in fr:
        if not bi:
            bi.append(f)
            continue
        last = bi[-1]
        if f[1] == last[1]:
            # 同类型: 间隔>=4 且 更极端 → 替换(旧分型作废)
            if f[0] - last[0] >= 4:
                if (f[1] == 'top' and f[2] > last[2]) or (f[1] == 'bottom' and f[2] < last[2]):
                    bi[-1] = f
            # 间隔不够或不够极端: 忽略
        else:
            # 相反类型: 间隔>=4 → 成笔; 间隔<4 → 忽略(不能成笔)
            if f[0] - last[0] >= 4:
                bi.append(f)
    return bi


# ═══ 第4层: 线段 (特征序列法, 标准版) ═══
def merge_feat(feat):
    """特征序列包含处理: 同向特征元素合并
    feat: 笔列表(相邻交替), 只取与线段方向相反的子序列"""
    out = []
    for f in feat:
        if not out:
            out.append(list(f))
            continue
        # 特征序列元素是笔: 用端点价格比较包含
        prev = out[-1]
        # 笔的区间 = [min(端点价格), max(端点价格)]
        def span(x):
            return min(x[2], x[2])  # 笔本身就是点值
        # 简化: 特征序列元素(笔)的包含 = 端点价格包含
        # 用笔端点值做包含处理
        if (f[2] >= prev[2] and f[2] <= prev[2]) or (f[2] <= prev[2] and f[2] >= prev[2]):
            continue  # 相等忽略
        out.append(f)
    return out


def calc_segments(bi):
    """线段划分(标准特征序列法)
    关键修正:
      1. 特征序列元素=反向笔的区间[lo,hi](笔的跨度)
      2. 特征序列包含处理: 相邻元素区间包含时合并(向上段取低低/向下段取高高)
      3. 分型: 向上段特征序列(向下笔)看高点顶分型; 向下段看低点底分型
      4. 线段终点=分型中间元素的前一笔
    返回线段端点序列(顶底交替)"""
    if len(bi) < 6:
        return []
    segs = [bi[0]]
    i = 0
    while i < len(bi) - 3:
        seg_dir = 'up' if bi[i][1] == 'bottom' else 'down'
        feat = []  # (bi_idx, lo, hi) 特征序列元素区间
        j = i + 1
        seg_end = None
        while j < len(bi):
            if (seg_dir == 'up' and bi[j][1] == 'bottom') or (seg_dir == 'down' and bi[j][1] == 'top'):
                lo = min(bi[j-1][2], bi[j][2])
                hi = max(bi[j-1][2], bi[j][2])
                feat.append((j, lo, hi))
                # 特征序列包含处理: 与前一元素区间包含则合并
                if len(feat) >= 2:
                    prev, cur = feat[-2], feat[-1]
                    if (cur[1] <= prev[1] and cur[2] >= prev[2]) or (cur[1] >= prev[1] and cur[2] <= prev[2]):
                        if seg_dir == 'up':
                            # 向下笔特征序列: 取低低
                            feat[-2] = (cur[0], min(prev[1], cur[1]), min(prev[2], cur[2]))
                        else:
                            # 向上笔特征序列: 取高高
                            feat[-2] = (cur[0], max(prev[1], cur[1]), max(prev[2], cur[2]))
                        feat.pop()
                # 特征序列分型(分型后至少一个确认元素=feat[-1])
                if len(feat) >= 3:
                    f0, f1, f2 = feat[-3], feat[-2], feat[-1]
                    if seg_dir == 'up':
                        if f1[2] > f0[2] and f1[2] > f2[2]:  # 顶分型: 中间元素高点最高
                            seg_end = f1[0] - 1  # 分型中间向下笔的起点(top)=线段终点
                            break
                    else:
                        if f1[1] < f0[1] and f1[1] < f2[1]:  # 底分型: 中间元素低点最低
                            seg_end = f1[0] - 1  # 中间向上笔的起点(bottom)=线段终点
                            break
            j += 1
        if seg_end is None or seg_end <= i:
            break
        segs.append(bi[seg_end])
        i = seg_end
    # 去重相邻同类型
    out = []
    for s in segs:
        if out and s[1] == out[-1][1]:
            if (s[1] == 'top' and s[2] > out[-1][2]) or (s[1] == 'bottom' and s[2] < out[-1][2]):
                out[-1] = s
        else:
            out.append(s)
    return out


# ═══ 第5层: 中枢 (笔级=日线级别, 标准4笔3段重叠) ═══
def calc_zhongshu_bi(bi):
    """中枢(笔级): 连续4笔构成3段, 3段重叠区间=[ZD,ZG]
    缠论标准: 中枢=至少3个次级别走势重叠(笔级=3段)
    延伸: 后续段(成对笔)与中枢区间重叠则延伸
    离开段(段与中枢无重叠)后中枢结束, 从离开处继续找新中枢"""
    zs_list = []
    i = 0
    while i < len(bi) - 3:
        a, b, c, d = bi[i], bi[i+1], bi[i+2], bi[i+3]
        spans = [
            (min(a[2], b[2]), max(a[2], b[2])),
            (min(b[2], c[2]), max(b[2], c[2])),
            (min(c[2], d[2]), max(c[2], d[2])),
        ]
        zg = min(s[1] for s in spans)
        zd = max(s[0] for s in spans)
        if zg <= zd:
            i += 1
            continue
        ext = 4
        j = i + 4
        gg = max(x[2] for x in [a, b, c, d])
        dd = min(x[2] for x in [a, b, c, d])
        while j < len(bi) - 1:
            seg_lo, seg_hi = min(bi[j][2], bi[j+1][2]), max(bi[j][2], bi[j+1][2])
            if seg_lo < zg and seg_hi > zd:
                ext += 1
                gg = max(gg, seg_hi)
                dd = min(dd, seg_lo)
                j += 2
            else:
                break
        zs_list.append({
            "bi_start": i, "bi_end": j - 1,
            "zd": zd, "zg": zg, "dd": dd, "gg": gg,
            "ext": ext,
        })
        i = j
    return zs_list


def last_zhongshu_effective(bi, zs_list):
    """最近有效中枢(前端显示用): 优先"雏形中枢"(最后3-4笔重叠, 贴近当前价)
    否则回退到最近已形成中枢"""
    tail = bi[-4:] if len(bi) >= 4 else bi
    if len(tail) >= 3:
        spans = []
        for pair in [(tail[0], tail[1]), (tail[1], tail[2])]:
            spans.append((min(pair[0][2], pair[1][2]), max(pair[0][2], pair[1][2])))
        if len(tail) >= 4:
            spans.append((min(tail[2][2], tail[3][2]), max(tail[2][2], tail[3][2])))
        zg = min(s[1] for s in spans)
        zd = max(s[0] for s in spans)
        if zg > zd:
            return {"zd": round(zd, 2), "zg": round(zg, 2), "ext": len(tail)}
    if zs_list:
        z = zs_list[-1]
        return {"zd": round(z["zd"], 2), "zg": round(z["zg"], 2), "ext": z["ext"]}
    return None


# ═══ 第6层: 走势类型 (趋势/盘整) ═══
def trend_type(zs_list):
    """用中枢序列判断走势: 上涨趋势(中枢依次抬高不重叠) / 下跌趋势 / 盘整"""
    if len(zs_list) < 2:
        return "盘整"
    # 取最近两个中枢判断方向
    z1, z2 = zs_list[-2], zs_list[-1]
    if z2["zg"] < z1["zd"]:
        return "下跌趋势"
    if z2["zd"] > z1["zg"]:
        return "上涨趋势"
    return "盘整"


# ═══ 第7层: MACD + 背驰 ═══
def macd_data(closes):
    def ema(arr, n):
        k = 2 / (n + 1)
        out = []
        prev = arr[0]
        for i, v in enumerate(arr):
            prev = v if i == 0 else v * k + prev * (1 - k)
            out.append(prev)
        return out
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, hist


def macd_area(dif, i1, i2):
    return sum(abs(d) for d in dif[max(0, i1):i2+1])


def macd_area_pref(dif):
    """前缀和: O(1)区间面积查询(全市场扫描性能优化)"""
    pref = [0.0]
    for v in dif:
        pref.append(pref[-1] + abs(v))
    return lambda i1, i2: pref[max(0, i2 + 1)] - pref[max(0, i1)]


def find_all_signals(bi, zs_list, dif, merged, max_gap=60, amp_lim=2.0):
    """全历史买卖点检测(落DB用)
    买点: 一买(创新低+背驰) → 二买(一买后回调不创新低) → 三买(中枢突破后回抽不进)
    卖点: 对称
    max_gap: 二/三买卖点与一买/中枢的最大交易日间隔(次级别回调周期)
    amp_lim: 三买突破段幅度上限(倍数于中枢ZG, 排除级别错位的暴涨)
    返回 [(type, date, price, ref_zd, ref_zg), ...] 按时间排序"""
    area = macd_area_pref(dif)
    out = []
    bottoms = [b for b in bi if b[1] == 'bottom']
    tops = [b for b in bi if b[1] == 'top']

    # ── 一买/二买 ──
    # 二买约束: 须在一买后max_gap交易日内(次级别回调, 缠论定义), 排除跨年跨周期误报
    for i in range(2, len(bottoms)):
        p1, p2, p3 = bottoms[i-2], bottoms[i-1], bottoms[i]
        if p3[2] < p2[2]:
            a1 = area(p1[0], p2[0])
            a2 = area(p2[0], p3[0])
            if a1 > 0 and a2 < a1 * 0.85:
                out.append(("一买", merged[p3[0]][0], round(p3[2], 2), 0, 0))
                for j in range(i + 1, len(bottoms)):
                    if bottoms[j][2] > p3[2]:
                        if bottoms[j][0] - p3[0] <= max_gap:
                            out.append(("二买", merged[bottoms[j][0]][0], round(bottoms[j][2], 2), 0, 0))
                        break

    # ── 三买: 每个中枢突破后第一个回抽bottom>ZG (只查中枢之后的bottom) ──
    for zs in zs_list:
        for b in bottoms:
            b_idx = bi.index(b)
            if b_idx <= zs["bi_end"]:  # 只查中枢形成后的回抽, 防止旧bottom占用
                continue
            if b_idx < 2:
                continue
            top = bi[b_idx - 1]
            start_bottom = bi[b_idx - 2]
            if top[2] > zs["zg"] and b[2] > zs["zg"] and start_bottom[2] <= zs["zg"]:
                if b[2] < zs["zg"] * 1.35:
                    # 时效约束: 回抽须在中枢后MAX_GAP交易日内
                    # 幅度约束: 突破段不超中枢ZG的2倍(排除暴涨暴跌)
                    if b_idx - zs["bi_end"] <= max_gap and top[2] < zs["zg"] * amp_lim:
                        out.append(("三买", merged[b[0]][0], round(b[2], 2),
                                    round(zs["zd"], 2), round(zs["zg"], 2)))
                    break

    # ── 一卖/二卖 ──
    for i in range(2, len(tops)):
        p1, p2, p3 = tops[i-2], tops[i-1], tops[i]
        if p3[2] > p2[2]:
            a1 = area(p1[0], p2[0])
            a2 = area(p2[0], p3[0])
            if a1 > 0 and a2 < a1 * 0.85:
                out.append(("一卖", merged[p3[0]][0], round(p3[2], 2), 0, 0))
                for j in range(i + 1, len(tops)):
                    if tops[j][2] < p3[2]:
                        if tops[j][0] - p3[0] <= max_gap:
                            out.append(("二卖", merged[tops[j][0]][0], round(tops[j][2], 2), 0, 0))
                        break

    # ── 三卖: 每个中枢跌破后第一个反抽top<ZD (只查中枢之后的top) ──
    for zs in zs_list:
        for t in tops:
            t_idx = bi.index(t)
            if t_idx <= zs["bi_end"]:  # 只查中枢形成后的反抽
                continue
            if t_idx < 2:
                continue
            bottom = bi[t_idx - 1]
            start_top = bi[t_idx - 2]
            if bottom[2] < zs["zd"] and t[2] < zs["zd"] and start_top[2] >= zs["zd"]:
                # 时效约束: 反抽须在中枢后MAX_GAP交易日内
                # 幅度约束: 跌破段不超中枢ZD的50%(排除暴涨暴跌)
                if t_idx - zs["bi_end"] <= max_gap and bottom[2] > zs["zd"] / amp_lim:
                    out.append(("三卖", merged[t[0]][0], round(t[2], 2),
                                round(zs["zd"], 2), round(zs["zg"], 2)))
                break

    out.sort(key=lambda x: x[1])
    return out


def is_bottom_divergence(dif, p1, p2, p3):
    """趋势底背驰: p3价格<p2(创新低) 且 第2段MACD面积<第1段85%(力度明显衰竭)
    p1,p2,p3 = 三个连续bottom的merged索引, 段1=p1→p2, 段2=p2→p3"""
    a1 = macd_area(dif, p1, p2)
    a2 = macd_area(dif, p2, p3)
    if a1 <= 0:
        return False
    return a2 < a1 * 0.85


def is_top_divergence(dif, p1, p2, p3):
    a1 = macd_area(dif, p1, p2)
    a2 = macd_area(dif, p2, p3)
    d1_max = max(dif[p1:p2+1])
    d2_max = max(dif[p2:p3+1])
    return a2 < a1 * 0.9 or d2_max < d1_max


# ═══ 第8层: 买卖点 (走势演化递推) ═══
# 递进逻辑(缠论标准): 下跌背驰→一买 → 回调不创新低→二买 → 突破中枢回抽不进→三买
# 信号 = 演化链上"窗口内最新出现的节点", chain展示完整递进
def find_buy_sell(bi, zs_list, trend, dif, merged, last_n_days):
    res = []
    chain = []  # 递进链 (type, date, price)
    if len(bi) < 8:
        return res, chain, []

    bottoms = [b for b in bi if b[1] == 'bottom']

    # ── 1. 找最近一买: 创新低 + 背驰(面积衰竭) ──
    yi_mai = None
    yi_idx = -1
    for i in range(len(bottoms) - 1, 1, -1):
        p1, p2, p3 = bottoms[i-2], bottoms[i-1], bottoms[i]
        if p3[2] < p2[2] and is_bottom_divergence(dif, p1[0], p2[0], p3[0]):
            yi_mai = p3
            yi_idx = i
            break
    if yi_mai is None:
        return res, chain, []
    chain.append(("一买", merged[yi_mai[0]][0], round(yi_mai[2], 2)))

    # ── 2. 从一买演化: 其后第一个bottom(不创新低)=二买 ──
    er_mai = None
    er_idx = -1
    for i in range(yi_idx + 1, len(bottoms)):
        if bottoms[i][2] > yi_mai[2]:
            er_mai = bottoms[i]
            er_idx = i
            break
    if er_mai is not None:
        chain.append(("二买", merged[er_mai[0]][0], round(er_mai[2], 2)))

    # ── 3. 从二买演化: 上涨突破最近中枢, 回抽bottom>ZG(不进中枢)=三买 ──
    san_mai = None
    last_zs = zs_list[-1] if zs_list else None
    if er_mai is not None and last_zs is not None:
        for i in range(er_idx + 1, len(bottoms)):
            b = bottoms[i]
            b_idx = bi.index(b)
            if b_idx < 2:
                continue
            top = bi[b_idx - 1]       # 突破段顶
            start_bottom = bi[b_idx - 2]  # 突破段起点
            if top[2] > last_zs["zg"] and b[2] > last_zs["zg"] and start_bottom[2] <= last_zs["zg"]:
                if b[2] < last_zs["zg"] * 1.35:
                    san_mai = b
                    break
        if san_mai is not None:
            chain.append(("三买", merged[san_mai[0]][0], round(san_mai[2], 2),
                          round(last_zs["zd"], 2), round(last_zs["zg"], 2)))

    # ── 4. 信号 = 窗口内最新演化节点(优先级: 三买>二买>一买) ──
    for node, typ in [(san_mai, "三买"), (er_mai, "二买"), (yi_mai, "一买")]:
        if node is None:
            continue
        if merged[node[0]][0] in last_n_days:
            if typ == "三买" and last_zs is not None:
                res.append((typ, merged[node[0]][0], round(node[2], 2),
                            round(last_zs["zd"], 2), round(last_zs["zg"], 2)))
            else:
                res.append((typ, merged[node[0]][0], round(node[2], 2), 0, 0))
            break  # 只报最新节点

    # ── 5. 卖点链(对称递进): 上涨背驰→一卖 → 反弹不创新高→二卖 → 跌破中枢反抽不回→三卖 ──
    sell_chain = []
    tops = [b for b in bi if b[1] == 'top']
    yi_sell = None
    yi_idx = -1
    for i in range(len(tops) - 1, 1, -1):
        p1, p2, p3 = tops[i-2], tops[i-1], tops[i]
        if p3[2] > p2[2] and is_top_divergence(dif, p1[0], p2[0], p3[0]):
            yi_sell = p3
            yi_idx = i
            break
    if yi_sell is not None:
        sell_chain.append(("一卖", merged[yi_sell[0]][0], round(yi_sell[2], 2)))
        # 二卖: 一卖后第一个top不创新高
        er_sell = None
        er_idx = -1
        for i in range(yi_idx + 1, len(tops)):
            if tops[i][2] < yi_sell[2]:
                er_sell = tops[i]
                er_idx = i
                break
        if er_sell is not None:
            sell_chain.append(("二卖", merged[er_sell[0]][0], round(er_sell[2], 2)))
            # 三卖: 二卖后跌破最近中枢, 反抽top<ZD(不进中枢)
            san_sell = None
            if last_zs is not None:
                for i in range(er_idx + 1, len(tops)):
                    t = tops[i]
                    t_idx = bi.index(t)
                    if t_idx < 2:
                        continue
                    bottom = bi[t_idx - 1]       # 下跌段底
                    start_top = bi[t_idx - 2]    # 下跌段起点
                    if bottom[2] < last_zs["zd"] and t[2] < last_zs["zd"] and start_top[2] >= last_zs["zd"]:
                        san_sell = t
                        break
            if san_sell is not None:
                sell_chain.append(("三卖", merged[san_sell[0]][0], round(san_sell[2], 2)))
    return res, chain, sell_chain


def analyze(symbol, window_days=7):
    """完整缠论分析: window_days=信号检测窗口(交易日)"""
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT date, open, high, low, close, close_qfq, volume FROM stock_daily "
        "WHERE symbol=? AND close_qfq>0 ORDER BY date", (symbol,)).fetchall()
    conn.close()
    if len(rows) < 150:
        return {"error": "数据不足"}
    qf_rows = []
    for r in rows:
        ratio = r[5] / r[4] if r[4] else 1
        qf_rows.append([r[0], r[2] * ratio, r[3] * ratio, r[5]])
    merged = merge_inclusion(qf_rows)
    bi = calc_bi(merged)
    segs = calc_segments(bi)
    zs_list = calc_zhongshu_bi(bi)  # 笔级中枢=日线级别
    trend = trend_type(zs_list)
    dif, dea, hist = macd_data([r[3] for r in qf_rows])

    last_n = set(r[0] for r in qf_rows[-window_days:])
    buy_sell, chain, sell_chain = find_buy_sell(bi, zs_list, trend, dif, merged, last_n)

    return {
        "symbol": symbol,
        "bars": len(rows),
        "bi_cnt": len(bi), "seg_cnt": len(segs), "zs_cnt": len(zs_list),
        "trend": trend,
        "last_zhongshu": last_zhongshu_effective(bi, zs_list),
        "buy_sell": [{"time": x[1], "type": x[0], "price": x[2]} for x in buy_sell],
        "chain": [{"time": x[1], "type": x[0], "price": x[2]} for x in chain],
        "sell_chain": [{"time": x[1], "type": x[0], "price": x[2]} for x in sell_chain],
        "bi": [{"time": merged[b[0]][0], "type": b[1], "price": round(b[2], 2)} for b in bi[-40:]],
        "segs": [{"time": merged[s[0]][0], "type": s[1], "price": round(s[2], 2)} for s in segs[-20:]],
        "cur_price": round(qf_rows[-1][3], 2),
        "cur_date": rows[-1][0],
    }


if __name__ == "__main__":
    import json
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "002920"
    d = analyze(sym)
    print(json.dumps(d, ensure_ascii=False, indent=1))
