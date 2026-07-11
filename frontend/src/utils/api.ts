const API_BASE = '/api'

export interface StockInfo {
  symbol: string
  name: string
}

export interface KlinePoint {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  turnover: number
}

export interface Signal {
  date: string
  type: string
  name: string
}

export interface KlineData {
  symbol: string
  kline: KlinePoint[]
  signals: Signal[]
}

export interface PickRecord {
  id: number
  date: string
  strategy_id: string
  symbol: string
  name: string
  close_qfq: number
  ma20: number
  ma60: number
  dist_ma20: number
  vol_ratio: number
  pct_20d: number
  buy_price: number
}

export interface PickDateSummary {
  date: string
  total_picks: number
  strategies: string
}

export interface WatchlistItem {
  id: number
  symbol: string
  name: string
  note: string
  added_at: string
}

export async function searchStocks(q: string): Promise<StockInfo[]> {
  const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(q)}`)
  return res.json()
}

export async function getKline(symbol: string, qfq = true, limit = 500): Promise<KlineData> {
  const res = await fetch(`${API_BASE}/kline/${symbol}?qfq=${qfq ? '1' : '0'}&limit=${limit}`)
  return res.json()
}

export async function getStockInfo(symbol: string): Promise<StockInfo> {
  const res = await fetch(`${API_BASE}/stock/${symbol}`)
  return res.json()
}

export async function getPicks(date?: string, strategy?: string): Promise<PickRecord[]> {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  if (strategy) params.set('strategy', strategy)
  const res = await fetch(`${API_BASE}/picks?${params}`)
  return res.json()
}

export async function getPickDates(strategy?: string): Promise<PickDateSummary[]> {
  const params = new URLSearchParams()
  if (strategy) params.set('strategy', strategy)
  const qs = params.toString()
  const res = await fetch(`${API_BASE}/picks/dates${qs ? '?' + qs : ''}`)
  return res.json()
}

export async function getWatchlist(): Promise<WatchlistItem[]> {
  const res = await fetch(`${API_BASE}/watchlist`)
  return res.json()
}

export async function addToWatchlist(symbol: string, name: string): Promise<WatchlistItem> {
  const res = await fetch(`${API_BASE}/watchlist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, name }),
  })
  return res.json()
}

export async function removeFromWatchlist(symbol: string): Promise<void> {
  await fetch(`${API_BASE}/watchlist/${symbol}`, { method: 'DELETE' })
}

export async function updateWatchlistNote(symbol: string, note: string): Promise<void> {
  await fetch(`${API_BASE}/watchlist/${symbol}/note`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  })
}
