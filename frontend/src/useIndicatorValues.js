import { useEffect, useRef, useState } from 'react'
import { fetchIndicatorPage } from './indicators.js'

const EMPTY = { data: new Map(), hasValues: false, loading: false, error: '' }

export function useIndicatorValues(pages, selected) {
  const cache = useRef(new Map())
  const [result, setResult] = useState(EMPTY)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!selected.length) {
      cache.current.clear()
      setResult(EMPTY)
      return
    }

    const controller = new AbortController()
    const { signal } = controller
    const columnNames = new Set(selected.flatMap(variant =>
      variant.chart_outputs.map(output => variant.outputs[output])
    ))

    // Each candle page has a stable identity until the date range is replaced.
    for (const columns of cache.current.values()) {
      for (const name of columns.keys()) {
        if (!columnNames.has(name)) columns.delete(name)
      }
    }

    function publish(loading, error = '') {
      const data = new Map([...columnNames].map(name => [name, []]))
      for (const page of pages) {
        for (const [name, points] of cache.current.get(page) ?? []) {
          data.get(name)?.push(...points)
        }
      }
      const hasValues = [...data.values()].some(points => points.some(point => point.value !== undefined))
      setResult({ data, hasValues, loading, error })
    }

    async function load() {
      try {
        // Show the newest matching values first, then backfill older pages.
        for (let index = pages.length - 1; index >= 0; index--) {
          const page = pages[index]
          const columns = cache.current.get(page) ?? new Map()
          const missing = selected.filter(variant =>
            variant.chart_outputs.some(output => !columns.has(variant.outputs[output]))
          )
          if (!missing.length) continue

          const values = await fetchIndicatorPage(page, missing, signal)
          if (signal.aborted) return
          for (const [name, points] of values) columns.set(name, points)
          cache.current.set(page, columns)
          if (index === pages.length - 1 && pages.length > 1) publish(true)
        }
        publish(false)
      } catch (problem) {
        if (!signal.aborted) {
          publish(false, problem instanceof TypeError ? 'Could not reach the indicator API.' : problem.message)
        }
      }
    }

    setResult(previous => ({ ...previous, loading: true, error: '' }))
    // Batch rapid candle-page changes before requesting the missing indicator columns.
    const timer = setTimeout(load, 80)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [pages, selected, attempt])

  return { ...result, retry: () => setAttempt(value => value + 1) }
}
