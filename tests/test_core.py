from datetime import datetime, timezone

from api.index import ACTUALS, FIDX, core_stats, hourly_actuals, index_forecasts, pairs, ts

UTC = timezone.utc


def test_hourly_actuals_averages_half_hours_and_drops_incomplete():
    rows = [
        {"t": "2026-01-01T00:00:00Z", "mw": 100},
        {"t": "2026-01-01T00:30:00Z", "mw": 200},
        {"t": "2026-01-01T01:00:00Z", "mw": 999},  # no :30 partner
    ]
    assert hourly_actuals(rows) == {datetime(2026, 1, 1, 0, tzinfo=UTC): 150}


FC = index_forecasts([
    {"p": "2026-01-14T07:30:00Z", "t": "2026-01-15T12:00:00Z", "mw": 1000},
    {"p": "2026-01-14T09:30:00Z", "t": "2026-01-15T12:00:00Z", "mw": 2000},
    {"p": "2026-01-14T11:30:00Z", "t": "2026-01-15T12:00:00Z", "mw": 3000},  # after gate
    {"p": "2026-01-15T09:30:00Z", "t": "2026-01-15T12:00:00Z", "mw": 4000},
])
ACT = {ts("2026-01-15T12:00:00Z"): 2500}


def test_gate_picks_latest_publish_before_d_minus_1_1100():
    [p] = pairs(ACT, FC, "gate")
    assert (p["publishTime"], p["forecast"], p["error"]) == ("2026-01-14T09:30:00Z", 2000, 500)


def test_horizon_picks_latest_publish_at_least_h_before_target():
    assert pairs(ACT, FC, "horizon", 2)[0]["forecast"] == 4000      # 09:30 <= 10:00
    assert pairs(ACT, FC, "horizon", 3)[0]["forecast"] == 3000      # cutoff 09:00 excludes 09:30
    assert pairs(ACT, FC, "horizon", 48) == []                      # nothing that early


def test_core_stats():
    ps = [{"error": 100, "actual": 1000}, {"error": -300, "actual": 1000}]
    c = core_stats(ps)
    assert (c["mae"], c["bias"], c["rmse"], c["nmaePct"], c["maxAbs"]) == (200, -100, 224, 20.0, 300)


def test_snapshot_gate_jan15_uses_jan14_0930_publish():
    ps = pairs(ACTUALS, FIDX, "gate", start=ts("2026-01-15T00:00:00Z"), end=ts("2026-01-15T23:00:00Z"))
    assert len(ps) == 24 and {p["publishTime"] for p in ps} == {"2026-01-14T09:30:00Z"}


def test_snapshot_horizon_never_uses_too_recent_publish():
    for h in (0, 4, 24):
        for p in pairs(ACTUALS, FIDX, "horizon", h):
            assert p["leadH"] >= h
