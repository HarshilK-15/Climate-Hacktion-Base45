"""Shipless simulation engine: hour-by-hour island power system model.

Owner: Elec B.

For every hour of the year it decides where the island's power comes from:
  1. solar panels (used directly)
  2. battery (charged from spare solar, or spare generator output)
  3. diesel generator (only when 1 and 2 cannot cover the demand)
and counts the diesel burned. engine/optimise.py tries a grid of solar and battery sizes on
every weather year at once and picks the cheapest over 20 years that never blacks out and keeps
the clinic powered through the darkest week on record with no diesel. From the same runs come
P50 (typical) and P90 (bad-year) diesel savings.

All money is in AUD (data/costs.md). Every assumption is in DEFAULTS so it is visible and replaceable.
"""
import copy
import hashlib
import json
import math

import numpy as np

from engine.dispatch import TECH_DEFAULTS, dispatch
from engine.optimise import optimise

HOURS = 8760
# Designs searched per plan request in the app (panels x battery), then refined 9 x 9 around the
# winner. Smaller than the command line's 30 x 20 so a plan comes back in a few seconds.
APP_GRID = (15, 10)

DEFAULTS = {
    # ---- costs (SOURCED: data/costs.md, AUD) ----
    "solar_cost_per_kw": 2800.0,     # installed microgrid solar, AUD/kWp (costs.md, IRENA)
    "battery_cost_per_kwh": 850.0,   # installed LiFePO4 BESS, AUD/kWh (costs.md, NREL ATB)
    "om_fraction": 0.015,            # solar + battery upkeep, 1.5% of CAPEX/yr (costs.md, NREL)
    "project_years": 20,             # financing term (costs.md section 3)
    "discount_rate": 0.04,           # concessionary rate, GCF / EU baseline (costs.md section 3)
    "diesel_price_per_litre": 2.10,  # fallback if the site gives none: portfolio average (costs.md)
    "co2_kg_per_litre": 2.68,        # kg CO2 per litre of diesel (costs.md section 3)
    "gen_om_per_kwh": 0.08,          # diesel generator upkeep, AUD/kWh made (costs.md, World Bank)
    # ---- costs (ASSUMPTION - replace with the battery supplier's warranty life) ----
    "battery_life_years": 10,
    # ---- technical (pv derate, battery, generator floor, fuel curve): see engine/dispatch.py ----
    **TECH_DEFAULTS,
}


def generator_size(site, load):
    """The site's own generator, or (ASSUMPTION) 1.25 x peak load rounded up to 10 kW if unknown."""
    return float(site.get("generator_kw") or math.ceil(1.25 * float(load.max()) / 10) * 10)


# Finished plans, keyed by a fingerprint of every input. The server builds every site's default plan
# at startup (portfolio map), so clicking a site with unchanged settings returns at once.
_PLANS = {}
_PLANS_MAX = 64


def _fingerprint(site, load, critical, ghi_years, weather_label, overrides, grid):
    h = hashlib.sha1()
    norm = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    h.update(json.dumps({k: norm(v) for k, v in site.items()}, sort_keys=True, default=str).encode())
    h.update(json.dumps([weather_label, overrides or {}, list(grid)], sort_keys=True, default=str).encode())
    for a in (load, critical, ghi_years):
        a = np.ascontiguousarray(a)
        h.update(str((a.shape, a.dtype)).encode())
        h.update(a.tobytes())
    return h.hexdigest()


def simulate(site, load, critical, ghi_years, weather_label, overrides=None, grid=APP_GRID):
    """The full plan for one site (see contracts/FORMATS.md). Same inputs -> cached answer.

    If the inputs exactly match a precomputed answer (engine/precomputed/, built by
    python -m engine.precompute), that answer is used instead of re-running the search: the map and
    the default plans come back at once, even on a cold serverless start (Vercel)."""
    key = _fingerprint(site, load, critical, ghi_years, weather_label, overrides, grid)
    if key not in _PLANS:
        if len(_PLANS) >= _PLANS_MAX:
            _PLANS.pop(next(iter(_PLANS)))          # forget the oldest
        pre = _precomputed(key)
        _PLANS[key] = pre if pre is not None else plan(site, load, critical, ghi_years, weather_label,
                                                       overrides, grid)[0]
    return copy.deepcopy(_PLANS[key])                # callers add to the result; keep the cache clean


def _precomputed(key):
    """The precomputed answer for exactly these inputs and this engine code, or None.
    Same fingerprint as engine.service (inputs + engine version), so a stale file is never used."""
    try:
        from engine import service                  # imported here: service imports this module
        fp = hashlib.sha1(key.encode() + json.dumps([service.ENGINE_VERSION, None]).encode()).hexdigest()[:16]
        hit = service._load_precomputed()["by_fp"].get(fp)
    except Exception:                                # no files / unreadable: just compute it
        return None
    return copy.deepcopy(hit) if hit else None


def plan(site, load, critical, ghi_years, weather_label, overrides=None, grid=APP_GRID):
    """simulate() without the cache -> (plan result, optimiser details such as yearly savings)."""
    p = dict(DEFAULTS, **(overrides or {}))
    price = float(site.get("diesel_price_per_litre") or p["diesel_price_per_litre"])
    peak = float(load.max())
    gen_kw = generator_size(site, load)
    daily = load.sum() / 365
    ghi_years = np.asarray(ghi_years, float)
    n_years = ghi_years.shape[0]

    # 1-4) Design search on every weather year, P50/P90, late-fuel-ship test (engine/optimise.py)
    opt = optimise(load, float(critical.max()), ghi_years, gen_kw, price, p, n_pv=grid[0], n_batt=grid[1])
    w, today = opt["winner"], opt["today"]
    best_s, best_b = w["solar_kw"], w["battery_kwh"]
    base_fuel = today["litres_per_year"]
    base_npc = today["cost"]["total"]
    p50, p90 = w["litres_saved_p50"], w["litres_saved_p90"]
    best_capex = w["capex"]
    payback = w["payback_years"]
    week = 168

    # 5) A typical week for the chart (median-sunshine week of the median-sunshine year)
    week_trace = {k: [round(v, 2) for v in vals] for k, vals in opt["week_trace"].items()}

    r1 = lambda x: round(float(x), 1)
    # SimResult (contracts/mock_simresult.json, brief Chapter 1.4): the flat summary the web app
    # and the AI use. Same numbers as the detailed sections below, just in the agreed shape.
    simresult = {
        "site": ", ".join(str(x) for x in (site.get("name"), site.get("country")) if x),
        "pv_kw": best_s,
        "battery_kwh": best_b,
        "generator_kw": r1(gen_kw),
        "capex_aud": round(best_capex),
        "lifetime_cost_aud": round(w["cost"]["total"]),
        "diesel_litres_saved_p50": round(p50),
        "diesel_litres_saved_p90": round(p90),
        "co2_tonnes_saved_per_year": round(p50 * p["co2_kg_per_litre"] / 1000),
        "blackout_hours_worst_year": w["blackout_hours_worst_year"],
        "critical_load_survives_7d_no_fuel": w["clinic_survives"],
        "years_simulated": int(n_years),
    }
    g = opt["grid"]
    return {
        "simresult": simresult,
        "site": {k: site.get(k) for k in ("id", "name", "country", "lat", "lon")},
        "weather": weather_label,
        "years_of_weather": int(n_years),
        "load": {"daily_kwh": r1(daily), "peak_kw": r1(peak), "generator_kw": r1(gen_kw),
                 "critical_kw": round(float(critical.max()), 2)},
        "today": {"diesel_litres_per_year": round(base_fuel), "diesel_cost_per_year": round(base_fuel * price),
                  "lifetime_cost": round(base_npc)},
        "design": {"solar_kw": best_s, "battery_kwh": best_b, "purchase_cost": round(best_capex),
                   "lifetime_cost": round(w["cost"]["total"]),
                   "lifetime_saving": round(base_npc - w["cost"]["total"]),
                   "litres_saved_p50": round(p50), "litres_saved_p90": round(p90),
                   "percent_diesel_cut_p50": r1(100 * p50 / base_fuel) if base_fuel else 0,
                   "money_saved_per_year_p50": round(p50 * price),
                   "solar_share_percent": r1(100 * w["solar_share"]),
                   "generator_hours_per_year": round(w["generator_hours"]),
                   "payback_years": r1(payback) if payback else None},
        "no_fuel_ship": {"hours_clinic_powered": w["clinic_hours_powered"], "of_hours": week,
                         "days_clinic_powered": r1(w["clinic_hours_powered"] / 24),
                         "first_outage_hour": w["clinic_first_outage_hour"],
                         "cloudiest_week_sunshine_vs_average_percent":
                             r1(100 * opt["worst_week_sun_vs_average"])},
        "week_trace": week_trace,
        "sweep": {"solar_kw": np.round(g["solar_kw"], 1).tolist(),
                  "battery_kwh": np.round(g["battery_kwh"], 1).tolist(),
                  "lifetime_cost": np.round(g["lifetime_cost"]).astype(int).tolist()},
        "assumptions": {**p, "diesel_price_per_litre": price},
    }, opt
