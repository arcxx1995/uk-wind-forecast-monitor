from datetime import date, timedelta

import pytest

from monitor.core import half_hours, index_forecasts, ts
from monitor.risk import QS, Risk, excess_cost, pinball, quantile

START = date(2026, 1, 1)
DAYS = 100


def synthetic(wind_err=lambda i: 1000 * (i % 5 - 2), late_wind=None, days=DAYS):
    """Flat demand 30 GW, wind forecast 10 GW, actual wind = forecast + a repeating error pattern."""
    ndf, wind_fc, nd, wind, mid, sip = [], [], {}, {}, {}, {}
    err_by_day = {}
    i = 0
    for k in range(days):
        d = START + timedelta(days=k)
        pub = ts(f"{d - timedelta(days=1)}T09:00:00Z")
        for t in half_hours(d):
            ndf.append((pub, t, 30000))
            if t.minute == 0:
                wind_fc.append((pub, t, 10000))
                if late_wind is not None:  # published after the gate: must never be used
                    wind_fc.append((ts(f"{d - timedelta(days=1)}T12:00:00Z"), t, late_wind))
            e = wind_err(i)
            nd[t], wind[t], mid[t], sip[t] = 30000, 10000 + e, 100.0, 100.0 + e / 100
            if t.minute == 0:
                err_by_day.setdefault(d.isoformat(), []).append(e)
            i += 1
    return {
        "ndf": index_forecasts(ndf), "sources": {"windfor": (None, index_forecasts(wind_fc), None)},
        "nd_actual": nd, "wind_hh": wind, "mid": mid, "sip": sip, "ocnmfd": {}, "wind_err_by_day": err_by_day,
    }


@pytest.fixture(scope="module")
def risk():
    return Risk(synthetic())


def test_quantile_and_pinball():
    assert quantile([1, 2, 3, 4, 5], 0.5) == 3 and quantile([0, 10], 0.25) == 2.5
    assert pinball(10, 0, 0.9) == 9 and pinball(-10, 0, 0.9) == pytest.approx(1)


def test_excess_cost_single_price():
    assert excess_cost(need=100, buy=80, sip=150, da=100) == 20 * 50 * 0.5  # short: pay SIP instead of DA
    assert excess_cost(need=80, buy=100, sip=150, da=100) == -20 * 50 * 0.5  # long: surplus resold above cost
    assert excess_cost(need=100, buy=80, sip=100, da=100) == 0  # SIP == DA: errors cost nothing


def test_residual_error_sign(risk):
    r = risk.rows_by_day[START][0]  # wind 2 GW below forecast -> more must be bought
    assert (r["rhat"], r["r"], r["e"]) == (20000, 22000, 2000)


def test_wind_only_band_is_mirrored_wind_error_quantile(risk):
    d = START + timedelta(days=40)
    wo, comb = risk.model_quantiles(d, "wind_only"), risk.model_quantiles(d, "combined")
    for q in QS:  # demand error is zero here, so eR = -wind error and both models must agree
        assert wo[q] == pytest.approx(comb[q])


def test_conditional_falls_back_to_combined_when_buckets_are_thin(risk):
    d = START + timedelta(days=40)
    cond = risk.model_quantiles(d, "conditional")
    # wind forecast is constant -> one tercile bucket gets everything; the empty ones fall back
    assert cond[(0, False)] == risk.model_quantiles(d, "combined")


def test_band_and_scores_ignore_d_minus_1(risk):
    d = START + timedelta(days=60)
    before = (risk.model_quantiles(d, "combined"), risk.scores(d))
    for r in risk.rows_by_day[d - timedelta(days=1)]:
        r["e"] = 10**9  # poison D-1: nothing for day D may change
    other = Risk(synthetic())
    assert (other.model_quantiles(d, "combined"), other.scores(d)) == before
    for r in risk.rows_by_day[d - timedelta(days=1)]:
        r["e"] = r["r"] - r["rhat"]


def test_frontier_higher_risk_aversion_buys_more():
    r = Risk.__new__(Risk)
    # q<0.9: cheaper on average (-10/day) but one day in 40 costs 300; q>=0.9: a steady 5/day
    days = [{q: ((300 if k == 0 else -10) if q < 0.9 else 5, 0, 48, 0) for q in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)}
            for k in range(40)]
    r.day_cost = lambda x, share: days[x]
    assert r.frontier(range(40), 0.05, lam=0)["q"] == 0.5  # mean only: -2.25 beats 5
    assert r.frontier(range(40), 0.05, lam=5)["q"] == 0.9  # tail-averse: CVaR95 145 vs 5


def test_decision_never_uses_post_gate_wind():
    r = Risk(synthetic(late_wind=99999))
    d = START + timedelta(days=50)
    dec = r.decision(d, 0.05, 0.5)
    assert {row["windFc"] for row in dec["rows"]} == {10000}
    assert dec["gate"] == f"{d - timedelta(days=1)}T11:00:00Z"
    assert dec["bandModel"]["name"] in ("wind_only", "combined", "conditional")
    t = dec["totals"]
    assert t["buyExpected"] <= t["buyConservative"]
    assert dec["recommendation"] is None and t["buyRecommended"] is None  # < 180 priced days: no policy yet


def test_policy_is_monthly_expanding_and_needs_history():
    r = Risk(synthetic(days=260))
    assert r.policy(date(2026, 6, 1), 0.05, 0.5) is None  # ~150 priced days before June: not enough
    p = r.policy(date(2026, 8, 1), 0.05, 0.5)
    assert p and p["q"] in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95) and p["rows"][0]["days"] >= 180
    # one level for the whole month, and the month's own days are never used
    assert {r.recommendation(date(2026, 8, k), 0.05, 0.5)["q"] for k in (1, 15, 31)} == {p["q"]}
    assert r.frontier([START + timedelta(days=i) for i in range((date(2026, 7, 30) - START).days + 1)], 0.05, 0.5)["rows"] == p["rows"]
