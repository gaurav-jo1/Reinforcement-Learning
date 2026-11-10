import { API_BASE_URL, fetchJson } from './candles.js'

export const INDICATOR_COLORS = [
  '#f5c36d', '#7eb7ff', '#d5a1ff', '#5ee0da', '#ffa585', '#a9db79',
  '#f59dc8', '#a5b5f8', '#efdc80', '#7bd6ff', '#ff928d', '#c4cf9b',
]

const FAMILY_NAMES = {
  atr: 'ATR', ema: 'EMA', rsi: 'RSI', bollinger: 'Bollinger Bands',
  donchian: 'Donchian', dmi: 'DMI / ADX', macd: 'MACD',
}
const PRICE_FAMILIES = new Set(['ema', 'bollinger', 'donchian'])

export async function fetchIndicatorCatalog(signal) {
  const catalog = await fetchJson(API_BASE_URL + '/indicators/catalog', signal)
  if (!Array.isArray(catalog?.families)) throw new Error('Invalid indicator catalog.')
  return {
    ready: Boolean(catalog.dataset),
    presetCount: catalog.families.reduce((count, family) => count + family.variants.length, 0),
    families: catalog.families.map(family => ({
      ...family,
      variants: family.variants.map(variant => ({
        ...variant,
        label: (FAMILY_NAMES[family.id] || family.label) + ' ' + Object.values(variant.params).join(' / '),
        pane: PRICE_FAMILIES.has(family.id) ? 'price' : family.id,
      })),
    })),
  }
}

export async function fetchIndicatorPage(candles, variants, signal) {
  const first = candles[0]
  const last = candles.at(-1)
  const instrumentToken = String(first.instrument_token)
  const url = new URL(API_BASE_URL + '/indicators/values')
  for (const variant of variants) url.searchParams.append('variant', variant.id)
  url.searchParams.set('start', first.timestamp)
  url.searchParams.set('end', new Date((last.time + 1) * 1000).toISOString())
  url.searchParams.set('instrument_token', instrumentToken)
  url.searchParams.set('limit', String(candles.length))

  const rows = await fetchJson(url, signal)
  if (!Array.isArray(rows)) throw new Error('Invalid indicator values.')
  const valuesByTime = new Map()
  for (const row of rows) {
    const time = Math.floor(Date.parse(row.bar_open_at) / 1000)
    if (String(row.instrument_token) === instrumentToken && row.timeframe === first.timeframe) {
      valuesByTime.set(time, row)
    }
  }

  const columns = new Set(variants.flatMap(variant =>
    variant.chart_outputs.map(output => variant.outputs[output])
  ))
  return new Map([...columns].map(column => [column, candles.map(({ time }) => {
    const value = valuesByTime.get(time)?.[column]
    return Number.isFinite(value) ? { time, value } : { time }
  })]))
}
