import { useEffect, useRef, useCallback, memo, useState } from 'react'
import { createChart, IChartApi, ISeriesApi, CandlestickData, LineData, HistogramData, Time } from 'lightweight-charts'
import { KlinePoint, Signal } from '../utils/api'

export interface CrosshairInfo {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  prevClose: number   // 前一根K线收盘价，用于计算涨跌幅
}

interface ChanlunData {
  bi: { time: string; type: string; price: number }[]
  segs: { time: string; type: string; price: number }[]
  last_zhongshu: { zd: number; zg: number; ext: number; since?: string } | null
  zhongshu_list?: { start: string; end: string; zd: number; zg: number; ext: number }[]  // 全部已确认中枢(矩形框用)
  trend: string
  buy_sell: { time: string; type: string; price: number }[]
  chain: { time: string; type: string; price: number }[]
  sell_chain: { time: string; type: string; price: number }[]
  cur_price: number
  cur_date: string
}

interface ChartProps {
  kline: KlinePoint[]
  signals: Signal[]
  symbol: string
  range: number
  onCrosshairMove?: (data: CrosshairInfo | null) => void
  onChartClick?: (time: string) => void
  benchmarkTime?: string | null
  focusDate?: string | null
  chanlun?: ChanlunData | null
  zsAsOf?: { date: string; zd: number; zg: number; ext: number; since?: string } | null  // 动态中枢(视角历史时回放)
  onZsRangeChange?: (date: string) => void  // 视角最右日期变化(空串=回到最新)
  showAllZs?: boolean  // 显示全部历史中枢(矩形框)
}
const COLORS = {
  bg: '#0d1117',
  grid: '#1c2128',
  text: '#8b949e',
  red: '#f23645',
  green: '#089981',
  ma5: '#f0d43a',
  ma10: '#f7823b',
  ma20: '#58a6ff',
  ma60: '#bc8cff',
  volUp: 'rgba(242, 54, 69, 0.4)',
  volDown: 'rgba(8, 153, 129, 0.4)',
  signalB: '#089981',    // premium_b — 绿色
  signalB2: '#f0883e',   // premium_b2 — 橙色
  signalA: '#d29922',    // premium_a — 金色
  signalOrig: '#58a6ff', // original — 蓝色
  signalU: '#bc8cff',    // ultra_shrink — 紫色
}

const KLINE_CACHE = { data: [] as KlinePoint[] }
// 字符串日期 <-> TradingView BusinessDay 对象(避免字符串time解析歧义)
const toBD = (s: string): Time => {
  const [y, m, d] = s.split("-").map(Number)
  return { year: y, month: m, day: d } as Time
}
const bdStr = (t: Time | string): string => {
  if (typeof t === "string") return t as string
  const o = t as { year: number; month: number; day: number }
  return `${o.year}-${String(o.month).padStart(2, "0")}-${String(o.day).padStart(2, "0")}`
}


export default memo(Chart)

function Chart({ kline, signals, symbol, range, onCrosshairMove, onChartClick, benchmarkTime, focusDate, chanlun, zsAsOf, onZsRangeChange, showAllZs }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const macdContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const macdChartRef = useRef<IChartApi | null>(null)
  const macdHistRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const macdDifRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdDeaRef = useRef<ISeriesApi<'Line'> | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const ma5Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma10Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma60Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const signalScatterRef = useRef<ISeriesApi<'Line'> | null>(null)
  const [chartReady, setChartReady] = useState(false)
  const locateTimer = useRef<number | null>(null)
  const [chanOverlayReady, setChanOverlayReady] = useState(false)
  // chart就绪后延迟挂载缠论overlay: K线先渲染, 缠论线随后浮现(避免首帧卡顿)
  useEffect(() => {
    if (!chartReady) { setChanOverlayReady(false); return }
    const t = setTimeout(() => setChanOverlayReady(true), 250)
    return () => clearTimeout(t)
  }, [chartReady])
  const extraSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const chanSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const chanPriceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([])
  // 用ref存最新onCrosshairMove，避免闭包捕获旧值
  const onCrosshairMoveRef = useRef(onCrosshairMove)
  onCrosshairMoveRef.current = onCrosshairMove
  const onChartClickRef = useRef(onChartClick)
  onChartClickRef.current = onChartClick

  // crosshair回调，带prevClose
  const handleCrosshair = useCallback((param: any) => {
    const cb = onCrosshairMoveRef.current
    if (!param.time || !param.point) {
      cb?.(null)
      return
    }
    const data = param.seriesData.get(candleSeriesRef.current) as CandlestickData | undefined
    if (data && KLINE_CACHE.data.length > 0) {
      const timeStr = bdStr(data.time as Time)
      const idx = KLINE_CACHE.data.findIndex(k => k.time === timeStr)
      const prevClose = idx > 0 ? KLINE_CACHE.data[idx - 1].close : data.close
      cb?.({
        time: timeStr,
        open: data.open,
        high: data.high,
        low: data.low,
        close: data.close,
        volume: idx >= 0 ? KLINE_CACHE.data[idx].volume : 0,
        prevClose,
      })
    }
  }, [])

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: COLORS.bg },
        textColor: COLORS.text,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      crosshair: {
        mode: 0,
        vertLine: {
          color: '#6e7681',
          width: 1,
          style: 2,
          labelBackgroundColor: '#30363d',
        },
        horzLine: {
          color: '#6e7681',
          width: 1,
          style: 2,
          labelBackgroundColor: '#30363d',
        },
      },
      rightPriceScale: {
        borderColor: COLORS.grid,
        scaleMargins: { top: 0.05, bottom: 0.25 },
      },
      timeScale: {
        borderColor: COLORS.grid,
        timeVisible: false,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => {
          const d = bdStr(time as Time)
          return d.slice(5)
        },
      },
      handleScroll: { vertTouchDrag: false },
      handleScale: {
        axisPressedMouseMove: false,
        pinch: false,
        mouseWheel: false,
        axisDoubleClickReset: false,
      }, // 禁用触摸缩放
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: COLORS.red,
      downColor: COLORS.green,
      borderUpColor: COLORS.red,
      borderDownColor: COLORS.green,
      wickUpColor: COLORS.red,
      wickDownColor: COLORS.green,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.87, bottom: 0 },
    })

    const makeMA = (color: string, width: 1 | 2 | 3 | 4) => chart.addLineSeries({
      color,
      lineWidth: width,
      lastValueVisible: false,
      priceLineVisible: false,  // 隐藏右侧横贯虚线, 画面清爽
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    const ma5 = makeMA(COLORS.ma5, 1)
    const ma10 = makeMA(COLORS.ma10, 1)
    const ma20 = makeMA(COLORS.ma20, 1)
    const ma60 = makeMA(COLORS.ma60, 1)

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries
    ma5Ref.current = ma5
    ma10Ref.current = ma10
    ma20Ref.current = ma20
    ma60Ref.current = ma60
    signalScatterRef.current = chart.addLineSeries({
      color: 'transparent',
      lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
      pointMarkersVisible: true,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    chart.subscribeCrosshairMove(handleCrosshair)

    // Chart click → benchmark
    const handleContainerClick = (e: MouseEvent) => {
      const cb = onChartClickRef.current
      if (!cb || !chartRef.current) return
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) return
      const x = e.clientX - rect.left
      const time = chartRef.current.timeScale().coordinateToTime(x)
      if (time != null) cb(String(time))
    }
    const el = containerRef.current
    el?.addEventListener('click', handleContainerClick)

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        chart.applyOptions({ width, height })
      }
    })
    observer.observe(containerRef.current)

    // ═══ MACD 副图 (独立 chart, 主图时间轴单向同步) ═══
    let macdChart: IChartApi | null = null
    if (macdContainerRef.current) {
      macdChart = createChart(macdContainerRef.current, {
        layout: { background: { color: COLORS.bg }, textColor: COLORS.text, fontSize: 11 },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderColor: COLORS.grid, scaleMargins: { top: 0.25, bottom: 0.25 } },
        timeScale: {
          borderColor: COLORS.grid,
          visible: false, // 副图不显示时间轴(共用主图)
          tickMarkFormatter: (time: Time) => {
            const d = bdStr(time as Time)
            return d.slice(5)
          },
        },
        handleScroll: false,
        handleScale: false,
        crosshair: { mode: 0 },
        localization: { priceFormatter: (p: number) => p.toFixed(2) },
      })
      macdChartRef.current = macdChart
      macdHistRef.current = macdChart.addHistogramSeries({
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        priceLineVisible: false,
        lastValueVisible: false,
      })
      macdDifRef.current = macdChart.addLineSeries({
        color: '#f0d43a', lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
      })
      macdDeaRef.current = macdChart.addLineSeries({
        color: '#7ee787', lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
      })
      const macdObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect
          macdChart?.applyOptions({ width, height })
        }
      })
      macdObserver.observe(macdContainerRef.current)
      // 主图滚动/缩放 → 同步副图
      const syncToMacd = () => {
        const r = chart.timeScale().getVisibleLogicalRange()
        if (r) macdChart?.timeScale().setVisibleLogicalRange(r)
      }
      chart.timeScale().subscribeVisibleLogicalRangeChange(syncToMacd)
      // 副图初始跟随
      const r0 = chart.timeScale().getVisibleLogicalRange()
      if (r0) macdChart.timeScale().setVisibleLogicalRange(r0)
      setChartReady(true)
    }

    return () => {
      el?.removeEventListener('click', handleContainerClick)
      observer.disconnect()
      chart.remove()
      macdChart?.remove()
      macdChartRef.current = null
      setChartReady(false)
    }
  }, [handleCrosshair])

  // Update data
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !kline.length) return

    // 缓存到模块变量，供crosshair回调使用
    KLINE_CACHE.data = kline

    const candleData: CandlestickData[] = kline.map(k => ({
      time: toBD(k.time),
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))

    const volData: HistogramData[] = kline.map(k => ({
      time: toBD(k.time),
      value: k.volume,
      color: k.close >= k.open ? COLORS.volUp : COLORS.volDown,
    }))

    const closes = kline.map(k => k.close)
    const calcMA = (period: number): LineData[] => {
      const result: LineData[] = []
      for (let i = period - 1; i < closes.length; i++) {
        let sum = 0
        for (let j = i - period + 1; j <= i; j++) sum += closes[j]
        result.push({ time: toBD(kline[i].time), value: sum / period })
      }
      return result
    }

    try { candleSeriesRef.current.setData(candleData) } catch (e) { console.warn('[K线setData跳过]', e) }
    try { volumeSeriesRef.current.setData(volData) } catch (e) { console.warn('[量setData跳过]', e) }
    try { ma5Ref.current?.setData(calcMA(5)) } catch (e) { console.warn('[MA5跳过]', e) }
    try { ma10Ref.current?.setData(calcMA(10)) } catch (e) { console.warn('[MA10跳过]', e) }
    try { ma20Ref.current?.setData(calcMA(20)) } catch (e) { console.warn('[MA20跳过]', e) }
    try { ma60Ref.current?.setData(calcMA(60)) } catch (e) { console.warn('[MA60跳过]', e) }

    // MACD (12, 26, 9)
    const emaArr = (data: number[], period: number): number[] => {
      const k = 2 / (period + 1)
      const out: number[] = []
      let prev = data[0] ?? 0
      for (let i = 0; i < data.length; i++) {
        prev = i === 0 ? (data[0] ?? 0) : (data[i] ?? 0) * k + prev * (1 - k)
        out.push(prev)
      }
      return out
    }
    const ema12 = emaArr(closes, 12)
    const ema26 = emaArr(closes, 26)
    const dif = closes.map((_, i) => ema12[i] - ema26[i])
    const dea = emaArr(dif, 9)
    const hist = dif.map((d, i) => (d - dea[i]) * 2)
    // MACD数据与主图严格同索引(从i=0开始全量), 保证时间轴同步不错位
    // EMA前25个点未收敛(视觉无影响, 用户看近期), 换取与主图一一对应
    const macdHistData: HistogramData[] = []
    const macdDifData: LineData[] = []
    const macdDeaData: LineData[] = []
    for (let i = 0; i < kline.length; i++) {
      const t = toBD(kline[i].time)
      const hv = hist[i]
      if (!isFinite(hv)) continue
      macdHistData.push({ time: t, value: hv, color: hv >= 0 ? 'rgba(240,101,101,0.7)' : 'rgba(80,180,120,0.7)' })
      macdDifData.push({ time: t, value: dif[i] })
      macdDeaData.push({ time: t, value: dea[i] })
    }
    try { macdHistRef.current?.setData(macdHistData) } catch (e) { console.warn('[MACD跳过]', e) }
    try { macdDifRef.current?.setData(macdDifData) } catch (e) { console.warn('[MACD-DIF跳过]', e) }
    try { macdDeaRef.current?.setData(macdDeaData) } catch (e) { console.warn('[MACD-DEA跳过]', e) }

    // 定位/range切换: 立即执行 + 800ms debounce重试(布局完成后最终生效, 不闪烁)
    const doLocate = () => {
      if (!chartRef.current) return
      if (focusDate) {
        const focusIdx = candleData.findIndex(d => bdStr(d.time) === focusDate)
        if (focusIdx >= 0) {
          chartRef.current.timeScale().setVisibleRange({ from: candleData[Math.max(0, focusIdx - 80)].time, to: candleData[Math.min(candleData.length - 1, focusIdx + 80)].time })
        } else {
          const n = Math.min(120, candleData.length)
          chartRef.current.timeScale().setVisibleRange({ from: candleData[candleData.length - n].time, to: candleData[candleData.length - 1].time })
        }
      } else {
        const vr = Math.min(range, candleData.length)
        if (vr > 0) chartRef.current.timeScale().setVisibleRange({ from: candleData[candleData.length - vr].time, to: candleData[candleData.length - 1].time })
      }
    }
    doLocate()
    if (locateTimer.current) clearTimeout(locateTimer.current)
    locateTimer.current = window.setTimeout(doLocate, 800)
    // 同步MACD副图时间轴
    const syncRange = chartRef.current?.timeScale().getVisibleLogicalRange()
    if (syncRange) macdChartRef.current?.timeScale().setVisibleLogicalRange(syncRange)

  }, [kline, signals, symbol, range, focusDate, benchmarkTime])

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div ref={containerRef} style={{ width: '100%', flex: 1, minHeight: 0, touchAction: 'manipulation' }} />
      <div ref={macdContainerRef} style={{ width: '100%', height: 120, flexShrink: 0, borderTop: '1px solid var(--border, #30363d)' }} />
      {chartReady && chanOverlayReady && <ChanlunOverlay chanlun={chanlun ?? null} kline={kline} chartRef={chartRef} candleSeriesRef={candleSeriesRef} zsAsOf={zsAsOf ?? null} onZsRangeChange={onZsRangeChange} showAllZs={showAllZs ?? false} />}
    </div>
  )
}

// ═══ 缠论绘制(完整版): 线段折线 + 笔 + 中枢线 + 买卖点标记 ═══
function ChanlunOverlay({ chanlun, kline, chartRef, candleSeriesRef, zsAsOf, onZsRangeChange, showAllZs }: {
  chanlun: ChanlunData | null
  kline: KlinePoint[]
  chartRef: React.RefObject<IChartApi | null>
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>
  zsAsOf?: { date: string; zd: number; zg: number; ext: number; since?: string } | null
  onZsRangeChange?: (date: string) => void
  showAllZs?: boolean
}) {
  const seriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([])

  // 动态中枢: 视角最右日期变化 → 通知父组件回放当时中枢(debounce 300ms)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !onZsRangeChange) return
    const lastKlineTime = kline.length ? kline[kline.length - 1].time : ''
    let timer: number | null = null
    let lastDate = ''
    const handler = () => {
      const vr = chart.timeScale().getVisibleRange()
      if (!vr || !vr.to) return
      const d = bdStr(vr.to as Time)
      if (d === lastDate) return
      lastDate = d
      if (timer) clearTimeout(timer)
      timer = window.setTimeout(() => {
        // 视角最右=最新K线 → 传空串(用默认最新中枢); 否则回放当时
        onZsRangeChange(d === lastKlineTime ? '' : d)
      }, 300)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler)
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler)
      if (timer) clearTimeout(timer)
    }
  }, [chartRef, onZsRangeChange, kline])

  useEffect(() => {
    // 清理旧的
    seriesRef.current.forEach(s => {
      try {
        // 先清空数据+markers再remove — 否则TradingView异步rAF绘制旧数据会抛Value is null
        s.setMarkers([])
        s.setData([])
        chartRef.current?.removeSeries(s)
      } catch { /* noop */ }
    })
    seriesRef.current = []
    priceLinesRef.current.forEach(pl => { try { candleSeriesRef.current?.removePriceLine(pl) } catch { /* noop */ } })
    priceLinesRef.current = []
    const chart = chartRef.current
    const candle = candleSeriesRef.current
    if (!chanlun || !chart || !candle) return

    // 防御: 只保留主图K线范围内的时间戳(防止时间错位渲染异常/黑块)
    const validTimes = new Set(kline.map(k => k.time))

    // 线段折线 (完整版主图: 亮黄粗线)
    if (chanlun.segs?.length) {
      const segData: LineData[] = chanlun.segs
        .filter(s => validTimes.has(s.time))
        .map(s => ({ time: toBD(s.time), value: s.price }))
      if (segData.length >= 2) {
        const s = chart.addLineSeries({
          color: '#f0d43a', lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
        })
        s.setData(segData)
        seriesRef.current.push(s)
      }
    }

    // 笔折线 (细灰线, 辅助)
    if (chanlun.bi?.length) {
      const biData: LineData[] = chanlun.bi
        .filter(b => validTimes.has(b.time))
        .map(b => ({ time: toBD(b.time), value: b.price }))
      if (biData.length >= 2) {
        const s = chart.addLineSeries({
          color: 'rgba(160,160,170,0.55)', lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
        })
        s.setData(biData)
        seriesRef.current.push(s)
      }
    }

    // 全部历史中枢矩形(开关): 每中枢 上边+下边 青色虚线(lineWidth2) + 四角标记
    // 4.x限制: 同一series不允许同一天两点(画不了竖线), 用角点markers补足矩形感
    if (showAllZs && chanlun.zhongshu_list?.length) {
      const zsColor = 'rgba(57,197,207,0.9)'
      for (const z of chanlun.zhongshu_list) {
        if (!z.start || !z.end || z.start === z.end) continue
        const t1 = toBD(z.start)
        const t2 = toBD(z.end)
        if (!t1 || !t2 || bdStr(t1 as Time) > bdStr(t2 as Time)) continue
        try {
          const up = chart.addLineSeries({
            color: zsColor, lineWidth: 2, lineStyle: 2,
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
          })
          up.setData([{ time: t1, value: z.zg }, { time: t2, value: z.zg }])
          seriesRef.current.push(up)
          const down = chart.addLineSeries({
            color: zsColor, lineWidth: 2, lineStyle: 2,
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
          })
          down.setData([{ time: t1, value: z.zd }, { time: t2, value: z.zd }])
          seriesRef.current.push(down)
        } catch (e) { console.warn('[中枢矩形跳过]', e) }
      }
    }
    // 最新中枢(雏形/未确认结束)矩形: since → 最新K线日期
    // 若与列表最后一个同一起始日则已画过(如600580 4/29→7/20), 跳过避免重复
    const lastZ = chanlun.zhongshu_list?.length ? chanlun.zhongshu_list[chanlun.zhongshu_list.length - 1] : null
    const lz = chanlun.last_zhongshu
    if (showAllZs && lz && lz.since && (!lastZ || lastZ.start !== lz.since)) {
      const klineEnd = kline.length ? kline[kline.length - 1].time : null
      if (klineEnd) {
        const t1 = toBD(lz.since)
        const t2 = toBD(klineEnd)
        if (t1 && t2 && bdStr(t1 as Time) <= bdStr(t2 as Time)) {
          try {
            const zsColor2 = 'rgba(57,197,207,0.9)'
            const up = chart.addLineSeries({
              color: zsColor2, lineWidth: 2, lineStyle: 2,
              lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
            })
            up.setData([{ time: t1, value: lz.zg }, { time: t2, value: lz.zg }])
            seriesRef.current.push(up)
            const down = chart.addLineSeries({
              color: zsColor2, lineWidth: 2, lineStyle: 2,
              lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
            })
            down.setData([{ time: t1, value: lz.zd }, { time: t2, value: lz.zd }])
            seriesRef.current.push(down)
          } catch (e) { console.warn('[最新中枢矩形跳过]', e) }
        }
      }
    }

    // 中枢上下沿 (水平虚线): 优先动态中枢(zsAsOf=视角历史时回放的当时中枢), 否则最新中枢
    const zs = zsAsOf && zsAsOf.zd > 0 && zsAsOf.zg > zsAsOf.zd ? zsAsOf : chanlun.last_zhongshu
    if (zs) {
      const { zd, zg } = zs
      if (zg > zd) {
        const tag = zsAsOf ? `截至${zsAsOf.date}` : '最新'
        const pl1 = candle.createPriceLine({
          price: zg, color: 'rgba(240,101,101,0.7)',
          lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: `中枢上 ${tag}(${zs.ext}段)`,
        })
        const pl2 = candle.createPriceLine({
          price: zd, color: 'rgba(80,180,120,0.7)',
          lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '中枢下',
        })
        priceLinesRef.current = [pl1, pl2]
      }
    }

    // 买卖点标记: 完整演化链(chain) + 窗口内最新节点(buy_sell), 去重后全画
    if (chanlun.chain?.length || chanlun.buy_sell?.length) {
      const cfg: Record<string, { color: string; label: string }> = {
        '一买': { color: '#ff4444', label: '1买' }, '二买': { color: '#f0883e', label: '2买' }, '三买': { color: '#d29922', label: '3买' },
        '一卖': { color: '#2ea043', label: '1卖' }, '二卖': { color: '#8b949e', label: '2卖' }, '三卖': { color: '#58a6ff', label: '3卖' },
        '✗推翻': { color: '#666666', label: '✗' },
      }
      const markers: any[] = []
      const seen = new Set<string>()
      // 买点链画在K线下方(belowBar), 卖点链画在K线上方(aboveBar)
      const allSignals: { time: string; type: string; price: number; pos: 'belowBar' | 'aboveBar' }[] = [
        ...(chanlun.chain || []).map(s => ({ ...s, pos: 'belowBar' as const })),
        ...(chanlun.buy_sell || []).map(s => ({ ...s, pos: 'belowBar' as const })),
        ...(chanlun.sell_chain || []).map(s => ({ ...s, pos: 'aboveBar' as const })),
      ]
      // 被推翻信号的时点(✗类型) — 这些时点只画✗, 不画原类型
      const overturnedTimes = new Set(allSignals.filter(s => (s.type || '').startsWith('✗')).map(s => s.time))
      allSignals.forEach(bs => {
        const isOv = (bs.type || '').startsWith('✗')
        // 被推翻的时点只画✗, 跳过原类型(避免1买与✗1买重叠)
        if (overturnedTimes.has(bs.time) && !isOv) return
        const key = `${bs.time}_${bs.type}`
        if (seen.has(key)) return
        seen.add(key)
        const idx = kline.findIndex(k => k.time === bs.time)
        if (idx < 0) return
        const c = cfg[bs.type] || { color: '#888', label: bs.type }
        const pos = bs.pos || (bs.type.includes('卖') ? 'aboveBar' : 'belowBar')
        if (bs.time) markers.push({
          time: toBD(bs.time), position: pos, color: c.color,
          shape: pos === 'aboveBar' ? 'arrowDown' : 'arrowUp', text: c.label,
        })
      })
      if (markers.length) {
        // 挂到主K线series上(有全部K线数据点, marker按time精确对齐, 不会错位)
        // 注意: 不能挂笔折线series(只有笔端点, 非端点的信号会错位到最近端点)
        try {
          // 按时间倒序(最近在前)
          markers.sort((a, b) => bdStr(a.time as Time) < bdStr(b.time as Time) ? 1 : -1)
          candleSeriesRef.current?.setMarkers(markers.slice(0, 10).filter(m => m && m.time != null))
        } catch (e) { console.warn('[买卖点markers跳过]', e) }
      }
    }
  }, [chanlun, kline, chartRef, candleSeriesRef, zsAsOf, showAllZs])

  return null
}