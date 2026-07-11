import { useState, useEffect, useCallback, useRef } from 'react'
import Chart, { CrosshairInfo } from './components/Chart'
import {
  KlineData, KlinePoint, Signal, PickRecord, WatchlistItem,
  searchStocks, getKline, getStockInfo, getPicks, getPickDates,
  getWatchlist, addToWatchlist, removeFromWatchlist, reorderWatchlist
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

export default function App() {
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<StockInfo[]>([])
  const [showSearch, setShowSearch] = useState(false)

  const [currentStock, setCurrentStock] = useState<StockInfo | null>(null)
  const [kline, setKline] = useState<KlineData | null>(null)
  const [signals, setSignals] = useState<Signal[]>([])
  const [range, setRange] = useState(RANGES[2]) // default 6m
  const [qfq, setQfq] = useState(true)

  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [picks, setPicks] = useState<PickRecord[]>([])
  const [pickDates, setPickDates] = useState<{ date: string; total: number }[]>([])
  const [selectedPickDate, setSelectedPickDate] = useState('')
  const [sidebarTab, setSidebarTab] = useState<'watchlist' | 'picks'>('watchlist')
  const [strategyFilter, setStrategyFilter] = useState('')  // '' = all
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)
  const [measureMode, setMeasureMode] = useState(false)
  const [benchmarkIdx, setBenchmarkIdx] = useState<number | null>(null)

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

  // Load K-line for current stock
  const loadStock = useCallback(async (symbol: string, name: string) => {
    setCurrentStock({ symbol, name })
    setSearchQuery('')
    setShowSearch(false)
    setBenchmarkIdx(null)  // 切股票清基准
    const data = await getKline(symbol, qfq, 600)
    setKline(data)
    setSignals(data.signals)
  }, [qfq])

  // Reload kline when qfq changes and a stock is selected
  useEffect(() => {
    if (currentStock) {
      const s = currentStock
      getKline(s.symbol, qfq, 600).then(data => {
        setKline(data)
        setSignals(data.signals)
      })
    }
  }, [qfq])

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
          const amp = ((Math.max(b.high, c.high) - Math.min(b.low, c.low)) / b.close) * 100
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
          const amp = ((Math.max(b.high, l.high) - Math.min(b.low, l.low)) / b.close) * 100
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
    loadStock(item.symbol, item.name)
  }

  // Select from picks
  const handleSelectPick = (pick: PickRecord) => {
    loadStock(pick.symbol, pick.name)
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
                  onMouseDown={() => loadStock(s.symbol, s.name)}>
                  <span className="sym">{s.symbol}</span>
                  <span className="name">{s.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="toolbar">
          <div className="qfq-toggle">
            <input type="checkbox" id="qfq" checked={qfq}
              onChange={e => setQfq(e.target.checked)} />
            <label htmlFor="qfq">前复权</label>
          </div>
          <button className={`toolbar-btn ${measureMode ? 'active' : ''}`}
            onClick={() => { setMeasureMode(m => !m); setBenchmarkIdx(null) }}
            style={{ fontSize: 11, padding: '2px 6px' }}>
            M
          </button>

          <div className="range-group">
            {RANGES.map(r => (
              <button key={r.label}
                className={`range-btn ${range.label === r.label ? 'active' : ''}`}
                onClick={() => setRange(r)}>
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, overflow: 'hidden' }}>
            <span className="symbol">{currentStock.symbol}</span>
            <span className="name">{currentStock.name}</span>
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
            {/* 信号图例 */}
            {signals.length > 0 && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0, marginLeft: 8 }}>
                {Array.from(new Set(signals.map(s => s.type))).map(t => (
                  <span key={t} className={`signal-badge ${t}`} title={
                    t === 'premium_b' ? '极品B策略' : t === 'premium_a' ? '极品A策略' : t === 'ultra_shrink' ? '超缩量策略' : '原版策略'
                  }>
                    {t === 'premium_b' ? '■极品B' : t === 'premium_a' ? '▲极品A' : t === 'ultra_shrink' ? '▼超缩量' : '◆原版'}
                  </span>
                ))}
              </div>
            )}
          </div>
          {/* 第二行：MA均线跟随光标 */}
          <div style={{ display: 'flex', gap: 10, fontSize: 11, color: 'var(--text-secondary)', padding: '2px 12px 4px', whiteSpace: 'nowrap' }}>
            <span>MA5 <span ref={ma5Ref} style={{ color: '#f0d43a' }}>--</span></span>
            <span>MA10 <span ref={ma10Ref} style={{ color: '#f7823b' }}>--</span></span>
            <span>MA20 <span ref={ma20Ref} style={{ color: '#58a6ff' }}>--</span></span>
            <span>MA60 <span ref={ma60Ref} style={{ color: '#bc8cff' }}>--</span></span>
          </div>
          {/* 第三行：区间测量 */}
          {measureMode && (
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
            {currentStock && kline ? (
              <Chart
                kline={kline.kline}
                signals={kline.signals}
                symbol={currentStock.symbol}
                range={range.days}
                onCrosshairMove={handleCrosshairMove}
                onChartClick={handleChartClick}
                benchmarkTime={benchmarkIdx !== null ? kline.kline[benchmarkIdx]?.time : null}
              />
            ) : (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                color: 'var(--text-muted)',
                fontSize: 14,
                flexDirection: 'column',
                gap: 8,
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
            📌 自选股 <span className="wl-count">{watchlist.length}</span>
          </button>
          <button className={`wl-tab ${sidebarTab === 'picks' ? 'active' : ''}`}
            onClick={() => setSidebarTab('picks')}>
              📋 选股 <span className="wl-count">{pickDates.length}天</span>
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
          ) : (
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
                          {st.trim() === 'premium_b' ? 'B' : st.trim() === 'premium_a' ? 'A' : st.trim() === 'ultra_shrink' ? '缩' : '原'}
                        </span>
                      ))}
                      <span className="pick-tag">{p.dist_ma20?.toFixed(1)}%</span>
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
