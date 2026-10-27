import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

export const mw = (v) => (v == null ? '–' : `${Math.round(v).toLocaleString()} MW`)
export const mwh = (v) => (v == null ? '–' : `${Math.round(v).toLocaleString()} MWh`)
const gw = (v) => String(+(v / 1000).toFixed(2))
const TZ = 'Europe/London'
export const day = (t) => new Date(t).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', timeZone: TZ })
export const dateTime = (t) =>
  new Date(t).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: TZ })
const clock = (t) => new Date(t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: TZ })

const axis = { stroke: 'var(--axis)', tick: { fill: 'var(--muted)', fontSize: 11 }, tickLine: false }
const grid = <CartesianGrid stroke="var(--grid)" vertical={false} />
const legend = (
  <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: 'var(--text-2)' }}>{v}</span>} />
)
const line = { dot: false, isAnimationActive: false, strokeWidth: 2 }

function Tip({ active, payload, title, rows }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="tip">
      <b>{title(d)}</b>
      {rows(d).map(([label, value, color]) => (
        <div className="row" key={label}>
          <span>{color && <span className="swatch" style={{ background: color }} />}{label}</span>
          <span>{value}</span>
        </div>
      ))}
    </div>
  )
}

// ponytail: >2000 points (multi-month ranges) are averaged per day so the SVG stays light.
function daily(points) {
  const by = new Map()
  for (const p of points) (by.get(p.day) ?? by.set(p.day, []).get(p.day)).push(p)
  const avg = (g, k) => (g.every((p) => p[k] != null) ? g.reduce((s, p) => s + p[k], 0) / g.length : null)
  return [...by.values()].map((g) => ({
    ...g[0], t: g[0].t, daily: true, n: g.length,
    ...Object.fromEntries(['actual', 'forecast', 'error', 'lo', 'hi', 'leadH'].map((k) => [k, avg(g, k)])),
  }))
}

export function ForecastChart({ points }) {
  const data = (points.length > 2000 ? daily(points) : points).map((p) => ({ ...p, band: p.lo == null ? null : [p.lo, p.hi] }))
  const isDaily = data[0]?.daily
  return (
    <ResponsiveContainer width="100%" height={360}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {grid}
        <XAxis dataKey="t" tickFormatter={day} minTickGap={40} {...axis} />
        <YAxis tickFormatter={gw} width={36} {...axis} />
        <Tooltip
          cursor={{ stroke: 'var(--axis)' }}
          content={
            <Tip
              title={(d) => (isDaily ? `${day(d.t)} (daily mean of ${d.n} points)` : `${dateTime(d.t)} UK`)}
              rows={(d) => [
                ['Actual', mw(d.actual), 'var(--series-1)'],
                ['Forecast', mw(d.forecast), 'var(--series-2)'],
                ['P10–P90 range', d.lo == null ? 'no band yet' : `${mw(d.lo)} – ${mw(d.hi)}`],
                ['Error (actual − fc)', mw(d.error)],
                isDaily ? ['Mean lead time', `${d.leadH.toFixed(1)}h`] : ['Published', `${dateTime(d.publishTime)} (${d.leadH}h ahead)`],
              ]}
            />
          }
        />
        {legend}
        <Area dataKey="band" name="P10–P90 range (trailing 30 days)" stroke="none" fill="var(--series-2)" fillOpacity={0.15} isAnimationActive={false} />
        <Line dataKey="lo" name="Conservative (P10)" stroke="var(--series-2)" strokeDasharray="4 3" {...line} strokeWidth={1.5} />
        <Line dataKey="forecast" name="Forecast" stroke="var(--series-2)" {...line} />
        <Line dataKey="actual" name="Actual" stroke="var(--series-1)" {...line} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** curves: [{name, color, data: [{h, mae}]}] */
export function HorizonChart({ curves, h }) {
  const merged = Array.from({ length: 49 }, (_, i) => ({
    h: i, ...Object.fromEntries(curves.map((c) => [c.name, c.data.find((r) => r.h === i)?.mae])),
  }))
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={merged} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {grid}
        <XAxis dataKey="h" type="number" domain={[0, 48]} ticks={[0, 6, 12, 18, 24, 30, 36, 42, 48]} unit="h" {...axis} />
        <YAxis tickFormatter={gw} width={36} domain={[0, 'auto']} {...axis} />
        <Tooltip
          cursor={{ stroke: 'var(--axis)' }}
          content={<Tip title={(d) => `Lead time ≥ ${d.h}h`} rows={(d) => curves.map((c) => [`MAE ${c.name}`, mw(d[c.name]), c.color])} />}
        />
        {legend}
        {h != null && <ReferenceLine x={h} stroke="var(--muted)" strokeDasharray="3 3" />}
        {curves.map((c) => <Line key={c.name} dataKey={c.name} name={`MAE ${c.name}`} stroke={c.color} connectNulls {...line} />)}
      </LineChart>
    </ResponsiveContainer>
  )
}

export function HistogramChart({ histogram, binSize = 500 }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={histogram} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
        {grid}
        <XAxis dataKey="bin" tickFormatter={gw} {...axis} />
        <YAxis width={44} allowDecimals={false} {...axis} />
        <Tooltip
          cursor={{ fill: 'var(--accent-wash)' }}
          content={<Tip title={(d) => `${mw(d.bin)} to ${mw(d.bin + binSize)}`} rows={(d) => [['Periods', d.count]]} />}
        />
        <ReferenceLine x={0} stroke="var(--muted)" />
        <Bar dataKey="count" radius={[4, 4, 0, 0]} isAnimationActive={false}>
          {histogram.map((b) => <Cell key={b.bin} fill={b.bin < 0 ? 'var(--neg)' : 'var(--pos)'} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export function MaeBars({ data, x, label }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
        {grid}
        <XAxis dataKey={x} {...axis} />
        <YAxis tickFormatter={gw} width={36} {...axis} />
        <Tooltip
          cursor={{ fill: 'var(--accent-wash)' }}
          content={<Tip title={(d) => label(d)} rows={(d) => [['MAE', mw(d.mae), 'var(--series-1)'], ['Bias', mw(d.bias)]]} />}
        />
        <Bar dataKey="mae" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function BiasBars({ data }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={1}>
        {grid}
        <XAxis dataKey="day" tickFormatter={day} minTickGap={24} {...axis} />
        <YAxis tickFormatter={gw} width={36} {...axis} />
        <Tooltip
          cursor={{ fill: 'var(--accent-wash)' }}
          content={<Tip title={(d) => day(d.day)} rows={(d) => [['Bias', mw(d.bias)], ['MAE', mw(d.mae)]]} />}
        />
        <ReferenceLine y={0} stroke="var(--axis)" />
        <Bar dataKey="bias" isAnimationActive={false}>
          {data.map((d) => <Cell key={d.day} fill={d.bias < 0 ? 'var(--neg)' : 'var(--pos)'} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export function BuyChart({ rows }) {
  const hasActual = rows.some((r) => r.buyActual != null)
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {grid}
        <XAxis dataKey="t" tickFormatter={clock} minTickGap={40} {...axis} />
        <YAxis width={44} tickFormatter={(v) => v.toLocaleString()} {...axis} />
        <Tooltip
          cursor={{ stroke: 'var(--axis)' }}
          content={
            <Tip
              title={(d) => `${dateTime(d.t)} UK`}
              rows={(d) => [
                ['Buy, conservative (P10 wind)', mw(d.buyConservative), 'var(--series-2)'],
                ['Buy, expected (P50 wind)', mw(d.buyExpected), 'var(--series-2)'],
                ...(hasActual ? [['Actual need', mw(d.buyActual), 'var(--series-3)']] : []),
                ['National demand fc', mw(d.ndf)],
                ['Wind fc (raw / P50 / P10)', `${mw(d.windFc)} / ${mw(d.windP50)} / ${mw(d.windP10)}`],
              ]}
            />
          }
        />
        {legend}
        <Area dataKey={(r) => (r.buyExpected == null ? null : [r.buyExpected, r.buyConservative])} name="Risk buffer" stroke="none" fill="var(--series-2)" fillOpacity={0.15} isAnimationActive={false} />
        <Line dataKey="buyConservative" name="Buy, conservative (P10 wind)" stroke="var(--series-2)" strokeDasharray="4 3" {...line} />
        <Line dataKey="buyExpected" name="Buy, expected (P50 wind)" stroke="var(--series-2)" {...line} />
        {hasActual && <Line dataKey="buyActual" name="Actual need (hindsight)" stroke="var(--series-3)" {...line} />}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
