# GB Renewables Forecast Monitor

A web app for a UK utility's day-ahead trading desk. It answers three questions:

1. **How much do we need to buy for tomorrow?** The answer is in MW per half-hour and MWh per day, built only from forecasts published before the GB day-ahead auction gate (11:00 UK, D-1).
2. **How good are the wind forecasts we rely on?** Accuracy is measured against actuals, by lead time and over 12 months.
3. **How good are the solar forecasts?**

**Live:** https://uk-wind-forecast-monitor-smoky.vercel.app. Access requires Vercel Authentication (Deployment Protection).

Stack: React (Vite + Recharts 3) · Python (FastAPI) · Docker · Vercel · GitHub Actions.

## Views

| Tab | What it shows |
|---|---|
| **Decision** | For a delivery day (default: the latest one with complete pre-gate data): <ul><li>MW to buy, expected and conservative, with the risk buffer between them</li><li>daily MWh totals</li><li>the publish times used</li><li>for past days, the realised need as a hindsight check</li><li>a half-hourly table</li></ul> |
| **Wind accuracy** | WINDFOR or DGWS, gate or fixed lead time, and presets (January 2026, last 30 days, last 12 months). Shows forecast vs actual with an out-of-sample P10–P90 band, KPI tiles, a source comparison, MAE vs lead time for both sources, the error histogram, MAE by UK hour, and daily bias. |
| **Solar accuracy** | DGWS solar vs AGWS, daylight periods only. Same views as wind. |

## Data (Elexon BMRS, public, no API key)

| Use | Dataset | Notes |
|---|---|---|
| Wind actual | `FUELHH` (`WIND`) | 30-min, transmission-metered wind |
| Wind forecast | `WINDFOR` | Hourly, 8 publishes/day (09:30 UTC is the last before the gate in winter) |
| Demand forecast | `NDF` | Half-hourly National Demand, republished every ~30 min. Stored pruned to the latest publish before each target's gate. |
| Demand actual | `demand/outturn` | `initialDemandOutturn` (ND) |
| Solar & wind on/offshore forecast | `DGWS` | Half-hourly. The day-ahead run lands **~16:45 UTC on D-1, after the gate** |
| Solar & wind on/offshore actual | `AGWS` | Half-hourly. Latest revision wins. Has gaps in some summer months. |

**Storage:**
- The snapshot is stored as monthly gzip partitions in `monitor/data/<dataset>/<YYYY-MM>.json.gz` (a few MB in total).
- It covers a rolling 13 months, plus **pinned Dec 2025 and Jan 2026**, so the original brief stays reproducible.
- **Refresh:** `.github/workflows/refresh.yml` runs `scripts/fetch_data.py` daily at 09:45 UTC (before the gate) and 00:30 UTC (yesterday's actuals). It commits the changed partitions, and Vercel's Git integration redeploys.

```bash
python scripts/fetch_data.py                 # current + previous month
python scripts/fetch_data.py --backfill      # 13 months + pinned
python scripts/fetch_data.py --months 2026-01,2026-02
```

## Method

**Time**
- Delivery days are Europe/London local days, so clock-change days have 46 or 50 half-hours.
- The gate is **strictly before 11:00 London time on D-1**, and is BST-aware.

**Forecast selection**
- **Gate:** the latest publish strictly before the gate.
- **Fixed lead time H:** the latest publish ≤ target − H.
- **DGWS:** its day-ahead run arrives after the gate, so in "gate" mode it is evaluated on the latest run before delivery-day midnight. The UI flags that this run is not usable for the auction.

**Alignment**
- WINDFOR and DGWS wind are hourly. Half-hourly actuals are averaged to the hour.
- Solar is compared half-hourly.

**Errors and bands**
- **Error = actual − forecast.** A negative error is a shortfall, which means more power has to be bought.
- **Out-of-sample error bands:** for delivery day D, the P10/P50/P90 of errors from local days D-31…D-2. These are only actuals that were known at the gate. Fewer than 100 errors means no band is shown.

**MW-to-buy**
- Need = `share × max(0, NDF − wind)` per half-hour.
  - **Expected:** wind = WINDFOR + rolling P50 error (bias-corrected).
  - **Conservative:** wind = WINDFOR + rolling P10 error (wind met in about 90% of recent periods).
  - `share` comes from the `PORTFOLIO_SHARE` env var (default `0.05`). It is a proxy for the client's own demand.
- **Solar is not subtracted.** National Demand already nets off embedded generation, and GB solar is mostly embedded.
- **Scope matters.** AGWS wind (which includes embedded wind) runs about 1.5 GW above FUELHH (transmission only) on average. WINDFOR tracks the wider scope. Correcting WINDFOR by its error against **FUELHH** re-scales it to the transmission wind that actually offsets National Demand. The backtest in the notebook checks this.

## Key findings

Last 12 months (Oct 2025 → Oct 2026). The notebook reproduces these with the app's own code.

| | MAE | Bias | Notes |
|---|---|---|---|
| WINDFOR gate vs FUELHH | 1.80 GW | −1.45 GW | 20% of mean actual. January 2026 alone: MAE 2,123 MW, bias −1,840 MW |
| WINDFOR gate vs AGWS (wider scope) | 1.35 GW | **+0.04 GW** | so the "over-forecast" is a **scope difference**, not weather error |
| DGWS wind (day-ahead run, after gate) vs AGWS | 2.30 GW | −2.08 GW | published later than WINDFOR, yet less accurate |
| DGWS solar (day-ahead run) vs AGWS | 0.50 GW | −0.16 GW | 11.6% of mean daylight output |

**Error vs lead time.** Error grows slowly with lead time: in January 2026, MAE was 1.8 GW at 0 h and 2.4 GW at 48 h. The scope/bias effect dominates.

**Out-of-sample P10 band.**
- Actual wind sat at or above the conservative line in **88.4%** of hours (target 90%).
- The weakest month was Sep 2026 at 74%, when summer turned to autumn.

**MW-to-buy backtest** (384 days, 5% share):
- Daily error of the expected quantity: MAE 1,551 MWh, bias +200 MWh.
- The conservative quantity covered the realised need on **90%** of days.
- The average buffer was about 2.8 GWh/day.

**DGWS timing.** DGWS's day-ahead run lands at about 16:45 UTC on D-1, after the 11:00 gate. It can be scored for accuracy but not traded on. WINDFOR (09:30) and NDF (08:45) are the only inputs available at the gate.

**Data quality.** AGWS has gaps in summer 2026 (for example, about 28 of 48 periods per day in June). There is a DGWS solar outlier of about 25 GW forecast on 26 Apr 2026. It is real source data and has been kept.

Full analysis: [`notebooks/analysis.ipynb`](notebooks/analysis.ipynb).

## Run locally

```bash
# API
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PORTFOLIO_SHARE=0.05 .venv/bin/uvicorn api.index:app --port 8000

# Web (proxies /api to :8000)
cd web && npm install && npm run dev        # http://localhost:5173
```

**Docker:**

```bash
PORTFOLIO_SHARE=0.05 docker compose up --build   # web http://localhost:8080, api http://localhost:8000
```

**Tests:**

```bash
.venv/bin/pip install pytest && .venv/bin/python -m pytest -q
```

The tests cover:
- BST and GMT gate cutoffs
- 46- and 50-period clock-change days
- the strict-`<` gate
- day-ahead vs gate selection
- the rolling band never using data from D-1 or later
- MW-to-buy maths and its floor at zero
- the solar night filter
- the decision table

**Notebook:**

```bash
.venv/bin/pip install pandas matplotlib jupyter && .venv/bin/jupyter lab notebooks/analysis.ipynb
```

## API

| Endpoint | Returns |
|---|---|
| `GET /api/meta` | data range, `latestDecisionDay`, `lastRefresh`, `share` |
| `GET /api/decision?day=YYYY-MM-DD` | half-hourly `ndf, windFc, windP50, windP10, buyExpected, buyConservative`, plus actuals when known; `totals` (MWh); `band`; `gate` |
| `GET /api/accuracy?source=windfor\|dgws_wind\|dgws_solar&mode=gate\|horizon&h=0..48&start&end` | `selection`, `points` (with the out-of-sample `lo`/`hi`), `stats`, `horizonCurve`. The range is at most 400 days. |

## Deploy (Vercel)

**Project setup** (`vercel.json`):
- builds `web/` into static files
- serves `api/index.py` (FastAPI) as the only Python function
- bundles `monitor/**` (logic + data) with it
- rewrites `/api/*` to that function

**Releasing:**
- Pushes to `main` deploy to production. Branches get preview deployments.

**One-time setup:**
- `vercel git connect`
- `vercel env add PORTFOLIO_SHARE production`
- Deployment Protection → Vercel Authentication on **all deployments** (Pro plan). Viewers must be members of the Vercel team, or use a shareable link.

Docker is for local and self-hosted runs only.

## Layout

```
api/index.py              FastAPI routes (the only Vercel function)
monitor/core.py           pure logic: selection, metrics, rolling bands, MW-to-buy
monitor/data.py           loads the partitions
monitor/data/             Elexon snapshot (monthly .json.gz)
scripts/fetch_data.py     snapshot refresh / backfill / retention
tests/test_core.py        pytest
web/src/                  main.jsx (tabs), Decision.jsx, Accuracy.jsx, charts.jsx
notebooks/analysis.ipynb  analysis
.github/workflows/        daily refresh
```

## Still out of scope

- client-specific demand data (CSV or API)
- confidence levels other than P10/P50
- demand-forecast accuracy views
- a per-user audit trail
- a database

## AI tools disclosure

Built with **Claude Code** (Anthropic, Claude Opus 5.5) as an AI coding assistant. It was used to:
- research the Elexon datasets, including their publish timing and scope differences
- scope both phases through Q&A with the developer
- write the code, tests, notebook and this README
- deploy the app

The developer confirmed the design decisions. Metrics are computed from real data and cross-checked between the API and the notebook.
