export const PAGE_SIZE = 2_000
export const MAX_PAGE_SIZE = 10_000
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '')
const marketTime = { timeZone: 'Asia/Kolkata' }

export const formatPrice = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
}).format
export const formatDate = new Intl.DateTimeFormat('en-IN', {
  ...marketTime, day: '2-digit', month: 'short', year: 'numeric',
}).format
export const formatCandleTime = new Intl.DateTimeFormat('en-IN', {
  ...marketTime, day: '2-digit', month: 'short', year: 'numeric',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
}).format

export async function fetchJson(url, signal) {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    const problem = await response.json().catch(() => null)
    const detail = typeof problem?.detail === 'string' ? problem.detail : 'Request failed'
    throw new Error(detail + ' (HTTP ' + response.status + ').')
  }
  return response.json()
}

export async function fetchCandles({ from, to }, before, limit, signal) {
  const url = new URL(API_BASE_URL + '/candles')
  url.searchParams.set('limit', String(limit))
  if (from) {
    const start = from + 'T00:00:00+05:30'
    if (before && Date.parse(before) <= Date.parse(start)) return { rows: [], count: 0 }
    url.searchParams.set('start', start)
  }
  if (before) {
    url.searchParams.set('end', before)
  } else if (to) {
    // Advance the calendar date so the selected To date is inclusive in IST.
    const nextDay = new Date(to + 'T00:00:00Z')
    nextDay.setUTCDate(nextDay.getUTCDate() + 1)
    url.searchParams.set('end', nextDay.toISOString().slice(0, 10) + 'T00:00:00+05:30')
  }

  const rows = await fetchJson(url, signal)
  if (!Array.isArray(rows)) throw new Error('Expected an array from /candles.')
  const byTime = new Map()
  let seriesId
  for (const row of rows) {
    const time = Math.floor(Date.parse(row?.timestamp) / 1000)
    if (!Number.isFinite(time) || !['open', 'high', 'low', 'close'].every(key => Number.isFinite(row[key]))) {
      throw new Error('The API returned a candle with an invalid timestamp or price.')
    }
    const id = row.instrument_token + '/' + row.timeframe
    seriesId ??= id
    if (id !== seriesId) throw new Error('Use a Parquet file with one instrument and timeframe.')
    byTime.set(time, { ...row, time })
  }
  return { rows: [...byTime.values()].sort((a, b) => a.time - b.time), count: rows.length }
}
