# UK Wind Forecast Monitor

A web app for a UK utility's day-ahead trading desk:

1. Shows the **latest national wind forecast that was actually available at decision time**, i.e. before the GB day-ahead auction gate (11:00 UK, D-1).
2. **Quantifies forecast accuracy**, so the desk can size how much extra power to buy.

**Live:** https://uk-wind-forecast-monitor-smoky.vercel.app

Stack: React (Vite + Recharts) · Python (FastAPI) · Docker · Vercel.

## Data

Both datasets come from Elexon BMRS (national level, no other sources):

| Dataset | Endpoint | Notes |
|---|---|---|
| Actuals | `FUELHH/stream`, `fuelType=WIND` | 30-min `startTime`, `generation` (MW) |
| Forecasts | `WINDFOR/stream` | **hourly** `startTime`, `publishTime`, `generation` (MW). 8 publishes/day (≈02:30, 04:30, 07:30, 09:30, 11:30, 15:30, 18:30, 22:30 UTC), each reaching ≈D+2 21:00 |

Scope: delivery (target) times **1–31 January 2026**. Forecasts are fetched from 29 Dec, so 1 Jan has its D-1 forecast.

The data is a **bundled snapshot** (`api/data/*.json`, about 1.4 MB), so the app never depends on Elexon being up. To refresh it:

```bash
python3 scripts/fetch_data.py
```

## Method

- **Alignment.** Forecasts are hourly, so the actual for hour `HH:00` is the mean of the `HH:00` and `HH:30` half-hours. Incomplete hours are dropped.
- **Forecast selection** (toggle in the UI):
  - **Day-ahead gate** (default): every hour of delivery day D uses the latest publish at or before **11:00 on D-1**. In January this is always the 09:30 D-1 publish, with lead times of 14.5–37.5 h.
  - **Fixed lead time**: a slider H from 0 to 48 h. Each hour uses the latest publish at or before `target − H`.
- **Error = actual − forecast.** A negative error means wind under-delivered, so the shortfall must be bought.
- **Metrics:**
  - MAE, RMSE, bias, nMAE (% of mean actual), worst hour
  - P10/P50/P90 of error
  - error histogram
  - MAE by hour of day
  - bias by day
  - MAE/RMSE/bias vs lead time (0–48 h)
- **Uncertainty band.** The chart shades `forecast + [P10, P90]` of January's error for the selected mode or lead time. The dashed "conservative" line (forecast + P10 error, floored at 0) is the wind level met in about 90% of hours.
- **Timezone.** UK time = UTC in January. The gate cutoff is computed in UTC; see the `ponytail:` note in `api/index.py`. Switch to `zoneinfo("Europe/London")` before extending into BST.

## Key findings (January 2026, gate forecast)

| MAE | RMSE | Bias | P10 / P90 error | nMAE |
|---|---|---|---|---|
| 2,123 MW | 2,617 MW | **−1,840 MW** | −4,312 / +489 MW | 18.3% |

- The forecast **systematically over-predicts** delivered wind. Even at 0 h lead time, MAE is about 1.8 GW and nearly all of it is bias.
- The shortfall **scales with forecast level**: the actual/forecast ratio is about 0.9 below 16 GW and about 0.8 above 18 GW. Delivered output rarely passes about 17–18 GW.
  - This is consistent with constraint curtailment, or with a capacity-scope mismatch between WINDFOR and FUELHH. That is a hypothesis; it hasn't been verified.
- In 90% of hours, actual ≥ **0.70 × gate forecast** (equivalently, forecast − 4.3 GW).

See [`notebooks/analysis.ipynb`](notebooks/analysis.ipynb) for the full analysis.

**Caveat:** this is one month, and every quantile is in-sample. Validate on more months, out-of-sample, before using these buffers for real procurement.

## Run locally

```bash
# API
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn api.index:app --port 8000

# Web (proxies /api to :8000)
cd web && npm install && npm run dev        # http://localhost:5173
```

### Docker

```bash
docker compose up --build                   # web http://localhost:8080, api http://localhost:8000
```

`web` is a Vite build served by nginx, which proxies `/api` to the `api` container (uvicorn).

### Tests

```bash
.venv/bin/pip install pytest && .venv/bin/python -m pytest -q
```

The tests cover:
- half-hour → hourly averaging
- gate and lead-time forecast selection, including edge cases
- metric maths
- snapshot checks: the Jan 15 gate forecast uses the Jan 14 09:30 publish, and no lead-time selection ever peeks past its cutoff

### Notebook

```bash
.venv/bin/pip install pandas matplotlib jupyter && .venv/bin/jupyter lab notebooks/analysis.ipynb
```

## API

`GET /api/analysis?mode=gate|horizon&h=0..48&start=ISO&end=ISO`

The response contains:
- `points`: per hour, `t, actual, forecast, error, publishTime, leadH, lo, hi`
- `stats`: `core`, `percentiles`, `histogram`, `byHour`, `byDay`
- `band`: P10/P50/P90
- `horizonCurve`: covers all of January

## Deploy (Vercel)

```bash
vercel deploy            # preview
vercel deploy --prod     # production
```

`vercel.json` sets up the deploy:
- builds `web/` into static files
- serves `api/index.py` (FastAPI) as a Python function, with `api/data/**` bundled
- rewrites `/api/*` to that function

Docker is for local and self-hosted use only, because Vercel doesn't run containers.

## Layout

```
api/index.py            FastAPI app + all forecast-selection / metric logic
api/data/               Elexon snapshot (JSON)
scripts/fetch_data.py   snapshot refresh
tests/test_core.py      pytest
web/                    React app (src/main.jsx, src/charts.jsx)
notebooks/              analysis notebook
Dockerfile.api, web/Dockerfile, docker-compose.yml, vercel.json
```

## Out of scope / next steps

- **Demand data**, to turn conservative wind into a "MW to buy" figure
- other renewables (solar)
- live data refresh or scheduled ingestion
- out-of-sample (rolling) error bands
- authentication
- BST-aware gate time

## AI tools disclosure

This project was built with the help of **Claude Code** (Anthropic, model Claude Opus 5.5), an AI coding assistant. It was used to:
- research the Elexon datasets (publish cadence, resolution, coverage)
- scope the MVP through Q&A with the developer
- write the code, tests, notebook and this README
- deploy the app

Design decisions were confirmed by the developer. All metrics were computed from the real data and cross-checked between the API and the notebook.
