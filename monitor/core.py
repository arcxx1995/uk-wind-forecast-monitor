"""Pure logic: forecast selection, accuracy metrics, rolling error bands, MW-to-buy.

Error convention: error = actual - forecast (negative => under-delivered, more power must be bought).
All datetimes are tz-aware UTC; "delivery day" is the Europe/London local date.
"""
import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from statistics import fmean, quantiles
from zoneinfo import ZoneInfo

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")
GATE = time(11)  # GB day-ahead auction gate closure, 11:00 UK on D-1
HIST_BIN = 500  # MW
MAX_H = 48
BAND_WINDOW = 30  # days of errors behind each delivery day's band
BAND_MIN_N = 100  # ponytail: fewer errors than this => no band (quantiles too noisy)
NIGHT_MW = 50  # solar: skip periods where both actual and forecast are below this


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day(t):
    return t.astimezone(LONDON).date()


def day_bounds(d):
    """UTC [start, end) of a London local day (23h/25h on clock-change days)."""
    nxt = d + timedelta(days=1)
    return (datetime.combine(d, time(0), LONDON).astimezone(UTC),
            datetime.combine(nxt, time(0), LONDON).astimezone(UTC))


def gate_cutoff(t):
    """11:00 London time on the day before t's delivery day, as UTC."""
    return datetime.combine(local_day(t) - timedelta(days=1), GATE, LONDON).astimezone(UTC)


def hourly(series):
    """Average :00 and :30 half-hours into hourly values (WINDFOR is hourly)."""
    buckets = defaultdict(list)
    for t, mw in series.items():
        buckets[t.replace(minute=0)].append(mw)
    return {h: fmean(v) for h, v in buckets.items() if len(v) == 2}  # ponytail: drop incomplete hours


def index_forecasts(rows):
    """[(publish, target, mw)] -> {target: [(publish, mw)] sorted by publish}."""
    idx = defaultdict(list)
    for p, t, mw in rows:
        idx[t].append((p, mw))
    for v in idx.values():
        v.sort()
    return dict(idx)


def latest_before(cands, cutoff, strict=False):
    i = bisect_left(cands, (cutoff, -math.inf)) if strict else bisect_right(cands, (cutoff, math.inf))
    return cands[i - 1] if i else None


def select(fidx, t, mode="gate", h=0):
    """gate: latest publish strictly before D-1 11:00.
    dayahead: latest publish strictly before delivery-day midnight (DGWS's day-ahead run lands ~16:45 D-1,
    after the gate, so it can be evaluated but not traded on).
    horizon: latest publish at or before t - h."""
    if mode == "gate":
        return latest_before(fidx.get(t, []), gate_cutoff(t), strict=True)
    if mode == "dayahead":
        return latest_before(fidx.get(t, []), day_bounds(local_day(t))[0], strict=True)
    return latest_before(fidx.get(t, []), t - timedelta(hours=h))


def pairs(actuals, fidx, mode="gate", h=0, start=None, end=None, night_mw=None):
    out = []
    for t in sorted(actuals):
        if (start and t < start) or (end and t > end):
            continue
        hit = select(fidx, t, mode, h)
        if hit is None:
            continue
        p, f = hit
        a = actuals[t]
        if night_mw is not None and a < night_mw and f < night_mw:
            continue
        out.append({
            "t": iso(t), "day": local_day(t).isoformat(), "actual": round(a), "forecast": round(f),
            "error": round(a - f), "publishTime": iso(p), "leadH": round((t - p).total_seconds() / 3600, 1),
        })
    return out


def pct(errors):
    if len(errors) < 2:
        return None
    q = quantiles(errors, n=10, method="inclusive")
    return {"p10": round(q[0]), "p50": round(q[4]), "p90": round(q[8])}


def core_stats(ps):
    e = [p["error"] for p in ps]
    if not e:
        return None
    mean_actual = fmean(p["actual"] for p in ps)
    return {
        "n": len(e),
        "mae": round(fmean(abs(x) for x in e)),
        "rmse": round(math.sqrt(fmean(x * x for x in e))),
        "bias": round(fmean(e)),
        "nmaePct": round(100 * fmean(abs(x) for x in e) / mean_actual, 1) if mean_actual else None,
        "maxAbs": max(abs(x) for x in e),
    }


def stats(ps, hist_bin=HIST_BIN):
    if len(ps) < 2:
        return None
    e = [p["error"] for p in ps]
    hist = Counter(math.floor(x / hist_bin) * hist_bin for x in e)
    by_hour, by_day = defaultdict(list), defaultdict(list)
    for p in ps:
        by_hour[ts(p["t"]).astimezone(LONDON).hour].append(p["error"])
        by_day[p["day"]].append(p["error"])
    agg = lambda g: {"mae": round(fmean(abs(x) for x in g)), "bias": round(fmean(g))}
    return {
        "core": core_stats(ps),
        "percentiles": pct(e),
        "histBin": hist_bin,
        "histogram": [{"bin": b, "count": hist[b]} for b in range(min(hist), max(hist) + hist_bin, hist_bin)],
        "byHour": [{"hour": k, **agg(v)} for k, v in sorted(by_hour.items())],
        "byDay": [{"day": k, **agg(v)} for k, v in sorted(by_day.items())],
    }


def errors_by_day(ps):
    out = defaultdict(list)
    for p in ps:
        out[p["day"]].append(p["error"])
    return out


def rolling_band(by_day, day, window=BAND_WINDOW):
    """Error quantiles from local days D-window-1 .. D-2: only actuals fully known at the D-1 gate."""
    errs = [e for i in range(2, window + 2) for e in by_day.get((day - timedelta(days=i)).isoformat(), ())]
    return pct(errs) if len(errs) >= BAND_MIN_N else None


def horizon_curve(actuals, fidx, start, end, night_mw=None):
    rows = []
    for h in range(MAX_H + 1):
        c = core_stats(pairs(actuals, fidx, "horizon", h, start, end, night_mw))
        if c:
            rows.append({"h": h, "mae": c["mae"], "rmse": c["rmse"], "bias": c["bias"], "n": c["n"]})
    # ponytail: long leads exist only for a few early publishes; under 25% of the h=0 sample is too thin to plot.
    return [r for r in rows if r["n"] >= 0.25 * rows[0]["n"]] if rows else rows


def half_hours(d):
    s, e = day_bounds(d)
    return [s + timedelta(minutes=30 * i) for i in range(int((e - s).total_seconds() // 1800))]


def mw_to_buy(demand, wind, share):
    """Client's residual need: its share of national demand minus its share of wind, floored at 0.
    Solar is deliberately not subtracted: National Demand already nets off (mostly embedded) solar."""
    if demand is None or wind is None:
        return None
    return round(share * max(0.0, demand - wind))
