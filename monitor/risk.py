"""Purchase-decision risk: residual uncertainty models, £ cost backtest, risk-adjusted recommendation, flags.

Residual (national, per half-hour):  forecast R^ = NDF - WINDFOR(gate),  actual R = ND - FUELHH wind,
error eR = R - R^  (positive => more had to be bought than forecast).

Everything for delivery day D uses only days <= D-2 (fully known at the D-1 gate) - out-of-sample by construction.
"""
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from statistics import fmean

from monitor.core import LONDON, gate_cutoff, half_hours, iso, latest_before, local_day, mw_to_buy, select

QS = (0.1, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)  # quantiles every model produces
BUY_QS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)  # candidate purchase levels (quantile of need)
PINBALL_QS = (0.1, 0.5, 0.9)
MODELS = ("wind_only", "combined", "conditional")
MIN_N = 100  # ponytail: fewer samples than this => fall back / no estimate
PEAK_HOURS = range(16, 20)  # UK-time evening peak, 16:00-19:30 starts
WIN = {"band": 30, "conditional": 60, "eval": 90, "margin": 365}
MIN_ROWS = 40  # a day needs this many complete half-hours to count in cost backtests
POLICY_MIN_DAYS = 180  # priced days before a purchase policy is shown at all
POLICY_MIN_TRACK = 60  # out-of-sample policy days before it can be called "recommended"


def quantile(sorted_vals, q):
    """Linear-interpolated quantile of an already-sorted list (same as statistics 'inclusive')."""
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def pinball(y, qhat, q):
    d = y - qhat
    return max(q * d, (q - 1) * d)


def excess_cost(need, buy, sip, da):
    """£ lost vs perfect hindsight for one half-hour under a single imbalance price:
    a shortfall is bought at SIP instead of DA; a surplus is sold back at SIP. MW -> MWh via 0.5h."""
    return (need - buy) * (sip - da) * 0.5


def cvar(values, alpha=0.95):
    """Mean of the worst (1 - alpha) share of values (at least one)."""
    worst = sorted(values, reverse=True)[:max(1, math.ceil(len(values) * (1 - alpha)))]
    return fmean(worst)


def days_back(d, lo, hi):
    """Days d-hi .. d-lo inclusive."""
    return [d - timedelta(days=i) for i in range(lo, hi + 1)]


class Risk:
    def __init__(self, data):
        self.ndf = data["ndf"]
        self.wind_idx = data["sources"]["windfor"][1]
        self.nd, self.wind = data["nd_actual"], data["wind_hh"]
        self.da, self.sip = data.get("mid", {}), data.get("sip", {})
        self.margin_idx = data.get("ocnmfd", {})
        self.wind_err_by_day = data["wind_err_by_day"]  # {iso day: [hourly WINDFOR gate errors]}
        self.rows_by_day = defaultdict(list)
        for t in sorted(self.ndf):
            n, w = select(self.ndf, t), select(self.wind_idx, t.replace(minute=0))
            if not n or not w:
                continue
            rhat = n[1] - w[1]
            a_nd, a_w = self.nd.get(t), self.wind.get(t)
            r = a_nd - a_w if a_nd is not None and a_w is not None else None
            self.rows_by_day[local_day(t)].append({
                "t": t, "rhat": rhat, "r": r, "e": None if r is None else r - rhat, "wfc": w[1],
                "peak": t.astimezone(LONDON).hour in PEAK_HOURS, "ndf": n[1], "ndfP": n[0], "windP": w[0],
            })

    # ---------- uncertainty models ----------
    def samples(self, d, window):
        return [r for x in days_back(d, 2, window + 1) for r in self.rows_by_day.get(x, ()) if r["e"] is not None]

    @lru_cache(maxsize=None)
    def model_quantiles(self, d, model):
        """{q: value} for unconditional models, or {bucket: {q: value}} for 'conditional'. None if too little data."""
        if model == "wind_only":
            errs = sorted(e for x in days_back(d, 2, WIN["band"] + 1) for e in self.wind_err_by_day.get(x.isoformat(), ()))
            return {q: -quantile(errs, 1 - q) for q in QS} if len(errs) >= MIN_N else None
        if model == "combined":
            es = sorted(r["e"] for r in self.samples(d, WIN["band"]))
            return {q: quantile(es, q) for q in QS} if len(es) >= MIN_N else None
        rows = self.samples(d, WIN["conditional"])
        if len(rows) < MIN_N:
            return None
        w = sorted(r["wfc"] for r in rows)
        edges = (quantile(w, 1 / 3), quantile(w, 2 / 3))
        buckets = defaultdict(list)
        for r in rows:
            buckets[(bisect_right(edges, r["wfc"]), r["peak"])].append(r["e"])
        fallback = self.model_quantiles(d, "combined")
        out = {"edges": edges}
        for b in [(i, p) for i in range(3) for p in (False, True)]:
            es = sorted(buckets.get(b, ()))
            out[b] = {q: quantile(es, q) for q in QS} if len(es) >= MIN_N else fallback
        return out

    def q_for(self, d, model, row):
        mq = self.model_quantiles(d, model)
        if mq is None or model != "conditional":
            return mq
        return mq[(bisect_right(mq["edges"], row["wfc"]), row["peak"])]

    # ---------- out-of-sample evaluation & champion ----------
    @lru_cache(maxsize=None)
    def day_eval(self, d, model):
        """Mean pinball loss and coverage of day d's own out-of-sample quantiles; None if not evaluable."""
        rows = [r for r in self.rows_by_day.get(d, ()) if r["e"] is not None]
        if not rows or self.model_quantiles(d, model) is None:
            return None
        pb, cov = [], {q: 0 for q in QS}
        for r in rows:
            qs = self.q_for(d, model, r)
            pb.append(fmean(pinball(r["e"], qs[q], q) for q in PINBALL_QS))
            for q in QS:
                cov[q] += r["e"] <= qs[q]
        return {"pinball": fmean(pb), "n": len(rows), "coverage": {q: c / len(rows) for q, c in cov.items()}}

    @lru_cache(maxsize=None)
    def scores(self, d):
        """Trailing-90-day mean pinball per model, using only days <= D-2."""
        out = {}
        for m in MODELS:
            evs = [e for x in days_back(d, 2, WIN["eval"] + 1) if (e := self.day_eval(x, m))]
            out[m] = round(fmean(e["pinball"] for e in evs)) if len(evs) >= 7 else None
        return out

    @lru_cache(maxsize=None)
    def champion(self, d):
        s = {m: v for m, v in self.scores(d).items() if v is not None and self.model_quantiles(d, m)}
        if s:
            return min(s, key=s.get)
        return next((m for m in ("combined", "wind_only") if self.model_quantiles(d, m)), None)

    # ---------- £ cost backtest & recommendation ----------
    @lru_cache(maxsize=None)
    def day_cost(self, d, share):
        """{q: (excess £, short half-hours, half-hours, short MWh)} for day d bought with its champion band, or None."""
        m = self.champion(d)
        rows = [r for r in self.rows_by_day.get(d, ()) if r["r"] is not None and r["t"] in self.sip and r["t"] in self.da]
        if m is None or len(rows) < MIN_ROWS:
            return None
        out = {}
        for q in BUY_QS:
            cost = short = short_mwh = 0.0
            for r in rows:
                need = mw_to_buy(r["r"], 0, share)
                buy = mw_to_buy(r["rhat"] + self.q_for(d, m, r)[q], 0, share)
                cost += excess_cost(need, buy, self.sip[r["t"]], self.da[r["t"]])
                short += need > buy
                short_mwh += max(0, need - buy) * 0.5
            out[q] = (cost, short, len(rows), short_mwh)
        return out

    def frontier(self, days, share, lam):
        """Per candidate quantile over the given days: mean / CVaR95 daily excess £, P(short), mean short MWh/day."""
        costs = [c for x in days if (c := self.day_cost(x, share))]
        if len(costs) < 14:
            return None
        rows = []
        for q in BUY_QS:
            daily = [c[q][0] for c in costs]
            rows.append({
                "q": q, "meanCost": round(fmean(daily)), "cvar95": round(cvar(daily)),
                "pShort": round(sum(c[q][1] for c in costs) / sum(c[q][2] for c in costs), 3),
                "shortMWh": round(fmean(c[q][3] for c in costs)), "days": len(costs),
            })
        best = min(rows, key=lambda r: r["meanCost"] + lam * r["cvar95"])
        return {"rows": rows, "q": best["q"], "lambda": lam}

    @lru_cache(maxsize=None)
    def policy(self, month, share, lam):
        """Purchase level for a whole month: q* over ALL priced history up to month start - 2 days (expanding window).
        A rolling 90-day choice proved too noisy (CVaR95 of ~5 days) and lost to fixed rules out-of-sample.
        'validated' only when earlier months' policies beat fixed P50 and P90 on the same objective."""
        first = min(self.rows_by_day)
        days = [first + timedelta(days=i) for i in range((month - timedelta(days=2) - first).days + 1)]
        f = self.frontier(days, share, lam)
        if not f or f["rows"][0]["days"] < POLICY_MIN_DAYS:
            return None
        daily = defaultdict(list)
        for x in days:
            c, p = self.day_cost(x, share), self.policy(x.replace(day=1), share, lam)
            if c and p:
                for k, q in (("policy", p["q"]), ("p50", 0.5), ("p90", 0.9)):
                    daily[k].append(c[q][0])
        n = len(daily["policy"])
        objective = {k: round(fmean(v) + lam * cvar(v)) for k, v in daily.items()} if n >= POLICY_MIN_TRACK else None
        return {**f, "month": month.isoformat(), "trackDays": n, "objective": objective,
                "validated": bool(objective) and objective["policy"] <= min(objective["p50"], objective["p90"])}

    def recommendation(self, d, share, lam):
        return self.policy(d.replace(day=1), share, lam)

    # ---------- risk flags ----------
    @lru_cache(maxsize=None)
    def day_signals(self, d):
        """Pre-gate signals for day d: forecast revision vs a day earlier, max hourly ramp, margin surplus."""
        hours = sorted({t.replace(minute=0) for t in half_hours(d)})
        gate = gate_cutoff(hours[0])
        fc = {h: w[1] for h in hours if (w := select(self.wind_idx, h))}
        older = {h: w[1] for h in hours if (w := latest_before(self.wind_idx.get(h, []), gate - timedelta(hours=24), strict=True))}
        common = [h for h in hours if h in fc and h in older]
        seq = [fc[h] for h in hours if h in fc]
        m = latest_before(self.margin_idx.get(d.isoformat(), []), gate, strict=True)
        return {
            "revision": round(fmean(abs(fc[h] - older[h]) for h in common)) if common else None,
            "ramp": max((abs(b - a) for a, b in zip(seq, seq[1:])), default=None),
            "margin": m[1] if m else None,
        }

    def flags(self, d):
        """Signals for day d compared with their own trailing distribution (P90 high, or P10 low for margin)."""
        sig = self.day_signals(d)
        out = []
        # margin is context only: tight-margin days proved calmer and cheaper in the backtest
        for key, label, window, tail, kind in (("revision", "Forecast revised a lot vs a day earlier", WIN["eval"], "high", "warning"),
                                               ("ramp", "Large hourly wind ramp", WIN["eval"], "high", "warning"),
                                               ("margin", "Tight system margin (NESO surplus)", WIN["margin"], "low", "info")):
            hist = sorted(v for x in days_back(d, 1, window) if (v := self.day_signals(x)[key]) is not None)
            if sig[key] is None or len(hist) < 30:
                continue
            thr = quantile(hist, 0.9 if tail == "high" else 0.1)
            on = sig[key] > thr if tail == "high" else sig[key] < thr
            out.append({"key": key, "label": label, "kind": kind, "value": sig[key], "threshold": round(thr), "flagged": on})
        return out

    def coverage90(self, d, m):
        """Share of the last 90 days' half-hours (up to D-2) where need stayed at or below model m's P90."""
        evs = [e for x in days_back(d, 2, WIN["eval"] + 1) if m and (e := self.day_eval(x, m))]
        n = sum(e["n"] for e in evs)
        return round(sum(e["coverage"][0.9] * e["n"] for e in evs) / n, 3) if n else None

    # ---------- decision table ----------
    def decision(self, d, share, lam):
        m = self.champion(d)
        rec = self.recommendation(d, share, lam)
        q_rec = rec["q"] if rec else None
        rows = []
        by_t = {r["t"]: r for r in self.rows_by_day.get(d, ())}
        for t in half_hours(d):
            r = by_t.get(t)
            qs = self.q_for(d, m, r) if r and m else None
            buy = lambda q: mw_to_buy(r["rhat"] + (qs[q] if qs else 0), 0, share) if r and q else None
            a_nd, a_w = self.nd.get(t), self.wind.get(t)
            w = select(self.wind_idx, t.replace(minute=0))
            rows.append({
                "t": iso(t), "ndf": r["ndf"] if r else None, "windFc": w[1] if w else None,
                "residualFc": r["rhat"] if r else None,
                "buyExpected": buy(0.5), "buyRecommended": buy(q_rec), "buyConservative": buy(0.9),
                "ndActual": a_nd, "windActual": a_w, "buyActual": mw_to_buy(a_nd, a_w, share),
                "ndfPublish": iso(r["ndfP"]) if r else None, "windPublish": iso(w[0]) if w else None,
            })
        mwh = lambda k: round(sum(r[k] for r in rows) * 0.5) if all(r[k] is not None for r in rows) else None
        return {
            "day": d.isoformat(), "share": share, "gate": iso(gate_cutoff(half_hours(d)[0])),
            "bandModel": {"name": m, "scores": self.scores(d), "coverage90": self.coverage90(d, m)},
            "recommendation": rec, "flags": self.flags(d), "rows": rows,
            "totals": {k: mwh(k) for k in ("buyExpected", "buyRecommended", "buyConservative", "buyActual")},
        }

    # ---------- backtest report ----------
    def report(self, start, end, share, lam):
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        evals = {m: [e for x in days if (e := self.day_eval(x, m))] for m in MODELS}
        models = []
        for m, evs in evals.items():
            if not evs:
                continue
            n = sum(e["n"] for e in evs)
            models.append({
                "model": m, "pinball": round(sum(e["pinball"] * e["n"] for e in evs) / n), "days": len(evs),
                "coverage": {str(q): round(sum(e["coverage"][q] * e["n"] for e in evs) / n, 3) for q in QS},
                "championDays": sum(self.champion(x) == m for x in days),
            })
        cum, totals, daily = [], {}, defaultdict(list)
        for x in days:
            # each day is bought at the quantile its own trailing window would have recommended
            c, rec = self.day_cost(x, share), self.recommendation(x, share, lam)
            if not c or not rec:
                continue
            for key, q in (("p50", 0.5), ("recommended", rec["q"]), ("p90", 0.9)):
                totals[key] = totals.get(key, 0) + c[q][0]
                daily[key].append(c[q][0])
            cum.append({"day": x.isoformat(), "q": rec["q"], **{k: round(v) for k, v in totals.items()}})
        realised = {k: {"total": round(sum(v)), "meanDaily": round(fmean(v)), "cvar95": round(cvar(v)),
                        "objective": round(fmean(v) + lam * cvar(v)), "days": len(v)} for k, v in daily.items()}
        scatter = [(r["e"], self.sip[r["t"]] - self.da[r["t"]]) for x in days for r in self.rows_by_day.get(x, ())
                   if r["e"] is not None and r["t"] in self.sip and r["t"] in self.da]
        step = max(1, len(scatter) // 2000)
        flag_rows = []
        for key in ("revision", "ramp", "margin"):
            groups = {True: [], False: []}
            for x in days:
                f = next((f for f in self.flags(x) if f["key"] == key), None)
                ev, c, rec = self.day_eval(x, "combined"), self.day_cost(x, share), self.recommendation(x, share, lam)
                if f and ev and c and rec:
                    mae = fmean(abs(r["e"]) for r in self.rows_by_day[x] if r["e"] is not None)
                    groups[f["flagged"]].append((mae, c[rec["q"]][0]))
            if groups[True] and groups[False]:
                flag_rows.append({"key": key, **{
                    f"{k}{'Flagged' if on else 'Other'}": round(fmean(g[i] for g in groups[on]))
                    for on in (True, False) for i, k in enumerate(("mae", "cost"))},
                    "daysFlagged": len(groups[True]), "daysOther": len(groups[False])})
        return {
            "frontier": self.frontier(days, share, lam), "models": models, "cumulative": cum, "realised": realised,
            "scatter": [{"e": round(e), "spread": round(s, 2)} for e, s in scatter[::step]],
            "correlation": round(_corr(scatter), 3) if len(scatter) > 2 else None,
            "flags": flag_rows,
        }


def _corr(pairs):
    xs, ys = zip(*pairs)
    mx, my = fmean(xs), fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy) if sx and sy else 0.0
