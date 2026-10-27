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

export default function Decision({ meta }) {
  const [day, setDay] = useState(meta.latestDecisionDay)
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
  const publishes = (k) => [...new Set(data?.rows.map((r) => r[k]).filter(Boolean))].map(dateTime).join(', ')

  return (
    <section aria-labelledby="dec">
      <h2 id="dec">How much to buy (MW)</h2>
      <div className="controls">
        <label>
          Delivery day
          <input type="date" min={meta.dataStart} max={meta.latestDecisionDay} value={day ?? ''} onChange={(e) => e.target.value && setDay(e.target.value)} />
        </label>
      </div>
      <p className="note">
        Portfolio share {(meta.share * 100).toFixed(1)}% of GB National Demand. Need = share × (demand forecast − wind),
        using only forecasts published before the gate ({data ? `${dateTime(data.gate)} UK` : '11:00 D-1'}).
        Expected uses wind corrected by its trailing 30-day median error (P50); conservative uses the P10 error, so wind
        was at least that high in about 90% of recent periods. Solar is not subtracted: National Demand already nets off embedded solar.
      </p>
      {!meta.latestDecisionDay && <p className="status">No delivery day has complete pre-gate forecasts yet.</p>}
      {err && <p className="status error">Could not load data: {err}</p>}
      {day && !data && !err && <p className="status">Loading…</p>}
      {data && (
        <>
          <div className="tiles">
            <Tile label="Expected purchase" value={mwh(t.buyExpected)} sub="P50 wind, whole day" />
            <Tile label="Conservative purchase" value={mwh(t.buyConservative)} sub="P10 wind, whole day" />
            <Tile
              label="Risk buffer"
              value={mwh(t.buyConservative != null && t.buyExpected != null ? t.buyConservative - t.buyExpected : null)}
              sub="conservative − expected"
            />
            {t.buyActual != null && <Tile label="Actual need (hindsight)" value={mwh(t.buyActual)} sub="from demand + wind outturn" />}
          </div>
          {!data.band && <p className="note error">Not enough recent error history for a band: P50/P10 fall back to the raw forecast.</p>}
          <BuyChart rows={data.rows} />
          <p className="note">
            Wind forecast published {publishes('windPublish') || '–'} · demand forecast published {publishes('ndfPublish') || '–'} (UK time).
            {data.band && ` Wind error band (trailing 30 days): P10 ${mw(data.band.p10)}, P50 ${mw(data.band.p50)}.`}
          </p>
          <details>
            <summary>Half-hourly table</summary>
            <table>
              <thead><tr><th>Time (UK)</th><th>Demand fc</th><th>Wind fc</th><th>Buy expected</th><th>Buy conservative</th><th>Actual need</th></tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.t}>
                    <td>{dateTime(r.t)}</td><td>{mw(r.ndf)}</td><td>{mw(r.windFc)}</td>
                    <td>{mw(r.buyExpected)}</td><td>{mw(r.buyConservative)}</td><td>{mw(r.buyActual)}</td>
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
