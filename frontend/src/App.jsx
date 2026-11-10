import { useEffect, useRef, useState } from 'react'
import CandleChart from './CandleChart.jsx'
import IndicatorPicker from './IndicatorPicker.jsx'
import { fetchCandles, formatCandleTime, formatDate, formatPrice, PAGE_SIZE } from './candles.js'

const allDates = { from: '', to: '' }
const number = new Intl.NumberFormat('en-IN')

export default function App() {
  const [draft, setDraft] = useState(allDates)
  const [data, setData] = useState({ rows: [], pages: [], range: allDates, hasOlder: false, revision: 0 })
  const [loading, setLoading] = useState('range')
  const [error, setError] = useState('')
  const [selectedIndicators, setSelectedIndicators] = useState([])
  const request = useRef(null)
  const { rows, pages, range, hasOlder, revision } = data
  const first = rows[0]
  const latest = rows.at(-1)

  useEffect(() => {
    load(allDates)
    return () => request.current?.abort()
  }, [])

  async function load(nextRange, olderThan, limit = PAGE_SIZE) {
    if (olderThan && request.current) return
    request.current?.abort()
    setError('')
    if (nextRange.from && nextRange.to && nextRange.from > nextRange.to) {
      setLoading(null)
      setError('The From date must be on or before the To date.')
      return
    }

    const controller = new AbortController()
    request.current = controller
    setLoading(olderThan ? 'older' : 'range')
    try {
      const page = await fetchCandles(nextRange, olderThan, limit, controller.signal)
      if (controller.signal.aborted) return
      if (olderThan && page.rows.some(row =>
        row.instrument_token !== first.instrument_token || row.timeframe !== first.timeframe
      )) {
        throw new Error('The source series changed. Select Latest to reload it.')
      }
      setData(previous => ({
        rows: olderThan ? [...page.rows, ...previous.rows] : page.rows,
        pages: olderThan
          ? (page.rows.length ? [page.rows, ...previous.pages] : previous.pages)
          : [page.rows],
        range: nextRange,
        hasOlder: page.count === limit,
        revision: previous.revision + (olderThan ? 0 : 1),
      }))
    } catch (problem) {
      if (!controller.signal.aborted) {
        setError(problem instanceof TypeError
          ? 'Could not reach the API. Check that FastAPI is running and allows this browser origin.'
          : problem.message)
      }
    } finally {
      if (request.current === controller) {
        request.current = null
        if (!controller.signal.aborted) setLoading(null)
      }
    }
  }

  function loadLatest() {
    setDraft(allDates)
    load(allDates)
  }

  return (
    <main className="workspace">
      <section className="panel" aria-labelledby="chart-title">
        <div className="panel-head">
          <div>
            <h1 id="chart-title">Price chart</h1>
            <p>{first
              ? <>{formatDate(first.time * 1000)} – {formatDate(latest.time * 1000)}
                  {' · '}{latest.timeframe === '5minute' ? '5 min' : latest.timeframe} · IST (UTC+05:30)</>
              : 'Select dates or load the latest candles.'}</p>
          </div>
          <form className="filters" onSubmit={event => {
            event.preventDefault()
            load(draft)
          }}>
            <label>From <input type="date" value={draft.from}
              onChange={event => setDraft({ ...draft, from: event.target.value })} /></label>
            <label>To <input type="date" value={draft.to}
              onChange={event => setDraft({ ...draft, to: event.target.value })} /></label>
            <button className="primary" type="submit" disabled={loading === 'range'}>Apply</button>
            <button type="button" onClick={loadLatest} disabled={loading === 'range'}>Latest</button>
          </form>
        </div>

        {error && <div className="error" role="alert">{error}</div>}
        <IndicatorPicker selected={selectedIndicators} onChange={setSelectedIndicators} />
        <div className="chart-area" aria-busy={Boolean(loading)}>
          {rows.length > 0
            ? <CandleChart
                key={revision}
                candles={rows}
                pages={pages}
                selectedIndicators={selectedIndicators}
                canLoadOlder={hasOlder && !loading && !error}
                onLoadOlder={limit => load(range, first.timestamp, limit)}
              />
            : <div className="placeholder">{loading ? 'Loading candles…' : error ? 'Chart unavailable' : 'No candles in this range.'}</div>}
        </div>

        <div className="panel-foot">
          <span role="status">{loading
            ? loading === 'older' ? 'Loading older candles…' : 'Loading candles…'
            : number.format(rows.length) + ' candles loaded'
              + (hasOlder ? ' · Zoom out or pan left for more' : '')}</span>
          {hasOlder && <button type="button" onClick={() => load(range, first.timestamp)}
            disabled={Boolean(loading)}>← Load older candles</button>}
        </div>
      </section>

      {latest && (
        <section className="panel" aria-labelledby="table-title">
          <div className="panel-head">
            <div><h2 id="table-title">Recent candles</h2><p>Instrument {latest.instrument_token} · OHLC in index points</p></div>
          </div>
          <div className="table-scroll">
            <table>
              <thead><tr><th scope="col">Bar open (IST)</th><th scope="col">Open</th><th scope="col">High</th><th scope="col">Low</th><th scope="col">Close</th></tr></thead>
              <tbody>{rows.slice(-5).reverse().map(row => (
                <tr key={row.time}>
                  <th scope="row">{formatCandleTime(row.time * 1000)}</th>
                  <td>{formatPrice(row.open)}</td><td>{formatPrice(row.high)}</td>
                  <td>{formatPrice(row.low)}</td><td>{formatPrice(row.close)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  )
}
