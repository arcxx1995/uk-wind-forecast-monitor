"""Live overlay: fetch tomorrow's latest pre-gate wind and demand forecasts straight from Elexon.

The stored snapshot (refreshed by a scheduled job) stays the source for history and backtests; this only tops up
the next delivery day, so the decision never waits on the job. Results are cached for TTL seconds per process,
and on any Elexon failure the app keeps serving the snapshot and says so.
"""
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from monitor.core import UTC, day_bounds, gate_cutoff, half_hours, iso, ts

BASE = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
TTL = 300  # seconds
LOOKBACK = timedelta(hours=26)  # publishes from D-2 ~09:00 onwards can still be the latest before the gate
CHUNK = timedelta(hours=2)  # NDF republishes every ~30 min: fetch in parallel 2-hour slices

_lock = threading.Lock()
_status = {}  # day -> status dict


def get(path, timeout=20):
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=timeout) as r:
        return json.load(r)


def z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(d, since):
    """(ndf_rows, wind_rows) as (publish, target, mw) for delivery day d, published in [since, min(now, gate))."""
    gate = gate_cutoff(half_hours(d)[0])
    until = min(datetime.now(UTC), gate)
    if since >= until:
        return [], []
    lo, hi = day_bounds(d)
    slices = []
    t = since
    while t < until:
        slices.append((t, min(t + CHUNK, until)))
        t += CHUNK
    with ThreadPoolExecutor(8) as ex:
        wind_job = ex.submit(get, f"WINDFOR/stream?publishDateTimeFrom={z(since)}&publishDateTimeTo={z(until)}")
        ndf_chunks = list(ex.map(lambda s: get(f"NDF/stream?publishDateTimeFrom={z(s[0])}&publishDateTimeTo={z(s[1])}"), slices))
        wind = wind_job.result()
    keep = lambda r: lo <= ts(r["startTime"]) < hi and ts(r["publishTime"]) < gate
    ndf_rows = [(ts(r["publishTime"]), ts(r["startTime"]), r["demand"])
                for c in ndf_chunks for r in c if r.get("boundary", "N") == "N" and keep(r)]
    wind_rows = [(ts(r["publishTime"]), ts(r["startTime"]), r["generation"]) for r in wind if keep(r)]
    return ndf_rows, wind_rows


def refresh(risk, d, last_refresh, fetcher=fetch, now=time.monotonic):
    """Top up day d from Elexon at most once per TTL; returns a status dict for the UI."""
    with _lock:
        st = _status.get(d)
        if st and now() - st["_at"] < TTL:
            return {k: v for k, v in st.items() if k != "_at"}
        gate = gate_cutoff(half_hours(d)[0])
        since = gate - LOOKBACK
        if last_refresh:
            since = max(since, ts(last_refresh) - timedelta(hours=1))  # the snapshot already holds older publishes
        try:
            ndf_rows, wind_rows = fetcher(d, since)
            risk.add_live(d, ndf_rows, wind_rows)
            st = {"ok": True, "fetchedAt": iso(datetime.now(UTC)), "newRows": len(ndf_rows) + len(wind_rows)}
        except Exception as e:  # network, HTTP or payload errors: keep serving the snapshot
            st = {"ok": False, "fetchedAt": iso(datetime.now(UTC)), "error": f"{type(e).__name__}: {e}"[:200]}
        _status[d] = {**st, "_at": now()}
        return st
