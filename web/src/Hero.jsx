import { useEffect, useState } from 'react'

// Kept free of ./charts so Recharts stays out of the entry chunk.
const TZ = 'Europe/London'
const longDay = (d) => new Date(`${d}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ })
const clock = (t) => new Date(t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: TZ })
const dateTime = (t) =>
  new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: TZ })

const STEPS = [
  ['Forecasts in, before the gate',
    'For each half-hour of the delivery day we take the NESO national demand forecast and the national wind forecast that were published before the 11:00 UK day-ahead auction gate on the day before. Nothing published later is used.'],
  ['Uncertainty measured on past days',
    'How wrong were those forecasts recently? Demand and wind errors from days that were fully known at the gate set a P50–P90 range for the need. Three error models compete, and the one that scored best recently is used.'],
  ['MWh to buy, with £ risk',
    'Your portfolio share of the remaining need, per half-hour and per day, at P50, at the monthly policy level and at P90. Alongside it: the historical £ cost of buying more or less, and warnings for days that tend to go wrong.'],
]

function Stat({ value, label, sub }) {
  return (
    <div className="hero-stat">
      <div className="hero-stat-value">{value}</div>
      <div className="hero-stat-label">{label}</div>
      {sub && <div className="hero-stat-sub">{sub}</div>}
    </div>
  )
}

export default function Hero({ meta, onOpenDecision }) {
  const [dec, setDec] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!meta?.nextDeliveryDay) return
    fetch(`/api/decision?day=${meta.nextDeliveryDay}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setDec, () => setDec(null))
  }, [meta?.nextDeliveryDay])

  const rec = dec?.recommendation
  const buy = dec?.totals.buyRecommended ?? dec?.totals.buyExpected
  const cov = dec?.bandModel.coverage90
  const windPub = dec?.rows.find((r) => r.windPublish)?.windPublish

  const showSteps = () => {
    setOpen(true)
    requestAnimationFrame(() => document.getElementById('how')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  return (
    <header className="hero">
      <p className="hero-eyebrow">GB day-ahead power · Elexon BMRS data · refreshed daily</p>
      <h1 className="hero-title">Day-ahead renewables forecasts, their real accuracy, and the cost of every purchase level</h1>
      <p className="hero-sub">
        For the day-ahead desk: how much power to buy for each half-hour of tomorrow, before the 11:00 auction gate.
        It is built only from the wind and demand forecasts available at that moment, with their uncertainty measured on
        past days and the £ impact of buying more or less.
      </p>
      <div className="hero-ctas">
        <button className="btn btn-primary" onClick={onOpenDecision}>See tomorrow's buy</button>
        <button className="btn btn-secondary" onClick={showSteps} aria-expanded={open} aria-controls="how">How it works</button>
      </div>

      <div className="hero-stats" aria-live="polite">
        <Stat
          value={buy != null ? `${buy.toLocaleString()} MWh` : 'Awaiting forecasts'}
          label={meta?.nextDeliveryDay ? `To buy for ${longDay(meta.nextDeliveryDay)}` : 'To buy'}
          sub={meta && (buy != null
            ? `${(meta.share * 100).toFixed(1)}% share · gate ${meta.gateClosed ? 'closed' : `closes ${clock(meta.nextGate)}`}`
            : `${meta.nextCovered} of ${meta.nextHalfHours} half-hours forecast so far`)}
        />
        <Stat
          value={rec ? `P${Math.round(rec.q * 100)}` : 'P50'}
          label="Purchase policy"
          sub={rec ? (rec.validated ? 'recommended: beats fixed rules' : 'not yet beating fixed rules') : 'building history'}
        />
        <Stat
          value={cov != null ? `${(cov * 100).toFixed(1)}%` : '–'}
          label="Band reliability"
          sub="need ≤ P90, last 90 days (target 90%)"
        />
        <Stat
          value={windPub ? clock(windPub) : '–'}
          label="Wind forecast used"
          sub={meta?.lastRefresh ? `data refreshed ${dateTime(meta.lastRefresh)}` : null}
        />
      </div>

      {open && (
        <section id="how" className="hero-how" aria-labelledby="how-title">
          <div className="hero-how-head">
            <h2 id="how-title">How it works</h2>
            <button className="btn-link" onClick={() => setOpen(false)}>Hide</button>
          </div>
          <ol className="steps">
            {STEPS.map(([title, body], i) => (
              <li key={title} className="step">
                <span className="step-num" aria-hidden="true">{i + 1}</span>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ol>
          <p className="hero-foot">
            Sources: NESO demand forecast &amp; outturn, NESO wind forecast, Elexon metered generation, APX market index and
            imbalance prices. Decision support only, not trading advice.
          </p>
        </section>
      )}
    </header>
  )
}
