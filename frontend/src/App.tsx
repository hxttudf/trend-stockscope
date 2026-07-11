import { useState, useEffect, useCallback, useRef } from 'react'
import Chart, { CrosshairInfo } from './components/Chart'
import {
  KlineData, Signal, PickRecord, WatchlistItem,
  searchStocks, getKline, getStockInfo, getPicks, getPickDates,
  getWatchlist, addToWatchlist, removeFromWatchlist
} from './utils/api'

const RANGES = [
  { label: '1月', days: 21 },
  { label: '3月', days: 63 },
  { label: '6月', days: 126 },
  { label: '1年', days: 252 },
  { label: '全部', days: 9999 },
]

const fmtVol = (v: number) => v >= 10000 ? (v / 10000).toFixed(2) + '万' : v.toFixed(0)

interface StockInfo {
  symbol: string
  name: string
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

  // ── Refs for crosshair direct-DOM updates (no react state re-render) ──
  const priceRef = useRef<HTMLSpanElement>(null)
  const changeRef = useRef<HTMLSpanElement>(null)
  const changePctRef = useRef<HTMLSpanElement>(null)
  const crosshairTimeRef = useRef<HTMLSpanElement>(null)
  const extraOpenRef = useRef<HTMLSpanElement>(null)
  const extraHighRef = useRef<HTMLSpanElement>(null)
  const extraLowRef = useRef<HTMLSpanElement>(null)
  const extraVolRef = useRef<HTMLSpanElement>(null)
  // Store last candle values to reset crosshair display
  const lastCandleRef = useRef<{ close: number; open: number; high: number; low: number; volume: number; prevClose: number; change: number; changePct: number } | null>(null)

  // Load watchlist and pick dates on mount
  useEffect(() => {
    getWatchlist().then(setWatchlist)
    getPickDates().then(dates => {
      setPickDates(dates.map(d => ({ date: d.date, total: d.total_picks })))
      if (dates.length > 0) {
        setSelectedPickDate(dates[0].date)
      }
    })
  }, [])

  // Load picks for selected date
  useEffect(() => {
    let cancelled = false
    if (selectedPickDate) {
      setPicks([])
      getPicks(selectedPickDate).then(data => {
        if (!cancelled) setPicks(data)
      })
    }
    return () => { cancelled = true }
  }, [selectedPickDate])

  // Load K-line for current stock
  const loadStock = useCallback(async (symbol: string, name: string) => {
    setCurrentStock({ symbol, name })
    setSearchQuery('')
    setShowSearch(false)
    const data = await getKline(symbol, qfq, 600)
    setKline(data)
    setSignals(data.signals)
  }, [qfq])

  // Update refs when kline data loads (update static display + cache last candle)
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
      change, changePct,
    }

    // Update displayed values
    if (priceRef.current) priceRef.current.textContent = last.close.toFixed(2)
    if (changeRef.current) {
      changeRef.current.textContent = (change >= 0 ? '+' : '') + change.toFixed(2)
      changeRef.current.className = `change ${change >= 0 ? 'up' : 'down'}`
    }
    if (changePctRef.current) {
      changePctRef.current.textContent = (changePct >= 0 ? '+' : '') + changePct.toFixed(2) + '%'
    }
    if (crosshairTimeRef.current) crosshairTimeRef.current.style.display = 'none'
    if (extraOpenRef.current) extraOpenRef.current.textContent = last.open.toFixed(2)
    if (extraHighRef.current) extraHighRef.current.textContent = last.high.toFixed(2)
    if (extraLowRef.current) extraLowRef.current.textContent = last.low.toFixed(2)
    if (extraVolRef.current) extraVolRef.current.textContent = fmtVol(last.volume)
  }, [kline])

  // Crosshair handler — directly updates DOM, no React state involved
  const handleCrosshairMove = useCallback((data: CrosshairInfo | null) => {
    const lc = lastCandleRef.current
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
      }
      if (crosshairTimeRef.current) {
        crosshairTimeRef.current.textContent = data.time
        crosshairTimeRef.current.style.display = ''
      }
      if (extraOpenRef.current) extraOpenRef.current.textContent = data.open.toFixed(2)
      if (extraHighRef.current) extraHighRef.current.textContent = data.high.toFixed(2)
      if (extraLowRef.current) extraLowRef.current.textContent = data.low.toFixed(2)
      if (extraVolRef.current) extraVolRef.current.textContent = fmtVol(data.volume)
    } else if (lc) {
      // Reset to last candle
      if (priceRef.current) priceRef.current.textContent = lc.close.toFixed(2)
      if (changeRef.current) {
        changeRef.current.textContent = (lc.change >= 0 ? '+' : '') + lc.change.toFixed(2)
        changeRef.current.className = `change ${lc.change >= 0 ? 'up' : 'down'}`
      }
      if (changePctRef.current) {
        changePctRef.current.textContent = (lc.changePct >= 0 ? '+' : '') + lc.changePct.toFixed(2) + '%'
      }
      if (crosshairTimeRef.current) crosshairTimeRef.current.style.display = 'none'
      if (extraOpenRef.current) extraOpenRef.current.textContent = lc.open.toFixed(2)
      if (extraHighRef.current) extraHighRef.current.textContent = lc.high.toFixed(2)
      if (extraLowRef.current) extraLowRef.current.textContent = lc.low.toFixed(2)
      if (extraVolRef.current) extraVolRef.current.textContent = fmtVol(lc.volume)
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

  // In watchlist?
  const isInWatchlist = currentStock ? watchlist.some(w => w.symbol === currentStock.symbol) : false

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

      {/* Stock Info Bar — 同花顺风格 (DOM via refs, no re-render on crosshair) */}
      {currentStock && (
        <div className="stock-info-bar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, overflow: 'hidden' }}>
            <span className="symbol">{currentStock.symbol}</span>
            <span className="name">{currentStock.name}</span>
            <span ref={priceRef} className="price">--</span>
            <span ref={changeRef} className="change"
              style={{ minWidth: 80 }}>
              {' '}
              <span ref={changePctRef} style={{ fontSize: 13 }}></span>
            </span>
            <span ref={crosshairTimeRef}
              style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap', display: 'none' }}>
            </span>
            {/* 额外数据：今开 最高 最低 成交量 */}
            <div style={{ display: 'flex', gap: 10, fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
              <span>开 <span ref={extraOpenRef}>--</span></span>
              <span>高 <span ref={extraHighRef}>--</span></span>
              <span>低 <span ref={extraLowRef}>--</span></span>
              <span>量 <span ref={extraVolRef}>--</span></span>
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
                watchlist.map(item => (
                  <div key={item.symbol}
                    className={`watchlist-item ${currentStock?.symbol === item.symbol ? 'active' : ''}`}
                    onClick={() => handleSelectWatchlist(item)}>
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
              {/* Date selector — 全部日期滚动 */}
              {pickDates.length > 0 && (
                <div className="picks-date-bar" style={{ maxHeight: 80, overflowY: 'auto' }}>
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
