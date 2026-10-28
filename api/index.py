"""Wind & solar forecast monitor API (FastAPI). Routes only; logic in monitor/core.py + monitor/risk.py, data in monitor/data.py."""
import math
import os
import sys
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Vercel runs this file directly
from monitor import core  # noqa: E402
from monitor.data import load  # noqa: E402
from monitor import live  # noqa: E402
from monitor.risk import Risk  # noqa: E402

MAX_RANGE_DAYS = 400


def env_number(name, default, lo, hi, lo_open=False):
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        v = math.nan
    if not ((lo < v) if lo_open else (lo <= v)) or not v <= hi:
        raise HTTPException(500, f"{name} must be a number in {'(' if lo_open else '['}{lo}, {hi}]")
    return v


def portfolio_share():
    return env_number("PORTFOLIO_SHARE", "0.05", 0, 1, lo_open=True)


def risk_aversion():
    """lambda in: recommended quantile minimises mean daily excess cost + lambda x CVaR95."""
    return env_number("RISK_AVERSION", "0.5", 0, 10)


@lru_cache(maxsize=1)
def risk():
    return Risk(load())


@lru_cache(maxsize=None)
def by_day(source, mode, h):
    actuals, fidx, night = load()["sources"][source]
    return core.errors_by_day(core.pairs(actuals, fidx, mode, h, night_mw=night))


@lru_cache(maxsize=None)
def band(source, mode, h, day):
    return core.rolling_band(by_day(source, mode, h), day)


@lru_cache(maxsize=32)
def horizon_curve(source, start, end):
    actuals, fidx, night = load()["sources"][source]
    return core.horizon_curve(actuals, fidx, start, end, night)


def parse_range(start, end):
    try:
        s, e = core.ts(start), core.ts(end)
    except ValueError:
        raise HTTPException(400, "start/end must be ISO-8601 datetimes")
    if s.tzinfo is None or e.tzinfo is None:
        raise HTTPException(400, "start/end must include a timezone (e.g. Z)")
    if not timedelta(0) <= e - s <= timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(400, f"end must be after start, at most {MAX_RANGE_DAYS} days")
    return s, e


def next_delivery_day():
    """The desk always decides for tomorrow (London), at today's 11:00 gate."""
    return datetime.now(core.UTC).astimezone(core.LONDON).date() + timedelta(days=1)


def complete(d):
    """Every half-hour of day d has both a pre-gate demand and wind forecast."""
    return len(risk().rows_by_day.get(d, ())) == len(core.half_hours(d))


app = FastAPI(title="Renewables Forecast Monitor")


@app.get("/api/meta")
def meta():
    data = load()
    now = datetime.now(core.UTC)
    nxt = next_delivery_day()
    gate = core.gate_cutoff(core.half_hours(nxt)[0])
    live_status = live.refresh(risk(), nxt, data["lastRefresh"])  # top up tomorrow before reporting coverage
    actual_days = sorted(core.local_day(t) for t in data["wind_hh"])
    last = max(core.local_day(t) for t in data["ndf"])
    latest = next((d for d in (last - timedelta(days=i) for i in range(7)) if complete(d)), None)
    return {
        "dataStart": actual_days[0].isoformat(), "dataEnd": actual_days[-1].isoformat(),
        "nextDeliveryDay": nxt.isoformat(), "nextGate": core.iso(gate), "gateClosed": now >= gate,
        "nextHalfHours": len(core.half_hours(nxt)), "nextCovered": len(risk().rows_by_day.get(nxt, ())),
        "latestDecisionDay": latest and latest.isoformat(), "lastRefresh": data["lastRefresh"], "live": live_status,
        "share": portfolio_share(), "riskAversion": risk_aversion(),
    }


@app.get("/api/decision")
def decision(day: date):
    live_status = live.refresh(risk(), day, load()["lastRefresh"]) if day == next_delivery_day() else None
    if not risk().rows_by_day.get(day):
        raise HTTPException(404, f"no pre-gate forecasts for {day}")
    return {**risk().decision(day, portfolio_share(), risk_aversion()), "live": live_status}


@app.get("/api/risk")
def risk_report(start: date, end: date):
    if not timedelta(0) <= end - start <= timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(400, f"end must be after start, at most {MAX_RANGE_DAYS} days")
    return {"share": portfolio_share(), "riskAversion": risk_aversion(),
            **risk().report(start, end, portfolio_share(), risk_aversion())}


@app.get("/api/accuracy")
def accuracy(
    source: str = Query("windfor", pattern="^(windfor|dgws_wind|dgws_solar)$"),
    mode: str = Query("gate", pattern="^(gate|horizon)$"),
    h: int = Query(4, ge=0, le=core.MAX_H),
    start: str = "2026-01-01T00:00:00Z",
    end: str = "2026-01-31T23:30:00Z",
):
    s, e = parse_range(start, end)
    h = h if mode == "horizon" else 0
    # DGWS's day-ahead run is published after the gate: evaluate it as "dayahead" and flag it in the UI.
    sel = "dayahead" if mode == "gate" and source.startswith("dgws") else mode
    actuals, fidx, night = load()["sources"][source]
    ps = core.pairs(actuals, fidx, sel, h, s, e, night)
    for p in ps:
        b = band(source, sel, h, date.fromisoformat(p["day"]))
        p["lo"], p["hi"] = (max(0, p["forecast"] + b["p10"]), p["forecast"] + b["p90"]) if b else (None, None)
    return {"selection": sel, "points": ps, "stats": core.stats(ps, 100 if source == "dgws_solar" else core.HIST_BIN), "horizonCurve": horizon_curve(source, s, e)}
