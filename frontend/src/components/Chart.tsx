import { useEffect, useRef, useCallback } from 'react'
import { createChart, IChartApi, ISeriesApi, CandlestickData, LineData, HistogramData, Time } from 'lightweight-charts'
import { KlinePoint, Signal } from '../utils/api'

interface CrosshairInfo {
  time: string
  open: number
  high: number
  low: number
  close: number
  prevClose: number   // 前一根K线收盘价，用于计算涨跌幅
}

interface ChartProps {
  kline: KlinePoint[]
  signals: Signal[]
  symbol: string
  range: number
  onCrosshairMove?: (data: CrosshairInfo | null) => void
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
  signalA: '#d29922',    // premium_a — 金色
  signalOrig: '#58a6ff', // original — 蓝色
  signalU: '#bc8cff',    // ultra_shrink — 紫色
}

const KLINE_CACHE = { data: [] as KlinePoint[] }

export default function Chart({ kline, signals, symbol, range, onCrosshairMove }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const ma5Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma10Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma60Ref = useRef<ISeriesApi<'Line'> | null>(null)

  // crosshair回调，带prevClose
  const handleCrosshair = useCallback((param: any) => {
    if (!param.time || !param.point) {
      onCrosshairMove?.(null)
      return
    }
    const data = param.seriesData.get(candleSeriesRef.current) as CandlestickData | undefined
    if (data && KLINE_CACHE.data.length > 0) {
      const timeStr = String(data.time)
      const idx = KLINE_CACHE.data.findIndex(k => k.time === timeStr)
      const prevClose = idx > 0 ? KLINE_CACHE.data[idx - 1].close : data.close
      onCrosshairMove?.({
        time: timeStr,
        open: data.open,
        high: data.high,
        low: data.low,
        close: data.close,
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
          const d = typeof time === 'string' ? time : String(time)
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
      scaleMargins: { top: 0.8, bottom: 0 },
    })

    const makeMA = (color: string, width: 1 | 2 | 3 | 4) => chart.addLineSeries({
      color,
      lineWidth: width,
      lastValueVisible: false,
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

    chart.subscribeCrosshairMove(handleCrosshair)

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        chart.applyOptions({ width, height })
      }
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
    }
  }, [handleCrosshair])

  // Update data
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !kline.length) return

    // 缓存到模块变量，供crosshair回调使用
    KLINE_CACHE.data = kline

    const candleData: CandlestickData[] = kline.map(k => ({
      time: k.time as Time,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))

    const volData: HistogramData[] = kline.map(k => ({
      time: k.time as Time,
      value: k.volume,
      color: k.close >= k.open ? COLORS.volUp : COLORS.volDown,
    }))

    const closes = kline.map(k => k.close)
    const calcMA = (period: number): LineData[] => {
      const result: LineData[] = []
      for (let i = period - 1; i < closes.length; i++) {
        let sum = 0
        for (let j = i - period + 1; j <= i; j++) sum += closes[j]
        result.push({ time: kline[i].time as Time, value: sum / period })
      }
      return result
    }

    candleSeriesRef.current.setData(candleData)
    volumeSeriesRef.current.setData(volData)
    ma5Ref.current?.setData(calcMA(5))
    ma10Ref.current?.setData(calcMA(10))
    ma20Ref.current?.setData(calcMA(20))
    ma60Ref.current?.setData(calcMA(60))

    // Signal markers — 策略信号标注（仅形状+非红绿色，避免混淆买卖）
    const signalConfig: Record<string, { color: string; shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square' }> = {
      premium_b:    { color: '#58a6ff',  shape: 'circle' },    // 蓝圆
      premium_a:    { color: '#d29922',  shape: 'square' },    // 金方
      original:     { color: '#bc8cff',  shape: 'arrowUp' },   // 紫箭上
      ultra_shrink: { color: '#f7823b',  shape: 'arrowDown' }, // 橙箭下
    }
    const defaultConfig = { color: '#58a6ff', shape: 'circle' as const }

    if (signals.length > 0) {
      const markers = signals
        .map(s => {
          const idx = kline.findIndex(k => k.time === s.date)
          if (idx < 0) return null
          const cfg = signalConfig[s.type] || defaultConfig
          return {
            time: s.date as Time,
            position: 'aboveBar' as const,
            color: cfg.color,
            shape: cfg.shape,
            size: 1,
          }
        })
        .filter(Boolean) as {
          time: Time
          position: 'aboveBar' | 'belowBar' | 'inBar'
          color: string
          shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
          size: number
        }[]

      if (markers.length) {
        candleSeriesRef.current.setMarkers(markers)
      } else {
        candleSeriesRef.current.setMarkers([])
      }
    } else {
      // 没信号时清空
      candleSeriesRef.current?.setMarkers([])
    }

    // Visible range
    const visibleRange = Math.min(range, candleData.length)
    chartRef.current?.timeScale().setVisibleRange({
      from: candleData[candleData.length - visibleRange].time,
      to: candleData[candleData.length - 1].time,
    })
  }, [kline, signals, symbol, range])

  return <div ref={containerRef} style={{ width: '100%', height: '100%', touchAction: 'manipulation' }} />
}
