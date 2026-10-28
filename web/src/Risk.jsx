import { useEffect, useState } from 'react'
import { CumulativeCost, ErrorSpreadScatter, FrontierBars, ReliabilityChart, gbp, mw } from './charts'

const MODEL_NAMES = { wind_only: 'Wind-only', combined: 'Demand + wind', conditional: 'Conditional (wind level × peak)' }
const FLAG_NAMES = { revision: 'Big forecast revision vs a day earlier', ramp: 'Large hourly wind ramp', margin: 'Tight NESO margin' }
const STRATEGIES = [['p50', 'Always P50'], ['recommended', 'Monthly policy'], ['p90', 'Always P90']]

function Tile({ label, value, sub }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

const minus = (d, n) => new Date(Date.parse(d) - n * 864e5).toISOString().slice(0, 10)

export default function Risk({ meta }) {
  const presets = [
    ['Last 12 months', minus(meta.dataEnd, 364) < meta.dataStart ? meta.dataStart : minus(meta.dataEnd, 364), meta.dataEnd],
    ['Last 90 days', minus(meta.dataEnd, 89), meta.dataEnd],
    ['January 2026', '2026-01-01', '2026-01-31'],
  ]
  const [[start, end], setRange] = useState(presets[0].slice(1))
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    const ctl = new AbortController()
    setData(null)
    fetch(`/api/risk?start=${start}&end=${end}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`API ${r.status}`))))
      .then((d) => { setData(d); setErr(null) })
      .catch((e) => e.name !== 'AbortError' && setErr(e.message))
    return () => ctl.abort()
  }, [start, end])

  const f = data?.frontier
  return (
    <section aria-labelledby="risk">
      <h2 id="risk">Risk & cost of the purchase decision</h2>
      <div className="controls">
        <label>
          Backtest range
          <span className="seg" role="group" aria-label="Range preset">
            {presets.map(([name, a, b]) => (
              <button key={name} aria-pressed={start === a && end === b} onClick={() => setRange([a, b])}>{name}</button>
            ))}
          </span>
        </label>
        <label>From<input type="date" min={meta.dataStart} max={end} value={start} onChange={(e) => e.target.value && setRange([e.target.value, end])} /></label>
        <label>To<input type="date" min={start} max={meta.dataEnd} value={end} onChange={(e) => e.target.value && setRange([start, e.target.value])} /></label>
      </div>
      <p className="note">
        Excess £ = cost of each day's purchase vs perfect hindsight, at a {(meta.share * 100).toFixed(1)}% portfolio share: shortfalls are
        settled at the GB single imbalance price instead of the day-ahead price (APX market index used as proxy), surpluses are sold
        back at it. Every day uses only data known at its own gate. Risk aversion λ = {meta.riskAversion} (admin setting).
      </p>
      {err && <p className="status error">Could not load data: {err}</p>}
      {!data && !err && <p className="status">Loading…</p>}
      {data && (
        <>
          <h3>Realised excess cost by strategy</h3>
          <p className="note">
            Over the {data.realised.p50?.days ?? 0} days where a monthly purchase policy existed (it needs 180 priced days of history).
            Objective = mean + λ × CVaR95; lower is better.
          </p>
          <div className="tiles">
            {STRATEGIES.map(([k, name]) => {
              const r = data.realised[k]
              return <Tile key={k} label={name} value={gbp(r?.total)} sub={r && `${gbp(r.meanDaily)}/day · worst-5% ${gbp(r.cvar95)} · objective ${gbp(r.objective)}`} />
            })}
          </div>
          <div className="card">
            <h3>Cumulative excess £ (lower is better)</h3>
            {data.cumulative.length ? <CumulativeCost data={data.cumulative} /> : <p className="status">Not enough priced days.</p>}
          </div>

          {f && (
            <>
              <h3>Cost vs risk by purchase level (whole range)</h3>
              <p className="note">
                Buying more cuts shortfall cost but adds surplus risk. In hindsight over this range, λ = {f.lambda} would pick <b>P{Math.round(f.q * 100)}</b>.
                Each chart is one measure, so no dual axis.
              </p>
              <div className="grid2">
                <div className="card"><h3>Mean excess £ per day</h3><FrontierBars rows={f.rows} dataKey="meanCost" label="Mean £/day" recQ={f.q} /></div>
                <div className="card"><h3>CVaR95: mean of the worst 5% of days</h3><FrontierBars rows={f.rows} dataKey="cvar95" label="CVaR95 £/day" recQ={f.q} /></div>
              </div>
            </>
          )}

          <div className="grid2">
            <div className="card">
              <h3>Uncertainty models (out-of-sample)</h3>
              <table>
                <thead><tr><th>Model</th><th>Pinball</th><th>P10 cov.</th><th>P50 cov.</th><th>P90 cov.</th><th>Champion days</th></tr></thead>
                <tbody>
                  {data.models.map((m) => (
                    <tr key={m.model}>
                      <td>{MODEL_NAMES[m.model]}</td><td>{m.pinball.toLocaleString()}</td>
                      {['0.1', '0.5', '0.9'].map((q) => <td key={q}>{(m.coverage[q] * 100).toFixed(1)}%</td>)}
                      <td>{m.championDays}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="note">Lower pinball is better; coverage should match the nominal level (10% / 50% / 90%).</p>
            </div>
            <div className="card">
              <h3>Calibration: share of periods with need below each quantile</h3>
              <ReliabilityChart models={data.models.map((m) => ({ ...m, name: MODEL_NAMES[m.model] }))} />
            </div>
            <div className="card">
              <h3>Does forecast error move imbalance prices?</h3>
              <ErrorSpreadScatter points={data.scatter} />
              <p className="note">
                Correlation {data.correlation} between residual error and (imbalance − APX). Prices barely react to this national
                forecast error, so a buffer pays for itself mainly by cutting tail risk, not average cost.
              </p>
            </div>
            <div className="card">
              <h3>Pre-gate risk flags: track record</h3>
              <table>
                <thead><tr><th>Flag</th><th>Days</th><th>MAE flagged / other</th><th>Excess £/day flagged / other</th></tr></thead>
                <tbody>
                  {data.flags.map((r) => (
                    <tr key={r.key}>
                      <td>{FLAG_NAMES[r.key]}</td><td>{r.daysFlagged} / {r.daysOther}</td>
                      <td>{mw(r.maeFlagged)} / {mw(r.maeOther)}</td><td>{gbp(r.costFlagged)} / {gbp(r.costOther)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="note">A useful flag shows clearly higher error and cost on flagged days.</p>
            </div>
          </div>
          <p className="note">Decision support only, not trading advice. Backtests are historical and national-level.</p>
        </>
      )}
    </section>
  )
}
