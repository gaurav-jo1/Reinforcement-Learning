import { useEffect, useRef, useState } from 'react'
import { CandlestickSeries, createChart, HistogramSeries, LineSeries, LineStyle, TickMarkType } from 'lightweight-charts'
import { formatCandleTime, formatPrice, MAX_PAGE_SIZE, PAGE_SIZE } from './candles.js'
import { useIndicatorValues } from './useIndicatorValues.js'

const zone = { timeZone: 'Asia/Kolkata' }
const axisFormatters = {
  [TickMarkType.Year]: new Intl.DateTimeFormat('en-IN', { ...zone, year: 'numeric' }),
  [TickMarkType.Month]: new Intl.DateTimeFormat('en-IN', { ...zone, month: 'short' }),
  [TickMarkType.DayOfMonth]: new Intl.DateTimeFormat('en-IN', { ...zone, day: 'numeric' }),
  [TickMarkType.Time]: new Intl.DateTimeFormat('en-IN', {
    ...zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }),
}
const OHLC = [['Open', 'open'], ['High', 'high'], ['Low', 'low'], ['Close', 'close']]
const EMPTY_POINTS = []
const OUTPUT_STYLES = {
  middle: LineStyle.Dashed, signal: LineStyle.Dashed,
  plus_di: LineStyle.Dashed, minus_di: LineStyle.Dotted,
}
const OUTPUT_LABELS = {
  middle: 'middle', upper: 'upper', lower: 'lower',
  adx: 'ADX', plus_di: '+DI', minus_di: '−DI', signal: 'signal', histogram: 'histogram',
}

export default function CandleChart({ candles, pages, selectedIndicators, canLoadOlder, onLoadOlder }) {
  const container = useRef(null)
  const chartState = useRef(null)
  const [hovered, setHovered] = useState(null)
  const active = hovered?.candle ?? candles.at(-1)
  const indicators = useIndicatorValues(pages, selectedIndicators)
  const paneCount = new Set(selectedIndicators.map(variant => variant.pane).filter(pane => pane !== 'price')).size

  useEffect(() => {
    const chart = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { color: '#151f29' },
        textColor: '#9caebd',
        fontFamily: 'system-ui, sans-serif',
        attributionLogo: true,
        panes: { separatorColor: '#334556', separatorHoverColor: '#547086' },
      },
      grid: { vertLines: { color: '#202e3b' }, horzLines: { color: '#263443' } },
      rightPriceScale: { borderColor: '#2c3b49' },
      timeScale: {
        borderColor: '#2c3b49',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        minBarSpacing: 0.001,
        enableConflation: true,
        tickMarkFormatter: (time, type) => axisFormatters[type]?.format(time * 1000) ?? null,
      },
      localization: {
        priceFormatter: formatPrice,
        timeFormatter: time => formatCandleTime(time * 1000),
      },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#57d9b2', downColor: '#fc8395',
      wickUpColor: '#57d9b2', wickDownColor: '#fc8395',
      borderVisible: false,
    })
    series.getPane().setStretchFactor(3)
    const plots = new Map()
    const onCrosshair = ({ seriesData }) => {
      const candle = seriesData.get(series)
      if (!candle) {
        setHovered(null)
        return
      }
      const values = new Map([...plots.values()].map(plot => [plot.column, seriesData.get(plot.api)?.value]))
      setHovered({ candle, values })
    }
    chart.subscribeCrosshairMove(onCrosshair)
    chartState.current = { chart, series, plots, panes: new Map(), firstTime: null }
    return () => {
      chart.unsubscribeCrosshairMove(onCrosshair)
      chart.remove()
      chartState.current = null
    }
  }, [])

  useEffect(() => {
    const state = chartState.current
    const scale = state.chart.timeScale()
    const previousRange = scale.getVisibleLogicalRange()
    const prepended = candles.findIndex(row => row.time === state.firstTime)
    state.series.setData(candles)
    state.firstTime = candles[0].time
    if (previousRange && prepended >= 0) {
      scale.setVisibleLogicalRange({
        from: previousRange.from + prepended,
        to: previousRange.to + prepended,
      })
    } else {
      showLatest()
    }
  }, [candles])

  useEffect(() => {
    const { chart, plots, panes } = chartState.current
    const activeIds = new Set(selectedIndicators.map(variant => variant.id))
    const activePanes = new Set(selectedIndicators.map(variant => variant.pane))

    for (const [key, plot] of plots) {
      if (!activeIds.has(plot.variantId)) {
        chart.removeSeries(plot.api)
        plots.delete(key)
      }
    }
    for (const [key, pane] of panes) {
      if (!activePanes.has(key)) {
        chart.removePane(pane.paneIndex())
        panes.delete(key)
      }
    }

    for (const variant of selectedIndicators) {
      const overlay = variant.pane === 'price'
      if (!overlay && !panes.has(variant.pane)) {
        panes.set(variant.pane, chart.addPane(true))
      }
      const paneIndex = overlay ? 0 : panes.get(variant.pane).paneIndex()

      for (const [index, output] of variant.chart_outputs.entries()) {
        const key = variant.id + ':' + output
        const column = variant.outputs[output]
        if (!plots.has(key)) {
          const histogram = output === 'histogram'
          const options = {
            color: histogram ? variant.color + '78' : variant.color,
            priceLineVisible: false,
            lastValueVisible: !overlay && index === 0,
            title: variant.label,
          }
          const api = histogram
            ? chart.addSeries(HistogramSeries, options, paneIndex)
            : chart.addSeries(LineSeries, {
                ...options, lineWidth: index === 0 ? 2 : 1,
                lineStyle: OUTPUT_STYLES[output] ?? LineStyle.Solid,
              }, paneIndex)
          plots.set(key, { api, variantId: variant.id, column })
        }
        const plot = plots.get(key)
        const points = indicators.data.get(column) ?? EMPTY_POINTS
        if (plot.points !== points) {
          plot.api.setData(points)
          plot.points = points
        }
      }
    }
  }, [selectedIndicators, indicators.data])

  useEffect(() => {
    if (!canLoadOlder) return
    const scale = chartState.current.chart.timeScale()
    let frame

    function checkHistory() {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const range = scale.getVisibleLogicalRange()
        if (!range) return

        const visibleBars = Math.ceil(range.to - range.from)
        const buffer = Math.min(1_000, Math.max(50, Math.ceil(visibleBars / 4)))
        if (range.from > buffer) return

        onLoadOlder(Math.min(MAX_PAGE_SIZE, Math.max(PAGE_SIZE, visibleBars)))
      })
    }

    scale.subscribeVisibleLogicalRangeChange(checkHistory)
    // After a page arrives, check whether the current zoom still needs more history.
    checkHistory()
    return () => {
      scale.unsubscribeVisibleLogicalRangeChange(checkHistory)
      cancelAnimationFrame(frame)
    }
  }, [canLoadOlder, onLoadOlder])

  function showLatest() {
    const scale = chartState.current.chart.timeScale()
    const lastIndex = candles.length - 1
    scale.setVisibleLogicalRange({ from: Math.max(-2, lastIndex - 119), to: lastIndex + 4 })
  }

  return (
    <>
      <div className="chart-details">
        <div className="chart-readout">
          <div className="bar-time">{formatCandleTime(active.time * 1000)} IST</div>
          <dl className="ohlc">
            {OHLC.map(([label, key]) => (
              <div key={key}><dt>{label}</dt><dd>{formatPrice(active[key])}</dd></div>
            ))}
          </dl>
        </div>
        <button type="button" onClick={showLatest}>Reset view</button>
      </div>
      {selectedIndicators.length > 0 && (
        <div className="indicator-readout">
          <ul aria-label="Indicator values at the active candle">
            {selectedIndicators.map(variant => (
              <li key={variant.id}>
                <span className="indicator-swatch" style={{ backgroundColor: variant.color }} aria-hidden="true" />
                <span>{variant.label}</span>
                {variant.chart_outputs.map(output => {
                  const column = variant.outputs[output]
                  const latest = indicators.data.get(column)?.at(-1)
                  const value = hovered ? hovered.values.get(column) : latest?.time === active.time ? latest.value : undefined
                  return <span className="indicator-point" key={output}>
                    {OUTPUT_LABELS[output] && <span className="indicator-point-label">{OUTPUT_LABELS[output]} </span>}
                    {Number.isFinite(value) ? formatPrice(value) : '–'}
                  </span>
                })}
              </li>
            ))}
          </ul>
          {indicators.loading && <p role="status">Loading indicator values…</p>}
          {indicators.error && <div className="indicator-notice" role="alert">
            <span>{indicators.error}</span>
            <button type="button" onClick={indicators.retry}>Retry indicator values</button>
          </div>}
          {!indicators.loading && !indicators.error && indicators.data.size > 0 && !indicators.hasValues &&
            <p>No indicator values for these candles.</p>}
        </div>
      )}
      <div className="chart" ref={container} style={{ '--indicator-pane-count': paneCount }}
        aria-label="Interactive NIFTY candlestick chart" />
    </>
  )
}
