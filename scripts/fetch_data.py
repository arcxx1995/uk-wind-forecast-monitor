"""Pull January 2026 national wind actuals (FUELHH) and forecasts (WINDFOR) from Elexon BMRS
into api/data/*.json. Re-run to refresh the snapshot."""
import json
import urllib.request
from pathlib import Path

BASE = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
OUT = Path(__file__).resolve().parent.parent / "api" / "data"


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    actuals = get(f"{BASE}/FUELHH/stream?settlementDateFrom=2026-01-01&settlementDateTo=2026-01-31&fuelType=WIND")
    actuals = sorted(
        ({"t": r["startTime"], "mw": r["generation"]} for r in actuals if r["fuelType"] == "WIND"),
        key=lambda r: r["t"],
    )

    # Forecasts from Dec 29 so Jan 1 delivery has its D-1 gate forecast.
    forecasts = get(f"{BASE}/WINDFOR/stream?publishDateTimeFrom=2025-12-29T00:00:00Z&publishDateTimeTo=2026-02-01T00:00:00Z")
    forecasts = sorted(
        ({"p": r["publishTime"], "t": r["startTime"], "mw": r["generation"]} for r in forecasts),
        key=lambda r: (r["p"], r["t"]),
    )

    (OUT / "actuals.json").write_text(json.dumps(actuals, separators=(",", ":")))
    (OUT / "forecasts.json").write_text(json.dumps(forecasts, separators=(",", ":")))
    print(f"actuals={len(actuals)} forecasts={len(forecasts)}")


if __name__ == "__main__":
    main()
