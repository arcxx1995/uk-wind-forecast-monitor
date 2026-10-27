from datetime import date, datetime, timezone

from monitor.core import (
    core_stats, decision, gate_cutoff, half_hours, hourly, index_forecasts, mw_to_buy, pairs, rolling_band, ts,
)

UTC = timezone.utc


def test_hourly_averages_half_hours_and_drops_incomplete():
    s = {ts("2026-01-01T00:00:00Z"): 100, ts("2026-01-01T00:30:00Z"): 200, ts("2026-01-01T01:00:00Z"): 999}
    assert hourly(s) == {datetime(2026, 1, 1, 0, tzinfo=UTC): 150}


def test_gate_cutoff_is_bst_aware():
    assert gate_cutoff(ts("2026-01-15T12:00:00Z")) == ts("2026-01-14T11:00:00Z")  # GMT
    assert gate_cutoff(ts("2026-07-15T12:00:00Z")) == ts("2026-07-14T10:00:00Z")  # BST
    # 23:30 UTC on 14 Jul is 00:30 BST on the 15th -> belongs to delivery day 15 Jul
    assert gate_cutoff(ts("2026-07-14T23:30:00Z")) == ts("2026-07-14T10:00:00Z")


def test_clock_change_days_have_46_and_50_periods():
    assert len(half_hours(date(2026, 3, 29))) == 46
    assert len(half_hours(date(2026, 10, 25))) == 50
    assert len(half_hours(date(2026, 1, 15))) == 48


T = ts("2026-01-15T12:00:00Z")
FC = index_forecasts([
    (ts("2026-01-14T07:30:00Z"), T, 1000),
    (ts("2026-01-14T09:30:00Z"), T, 2000),
    (ts("2026-01-14T11:00:00Z"), T, 2500),  # exactly at gate -> too late
    (ts("2026-01-15T09:30:00Z"), T, 4000),
])
ACT = {T: 2500}


def test_gate_is_strictly_before_1100_d_minus_1():
    [p] = pairs(ACT, FC, "gate")
    assert (p["publishTime"], p["forecast"], p["error"]) == ("2026-01-14T09:30:00Z", 2000, 500)


def test_horizon_picks_latest_publish_at_least_h_before_target():
    assert pairs(ACT, FC, "horizon", 2)[0]["forecast"] == 4000      # 09:30 <= 10:00
    assert pairs(ACT, FC, "horizon", 3)[0]["forecast"] == 2500      # cutoff 09:00 -> 11:00 D-1 run
    assert pairs(ACT, FC, "horizon", 48) == []


def test_dayahead_takes_latest_run_before_delivery_midnight():
    fc = index_forecasts([(ts("2026-01-14T16:45:00Z"), T, 3000), (ts("2026-01-15T07:00:00Z"), T, 3500)])
    assert pairs(ACT, fc, "gate") == []  # 16:45 D-1 is after the gate
    assert pairs(ACT, fc, "dayahead")[0]["forecast"] == 3000


def test_night_filter_drops_periods_where_both_are_near_zero():
    assert pairs({T: 10}, index_forecasts([(ts("2026-01-14T09:30:00Z"), T, 20)]), night_mw=50) == []
    assert len(pairs({T: 10}, index_forecasts([(ts("2026-01-14T09:30:00Z"), T, 80)]), night_mw=50)) == 1


def test_core_stats():
    c = core_stats([{"error": 100, "actual": 1000}, {"error": -300, "actual": 1000}])
    assert (c["mae"], c["bias"], c["rmse"], c["nmaePct"], c["maxAbs"]) == (200, -100, 224, 20.0, 300)


def test_rolling_band_uses_only_d_minus_31_to_d_minus_2():
    by_day = {f"2026-01-{d:02d}": [-1000] * 10 for d in range(1, 32)}
    by_day["2026-01-30"] = [999999] * 1000  # D-1 for 31 Jan: must be ignored
    by_day["2026-01-31"] = [999999] * 1000  # D itself: must be ignored
    b = rolling_band(by_day, date(2026, 1, 31))
    assert b == {"p10": -1000, "p50": -1000, "p90": -1000}
    assert rolling_band({}, date(2026, 1, 31)) is None  # too few errors


def test_mw_to_buy_scales_and_floors_at_zero():
    assert mw_to_buy(30000, 10000, 0.05) == 1000
    assert mw_to_buy(10000, 30000, 0.05) == 0
    assert mw_to_buy(None, 10000, 0.05) is None


def test_decision_uses_pre_gate_forecasts_and_band():
    d = date(2026, 1, 15)
    hh = half_hours(d)
    pre, late = ts("2026-01-14T09:00:00Z"), ts("2026-01-14T12:00:00Z")
    ndf = index_forecasts([(pre, t, 30000) for t in hh] + [(late, t, 99999) for t in hh])
    wind = index_forecasts([(pre, t.replace(minute=0), 10000) for t in hh])
    out = decision(d, ndf, wind, {"p10": -4000, "p50": -2000, "p90": 500}, 0.1, {}, {})
    r = out["rows"][0]
    assert (r["ndf"], r["windP50"], r["windP10"], r["buyExpected"], r["buyConservative"]) == (30000, 8000, 6000, 2200, 2400)
    assert out["totals"]["buyExpected"] == 2200 * 48 * 0.5 and out["totals"]["buyActual"] is None
    assert out["gate"] == "2026-01-14T11:00:00Z"
