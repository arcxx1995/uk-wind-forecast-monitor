"""Refresh the Elexon BMRS snapshot in monitor/data/<dataset>/<YYYY-MM>.json.gz.

    python scripts/fetch_data.py                      # current + previous month (nightly job)
    python scripts/fetch_data.py --backfill           # last 12 months + pinned months
    python scripts/fetch_data.py --months 2026-01,2026-02

Rows are stored as compact lists (see monitor/data.py for the schema of each dataset).
Partitions older than 13 months are pruned, except PINNED (the original Jan 2026 brief).
"""
import argparse
import gzip
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor.core import gate_cutoff, ts  # noqa: E402

BASE = "https://data.elexon.co.uk/bmrs/api/v1"
OUT = ROOT / "monitor" / "data"
PINNED = {"2025-12", "2026-01"}
KEEP_MONTHS = 13
PSR = {"Solar": "solar", "Wind Onshore": "wind_on", "Wind Offshore": "wind_off"}


def get(path, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{BASE}/{path}", timeout=300) as r:
                return json.load(r)
        except OSError:  # URLError, timeouts, resets
            if i == tries - 1:
                raise
            time.sleep(5 * 2 ** i)


def month_bounds(m):
    start = date.fromisoformat(m + "-01")
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def z(d):
    return f"{d.isoformat()}T00:00:00Z"


def fetch_month(m):
    start, end = month_bounds(m)
    last = end - timedelta(days=1)
    pub = f"publishDateTimeFrom={z(start)}&publishDateTimeTo={z(end)}"
    sd = f"settlementDateFrom={start}&settlementDateTo={last}"

    out = {
        "fuelhh_wind": [[r["startTime"], r["generation"]]
                        for r in get(f"datasets/FUELHH/stream?{sd}&fuelType=WIND")],
        "demand_outturn": [[r["startTime"], r["initialDemandOutturn"]]
                           for r in get(f"demand/outturn/stream?{sd}")],
        "windfor": [[r["publishTime"], r["startTime"], r["generation"]]
                    for r in get(f"datasets/WINDFOR/stream?{pub}")],
        "dgws": [[r["publishTime"], r["startTime"], PSR[r["psrType"]], round(r["quantity"])]
                 for r in get(f"datasets/DGWS/stream?{pub}") if r["psrType"] in PSR],
        "agws": [[r["publishTime"], r["startTime"], PSR[r["psrType"]], round(r["quantity"])]
                 for r in get(f"datasets/AGWS/stream?{pub}") if r["psrType"] in PSR],
        "ndf_gate": fetch_ndf_gate(start, end),
    }
    return {k: sorted(v) for k, v in out.items()}


def fetch_ndf_gate(start, end):
    """NDF is republished every ~30 min (~85k rows/month), so fetch per day in parallel and keep only
    the latest publish strictly before each target's day-ahead gate.
    ponytail: drop the pruning if a demand horizon mode is ever needed."""
    days = [start - timedelta(days=2) + timedelta(days=i) for i in range((end - start).days + 2)]
    with ThreadPoolExecutor(8) as ex:
        chunks = ex.map(lambda d: get(f"datasets/NDF/stream?publishDateTimeFrom={z(d)}"
                                      f"&publishDateTimeTo={z(d + timedelta(days=1))}"), days)
    lo, hi = datetime.combine(start, datetime.min.time(), timezone.utc), datetime.combine(end, datetime.min.time(), timezone.utc)
    best = {}
    for r in (r for c in chunks for r in c if r.get("boundary", "N") == "N"):
        t, p = ts(r["startTime"]), ts(r["publishTime"])
        if lo <= t < hi and p < gate_cutoff(t) and (t not in best or p > ts(best[t][0])):
            best[t] = [r["publishTime"], r["startTime"], r["demand"]]
    return list(best.values())


def write(m, data):
    for ds, rows in data.items():
        (OUT / ds).mkdir(parents=True, exist_ok=True)
        with gzip.open(OUT / ds / f"{m}.json.gz", "wt") as f:
            json.dump(rows, f, separators=(",", ":"))
    print(m, {k: len(v) for k, v in data.items()}, flush=True)


def months_back(today, n):
    k = today.year * 12 + today.month - 1
    return [f"{(k - i) // 12}-{(k - i) % 12 + 1:02d}" for i in range(n)]


def prune(today):
    keep = set(months_back(today, KEEP_MONTHS)) | PINNED
    for f in OUT.glob("*/*.json.gz"):
        if f.name[:7] not in keep:
            f.unlink()
            print("pruned", f.relative_to(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", help="comma-separated YYYY-MM")
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    recent = months_back(today, KEEP_MONTHS)
    months = (args.months.split(",") if args.months
              else sorted(set(recent) | PINNED) if args.backfill
              else recent[:2])
    for m in months:
        write(m, fetch_month(m))
    prune(today)
    (OUT / "_meta.json").write_text(json.dumps({"lastRefresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))


if __name__ == "__main__":
    main()
