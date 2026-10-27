import { useEffect, useState } from 'react'
import { BiasBars, ForecastChart, HistogramChart, HorizonChart, MaeBars, day, mw } from './charts'

const SOURCES = {
  wind: [['windfor', 'WINDFOR'], ['dgws_wind', 'DGWS (on+offshore)']],
  solar: [['dgws_solar', 'DGWS solar']],
}
const COLORS = ['var(--series-1)', 'var(--series-2)']

function Tile({ label, value, sub }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

function presets(meta) {
  const end = meta.dataEnd
  const minus = (d, n) => new Date(Date.parse(d) - n * 864e5).toISOString().slice(0, 10)
  return [
    ['January 2026', '2026-01-01', '2026-01-31'],
    ['Last 30 days', minus(end, 29), end],
    ['Last 12 months', minus(end, 364) < meta.dataStart ? meta.dataStart : minus(end, 364), end],
  ]
}

const fetchJson = (url, signal) => fetch(url, { signal }).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`API ${r.status}`))))

export default function Accuracy({ fuel, meta }) {
  const sources = SOURCES[fuel]
  const [source, setSource] = useState(sources[0][0])
  const [mode, setMode] = useState('gate')
  const [h, setH] = useState(4)
  const [[start, end], setRange] = useState(presets(meta)[1].slice(1))
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    const ctl = new AbortController()
    // London midnight-to-midnight; UTC strings are close enough for range filtering (±1h in BST).
    const q = (src) => `/api/accuracy?${new URLSearchParams({ source: src, mode, h, start: `${start}T00:00:00Z`, end: `${end}T23:30:00Z` })}`
    setData(null)
    Promise.all(sources.map(([s]) => fetchJson(q(s), ctl.signal)))
      .then((all) => { setData(Object.fromEntries(sources.map(([s], i) => [s, all[i]]))); setErr(null) })
      .catch((e) => e.name !== 'AbortError' && setErr(e.message))
    return () => ctl.abort()
  }, [mode, h, start, end, fuel])

  const d = data?.[source]
  const s = d?.stats
  const unit = fuel === 'solar' ? 'half-hours (daylight only)' : 'hours'

  return (
    <>
      <section aria-labelledby="fc">
        <h2 id="fc">{fuel === 'solar' ? 'Solar' : 'Wind'} forecast vs actual (GW)</h2>
        <div className="controls">
          {sources.length > 1 && (
            <label>
              Forecast source
              <span className="seg" role="group" aria-label="Forecast source">
                {sources.map(([k, name]) => <button key={k} aria-pressed={source === k} onClick={() => setSource(k)}>{name}</button>)}
              </span>
            </label>
          )}
          <label>
            Forecast selection
            <span className="seg" role="group" aria-label="Forecast selection">
              <button aria-pressed={mode === 'gate'} onClick={() => setMode('gate')}>Day-ahead gate (before 11:00 D-1)</button>
              <button aria-pressed={mode === 'horizon'} onClick={() => setMode('horizon')}>Fixed lead time</button>
            </span>
          </label>
          {mode === 'horizon' && (
            <label>
              Lead time: at least {h}h
              <input type="range" min={0} max={48} value={h} onChange={(e) => setH(+e.target.value)} />
            </label>
          )}
          <label>
            Range
            <span className="seg" role="group" aria-label="Range preset">
              {presets(meta).map(([name, a, b]) => (
                <button key={name} aria-pressed={start === a && end === b} onClick={() => setRange([a, b])}>{name}</button>
              ))}
            </span>
          </label>
          <label>
            From
            <input type="date" min={meta.dataStart} max={end} value={start} onChange={(e) => e.target.value && setRange([e.target.value, end])} />
          </label>
          <label>
            To
            <input type="date" min={start} max={meta.dataEnd} value={end} onChange={(e) => e.target.value && setRange([start, e.target.value])} />
          </label>
        </div>
        <p className="note">
          {mode === 'gate'
            ? 'Each delivery day uses the latest forecast published strictly before 11:00 UK time on the day before: what a day-ahead trader actually had.'
            : `Each period uses the latest forecast published at least ${h}h before it.`}
          {' '}Band = forecast + P10…P90 of errors from the 30 days ending two days before delivery (out-of-sample).
          {fuel === 'solar' && ' Night periods (actual and forecast both < 50 MW) are excluded.'}
        </p>
        {d?.selection === 'dayahead' && (
          <p className="note warn" role="note">
            ⚠ DGWS publishes its day-ahead forecast at about 16:45 UTC on D-1, after the 11:00 gate. Shown here is that run (latest
            before delivery-day midnight): useful for accuracy comparison, but not available for the day-ahead auction.
          </p>
        )}
        {err && <p className="status error">Could not load data: {err}</p>}
        {!d && !err && <p className="status">Loading…</p>}
        {d && (d.points.length ? <ForecastChart points={d.points} /> : <p className="status">No forecast data for this selection.</p>)}
      </section>

      {s && (
        <section aria-labelledby="acc">
          <h2 id="acc">Forecast accuracy</h2>
          <div className="tiles">
            <Tile label="MAE" value={mw(s.core.mae)} sub={`${s.core.nmaePct}% of mean actual`} />
            <Tile label="RMSE" value={mw(s.core.rmse)} />
            <Tile label="Bias (mean error)" value={mw(s.core.bias)} sub={s.core.bias < 0 ? 'forecast too high on average' : 'forecast too low on average'} />
            <Tile label="P10 / P90 error (MW)" value={`${s.percentiles.p10.toLocaleString()} / ${s.percentiles.p90.toLocaleString()}`} sub="80% of periods in range" />
            <Tile label="Worst period" value={mw(s.core.maxAbs)} sub={`${s.core.n.toLocaleString()} ${unit}`} />
          </div>
          {sources.length > 1 && (
            <table className="compare">
              <caption>Same range and selection. WINDFOR is scored against FUELHH (transmission-metered wind); DGWS against AGWS, which also includes embedded wind (~1.5 GW more on average), so the two rows measure different scopes</caption>
              <thead><tr><th>Source</th><th>MAE</th><th>RMSE</th><th>Bias</th><th>n</th></tr></thead>
              <tbody>
                {sources.map(([k, name]) => {
                  const c = data[k].stats?.core
                  return <tr key={k}><td>{name}</td><td>{mw(c?.mae)}</td><td>{mw(c?.rmse)}</td><td>{mw(c?.bias)}</td><td>{c?.n ?? '–'}</td></tr>
                })}
              </tbody>
            </table>
          )}
          <div className="grid2">
            <div className="card">
              <h3>MAE vs lead time, GW</h3>
              <HorizonChart curves={sources.map(([k, name], i) => ({ name, color: COLORS[i], data: data[k].horizonCurve }))} h={mode === 'horizon' ? h : null} />
            </div>
            <div className="card">
              <h3>Error distribution, GW ({unit} per {s.histBin} MW bin)</h3>
              <HistogramChart histogram={s.histogram} binSize={s.histBin} />
            </div>
            <div className="card">
              <h3>MAE by hour of day, GW (UK time)</h3>
              <MaeBars data={s.byHour} x="hour" label={(r) => `${String(r.hour).padStart(2, '0')}:00`} />
            </div>
            <div className="card">
              <h3>Daily bias, GW (red = shortfall)</h3>
              <BiasBars data={s.byDay} />
            </div>
          </div>
          <details>
            <summary>Daily error table</summary>
            <table>
              <thead><tr><th>Day</th><th>MAE (MW)</th><th>Bias (MW)</th></tr></thead>
              <tbody>
                {s.byDay.map((r) => (
                  <tr key={r.day}><td>{day(r.day)}</td><td>{r.mae.toLocaleString()}</td><td>{r.bias.toLocaleString()}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
      )}
    </>
  )
}
