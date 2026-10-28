import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import Hero from './Hero'
import './styles.css'

const Decision = lazy(() => import('./Decision'))
const Accuracy = lazy(() => import('./Accuracy'))
const Risk = lazy(() => import('./Risk'))

const TABS = [['decision', 'Decision'], ['risk', 'Risk & cost'], ['wind', 'Wind accuracy'], ['solar', 'Solar accuracy']]
const fromHash = () => (TABS.some(([k]) => k === location.hash.slice(1)) ? location.hash.slice(1) : 'decision')

function App() {
  const [tab, setTab] = useState(fromHash)
  const [meta, setMeta] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    const onHash = () => setTab(fromHash())
    addEventListener('hashchange', onHash)
    return () => removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    fetch('/api/meta')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`API ${r.status}`))))
      .then(setMeta, (e) => setErr(e.message))
  }, [])

  const openDecision = () => {
    location.hash = 'decision'
    requestAnimationFrame(() => document.getElementById('views')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  return (
    <main>
      <div className="brand">GB Renewables Forecast Monitor</div>
      <Hero meta={meta} onOpenDecision={openDecision} />
      <nav id="views" className="tabs" role="tablist" aria-label="Views">
        {TABS.map(([k, name]) => (
          <a key={k} href={`#${k}`} role="tab" aria-selected={tab === k}>{name}</a>
        ))}
      </nav>
      {err && <p className="status error">Could not load data: {err}</p>}
      {!meta && !err && <p className="status">Loading…</p>}
      {meta && (
        <Suspense fallback={<p className="status">Loading…</p>}>
          {tab === 'decision' ? <Decision meta={meta} /> : tab === 'risk' ? <Risk meta={meta} /> : <Accuracy key={tab} fuel={tab} meta={meta} />}
        </Suspense>
      )}
    </main>
  )
}

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>)
