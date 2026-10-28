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
| **Decision** | For a delivery day (default: **tomorrow**, the day the desk is buying for; earlier days are replayed as backtests): <ul><li>MW to buy at P50 (expected), at the monthly **policy** level, and at P90 (conservative)</li><li>daily MWh totals</li><li>which uncertainty model is in use</li><li>pre-gate risk warnings (forecast revision, wind ramp), with margin shown as context</li><li>the publish times used</li><li>for past days, the realised need as a hindsight check</li><li>a half-hourly table</li></ul> |
| **Risk & cost** | £ backtest of buying at P50, at the policy level and at P90 vs perfect hindsight (excess £, CVaR95); the cost-vs-risk frontier by purchase level; out-of-sample scores and a calibration chart for the three uncertainty models; forecast error vs imbalance spread; the risk flags' track record. |
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
| Day-ahead price proxy | `MID` (APXMIDP) | Half-hourly APX market index. **Not** the EPEX/N2EX auction price (that isn't on Elexon) |
| Imbalance price | `balancing/settlement/system-prices/{date}` | GB **single** cash-out price (SSP = SBP), plus net imbalance volume. One API call per day |
| Margin | `OCNMFD` | NESO daily surplus margin, published ~11:09 for D+2 onwards. (LOLP/de-rated margin only reach 12h ahead, so they are not available at the gate.) |

**Storage:**
- The snapshot is stored as monthly gzip partitions in `monitor/data/<dataset>/<YYYY-MM>.json.gz` (a few MB in total).
- It covers a rolling 13 months, plus **pinned Dec 2025 and Jan 2026**, so the original brief stays reproducible.
- **Live overlay for tomorrow** (`monitor/live.py`): when the page loads, the API asks Elexon directly for any WINDFOR and NDF publishes for the next delivery day.
  - It only asks for publishes since the snapshot (window: from the later of gate − 26h and the last refresh − 1h, up to the earlier of now and the gate).
  - It merges them in memory and caches the result for 5 minutes. A check of the full 26h window took about 1.6s.
  - If Elexon fails, the app keeps serving the snapshot and says so in the UI. Tomorrow's decision therefore no longer depends on the scheduled job running on time.
- **Snapshot refresh** (history and backtests): `.github/workflows/refresh.yml` runs `scripts/fetch_data.py` several times in the pre-gate window (08:40, 09:40, 10:10, 10:35 UTC) and at 00:30 UTC for yesterday's actuals. It commits the changed partitions, and Vercel's Git integration redeploys. GitHub's scheduled runs are best-effort and can be hours late; the live overlay covers that.

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
  - Expected, policy and conservative quantities come from the residual band below (P50, the monthly policy level, and P90 of need).
  - `share` comes from the `PORTFOLIO_SHARE` env var (default `0.05`). It is a proxy for the client's own demand.
- **Solar is not subtracted.** National Demand already nets off embedded generation, and GB solar is mostly embedded.
- **Scope matters.** AGWS wind (which includes embedded wind) runs about 1.5 GW above FUELHH (transmission only) on average. WINDFOR tracks the wider scope. Correcting WINDFOR by its error against **FUELHH** re-scales it to the transmission wind that actually offsets National Demand. The backtest in the notebook checks this.

**Uncertainty: residual band, champion/challenger** (`monitor/risk.py`)
- Residual forecast `R̂ = NDF − WINDFOR(gate)`; actual `R = ND − FUELHH`; error `eR = R − R̂`.
- Purchase at quantile q: `share × max(0, R̂ + Q_q(eR))`. Expected = P50, conservative = P90 of need.
- Three models, each built for day D only from days ≤ D-2:
  - **wind-only:** the earlier method, mirrored to the residual.
  - **demand + wind:** trailing 30 days of eR.
  - **conditional:** trailing 60 days of eR, split by wind-forecast tercile × evening peak (16:00–19:30 UK). Falls back to demand + wind when a bucket has fewer than 100 samples.
- Each day uses the model with the lowest mean pinball loss (q = 0.1/0.5/0.9) over the trailing 90 days.

**£ cost and purchase policy**
- **Excess £ vs perfect hindsight**, per half-hour: `(need − buy) × (imbalance price − APX) × 0.5h`. Shortfalls settle at the imbalance price instead of the day-ahead price; surpluses are sold back at it.
- **Purchase policy:** for each month, the quantile in {P50…P95} that minimises `mean daily excess £ + λ × CVaR95`.
  - It is computed over **all** priced history before the month (an expanding window), and needs 180 days of it.
  - `λ` comes from the `RISK_AVERSION` env var (default `0.5`).
  - It is labelled **recommended** only when its own out-of-sample track record (at least 60 days) matches or beats always-P50 and always-P90 on that objective.
  - A rolling 90-day version was tried first and lost to both fixed rules, so it was replaced.

**Pre-gate flags** (each against its own trailing distribution)
- **Revision:** mean |WINDFOR at the gate − WINDFOR from a day earlier|, flagged above P90.
- **Ramp:** largest hourly change in the gate forecast, flagged above P90.
- **Margin:** NESO surplus margin, below P10. Shown as context only: tight-margin days were historically calmer.

## Key findings

Last 12 months (Oct 2025 → Oct 2026). The notebook reproduces these with the app's own code.

| | MAE | Bias | Notes |
|---|---|---|---|
| WINDFOR gate vs FUELHH | 1.80 GW | −1.45 GW | 20% of mean actual. January 2026 alone: MAE 2,123 MW, bias −1,840 MW |
| WINDFOR gate vs AGWS (wider scope) | 1.35 GW | **+0.04 GW** | so the "over-forecast" is a **scope difference**, not weather error |
| DGWS wind (day-ahead run, after gate) vs AGWS | 2.30 GW | −2.08 GW | published later than WINDFOR, yet less accurate |
| DGWS solar (day-ahead run) vs AGWS | 0.50 GW | −0.16 GW | 11.6% of mean daylight output |

**Error vs lead time.** Error grows slowly with lead time: in January 2026, MAE was 1.8 GW at 0 h and 2.4 GW at 48 h. The scope/bias effect dominates.

**Out-of-sample P10 band** (wind-only, as shown on the Wind accuracy tab).
- Actual wind sat at or above the conservative line in **88.4%** of hours (target 90%).
- The weakest month was Sep 2026 at 74%, when summer turned to autumn.


**Purchase decision (12 months, 5% share).**

| Model (out-of-sample) | Pinball | P10 / P50 / P90 coverage |
|---|---|---|
| Wind-only (previous) | 544 | 15.3% / 47.7% / 84.5% |
| Demand + wind | 535 | 10.3% / 49.0% / 88.1% |
| **Conditional** (used on every day) | **476** | 10.4% / 50.2% / 88.8% |

- **Need sizing:** P90-of-need covered the realised need on **95%** of 386 days, for an average buffer of about 2.7 GWh/day. The P50 quantity was off by MAE 1.3 GWh/day, with a bias of −0.1 GWh.
- **Prices barely react to this forecast error.** The correlation between residual error and (imbalance − APX) is **0.06**; the mean spread is £0.6/MWh.
- **The value of a buffer is tail-risk reduction, not average saving.** Across the year, P50 vs P90 differ by about 1% in total excess £ over the policy period. The policy currently settles on **P50** at λ = 0.5.
- **Flags:** ramp days had MAE 2.33 vs 1.93 GW and excess cost £6.9k vs £4.0k/day, so the ramp flag is useful. The revision flag is mildly useful. Tight-margin days were *cheaper* (£1.0k vs £4.7k/day).

**DGWS timing.** DGWS's day-ahead run lands at about 16:45 UTC on D-1, after the 11:00 gate. It can be scored for accuracy but not traded on. WINDFOR (09:30) and NDF (08:45) are the only inputs available at the gate.

**Data quality.** AGWS has gaps in summer 2026 (for example, about 28 of 48 periods per day in June). There is a DGWS solar outlier of about 25 GW forecast on 26 Apr 2026. It is real source data and has been kept.

Full analysis: [`notebooks/analysis.ipynb`](notebooks/analysis.ipynb).

## Run locally

```bash
# API
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PORTFOLIO_SHARE=0.05 RISK_AVERSION=0.5 .venv/bin/uvicorn api.index:app --port 8000

# Web (proxies /api to :8000)
cd web && npm install && npm run dev        # http://localhost:5173
```

**Docker:**

```bash
PORTFOLIO_SHARE=0.05 RISK_AVERSION=0.5 docker compose up --build   # web http://localhost:8080, api http://localhost:8000
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
| `GET /api/meta` | data range, `latestDecisionDay`, `lastRefresh`, `share`, `riskAversion` |
| `GET /api/decision?day=YYYY-MM-DD` | half-hourly `ndf, windFc, residualFc, buyExpected, buyRecommended, buyConservative`, plus actuals when known; `totals` (MWh); `bandModel`; `recommendation` (policy q, frontier, track record, `validated`); `flags`; `gate` |
| `GET /api/risk?start=YYYY-MM-DD&end=YYYY-MM-DD` | `frontier`, `models` (pinball, coverage, champion days), `cumulative` and `realised` excess £ per strategy, error-vs-spread `scatter` + `correlation`, flag track record |
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
- `vercel env add PORTFOLIO_SHARE production` and `vercel env add RISK_AVERSION production`
- Deployment Protection → Vercel Authentication on **all deployments** (Pro plan). Viewers must be members of the Vercel team, or use a shareable link.

Docker is for local and self-hosted runs only.

## Layout

```
api/index.py              FastAPI routes (the only Vercel function)
monitor/core.py           pure logic: selection, metrics, rolling bands, MW-to-buy
monitor/risk.py           residual bands, champion, £ backtest, purchase policy, flags
monitor/live.py           live Elexon top-up for tomorrow (5-minute cache, snapshot fallback)
monitor/data.py           loads the partitions
monitor/data/             Elexon snapshot (monthly .json.gz)
scripts/fetch_data.py     snapshot refresh / backfill / retention
tests/                    pytest (test_core.py, test_risk.py)
web/src/                  main.jsx (tabs), Decision.jsx, Risk.jsx, Accuracy.jsx, charts.jsx
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
