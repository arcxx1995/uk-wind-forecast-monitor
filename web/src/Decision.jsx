import { useEffect, useState } from 'react'
import { BuyChart, dateTime, mw, mwh } from './charts'

function Tile({ label, value, sub }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

const MODEL_NAMES = { wind_only: 'wind-only', combined: 'demand + wind', conditional: 'conditional (wind level × peak)' }

const longDay = (d) => new Date(`${d}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short', timeZone: 'Europe/London' })

/** Live decision for tomorrow (gate open/closed, forecast coverage) vs a past day replayed as a backtest. */
function DayStatus({ day, meta }) {
  if (day !== meta.nextDeliveryDay) {
    return (
      <p className="note warn" role="note">
        Backtest view: {longDay(day)} has already been delivered. This replays the forecasts that were available at its gate
        and, where known, compares them with what actually happened.
      </p>
    )
  }
  const missing = meta.nextHalfHours - meta.nextCovered
  return (
    <p className="note warn" role="status">
      <b>Live decision for {longDay(day)}.</b>{' '}
      {meta.gateClosed
        ? `The auction gate closed at ${dateTime(meta.nextGate)} UK; the figures below use the forecasts published before it.`
        : `The auction gate closes at ${dateTime(meta.nextGate)} UK. Until then the figures update as new forecasts are published.`}
      {missing > 0 && ` Only ${meta.nextCovered} of ${meta.nextHalfHours} half-hours have pre-gate demand and wind forecasts in the latest data (refreshed ${meta.lastRefresh ? dateTime(meta.lastRefresh) : '–'} UK). The wind forecast normally lands at about 09:30 and the demand forecast at about 08:45; the next data refresh will fill the rest.`}
    </p>
  )
}

export default function Decision({ meta }) {
  const [day, setDay] = useState(meta.nextDeliveryDay)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!day) return
    const ctl = new AbortController()
    setData(null)
    fetch(`/api/decision?day=${day}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : r.json().then((b) => Promise.reject(new Error(b.detail ?? `API ${r.status}`)))))
      .then((d) => { setData(d); setErr(null) })
      .catch((e) => e.name !== 'AbortError' && setErr(e.message))
    return () => ctl.abort()
  }, [day])

  const t = data?.totals
  const rec = data?.recommendation
  const recRow = rec?.rows.find((r) => r.q === rec.q)
  const margin = data?.flags.find((f) => f.kind === 'info')
  const publishes = (k) => [...new Set(data?.rows.map((r) => r[k]).filter(Boolean))].map(dateTime).join(', ')

  return (
    <section aria-labelledby="dec">
      <h2 id="dec">How much to buy (MW)</h2>
      <div className="controls">
        <label>
          Delivery day
          <input type="date" min={meta.dataStart} max={meta.nextDeliveryDay} value={day ?? ''} onChange={(e) => e.target.value && setDay(e.target.value)} />
        </label>
        {day !== meta.nextDeliveryDay && (
          <button className="btn btn-secondary" onClick={() => setDay(meta.nextDeliveryDay)}>Back to tomorrow</button>
        )}
      </div>
      <DayStatus day={day} meta={meta} />
      <p className="note">
        Portfolio share {(meta.share * 100).toFixed(1)}% of GB National Demand. Need = share × (demand forecast − wind),
        using only forecasts published before the gate ({data ? `${dateTime(data.gate)} UK` : '11:00 D-1'}). Uncertainty is the
        combined demand + wind (residual) error from days up to D-2, using whichever model scored best recently.
        Solar is not subtracted: National Demand already nets off embedded solar.
      </p>
      {err && <p className="status error">Could not load data: {err}</p>}
      {day && !data && !err && <p className="status">Loading…</p>}
      {data && (
        <>
          {data.flags.some((f) => f.flagged && f.kind === 'warning') && (
            <ul className="flags" aria-label="Risk flags">
              {data.flags.filter((f) => f.flagged && f.kind === 'warning').map((f) => (
                <li key={f.key}>⚠ {f.label}: {f.value.toLocaleString()} MW (90-day threshold {f.threshold.toLocaleString()} MW)</li>
              ))}
            </ul>
          )}
          <div className="tiles">
            <Tile
              label={rec ? `${rec.validated ? 'Recommended' : 'Policy level'} P${Math.round(rec.q * 100)}` : 'Recommended purchase'}
              value={mwh(t.buyRecommended)}
              sub={rec
                ? `${rec.validated ? 'matches or beats fixed P50/P90 on its track record' : 'not yet shown to beat fixed P50/P90'} · ${rec.trackDays} days tracked`
                : 'needs 180 priced days of history'}
            />
            <Tile label="Expected (P50)" value={mwh(t.buyExpected)} sub="median need, whole day" />
            <Tile label="Conservative (P90)" value={mwh(t.buyConservative)} sub="need exceeded on ~10% of recent periods" />
            {t.buyActual != null && <Tile label="Actual need (hindsight)" value={mwh(t.buyActual)} sub="from demand + wind outturn" />}
          </div>
          <BuyChart rows={data.rows} recQ={rec?.q} />
          <p className="note">
            Band model: <b>{MODEL_NAMES[data.bandModel.name] ?? '–'}</b> (lowest 90-day pinball loss
            {Object.entries(data.bandModel.scores).filter(([, v]) => v != null).map(([k, v]) => ` · ${MODEL_NAMES[k]} ${v.toLocaleString()}`).join('')}).
            {rec && ` Purchase level for ${rec.month.slice(0, 7)} = quantile minimising mean excess £ + ${rec.lambda} × CVaR95 over all ${recRow.days} priced days before the month (risk aversion set by admin; historical ${recRow ? `£${recRow.meanCost.toLocaleString()}/day, worst-5% days £${recRow.cvar95.toLocaleString()}` : ''}).`}
            {margin && ` NESO surplus margin for the day: ${margin.value.toLocaleString()} MW${margin.flagged ? ' (tight vs past year; historically not a higher-cost signal)' : ''}.`}
            {' '}Wind forecast published {publishes('windPublish') || '–'} · demand forecast published {publishes('ndfPublish') || '–'} (UK time).
          </p>
          <p className="note">Decision support only, not trading advice. £ uses the APX market index as a day-ahead price proxy and the GB single imbalance price.</p>
          <details>
            <summary>Half-hourly table</summary>
            <table>
              <thead><tr><th>Time (UK)</th><th>Demand fc</th><th>Wind fc</th><th>Buy expected</th><th>Buy recommended</th><th>Buy conservative</th><th>Actual need</th></tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.t}>
                    <td>{dateTime(r.t)}</td><td>{mw(r.ndf)}</td><td>{mw(r.windFc)}</td>
                    <td>{mw(r.buyExpected)}</td><td>{mw(r.buyRecommended)}</td><td>{mw(r.buyConservative)}</td><td>{mw(r.buyActual)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </section>
  )
}
