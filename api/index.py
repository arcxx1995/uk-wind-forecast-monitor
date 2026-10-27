"""Wind forecast monitor API: forecast selection + accuracy metrics over the bundled Elexon snapshot.

Error convention: error = actual - forecast (negative => wind under-delivered, more power must be bought).
"""
import json
import math
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import fmean, quantiles

from fastapi import FastAPI, HTTPException, Query

DATA = Path(__file__).resolve().parent / "data"
GATE_HOUR = 11  # GB day-ahead auction gate closure, 11:00 UK on D-1
HIST_BIN = 500  # MW
MAX_H = 48


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def hourly_actuals(rows):
    """Average the :00 and :30 half-hours into an hourly value (forecasts are hourly)."""
    buckets = defaultdict(list)
    for r in rows:
        buckets[ts(r["t"]).replace(minute=0)].append(r["mw"])
    return {h: fmean(v) for h, v in buckets.items() if len(v) == 2}  # ponytail: drop incomplete hours


def index_forecasts(rows):
    """target time -> [(publish time, mw)] sorted by publish time."""
    idx = defaultdict(list)
    for r in rows:
        idx[ts(r["t"])].append((ts(r["p"]), r["mw"]))
    for v in idx.values():
        v.sort()
    return idx


def gate_cutoff(t):
    # ponytail: UK time == UTC in January; use zoneinfo("Europe/London") if the range ever spans BST.
    return datetime.combine(t.date() - timedelta(days=1), time(GATE_HOUR), tzinfo=timezone.utc)


def latest_before(cands, cutoff):
    i = bisect_right(cands, (cutoff, math.inf))
    return cands[i - 1] if i else None


def pairs(actuals, fidx, mode="gate", h=0, start=None, end=None):
    out = []
    for t in sorted(actuals):
        if (start and t < start) or (end and t > end):
            continue
        cutoff = gate_cutoff(t) if mode == "gate" else t - timedelta(hours=h)
        hit = latest_before(fidx.get(t, []), cutoff)
        if hit is None:
            continue
        p, f = hit
        a = actuals[t]
        out.append({
            "t": iso(t), "actual": round(a), "forecast": f, "error": round(a - f),
            "publishTime": iso(p), "leadH": round((t - p).total_seconds() / 3600, 1),
        })
    return out


def pct(errors):
    q = quantiles(errors, n=10, method="inclusive")
    return {"p10": round(q[0]), "p50": round(q[4]), "p90": round(q[8])}


def core_stats(ps):
    e = [p["error"] for p in ps]
    if not e:
        return None
    return {
        "n": len(e),
        "mae": round(fmean(abs(x) for x in e)),
        "rmse": round(math.sqrt(fmean(x * x for x in e))),
        "bias": round(fmean(e)),
        "nmaePct": round(100 * fmean(abs(x) for x in e) / fmean(p["actual"] for p in ps), 1),
        "maxAbs": max(abs(x) for x in e),
    }


def stats(ps):
    if len(ps) < 2:
        return None
    e = [p["error"] for p in ps]
    hist = Counter(math.floor(x / HIST_BIN) * HIST_BIN for x in e)
    by_hour, by_day = defaultdict(list), defaultdict(list)
    for p in ps:
        by_hour[int(p["t"][11:13])].append(p["error"])
        by_day[p["t"][:10]].append(p["error"])
    agg = lambda g: {"mae": round(fmean(abs(x) for x in g)), "bias": round(fmean(g))}
    return {
        "core": core_stats(ps),
        "percentiles": pct(e),
        "histogram": [{"bin": b, "count": hist[b]} for b in range(min(hist), max(hist) + HIST_BIN, HIST_BIN)],
        "byHour": [{"hour": k, **agg(v)} for k, v in sorted(by_hour.items())],
        "byDay": [{"day": k, **agg(v)} for k, v in sorted(by_day.items())],
    }


# ---- data (loaded once per process) ----
ACTUALS = hourly_actuals(json.loads((DATA / "actuals.json").read_text()))
FIDX = index_forecasts(json.loads((DATA / "forecasts.json").read_text()))


@lru_cache(maxsize=None)
def band(mode, h):
    """Error quantiles over all of January for this mode/horizon (in-sample by design)."""
    return pct([p["error"] for p in pairs(ACTUALS, FIDX, mode, h)])


@lru_cache(maxsize=1)
def horizon_curve():
    rows = []
    for h in range(MAX_H + 1):
        c = core_stats(pairs(ACTUALS, FIDX, "horizon", h))
        if c:
            rows.append({"h": h, "mae": c["mae"], "rmse": c["rmse"], "bias": c["bias"], "n": c["n"]})
    return rows


app = FastAPI(title="Wind Forecast Monitor")


@app.get("/api/analysis")
def analysis(
    mode: str = Query("gate", pattern="^(gate|horizon)$"),
    h: int = Query(4, ge=0, le=MAX_H),
    start: str = "2026-01-01T00:00:00Z",
    end: str = "2026-01-31T23:00:00Z",
):
    try:
        s, e = ts(start), ts(end)
    except ValueError:
        raise HTTPException(400, "start/end must be ISO-8601 datetimes")
    if s.tzinfo is None or e.tzinfo is None:
        raise HTTPException(400, "start/end must include a timezone (e.g. Z)")
    ps = pairs(ACTUALS, FIDX, mode, h, s, e)
    b = band(mode, h)
    for p in ps:
        p["lo"], p["hi"] = max(0, p["forecast"] + b["p10"]), p["forecast"] + b["p90"]  # wind output can't go negative
    return {"points": ps, "stats": stats(ps), "band": b, "horizonCurve": horizon_curve()}
