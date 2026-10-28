from datetime import date, timedelta

import pytest

from monitor import live
from monitor.core import half_hours, ts
from monitor.risk import Risk
from tests.test_risk import START, synthetic

D = START + timedelta(days=50)
GATE = ts(f"{D - timedelta(days=1)}T11:00:00Z")


@pytest.fixture(autouse=True)
def clear_cache():
    live._status.clear()


def fake(rows_ndf, rows_wind, calls):
    def fetcher(d, since):
        calls.append((d, since))
        return rows_ndf, rows_wind
    return fetcher


def test_live_rows_update_the_decision_but_post_gate_publishes_are_ignored():
    r = Risk(synthetic())
    newer, late = GATE - timedelta(minutes=30), GATE + timedelta(minutes=5)
    wind = [(newer, t, 12000) for t in half_hours(D) if t.minute == 0] + [(late, t, 99999) for t in half_hours(D) if t.minute == 0]
    st = live.refresh(r, D, None, fetcher=fake([], wind, []))
    assert st["ok"] and st["newRows"] == len(wind)
    assert {row["windFc"] for row in r.decision(D, 0.05, 0.5)["rows"]} == {12000}


def test_refresh_is_cached_for_ttl_and_starts_after_the_snapshot():
    r, calls, clock = Risk(synthetic()), [], [1000.0]
    last_refresh = f"{D - timedelta(days=1)}T09:45:00Z"
    live.refresh(r, D, last_refresh, fetcher=fake([], [], calls), now=lambda: clock[0])
    live.refresh(r, D, last_refresh, fetcher=fake([], [], calls), now=lambda: clock[0] + live.TTL - 1)
    assert len(calls) == 1 and calls[0][1] == ts(last_refresh) - timedelta(hours=1)
    live.refresh(r, D, last_refresh, fetcher=fake([], [], calls), now=lambda: clock[0] + live.TTL + 1)
    assert len(calls) == 2


def test_elexon_failure_keeps_the_snapshot():
    r = Risk(synthetic())
    before = r.decision(D, 0.05, 0.5)["totals"]

    def boom(d, since):
        raise OSError("timed out")
    st = live.refresh(r, D, None, fetcher=boom)
    assert not st["ok"] and "timed out" in st["error"]
    assert r.decision(D, 0.05, 0.5)["totals"] == before


def test_fetch_window_stops_at_the_gate(monkeypatch):
    urls = []
    monkeypatch.setattr(live, "get", lambda path: urls.append(path) or [])
    d = date(2026, 1, 15)  # in the past: until = gate
    live.fetch(d, ts("2026-01-14T07:00:00Z"))
    assert all("To=2026-01-14T11:00:00Z" in u or "To=2026-01-14T09:00:00Z" in u for u in urls if "NDF" in u)
    assert any(u.startswith("WINDFOR") and "publishDateTimeTo=2026-01-14T11:00:00Z" in u for u in urls)
    assert sum(u.startswith("NDF") for u in urls) == 2  # 07:00-09:00, 09:00-11:00
