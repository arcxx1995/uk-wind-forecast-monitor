import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BiasBars, ForecastChart, HistogramChart, HorizonChart, MaeBars, day, mw } from './charts'
import './styles.css'

const MIN_DAY = '2026-01-01'
const MAX_DAY = '2026-01-31'

function Tile({ label, value, sub }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

function App() {
  const [mode, setMode] = useState('gate')
  const [h, setH] = useState(4)
  const [start, setStart] = useState(MIN_DAY)
  const [end, setEnd] = useState(MAX_DAY)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    const ctl = new AbortController()
    const q = new URLSearchParams({ mode, h, start: `${start}T00:00:00Z`, end: `${end}T23:00:00Z` })
    fetch(`/api/analysis?${q}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`API ${r.status}`))))
      .then((d) => { setData(d); setErr(null) })
      .catch((e) => e.name !== 'AbortError' && setErr(e.message))
    return () => ctl.abort()
  }, [mode, h, start, end])

  const s = data?.stats
  const b = data?.band

  return (
    <main>
      <h1>UK Wind Forecast Monitor</h1>
      <p className="lede">
        National wind generation: Elexon BMRS actuals (FUELHH) vs forecasts (WINDFOR), delivery days in January 2026.
        Error = actual − forecast. A negative error means wind fell short and the gap must be bought.
      </p>

      <section aria-labelledby="decision">
        <h2 id="decision">Forecast available at decision time (GW)</h2>
        <div className="controls">
          <label>
            Forecast selection
            <span className="seg" role="group" aria-label="Forecast selection">
              <button aria-pressed={mode === 'gate'} onClick={() => setMode('gate')}>Day-ahead gate (11:00 D-1)</button>
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
            From
            <input type="date" min={MIN_DAY} max={end} value={start} onChange={(e) => e.target.value && setStart(e.target.value)} />
          </label>
          <label>
            To
            <input type="date" min={start} max={MAX_DAY} value={end} onChange={(e) => e.target.value && setEnd(e.target.value)} />
          </label>
        </div>
        <p className="note">
          {mode === 'gate'
            ? 'Each delivery day uses the latest forecast published before 11:00 UK time on the day before: what a day-ahead trader actually had.'
            : `Each hour uses the latest forecast published at least ${h}h before it.`}
          {b && ` Shaded band = forecast + January P10…P90 error (${mw(b.p10)} … ${mw(b.p90)}); dashed line = conservative P10 wind.`}
        </p>
        {err && <p className="status error">Could not load data: {err}</p>}
        {!data && !err && <p className="status">Loading…</p>}
        {data && (data.points.length ? <ForecastChart points={data.points} /> : <p className="status">No forecast data for this selection.</p>)}
      </section>

      {s && (
        <section aria-labelledby="accuracy">
          <h2 id="accuracy">Forecast accuracy</h2>
          <div className="tiles">
            <Tile label="MAE" value={mw(s.core.mae)} sub={`${s.core.nmaePct}% of mean actual`} />
            <Tile label="RMSE" value={mw(s.core.rmse)} />
            <Tile label="Bias (mean error)" value={mw(s.core.bias)} sub={s.core.bias < 0 ? 'forecast too high on average' : 'forecast too low on average'} />
            <Tile label="P10 / P90 error (MW)" value={`${s.percentiles.p10.toLocaleString()} / ${s.percentiles.p90.toLocaleString()}`} sub="80% of hours fall within this range" />
            <Tile label="Worst hour" value={mw(s.core.maxAbs)} sub={`${s.core.n} hours compared`} />
          </div>
          <div className="grid2">
            <div className="card">
              <h3>Error vs lead time, GW (all of January)</h3>
              <HorizonChart curve={data.horizonCurve} h={mode === 'horizon' ? h : null} />
            </div>
            <div className="card">
              <h3>Error distribution, GW (hours per 500 MW bin)</h3>
              <HistogramChart histogram={s.histogram} />
            </div>
            <div className="card">
              <h3>MAE by hour of day, GW (UTC)</h3>
              <MaeBars data={s.byHour} x="hour" label={(d) => `${String(d.hour).padStart(2, '0')}:00`} />
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
                {s.byDay.map((d) => (
                  <tr key={d.day}><td>{day(d.day)}</td><td>{d.mae.toLocaleString()}</td><td>{d.bias.toLocaleString()}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
      )}
    </main>
  )
}

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>)
