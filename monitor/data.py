"""Load the Elexon snapshot partitions (monitor/data/<dataset>/<YYYY-MM>.json.gz) once per process.

Stored row schemas (compact lists, ISO-8601 UTC strings):
    fuelhh_wind     [t, mw]                    half-hourly wind actual
    demand_outturn  [t, mw]                    half-hourly National Demand outturn
    windfor         [publish, t, mw]           hourly wind forecast, every publish
    dgws            [publish, t, type, mw]     half-hourly day-ahead forecast, type in solar|wind_on|wind_off
    agws            [publish, t, type, mw]     half-hourly actual by type (revisions: latest publish wins)
    ndf_gate        [publish, t, mw]           half-hourly demand forecast, latest publish before the gate only
"""
import gzip
import json
from collections import defaultdict
from functools import cache
from pathlib import Path

from monitor.core import NIGHT_MW, hourly, index_forecasts, ts

DATA = Path(__file__).resolve().parent / "data"


def rows(ds):
    out = []
    for f in sorted((DATA / ds).glob("*.json.gz")):
        with gzip.open(f, "rt") as fh:
            out += json.load(fh)
    return out


def latest_revision(rs):
    """[publish, t, type, mw] -> {type: {t: mw}} keeping the latest publish per (t, type)."""
    best = {}
    for p, t, k, mw in rs:
        if (t, k) not in best or p > best[(t, k)][0]:
            best[(t, k)] = (p, mw)
    out = defaultdict(dict)
    for (t, k), (_, mw) in best.items():
        out[k][ts(t)] = mw
    return out


def dgws_index(rs, kinds, to_hourly):
    """Sum the given DGWS types per (publish, half-hour); optionally average to hourly per publish."""
    summed, seen = defaultdict(float), defaultdict(set)
    for p, t, k, mw in rs:
        if k in kinds:
            summed[(p, t)] += mw
            seen[(p, t)].add(k)
    hh = [(ts(p), ts(t), v) for (p, t), v in summed.items() if len(seen[(p, t)]) == len(kinds)]
    if not to_hourly:
        return index_forecasts(hh)
    buckets = defaultdict(list)
    for p, t, v in hh:
        buckets[(p, t.replace(minute=0))].append(v)
    return index_forecasts([(p, h, sum(v) / 2) for (p, h), v in buckets.items() if len(v) == 2])


@cache
def load():
    wind_hh = {ts(t): mw for t, mw in rows("fuelhh_wind")}
    wind_h = hourly(wind_hh)
    dgws = rows("dgws")
    agws = latest_revision(rows("agws"))
    # AGWS wind includes embedded wind (~1.5 GW above FUELHH on average), so DGWS wind is scored against its own scope.
    agws_wind = {t: mw + agws["wind_off"][t] for t, mw in agws["wind_on"].items() if t in agws["wind_off"]}
    meta = DATA / "_meta.json"
    return {
        "wind_hh": wind_hh,
        "nd_actual": {ts(t): mw for t, mw in rows("demand_outturn")},
        "ndf": index_forecasts([(ts(p), ts(t), mw) for p, t, mw in rows("ndf_gate")]),
        "lastRefresh": json.loads(meta.read_text())["lastRefresh"] if meta.exists() else None,
        # source -> (actuals, forecast index, night filter)
        "sources": {
            "windfor": (wind_h, index_forecasts([(ts(p), ts(t), mw) for p, t, mw in rows("windfor")]), None),
            "dgws_wind": (hourly(agws_wind), dgws_index(dgws, {"wind_on", "wind_off"}, to_hourly=True), None),
            "dgws_solar": (agws["solar"], dgws_index(dgws, {"solar"}, to_hourly=False), NIGHT_MW),
        },
    }
