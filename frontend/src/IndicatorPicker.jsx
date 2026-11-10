import { useEffect, useState } from 'react'
import { fetchIndicatorCatalog, INDICATOR_COLORS } from './indicators.js'

export default function IndicatorPicker({ selected, onChange }) {
  const [catalog, setCatalog] = useState(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const full = selected.length === INDICATOR_COLORS.length
  const selectedIds = new Set(selected.map(variant => variant.id))

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    fetchIndicatorCatalog(controller.signal).then(result => {
      if (!controller.signal.aborted) setCatalog(result)
    }).catch(problem => {
      if (!controller.signal.aborted) {
        setError(problem instanceof TypeError ? 'Could not reach the indicator API.' : problem.message)
      }
    })
    return () => controller.abort()
  }, [attempt])

  function toggle(variant, checked) {
    if (!checked) {
      onChange(selected.filter(item => item.id !== variant.id))
      return
    }
    if (full || selectedIds.has(variant.id)) return
    const color = INDICATOR_COLORS.find(candidate => !selected.some(item => item.color === candidate))
    onChange([...selected, { ...variant, color }])
  }

  return (
    <section className="indicator-picker" aria-labelledby="indicator-title">
      <div className="indicator-toolbar">
        <div>
          <h3 id="indicator-title">Indicators</h3>
          {catalog?.ready && <p>{catalog.presetCount} presets across {catalog.families.length} indicator families.</p>}
        </div>
        {catalog?.ready && (
          <details className="indicator-dropdown">
            <summary>Choose presets ({selected.length}/{INDICATOR_COLORS.length})</summary>
            <div className="indicator-options">
              {catalog.families.map(family => (
                <fieldset key={family.id}>
                  <legend>{family.label}</legend>
                  {family.variants.map(variant => {
                    const checked = selectedIds.has(variant.id)
                    return (
                      <label key={variant.id}>
                        <input type="checkbox" checked={checked} disabled={full && !checked}
                          onChange={event => toggle(variant, event.target.checked)} />
                        {variant.label}
                      </label>
                    )
                  })}
                </fieldset>
              ))}
            </div>
          </details>
        )}
      </div>
      {error && <div className="indicator-notice" role="status">
        <span>{error}</span>
        <button type="button" onClick={() => { setCatalog(null); setAttempt(value => value + 1) }}>Retry</button>
      </div>}
      {!error && !catalog && <p className="indicator-notice">Loading indicator presets…</p>}
      {!error && catalog && !catalog.ready && <div className="indicator-notice">
        <span>Build the indicator dataset with <code>python indicators.py</code>, then reload the presets.</span>
        <button type="button" onClick={() => { setCatalog(null); setAttempt(value => value + 1) }}>Reload presets</button>
      </div>}
      {selected.length > 0 && <ul className="indicator-chips" aria-label="Selected indicators">
        {selected.map(variant => (
          <li key={variant.id}>
            <button type="button" aria-label={'Remove ' + variant.label}
              onClick={() => toggle(variant, false)}>
              <span className="indicator-swatch" style={{ backgroundColor: variant.color }} aria-hidden="true" />
              {variant.label}
              <span className="indicator-remove" aria-hidden="true">×</span>
            </button>
          </li>
        ))}
      </ul>}
    </section>
  )
}
