import { useState, useEffect, useCallback, useRef , Component , type ReactNode } from 'react'
import Chart, { CrosshairInfo } from './components/Chart'
import {
  KlineData, KlinePoint, Signal, PickRecord, WatchlistItem,
  searchStocks, getKline, getStockInfo, getPicks, getPickDates,
  getLaogaoPicks, getLaogaoDates,
  getWatchlist, addToWatchlist, removeFromWatchlist, updateWatchlistNote, reorderWatchlist,
} from './utils/api'

const RANGES = [
  { label: '1月', days: 21 },
  { label: '3月', days: 63 },
  { label: '6月', days: 126 },
  { label: '1年', days: 252 },
  { label: '全部', days: 9999 },
]

const STRATEGY_TABS = [
  { key: '',      label: '全部' },       // show all
  { key: 'premium_b',    label: 'B' },
  { key: 'premium_b2',   label: 'B2' },
  { key: 'premium_a',    label: 'A' },
  { key: 'ultra_shrink', label: '缩' },
  { key: 'original',     label: '原' },
]

const fmtVol = (v: number) => v >= 10000 ? (v / 10000).toFixed(2) + '万' : v.toFixed(0)

interface StockInfo {
  symbol: string
  name: string
}

interface LastCandle {
  close: number; open: number; high: number; low: number
  volume: number; prevClose: number
  change: number; changePct: number; date: string
}

/** "至今涨跌幅" = (最新收盘 - 光标K前一根收盘) / 光标K前一根收盘 */
function gainToToday(klineData: KlinePoint[], anchorClose: number): number | null {
  if (!klineData?.length) return null
  const latestClose = klineData[klineData.length - 1].close
  if (!anchorClose) return null
  return ((latestClose - anchorClose) / anchorClose) * 100
}

/** 计算某根K线位置的均线值 */
function maAt(closes: number[], period: number, idx: number): number | null {
  if (idx < period - 1 || idx >= closes.length) return null
  let sum = 0
  for (let i = idx - period + 1; i <= idx; i++) sum += closes[i]
  return sum / period
}

/** 更新MA显示 */
function updateMAs(closes: number[], idx: number, refs: HTMLSpanElement[]) {
  const periods = [5, 10, 20, 60]
  for (let i = 0; i < periods.length; i++) {
    const v = maAt(closes, periods[i], idx)
    if (refs[i]) refs[i].textContent = v != null ? v.toFixed(2) : '--'
  }
}

function clearMAs(refs: HTMLSpanElement[]) {
  for (const r of refs) { if (r) r.textContent = '--' }
}

// 图表错误边界: 渲染崩溃时显示错误提示(避免整个页面黑屏)
class ChartErrorBoundary extends Component<{ children: ReactNode }, { error: string | null; stack: string }> {
  state = { error: null as string | null, stack: '' }
  static getDerivedStateFromError(err: any) {
    return { error: String((err && err.message) || err), stack: String((err && err.stack) || '') }
  }
  componentDidCatch(err: any, info: any) {
    console.error('[Chart崩溃]', err, info)
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, color: '#f85149', fontFamily: 'monospace', fontSize: 13 }}>
          <b>图表渲染失败</b>
          <div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{this.state.error}</div>
          {this.state.stack && (
            <details style={{ marginTop: 8 }}><summary>错误堆栈(发给开发者)</summary>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 11, color: '#999', maxHeight: 200, overflow: 'auto' }}>{this.state.stack}</pre>
            </details>
          )}
          <button onClick={() => this.setState({ error: null, stack: '' })} style={{ marginTop: 12, padding: '6px 14px', cursor: 'pointer' }}>重试</button>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<StockInfo[]>([])
  const [showSearch, setShowSearch] = useState(false)

  const [currentStock, setCurrentStock] = useState<StockInfo | null>(null)
  const [kline, setKline] = useState<KlineData | null>(null)
  const [signals, setSignals] = useState<Signal[]>([])
  // 工具栏开关记忆: localStorage读取(useState惰性初始化, 首帧即正确值无闪烁)
  const savedToggles = (() => {
    try { return JSON.parse(localStorage.getItem('stockscope_toggles') || '{}') } catch { return {} }
  })()
  const [range, setRange] = useState(RANGES.find(r => r.label === savedToggles.rangeLabel) ?? RANGES[2]) // default 6m
  const [qfq, setQfq] = useState(typeof savedToggles.qfq === 'boolean' ? savedToggles.qfq : true)
  const [showBoll, setShowBoll] = useState(typeof savedToggles.showBoll === 'boolean' ? savedToggles.showBoll : false)  // 布林带显示开关(布按钮)
  const [showMa, setShowMa] = useState(typeof savedToggles.showMa === 'boolean' ? savedToggles.showMa : true)       // 均线显示开关(均按钮)




  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [picks, setPicks] = useState<PickRecord[]>([])
  const [pickDates, setPickDates] = useState<{ date: string; total: number }[]>([])
  const [selectedPickDate, setSelectedPickDate] = useState('')
  const [sidebarTab, setSidebarTab] = useState<'watchlist' | 'picks' | 'laogao' | 'chanlun' | 'chanlun_etf' | 'chanlun_index'>('watchlist')
  const [strategyFilter, setStrategyFilter] = useState('')  // '' = all
  const [laogaoPicks, setLaogaoPicks] = useState<import('./utils/api').LaogaoPick[]>([])
  const [laogaoDates, setLaogaoDates] = useState<{ date: string; total: number; worth_cnt: number }[]>([])
  const [selectedLaogaoDate, setSelectedLaogaoDate] = useState('')
  const [chanlunDates, setChanlunDates] = useState<{ date: string; total: number }[]>([])
  const [chanlunSignals, setChanlunSignals] = useState<any[]>([])
  const [selectedChanlunDate, setSelectedChanlunDate] = useState('')
  const [chanlunTypeFilter, setChanlunTypeFilter] = useState('')
  const [chanlunPreview, setChanlunPreview] = useState(false)  // 盘中预览模式
  const [chanlunEtf, setChanlunEtf] = useState(false)  // 缠论tab: 只看ETF信号
  const [chanlunIndex, setChanlunIndex] = useState(false)  // 缠论tab: 只看指数信号
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)
  const [measureMode, setMeasureMode] = useState(typeof savedToggles.measureMode === 'boolean' ? savedToggles.measureMode : false)
  const [chanlunMode, setChanlunMode] = useState(true)  // 默认选中缠(笔/中枢/买卖点)
  const [showAllZs, setShowAllZs] = useState(typeof savedToggles.showAllZs === 'boolean' ? savedToggles.showAllZs : false)  // 显示全部历史中枢(矩形框)
  const [showTrend, setShowTrend] = useState(typeof savedToggles.showTrend === 'boolean' ? savedToggles.showTrend : false)   // 显示趋势信号(daily_picks)
  const [showBottom, setShowBottom] = useState(typeof savedToggles.showBottom === 'boolean' ? savedToggles.showBottom : false)  // 显示底部信号(bottom_confirm)
  const [chanlunData, setChanlunData] = useState<any>(null)
  const [zsAsOf, setZsAsOf] = useState<any>(null)  // 动态中枢: {date, zd, zg, ext, since} 视角历史时回放当时中枢
  const zsSeq = useRef(0)  // 动态中枢请求独立序号(防旧股票/旧日期响应覆盖)
  const loadSeq = useRef(0)  // K线请求序号: 防快速切股竞态(旧响应覆盖新)
  const chanlunSeq = useRef(0)  // chanlun请求独立序号(不干扰K线)
  const [benchmarkIdx, setBenchmarkIdx] = useState<number | null>(null)

  // 开关变化即持久化(单一数据源: state本身, 无闭包过期问题)
  useEffect(() => {
    try {
      localStorage.setItem('stockscope_toggles', JSON.stringify({
        qfq, showBoll, showMa, measureMode, showAllZs, showTrend, showBottom,
        rangeLabel: range.label,
      }))
    } catch { /* 存储满忽略 */ }
  }, [qfq, showBoll, showMa, measureMode, showAllZs, showTrend, showBottom, range.label])
  const [focusDate, setFocusDate] = useState<string | null>(null)  // 选股跳转时聚焦的日期
  const [highlightSignal, setHighlightSignal] = useState<{ date: string; label: string } | null>(null)  // 来源信号高亮(从选股/底部确认/缠论tab点击)

  // ── Refs for crosshair direct-DOM updates ──
  const priceRef = useRef<HTMLSpanElement>(null)
  const changeRef = useRef<HTMLSpanElement>(null)
  const changePctRef = useRef<HTMLSpanElement>(null)
  const crosshairTimeRef = useRef<HTMLSpanElement>(null)
  const extraOpenRef = useRef<HTMLSpanElement>(null)
  const extraHighRef = useRef<HTMLSpanElement>(null)
  const extraLowRef = useRef<HTMLSpanElement>(null)
  const extraCloseRef = useRef<HTMLSpanElement>(null)
  const extraVolRef = useRef<HTMLSpanElement>(null)
  const dayGainRef = useRef<HTMLSpanElement>(null)
  const ma5Ref = useRef<HTMLSpanElement>(null)
  const ma10Ref = useRef<HTMLSpanElement>(null)
  const ma20Ref = useRef<HTMLSpanElement>(null)
  const ma60Ref = useRef<HTMLSpanElement>(null)
  const measureRef = useRef<HTMLSpanElement>(null)  // 区间测量显示
  const lastCandleRef = useRef<LastCandle | null>(null)

  // Stored kline for gainToToday (avoid stale closure)
  const klineRef = useRef<KlinePoint[]>([])

  // ── Refs for measurement mode (used inside crosshair callback) ──
  const measureModeRef = useRef(false)
  const benchmarkRef = useRef<number | null>(null)

  // ── update 至今涨跌幅 via refs, no React re-render ──
  // 至今涨跌幅 = (最新收盘 - 前一根收盘) / 前一根收盘
  const updateGainToToday = (anchorClose: number) => {
    const arr = klineRef.current
    if (!arr.length || !dayGainRef.current) return
    const dg = gainToToday(arr, anchorClose)
    if (dg === null) return
    dayGainRef.current.textContent = (dg >= 0 ? '+' : '') + dg.toFixed(2) + '%'
    dayGainRef.current.style.color = dg >= 0 ? 'var(--red)' : 'var(--green)'
  }

  // Keep ref in sync with state every render
  if (kline?.kline) klineRef.current = kline.kline
  measureModeRef.current = measureMode
  benchmarkRef.current = benchmarkIdx

  // Load watchlist and pick dates on mount
  useEffect(() => {
    getWatchlist().then(setWatchlist)
  }, [])

  // Load pick dates (filtered by strategy)
  useEffect(() => {
    getPickDates(strategyFilter || undefined).then(dates => {
      setPickDates(dates.map(d => ({ date: d.date, total: d.total_picks })))
      // 如果当前日期不在新日期列表里，选第一个
      if (dates.length > 0) {
        if (!dates.find(d => d.date === selectedPickDate)) {
          setSelectedPickDate(dates[0].date)
        }
      } else {
        setSelectedPickDate('')
      }
    })
  }, [strategyFilter])

  // Load picks for selected date + strategy filter
  useEffect(() => {
    let cancelled = false
    if (selectedPickDate) {
      setPicks([])
      getPicks(selectedPickDate, strategyFilter || undefined).then(data => {
        if (!cancelled) setPicks(data)
      })
    }
    return () => { cancelled = true }
  }, [selectedPickDate, strategyFilter])

  // ── 老高多重确认策略 ──
  useEffect(() => {
    getLaogaoDates().then(dates => {
      setLaogaoDates(dates)
      if (dates.length > 0) {
        if (!dates.find(d => d.date === selectedLaogaoDate)) {
          setSelectedLaogaoDate(dates[0].date)
        }
      } else {
        setSelectedLaogaoDate('')
      }
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    if (selectedLaogaoDate) {
      setLaogaoPicks([])
      getLaogaoPicks(selectedLaogaoDate).then(data => {
        if (!cancelled) setLaogaoPicks(data)
      })
    }
    return () => { cancelled = true }
  }, [selectedLaogaoDate])

  // ── 缠论信号 ──
  useEffect(() => {
    let cancelled = false
    const pv = chanlunPreview ? '&preview=1' : ''
    const ev = chanlunEtf ? '&etf=1' : ''
    const cv = chanlunIndex ? '&category=index' : ''
    const q = chanlunTypeFilter
      ? `/stockscope/api/chanlun/dates?type=${encodeURIComponent(chanlunTypeFilter)}${pv}${ev}${cv}`
      : `/stockscope/api/chanlun/dates${(pv || ev || cv) ? '?' + [pv.replace(/^&/, ''), ev.replace(/^&/, ''), cv.replace(/^&/, '')].filter(Boolean).join('&') : ''}`
    fetch(q).then(r => r.json()).then((dates: { date: string; total: number }[]) => {
      if (cancelled) return
      setChanlunDates(dates)
      if (dates.length > 0) {
        setSelectedChanlunDate(prev => (dates.find(d => d.date === prev) ? prev : dates[0].date))
      } else {
        setSelectedChanlunDate('')
      }
    })
    return () => { cancelled = true }
  }, [chanlunTypeFilter, chanlunPreview, chanlunEtf, chanlunIndex])

  useEffect(() => {
    let cancelled = false
    if (selectedChanlunDate) {
      setChanlunSignals([])
      const pv = chanlunPreview ? '&preview=1' : ''
      const q = `/stockscope/api/chanlun/signals?date=${selectedChanlunDate}${chanlunTypeFilter ? `&type=${encodeURIComponent(chanlunTypeFilter)}` : ''}${pv}${chanlunEtf ? '&etf=1' : ''}${chanlunIndex ? '&category=index' : ''}`
      fetch(q).then(r => r.json()).then(data => {
        if (!cancelled) setChanlunSignals(data)
      })
    }
    return () => { cancelled = true }
  }, [selectedChanlunDate, chanlunTypeFilter, chanlunPreview, chanlunEtf, chanlunIndex])

  // Load K-line for current stock
  const loadStock = useCallback(async (symbol: string, name: string, signalDate?: string) => {
    setCurrentStock({ symbol, name })
    setSearchQuery('')
    setShowSearch(false)
    setBenchmarkIdx(null)  // 切股票清基准
    const seq = ++loadSeq.current
    const data = await getKline(symbol, qfq, 1000)
    if (seq !== loadSeq.current) return  // 已被更新的切股请求覆盖
    setKline(data)
    setSignals(data.signals)
    // 有指定信号日期则跳转到该日期，否则定位最新K线(不跳选股信号日期—选股信号可能很旧)
    setFocusDate(signalDate || null)
  }, [qfq, selectedPickDate])

  // Reload kline when qfq changes and a stock is selected
  useEffect(() => {
    if (currentStock) {
      const s = currentStock
      const seq = ++loadSeq.current
      getKline(s.symbol, qfq, 1000).then(data => {
        if (seq !== loadSeq.current) return
        setKline(data)
        setSignals(data.signals)
        // qfq切换保持当前focusDate不变(不跳选股信号日期)
      })
    }
  }, [qfq])

  // Load chanlun data when mode enabled or stock changes
  useEffect(() => {
    if (chanlunMode && currentStock) {
      const seq = ++chanlunSeq.current
      zsSeq.current++  // 切股/重载: 使旧股票的动态中枢请求失效
      setZsAsOf(null)  // 回到默认最新中枢
      fetch(`/stockscope/api/chanlun/${currentStock.symbol}`)
        .then(r => r.json())
        .then(d => { if (seq === chanlunSeq.current) setChanlunData(d) })
        .catch(() => { if (seq === chanlunSeq.current) setChanlunData(null) })
    } else {
      setChanlunData(null)
      setZsAsOf(null)
    }
  }, [chanlunMode, currentStock, qfq])

  // 动态中枢: 视角滚到历史时, 按当时日期回放中枢(轻量as_of请求)
  const loadZsAsOf = useCallback((date: string) => {
    if (!currentStock) return
    const seq = ++zsSeq.current
    if (!date) {  // 视角回到最新 → 用默认最新中枢
      if (seq === zsSeq.current) setZsAsOf(null)
      return
    }
    fetch(`/stockscope/api/chanlun/${currentStock.symbol}?as_of=${date}`)
      .then(r => r.json())
      .then(d => { if (seq === zsSeq.current && d?.last_zhongshu) setZsAsOf({ date, ...d.last_zhongshu }) })
      .catch(() => { if (seq === zsSeq.current) setZsAsOf(null) })
  }, [currentStock])

  // Update info bar when kline data loads
  useEffect(() => {
    if (!kline?.kline?.length) return
    const arr = kline.kline
    const last = arr[arr.length - 1]
    const prev = arr.length > 1 ? arr[arr.length - 2] : null
    const change = last.close - (prev?.close ?? last.close)
    const changePct = prev?.close ? (change / prev.close * 100) : 0

    lastCandleRef.current = {
      close: last.close, open: last.open, high: last.high, low: last.low,
      volume: last.volume, prevClose: prev?.close ?? last.close,
      change, changePct, date: last.time,
    }

    if (priceRef.current) priceRef.current.textContent = last.close.toFixed(2)
    if (changeRef.current) {
      changeRef.current.textContent = (change >= 0 ? '+' : '') + change.toFixed(2)
      changeRef.current.className = `change ${change >= 0 ? 'up' : 'down'}`
    }
    if (changePctRef.current) {
      changePctRef.current.textContent = (changePct >= 0 ? '+' : '') + changePct.toFixed(2) + '%'
      changePctRef.current.style.color = changePct >= 0 ? 'var(--red)' : 'var(--green)'
    }
    if (crosshairTimeRef.current) {
      crosshairTimeRef.current.textContent = last.time
      crosshairTimeRef.current.style.display = ''
    }
    if (extraOpenRef.current) extraOpenRef.current.textContent = last.open.toFixed(2)
    if (extraHighRef.current) extraHighRef.current.textContent = last.high.toFixed(2)
    if (extraLowRef.current) extraLowRef.current.textContent = last.low.toFixed(2)
    if (extraCloseRef.current) extraCloseRef.current.textContent = last.close.toFixed(2)
    if (extraVolRef.current) extraVolRef.current.textContent = fmtVol(last.volume)
    updateGainToToday(last.close)  // latest → latest = 0%
    // 初始清除MA和测量
    clearMAs([ma5Ref.current, ma10Ref.current, ma20Ref.current, ma60Ref.current].filter(Boolean) as HTMLSpanElement[])
    if (measureRef.current) measureRef.current.textContent = ''
  }, [kline])

  // ── 至今涨跌幅不再依赖 range 范围 ──

  // Crosshair handler — directly updates DOM, no React state involved
  const handleCrosshairMove = useCallback((data: CrosshairInfo | null) => {
    const lc = lastCandleRef.current
    const arr = klineRef.current
    if (data && lc) {
      const change = data.close - data.prevClose
      const changePct = data.prevClose ? (change / data.prevClose * 100) : 0
      if (priceRef.current) priceRef.current.textContent = data.close.toFixed(2)
      if (changeRef.current) {
        changeRef.current.textContent = (change >= 0 ? '+' : '') + change.toFixed(2)
        changeRef.current.className = `change ${change >= 0 ? 'up' : 'down'}`
      }
      if (changePctRef.current) {
        changePctRef.current.textContent = (changePct >= 0 ? '+' : '') + changePct.toFixed(2) + '%'
        changePctRef.current.style.color = changePct >= 0 ? 'var(--red)' : 'var(--green)'
      }
      if (crosshairTimeRef.current) {
        crosshairTimeRef.current.textContent = data.time
        crosshairTimeRef.current.style.display = ''
      }
      if (extraOpenRef.current) extraOpenRef.current.textContent = data.open.toFixed(2)
      if (extraHighRef.current) extraHighRef.current.textContent = data.high.toFixed(2)
      if (extraLowRef.current) extraLowRef.current.textContent = data.low.toFixed(2)
      if (extraCloseRef.current) extraCloseRef.current.textContent = data.close.toFixed(2)
      if (extraVolRef.current) extraVolRef.current.textContent = fmtVol(data.volume)
      updateGainToToday(data.prevClose)
      // MA从kline数据直接计算
      const closes = arr.map(k => k.close)
      const cursorIdx = arr.findIndex(k => k.time === data.time)
      if (cursorIdx >= 0) {
        updateMAs(closes, cursorIdx, [ma5Ref.current, ma10Ref.current, ma20Ref.current, ma60Ref.current].filter(Boolean) as HTMLSpanElement[])
      } else {
        clearMAs([ma5Ref.current, ma10Ref.current, ma20Ref.current, ma60Ref.current].filter(Boolean) as HTMLSpanElement[])
      }
      // 区间测量
      if (measureModeRef.current && benchmarkRef.current !== null && cursorIdx >= 0 && measureRef.current) {
        const bi = benchmarkRef.current
        const ci = cursorIdx
        const b = arr[bi]
        const c = arr[ci]
        if (b && c) {
          const pct = ((c.close - b.close) / b.close) * 100
          const days = Math.abs(ci - bi)
          // 区间振幅：取所有K线最高最低
          const sIdx = Math.min(bi, ci), eIdx = Math.max(bi, ci)
          let maxH = -Infinity, minL = Infinity
          for (let i = sIdx; i <= eIdx; i++) {
            if (arr[i].high > maxH) maxH = arr[i].high
            if (arr[i].low < minL) minL = arr[i].low
          }
          const amp = ((maxH - minL) / b.close) * 100
          const sign = pct >= 0 ? '+' : ''
          measureRef.current.textContent = `涨幅${sign}${pct.toFixed(2)}%  天数${days}天  振幅${amp.toFixed(2)}%`
          measureRef.current.style.color = pct >= 0 ? 'var(--red)' : 'var(--green)'
        }
      } else if (measureRef.current) {
        measureRef.current.textContent = benchmarkRef.current !== null ? '点击设基准 - 移动光标测量' : ''
      }
    } else if (lc) {
      if (priceRef.current) priceRef.current.textContent = lc.close.toFixed(2)
      if (changeRef.current) {
        changeRef.current.textContent = (lc.change >= 0 ? '+' : '') + lc.change.toFixed(2)
        changeRef.current.className = `change ${lc.change >= 0 ? 'up' : 'down'}`
      }
      if (changePctRef.current) {
        changePctRef.current.textContent = (lc.changePct >= 0 ? '+' : '') + lc.changePct.toFixed(2) + '%'
        changePctRef.current.style.color = lc.changePct >= 0 ? 'var(--red)' : 'var(--green)'
      }
      if (crosshairTimeRef.current) {
        crosshairTimeRef.current.textContent = lc.date
        crosshairTimeRef.current.style.display = ''
      }
      if (extraOpenRef.current) extraOpenRef.current.textContent = lc.open.toFixed(2)
      if (extraHighRef.current) extraHighRef.current.textContent = lc.high.toFixed(2)
      if (extraLowRef.current) extraLowRef.current.textContent = lc.low.toFixed(2)
      if (extraCloseRef.current) extraCloseRef.current.textContent = lc.close.toFixed(2)
      if (extraVolRef.current) extraVolRef.current.textContent = fmtVol(lc.volume)
      updateGainToToday(lc.close)
      clearMAs([ma5Ref.current, ma10Ref.current, ma20Ref.current, ma60Ref.current].filter(Boolean) as HTMLSpanElement[])
      // 复位时更新测量（基准→最新）
      if (measureModeRef.current && benchmarkRef.current !== null && measureRef.current) {
        const bi = benchmarkRef.current
        const li = arr.length - 1
        if (bi >= 0 && bi < arr.length && li >= 0) {
          const b = arr[bi]
          const l = arr[li]
          const pct = ((l.close - b.close) / b.close) * 100
          const days = li - bi
          // 区间振幅：取所有K线最高最低
          const sIdx = Math.min(bi, li), eIdx = Math.max(bi, li)
          let maxH = -Infinity, minL = Infinity
          for (let i = sIdx; i <= eIdx; i++) {
            if (arr[i].high > maxH) maxH = arr[i].high
            if (arr[i].low < minL) minL = arr[i].low
          }
          const amp = ((maxH - minL) / b.close) * 100
          const sign = pct >= 0 ? '+' : ''
          measureRef.current.textContent = `涨幅${sign}${pct.toFixed(2)}%  天数${days}天  振幅${amp.toFixed(2)}%`
          measureRef.current.style.color = pct >= 0 ? 'var(--red)' : 'var(--green)'
        }
      } else if (measureRef.current) {
        measureRef.current.textContent = ''
      }
    }
  }, [])

  // Search handler
  useEffect(() => {
    if (searchQuery.length < 1) {
      setSearchResults([])
      setShowSearch(false)
      return
    }
    const timer = setTimeout(async () => {
      const results = await searchStocks(searchQuery)
      setSearchResults(results)
      setShowSearch(results.length > 0)
    }, 200)
    return () => clearTimeout(timer)
  }, [searchQuery])

  // Add to watchlist
  const handleAddWatchlist = async () => {
    if (!currentStock) return
    await addToWatchlist(currentStock.symbol, currentStock.name)
    const wl = await getWatchlist()
    setWatchlist(wl)
  }

  // Remove from watchlist
  const handleRemoveWatchlist = async (symbol: string) => {
    await removeFromWatchlist(symbol)
    const wl = await getWatchlist()
    setWatchlist(wl)
  }

  // Select from watchlist
  const handleSelectWatchlist = (item: WatchlistItem) => {
    setHighlightSignal(null)
    loadStock(item.symbol, item.name)
  }

  // Select from picks — jump to signal date
  const handleSelectPick = (pick: PickRecord) => {
    setHighlightSignal({ date: pick.date, label: (pick.strategy_id || 'original').split(',')[0] })
    loadStock(pick.symbol, pick.name, pick.date)
  }

  // Chart click → toggle benchmark
  const handleChartClick = (time: string) => {
    if (!measureMode) return
    const idx = klineRef.current.findIndex(k => k.time === time)
    if (idx < 0) return
    setBenchmarkIdx(prev => prev === idx ? null : idx)
  }

  // In watchlist?
  const isInWatchlist = currentStock ? watchlist.some(w => w.symbol === currentStock.symbol) : false

  // ── Watchlist drag & drop ──
  const dragIdx = useRef<number | null>(null)
  const handleDragStart = (e: React.DragEvent, idx: number) => {
    dragIdx.current = idx
    e.dataTransfer.effectAllowed = 'move'
  }
  const handleDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (idx !== dragOverIdx) setDragOverIdx(idx)
  }
  const handleDragLeave = () => setDragOverIdx(null)
  const handleDrop = async (e: React.DragEvent, dropIdx: number) => {
    e.preventDefault()
    setDragOverIdx(null)
    const srcIdx = dragIdx.current
    dragIdx.current = null
    if (srcIdx === null || srcIdx === dropIdx) return
    const newList = [...watchlist]
    const [removed] = newList.splice(srcIdx, 1)
    newList.splice(dropIdx, 0, removed)
    setWatchlist(newList)
    await reorderWatchlist(newList.map(w => w.symbol))
  }
  const handleDragEnd = () => {
    dragIdx.current = null
    setDragOverIdx(null)
  }

  return (
    <>
      {/* Header */}
      <div className="header">
        <h1>Stock<span className="accent">Scope</span></h1>

        <div className="search-wrapper">
          <span className="search-icon">🔍</span>
          <input
            type="text"
            placeholder="搜索股票代码/名称..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onFocus={() => searchResults.length > 0 && setShowSearch(true)}
            onBlur={() => setTimeout(() => setShowSearch(false), 200)}
          />
          {showSearch && (
            <div className="search-results">
              {searchResults.map(s => (
                <div key={s.symbol} className="search-result-item"
                  onMouseDown={() => { setHighlightSignal(null); loadStock(s.symbol, s.name) }}>
                  <span className="sym">{s.symbol}</span>
                  <span className="name">{s.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="toolbar">
          <button className={`toolbar-btn ${showMa ? 'active' : ''}`}
            onClick={() => { setShowMa((s: boolean) => !s) }}
            style={{ fontSize: 11, padding: '2px 6px', marginRight: 6 }}
            title="均线显示开关(MA5/10/20/60)">
            均
          </button>
          <button className={`toolbar-btn ${showBoll ? 'active' : ''}`}
            onClick={() => { setShowBoll((s: boolean) => !s) }}
            style={{ fontSize: 11, padding: '2px 6px', marginRight: 6 }}
            title="布林带显示开关(BOLL 20,2 上红/中蓝/下绿)">
            布
          </button>
          <div className="qfq-toggle">
            <input type="checkbox" id="qfq" checked={qfq}
              onChange={e => { setQfq(e.target.checked);  }} />
            <label htmlFor="qfq">前复权</label>
          </div>
          <button className={`toolbar-btn ${measureMode ? 'active' : ''}`}
            onClick={() => { setMeasureMode((m: boolean) => !m); setBenchmarkIdx(null) }}
            style={{ fontSize: 11, padding: '2px 6px' }}>
            M
          </button>
          <button className={`toolbar-btn ${showAllZs ? 'active' : ''}`}
            onClick={() => { setShowAllZs((s: boolean) => !s) }}
            style={{ fontSize: 11, padding: '2px 6px' }}
            title="中枢显示开关(青色上下沿线+矩形框)">
            枢
          </button>
          <button className={`toolbar-btn ${showTrend ? 'active' : ''}`}
            onClick={() => { setShowTrend((s: boolean) => !s) }}
            style={{ fontSize: 11, padding: '2px 6px' }}
            title="趋势信号显示开关(选股策略标记)">
            趋
          </button>
          <button className={`toolbar-btn ${showBottom ? 'active' : ''}`}
            onClick={() => { setShowBottom((s: boolean) => !s) }}
            style={{ fontSize: 11, padding: '2px 6px' }}
            title="底部信号显示开关(底部确认策略标记)">
            底
          </button>

          <div className="range-group">
            {RANGES.map(r => (
              <button key={r.label}
                className={`range-btn ${range.label === r.label ? 'active' : ''}`}
                onClick={() => { setRange(r); setFocusDate(null);  }}>
                {r.label}
              </button>
            ))}
          </div>

          {currentStock && (
            <button className="toolbar-btn" onClick={handleAddWatchlist}
              disabled={isInWatchlist}>
              {isInWatchlist ? '已关注' : '+ 关注'}
            </button>
          )}
        </div>
      </div>

      {/* Stock Info Bar — DOM via refs, no re-render on crosshair */}
      {currentStock && (
        <div className="stock-info-bar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, overflow: 'hidden', flexWrap: 'nowrap', whiteSpace: 'nowrap' }}>
            <span className="symbol">{currentStock.symbol}</span>
            <span className="name">{currentStock.name}</span>
            {kline?.mktcap ? (
              <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                总市值 {(kline.mktcap / 10000).toFixed(1)}亿
              </span>
            ) : null}
            {kline?.nmc ? (
              <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                流通 {(kline.nmc / 10000).toFixed(1)}亿
              </span>
            ) : null}
            <span ref={priceRef} className="price">--</span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>涨跌</span>
            <span ref={changeRef} className="change"></span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>涨跌幅</span>
            <span ref={changePctRef} style={{ fontSize: 13, display: 'inline-block' }}></span>
            <span ref={crosshairTimeRef}
              style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
            </span>
            {/* 额外数据：开 高 低 收 量 至今涨幅 */}
            <div style={{ display: 'flex', gap: 10, fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
              <span>开 <span ref={extraOpenRef}>--</span></span>
              <span>高 <span ref={extraHighRef}>--</span></span>
              <span>低 <span ref={extraLowRef}>--</span></span>
              <span>收 <span ref={extraCloseRef}>--</span></span>
              <span>量 <span ref={extraVolRef}>--</span></span>
              <span style={{ color: 'var(--text-muted)' }}>至今</span>
              <span ref={dayGainRef} style={{ fontWeight: 500 }}></span>
            </div>
            {/* 信号图例 — 固定展示全部5个策略(缩写, 防换行撑高数据栏) */}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0, marginLeft: 8, flexWrap: 'nowrap', whiteSpace: 'nowrap' }}>
              <span className="signal-badge premium_b">▲极B</span>
              <span className="signal-badge premium_b2">⬡极B2</span>
              <span className="signal-badge premium_a">■极A</span>
              <span className="signal-badge original">●原</span>
              <span className="signal-badge ultra_shrink">▼超缩</span>
              <span className="signal-badge bottom_confirm">▲底确</span>
            </div>
          </div>
          {/* 第二行：MA均线跟随光标 */}
          <div style={{ display: 'flex', gap: 10, fontSize: 11, color: 'var(--text-secondary)', padding: '2px 12px 4px', whiteSpace: 'nowrap' }}>
            <span>MA5 <span ref={ma5Ref} style={{ color: '#f0d43a' }}>--</span></span>
            <span>MA10 <span ref={ma10Ref} style={{ color: '#f7823b' }}>--</span></span>
            <span>MA20 <span ref={ma20Ref} style={{ color: '#58a6ff' }}>--</span></span>
            <span>MA60 <span ref={ma60Ref} style={{ color: '#bc8cff' }}>--</span></span>
          </div>
          {/* 第三行：区间测量 (有基准才显示, 避免空行撑高数据栏) */}
          {measureMode && benchmarkIdx !== null && (
            <div style={{ display: 'flex', gap: 10, fontSize: 11, color: 'var(--text-secondary)', padding: '0 12px 4px', whiteSpace: 'nowrap' }}>
              <span ref={measureRef}></span>
            </div>
          )}
        </div>
      )}

      {/* Main Layout */}
      <div className="main-layout">
        {/* Chart */}
        <div className="chart-area">
          <div className="chart-container">
            <ChartErrorBoundary key={currentStock?.symbol || 'none'}>
            <Chart
              kline={kline?.kline ?? []}
              signals={kline?.signals ?? []}
              symbol={currentStock?.symbol ?? ''}
              range={range.days}
              onCrosshairMove={handleCrosshairMove}
              onChartClick={handleChartClick}
              benchmarkTime={benchmarkIdx !== null && kline ? kline.kline[benchmarkIdx]?.time : null}
              highlightSignal={highlightSignal}
              focusDate={focusDate}
              chanlun={chanlunData}
              zsAsOf={zsAsOf}
              onZsRangeChange={loadZsAsOf}
              showAllZs={showAllZs}
              showTrend={showTrend}
              showBottom={showBottom}
              showBoll={showBoll}
              showMa={showMa}
            />
            </ChartErrorBoundary>
            {(!currentStock || !kline) && (
              <div style={{
                position: 'absolute', inset: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'var(--text-muted)', fontSize: 14,
                flexDirection: 'column', gap: 8,
                background: 'var(--bg, #0d1117)', pointerEvents: 'none', zIndex: 5,
              }}>
                <div style={{ fontSize: 32 }}>📈</div>
                <div>搜索股票查看K线图</div>
                <div style={{ fontSize: 12 }}>支持代码或名称模糊搜索</div>
              </div>
            )}
          </div>
          </div>
          {/* Right Sidebar — 自选股/每日选股 切换 */}
          <div className="watchlist-panel">
          <div className="wl-tabs">
          <button className={`wl-tab ${sidebarTab === 'watchlist' ? 'active' : ''}`}
            onClick={() => setSidebarTab('watchlist')}>
            自选股 <span className="wl-count">{watchlist.length}</span>
          </button>
          <button className={`wl-tab ${sidebarTab === 'picks' ? 'active' : ''}`}
            onClick={() => setSidebarTab('picks')}>
              趋势选股
            </button>
          <button className={`wl-tab ${sidebarTab === 'laogao' ? 'active' : ''}`}
            onClick={() => setSidebarTab('laogao')}>
              底部确认
            </button>
          <button className={`wl-tab ${sidebarTab === 'chanlun_index' ? 'active' : ''}`}
            onClick={() => { setSidebarTab('chanlun_index'); setChanlunEtf(false); setChanlunIndex(true) }}>
              缠论指数
            </button>
          <button className={`wl-tab ${sidebarTab === 'chanlun_etf' ? 'active' : ''}`}
            onClick={() => { setSidebarTab('chanlun_etf'); setChanlunEtf(true); setChanlunIndex(false) }}>
              缠论ETF
            </button>
          <button className={`wl-tab ${sidebarTab === 'chanlun' ? 'active' : ''}`}
            onClick={() => { setSidebarTab('chanlun'); setChanlunEtf(false); setChanlunIndex(false) }}>
              缠论
            </button>
          </div>

          {sidebarTab === 'watchlist' ? (
            <div className="watchlist-items">
              {watchlist.length === 0 ? (
                <div className="watchlist-empty">
                  暂无自选股<br />
                  搜索股票后点击「+ 关注」添加
                </div>
              ) : (
                watchlist.map((item, idx) => (
                  <div key={item.symbol}
                    className={`watchlist-item ${currentStock?.symbol === item.symbol ? 'active' : ''} ${dragOverIdx === idx ? 'drag-over' : ''}`}
                    draggable
                    onDragStart={e => handleDragStart(e, idx)}
                    onDragOver={e => handleDragOver(e, idx)}
                    onDragLeave={handleDragLeave}
                    onDrop={e => handleDrop(e, idx)}
                    onDragEnd={handleDragEnd}
                    onClick={() => handleSelectWatchlist(item)}>
                    <span className="drag-handle">⋮⋮</span>
                    <span className="wl-sym">{item.symbol}</span>
                    <span className="wl-name">{item.name}</span>
                    <button className="wl-remove"
                      onClick={e => { e.stopPropagation(); handleRemoveWatchlist(item.symbol) }}>
                      ×
                    </button>
                  </div>
                ))
              )}
            </div>
          ) : sidebarTab === 'picks' ? (
            <div className="watchlist-items">
              {/* Strategy filter tabs — 先策略 */}
              <div className="picks-strategy-bar" style={{ display: 'flex', gap: 4, padding: '4px 8px', borderBottom: '1px solid var(--border)' }}>
                {STRATEGY_TABS.map(st => (
                  <button key={st.key}
                    className={`range-btn ${strategyFilter === st.key ? 'active' : ''}`}
                    onClick={() => setStrategyFilter(st.key)}
                    style={{ fontSize: 11, padding: '2px 6px' }}>
                    {st.label}
                  </button>
                ))}
              </div>
              {/* Date selector — 后日期 */}
              {pickDates.length > 0 && (
                <div className="picks-date-bar" style={{ maxHeight: 200, overflowY: 'auto' }}>
                  {pickDates.map(d => (
                    <button key={d.date}
                      className={`range-btn ${d.date === selectedPickDate ? 'active' : ''}`}
                      onClick={() => setSelectedPickDate(d.date)}>
                      {d.date.slice(5)} <span className="wl-count">{d.total}</span>
                    </button>
                  ))}
                </div>
              )}
              {picks.length === 0 ? (
                <div className="watchlist-empty">
                  {selectedPickDate
                    ? `${selectedPickDate} 无策略选股数据`
                    : '暂无选股数据\n15:22定时同步后更新'}
                </div>
              ) : (
                picks.map(p => (
                  <div key={p.symbol}
                    className={`watchlist-item ${currentStock?.symbol === p.symbol ? 'active' : ''}`}
                    onClick={() => handleSelectPick(p)}>
                    <div style={{ flex: 1 }}>
                      <span className="wl-sym">{p.symbol}</span>
                      <span className="wl-name">{p.name}</span>
                    </div>
                    <div className="pc-tags" style={{ flexShrink: 0 }}>
                      {p.strategy_id?.split(',').map((st: string) => (
                        <span key={st} className={`pick-tag ${st.trim()}`}>
                          {st.trim() === 'premium_b' ? 'B' : st.trim() === 'premium_b2' ? 'B2' : st.trim() === 'premium_a' ? 'A' : st.trim() === 'ultra_shrink' ? '缩' : '原'}
                        </span>
                      ))}
                      <span className="pick-tag">{p.dist_ma20?.toFixed(1)}%</span>
                      {p.ret_pct != null && (
                        <span className="pick-tag" title="信号日收盘→最新收盘"
                          style={{ color: p.ret_pct >= 0 ? '#f85149' : '#3fb950', fontWeight: 600 }}>
                          {p.ret_pct >= 0 ? '+' : ''}{p.ret_pct}%
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : sidebarTab === 'chanlun' || sidebarTab === 'chanlun_etf' || sidebarTab === 'chanlun_index' ? (
            <div className="watchlist-items">
              {/* 类型过滤 */}
              <div className="picks-strategy-bar" style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', gap: 4, marginBottom: 4, alignItems: 'center' }}>
                  <span
                    className={`range-btn ${chanlunPreview ? 'active' : ''}`}
                    onClick={() => setChanlunPreview(!chanlunPreview)}
                    title="盘中预览: 用今日未收盘数据提前算信号(未确认); 关闭=正式已确认信号"
                    style={{ fontSize: 11, padding: '2px 6px', cursor: 'pointer', color: chanlunPreview ? '#a371f7' : undefined, borderColor: chanlunPreview ? '#a371f7' : undefined }}>
                    🕐 盘中{chanlunPreview ? 'ON' : 'OFF'}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                  {['', '一买', '二买', '三买', '二三买', 'd3', 'w30'].map(t => (
                    <button key={t || 'all'}
                      className={`range-btn ${chanlunTypeFilter === t ? 'active' : ''}`}
                      onClick={() => setChanlunTypeFilter(t)}
                      style={{ fontSize: 11, padding: '2px 6px' }}>
                      {t === '' ? '全' : t === 'd3' ? 'D3' : t === 'w30' ? 'W30' : t.replace('二三买', '2+3买').replace('一买', '1买').replace('二买', '2买').replace('三买', '3买')}
                    </button>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                  {['一卖', '二卖', '三卖', '二三卖'].map(t => (
                    <button key={t}
                      className={`range-btn ${chanlunTypeFilter === t ? 'active' : ''}`}
                      onClick={() => setChanlunTypeFilter(t)}
                      style={{ fontSize: 11, padding: '2px 6px' }}>
                      {t === '二三卖' ? '2+3卖' : t.replace('一卖', '1卖').replace('二卖', '2卖').replace('三卖', '3卖')}
                    </button>
                  ))}
                </div>
              </div>
              {/* 日期选择 */}
              {chanlunDates.length > 0 && (
                <div className="picks-date-bar" style={{ maxHeight: 120, overflowY: 'auto', borderBottom: '1px solid var(--border)' }}>
                  {chanlunDates.map(d => (
                    <button key={d.date}
                      className={`range-btn ${d.date === selectedChanlunDate ? 'active' : ''}`}
                      onClick={() => setSelectedChanlunDate(d.date)}>
                      {d.date.slice(5)} <span className="wl-count">{d.total}</span>
                    </button>
                  ))}
                </div>
              )}
              <div style={{ padding: '6px 10px', fontSize: 11, color: '#888', borderBottom: '1px solid var(--border)' }}>
                缠论: 红1买/橙2买/黄3买↑ 绿1卖/灰2卖/蓝3卖↓
              </div>
              {chanlunSignals.length === 0 ? (
                <div className="watchlist-empty">
                  {selectedChanlunDate
                    ? `${selectedChanlunDate} 无缠论信号`
                    : '暂无缠论信号\n历史扫描完成后更新'}
                </div>
              ) : (
                // 合并同股同日的重合信号(如 二买+三买 同一天), 逐信号保留推翻✗标记
                (() => {
                  const mergedList: any[] = []
                  const byKey: Record<string, any> = {}
                  chanlunSignals.forEach((s: any) => {
                    const key = `${s.symbol}_${s.date}`
                    if (byKey[key]) {
                      byKey[key].zd = byKey[key].zd || s.zd
                      byKey[key].zg = byKey[key].zg || s.zg
                      // 逐类型status: error优先
                      if (!(s.type in byKey[key].typeStatus)) byKey[key].typeStatus[s.type] = s.status
                      else if (s.status === 'error') byKey[key].typeStatus[s.type] = 'error'
                      if (s.status === 'error') byKey[key].status = 'error'
                      // 强度取更强
                      const prio: Record<string, number> = { strong: 0, neutral: 1, weak: 2 }
                      if ((prio[s.strength] ?? 1) < (prio[byKey[key].strength] ?? 1)) byKey[key].strength = s.strength
                    } else {
                      byKey[key] = { ...s, typeStatus: { [s.type]: s.status } }
                      mergedList.push(byKey[key])
                    }
                  })
                  return mergedList.map(s => {
                    // 逐类型拼接: 二买✗+三买 / 二买+三买✗
                    const dispType = Object.keys(s.typeStatus).map(t =>
                      t + (s.typeStatus[t] === 'error' ? '✗' : '')).join('+')
                    const hasErr = s.status === 'error'
                    return (
                    <div key={s.symbol + s.date + dispType}
                      className={`watchlist-item ${currentStock?.symbol === s.symbol ? 'active' : ''} ${hasErr ? 'sig-error' : ''}`}
                      onClick={() => { setHighlightSignal(null); loadStock(s.symbol, s.name, s.date) }}
                      style={hasErr ? { opacity: 0.55 } : undefined}>
                      <div style={{ flex: 1 }}>
                        <span className="wl-sym">{s.symbol}</span>
                        <span className="wl-name">{s.name}</span>
                      </div>
                      <div className="pc-tags" style={{ flexShrink: 0 }}>
                        {s.strength === 'strong' && (
                          <span className="pick-tag" style={{ color: '#3fb950', borderColor: '#3fb950' }}>🟢强{s.score?.toFixed(0)}</span>
                        )}
                        {s.strength === 'weak' && (
                          <span className="pick-tag" style={{ color: '#f85149', borderColor: '#f85149' }}>🔴弱{s.score?.toFixed(0)}</span>
                        )}
                        {(!s.strength || s.strength === 'neutral') && (
                          <span className="pick-tag" style={{ color: '#8b949e', borderColor: '#8b949e' }}>分{s.score?.toFixed(0)}</span>
                        )}
                        <span className="pick-tag" style={{ color: hasErr ? '#ff4444' : (dispType.includes('买') ? '#f0883e' : '#58a6ff') }}>
                          {dispType}
                        </span>
                        {s.status === 'preview' && (
                          <span className="pick-tag" style={{ color: '#a371f7', borderColor: '#a371f7', fontStyle: 'italic' }}>未确认</span>
                        )}
                        {s.ret_pct != null && (
                          <span className="pick-tag" title="信号日收盘→最新收盘"
                            style={{ color: s.ret_pct >= 0 ? '#f85149' : '#3fb950', fontWeight: 600 }}>
                            {s.ret_pct >= 0 ? '+' : ''}{s.ret_pct}%
                          </span>
                        )}
                      </div>
                    </div>
                    )
                  })
                })()
              )}
            </div>
          ) : (
            <div className="watchlist-items">
              {/* Date selector */}
              {laogaoDates.length > 0 && (
                <div className="picks-date-bar" style={{ maxHeight: 120, overflowY: 'auto', borderBottom: '1px solid var(--border)' }}>
                  {laogaoDates.map(d => (
                    <button key={d.date}
                      className={`range-btn ${d.date === selectedLaogaoDate ? 'active' : ''}`}
                      onClick={() => setSelectedLaogaoDate(d.date)}>
                      {d.date.slice(5)} <span className="wl-count">买{d.worth_cnt}</span>
                    </button>
                  ))}
                </div>
              )}
              <div style={{ padding: '6px 10px', fontSize: 11, color: '#888', borderBottom: '1px solid var(--border)' }}>
                底部连续确认≥4期买入 / 确认≥3期观察
              </div>
              {laogaoPicks.length === 0 ? (
                <div className="watchlist-empty">
                  {selectedLaogaoDate
                    ? `${selectedLaogaoDate} 无底部确认信号`
                    : '暂无底部确认信号\n每日15:22自动更新'}
                </div>
              ) : (
                laogaoPicks.map(p => (
                  <div key={p.symbol + p.status}
                    className={`watchlist-item ${currentStock?.symbol === p.symbol ? 'active' : ''}`}
                    onClick={() => { setHighlightSignal({ date: p.date, label: 'bottom_confirm' }); loadStock(p.symbol, p.name, p.date) }}>
                    <div style={{ flex: 1 }}>
                      <span className="wl-sym">{p.symbol}</span>
                      <span className="wl-name">{p.name}</span>
                    </div>
                    <div className="pc-tags" style={{ flexShrink: 0 }}>
                      <span className={`pick-tag ${p.status === 'worth' ? 'premium_b' : 'ultra_shrink'}`}>
                        {p.status === 'worth' ? '买' : '观'}
                      </span>
                      <span className="pick-tag">{p.streak}期</span>
                      <span className="pick-tag">{p.score.toFixed(0)}分</span>
                      <span className="pick-tag">{p.stage.slice(0, 1)}</span>
                      {p.ret_pct != null && (
                        <span className="pick-tag" title="信号日收盘→最新收盘"
                          style={{ color: p.ret_pct >= 0 ? '#f85149' : '#3fb950', fontWeight: 600 }}>
                          {p.ret_pct >= 0 ? '+' : ''}{p.ret_pct}%
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
