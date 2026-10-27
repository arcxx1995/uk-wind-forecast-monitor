import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

const mw = (v) => `${Math.round(v).toLocaleString()} MW`
const gw = (v) => (v / 1000).toFixed(v % 1000 ? 1 : 0)
const day = (t) => new Date(t).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' })
const dateTime = (t) =>
  new Date(t).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })

const axis = { stroke: 'var(--axis)', tick: { fill: 'var(--muted)', fontSize: 11 }, tickLine: false }
const grid = <CartesianGrid stroke="var(--grid)" vertical={false} />
const legend = (
  <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: 'var(--text-2)' }}>{v}</span>} />
)

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

export function ForecastChart({ points }) {
  const data = points.map((p) => ({ ...p, band: [p.lo, p.hi] }))
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
              title={(d) => `${dateTime(d.t)} UTC`}
              rows={(d) => [
                ['Actual', mw(d.actual), 'var(--series-1)'],
                ['Forecast', mw(d.forecast), 'var(--series-2)'],
                ['P10–P90 range', `${mw(d.lo)} – ${mw(d.hi)}`],
                ['Error (actual − fc)', mw(d.error)],
                ['Published', `${dateTime(d.publishTime)} (${d.leadH}h ahead)`],
              ]}
            />
          }
        />
        {legend}
        <Area dataKey="band" name="P10–P90 range" stroke="none" fill="var(--series-2)" fillOpacity={0.15} isAnimationActive={false} />
        <Line dataKey="lo" name="Conservative (P10)" stroke="var(--series-2)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
        <Line dataKey="forecast" name="Forecast" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line dataKey="actual" name="Actual" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

export function HorizonChart({ curve, h }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={curve} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {grid}
        <XAxis dataKey="h" type="number" domain={[0, 48]} ticks={[0, 6, 12, 18, 24, 30, 36, 42, 48]} unit="h" {...axis} />
        <YAxis tickFormatter={gw} width={36} domain={[0, 'auto']} {...axis} />
        <Tooltip
          cursor={{ stroke: 'var(--axis)' }}
          content={
            <Tip
              title={(d) => `Lead time ≥ ${d.h}h`}
              rows={(d) => [
                ['MAE', mw(d.mae), 'var(--series-1)'],
                ['RMSE', mw(d.rmse), 'var(--series-2)'],
                ['Bias', mw(d.bias)],
              ]}
            />
          }
        />
        {legend}
        {h != null && <ReferenceLine x={h} stroke="var(--muted)" strokeDasharray="3 3" />}
        <Line dataKey="mae" name="MAE" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line dataKey="rmse" name="RMSE" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
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
        <YAxis width={36} allowDecimals={false} {...axis} />
        <Tooltip
          cursor={{ fill: 'var(--accent-wash)' }}
          content={<Tip title={(d) => `${mw(d.bin)} to ${mw(d.bin + binSize)}`} rows={(d) => [['Hours', d.count]]} />}
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
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
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

export { mw, day }
