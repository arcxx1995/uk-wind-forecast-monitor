"""Wind & solar forecast monitor API (FastAPI). Routes only; logic in monitor/core.py, data in monitor/data.py."""
import os
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Vercel runs this file directly
from monitor import core  # noqa: E402
from monitor.data import load  # noqa: E402

MAX_RANGE_DAYS = 400


def portfolio_share():
    try:
        share = float(os.environ.get("PORTFOLIO_SHARE", "0.05"))
    except ValueError:
        share = -1
    if not 0 < share <= 1:
        raise HTTPException(500, "PORTFOLIO_SHARE must be a fraction in (0, 1]")
    return share


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


def decision_for(d):
    data, share = load(), portfolio_share()
    return core.decision(d, data["ndf"], data["sources"]["windfor"][1], band("windfor", "gate", 0, d),
                         share, data["nd_actual"], data["wind_hh"])


def complete(dec):
    return all(r["ndf"] is not None and r["windFc"] is not None for r in dec["rows"])


app = FastAPI(title="Renewables Forecast Monitor")


@app.get("/api/meta")
def meta():
    data = load()
    actual_days = sorted(core.local_day(t) for t in data["wind_hh"])
    last = max(core.local_day(t) for t in data["ndf"])
    latest = next((d for d in (last - timedelta(days=i) for i in range(7)) if complete(decision_for(d))), None)
    return {
        "dataStart": actual_days[0].isoformat(), "dataEnd": actual_days[-1].isoformat(),
        "latestDecisionDay": latest and latest.isoformat(), "lastRefresh": data["lastRefresh"],
        "share": portfolio_share(),
    }


@app.get("/api/decision")
def decision(day: date):
    dec = decision_for(day)
    if not any(r["ndf"] is not None or r["windFc"] is not None for r in dec["rows"]):
        raise HTTPException(404, f"no pre-gate forecasts for {day}")
    return dec


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
