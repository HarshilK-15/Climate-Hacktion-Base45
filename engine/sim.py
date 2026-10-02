"""Shipless simulation engine: hour-by-hour island power system model.

Owner: Elec B.

For every hour of the year it decides where the island's power comes from:
  1. solar panels (used directly)
  2. battery (charged from spare solar, or spare generator output)
  3. diesel generator (only when 1 and 2 cannot cover the demand)
and counts the diesel burned. It tries a grid of solar and battery sizes, picks the one
with the lowest lifetime cost, then re-runs that design on every year of weather to get
P50 (typical) and P90 (bad-year) diesel savings.

All money is in USD. Every assumption is in DEFAULTS so it is visible and replaceable.
"""
import math

import numpy as np

HOURS = 8760

DEFAULTS = {
    # ---- costs (ASSUMPTIONS - replace with sourced Pacific figures) ----
    "solar_cost_per_kw": 2500.0,     # installed cost, remote island
    "battery_cost_per_kwh": 700.0,   # installed cost incl. inverter share
    "battery_life_years": 10,
    "om_fraction": 0.015,            # yearly upkeep as a share of purchase cost
    "project_years": 20,
    "discount_rate": 0.06,
    # ---- technical ----
    "pv_derate": 0.80,               # dust, heat, wiring and inverter losses
    "batt_eff_charge": 0.95,
    "batt_eff_discharge": 0.95,
    "batt_min_soc": 0.20,            # never empty the battery below 20%
    "batt_c_rate": 0.5,              # max charge/discharge power = 0.5 x capacity
    "batt_start_soc": 0.5,
    "gen_min_load": 0.30,            # diesel generators should not run below ~30% load
    # Linear fuel curve: litres/hour = fuel_a x generator size + fuel_b x output.
    # Typical values used by microgrid tools - replace with your generator's datasheet.
    "fuel_a": 0.08,
    "fuel_b": 0.25,
}


def dispatch(load, ghi, solar_kw, battery_kwh, gen_kw, p, gen_available=True, record=False):
    """Simulate hour by hour.

    load: (H,) demand in kW.  ghi: (H,) or (H, Y) sunshine in W/m2.
    solar_kw / battery_kwh: numbers or numpy arrays (to test many designs at once).
    Returns totals (numpy arrays) and, if record=True, an hourly trace.
    """
    S = np.asarray(solar_kw, float)
    B = np.asarray(battery_kwh, float)
    shape = np.broadcast(S, B, np.asarray(ghi[0], float)).shape
    zeros = np.zeros(shape)
    B = np.broadcast_to(B, shape)
    soc = B * p["batt_start_soc"]
    bmin = B * p["batt_min_soc"]
    plim = B * p["batt_c_rate"]
    ec, ed = p["batt_eff_charge"], p["batt_eff_discharge"]
    minload = p["gen_min_load"] * gen_kw
    k_pv = p["pv_derate"] / 1000.0

    tot = {k: zeros.copy() for k in (
        "fuel_l", "gen_kwh", "gen_to_load", "solar_direct", "batt_out",
        "unserved", "curtailed", "gen_hours", "soc_min_frac")}
    tot["soc_min_frac"] += 1.0
    trace = {k: [] for k in ("load", "solar", "battery", "diesel", "soc_pct", "unserved")} if record else None

    for t in range(len(load)):
        L = load[t]
        pv = S * (ghi[t] * k_pv)
        direct = np.minimum(pv, L)
        surplus = pv - direct
        deficit = L - direct

        ch = np.minimum(np.minimum(surplus, plim), np.maximum(B - soc, 0) / ec)
        soc = soc + ch * ec
        curt = surplus - ch

        avail = np.maximum(np.minimum(plim, (soc - bmin) * ed), 0)
        dis = np.minimum(deficit, avail)
        soc = soc - dis / ed
        rest = deficit - dis

        if gen_available:
            on = rest > 1e-9
            out = np.where(on, np.minimum(np.maximum(rest, minload), gen_kw), 0.0)
            to_load = np.minimum(out, rest)
            extra = out - to_load          # forced by minimum load: store it if we can
            ch2 = np.minimum(np.minimum(extra, np.maximum(plim - ch, 0)), np.maximum(B - soc, 0) / ec)
            soc = soc + ch2 * ec
            tot["fuel_l"] += np.where(on, p["fuel_a"] * gen_kw + p["fuel_b"] * out, 0.0)
            tot["gen_kwh"] += out
            tot["gen_hours"] += on
        else:
            to_load = zeros
        uns = rest - to_load

        tot["gen_to_load"] += to_load
        tot["solar_direct"] += direct
        tot["batt_out"] += dis
        tot["unserved"] += uns
        tot["curtailed"] += curt
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(B > 0, soc / np.where(B > 0, B, 1), 1.0)
        tot["soc_min_frac"] = np.minimum(tot["soc_min_frac"], frac)

        if record:
            trace["load"].append(float(L))
            trace["solar"].append(float(direct))
            trace["battery"].append(float(dis))
            trace["diesel"].append(float(to_load))
            trace["soc_pct"].append(float(frac * 100))
            trace["unserved"].append(float(uns))
    tot["load_kwh"] = float(np.sum(load))
    return tot, trace


def _annuity(rate, years):
    return sum(1 / (1 + rate) ** y for y in range(1, years + 1))


def lifetime_cost(solar_kw, battery_kwh, fuel_l_per_year, price, p):
    capex = solar_kw * p["solar_cost_per_kw"] + battery_kwh * p["battery_cost_per_kwh"]
    r, n = p["discount_rate"], p["project_years"]
    replace = 0.0
    k = 1
    while k * p["battery_life_years"] < n:
        replace = replace + battery_kwh * p["battery_cost_per_kwh"] / (1 + r) ** (k * p["battery_life_years"])
        k += 1
    yearly = fuel_l_per_year * price + p["om_fraction"] * capex
    return capex + replace + _annuity(r, n) * yearly, capex


def simulate(site, load, critical, ghi_years, weather_label, overrides=None):
    p = dict(DEFAULTS, **(overrides or {}))
    price = float(site.get("diesel_price_per_litre") or 1.8)
    peak = float(load.max())
    gen_kw = float(site.get("generator_kw") or math.ceil(1.25 * peak / 10) * 10)
    daily = load.sum() / 365
    n_years = ghi_years.shape[0]

    # 1) Today: diesel only
    base, _ = dispatch(load, np.zeros(HOURS), 0.0, 0.0, gen_kw, p)
    base_fuel = float(base["fuel_l"])

    # 2) Search designs on a typical (median sunshine) year
    yearly_sun = ghi_years.sum(axis=1)
    typ = int(np.argsort(yearly_sun)[len(yearly_sun) // 2])
    solar_full = daily / 4.0      # roughly the panels needed to make a full day's energy
    S_grid = np.round(np.linspace(0, 1.6 * solar_full, 13), 1)
    B_grid = np.round(np.linspace(0, 2.0 * daily, 13), 1)
    S = S_grid[:, None]
    Bm = B_grid[None, :]
    sweep, _ = dispatch(load, ghi_years[typ], S, Bm, gen_kw, p)
    npc, capex = lifetime_cost(S, Bm, sweep["fuel_l"], price, p)
    ok = sweep["unserved"] <= base["unserved"] + 1e-6
    npc_masked = np.where(ok, npc, np.inf)
    i, j = np.unravel_index(np.argmin(npc_masked), npc.shape)
    best_s, best_b = float(S_grid[i]), float(B_grid[j])
    base_npc, _ = lifetime_cost(0.0, 0.0, base_fuel, price, p)

    # 3) Run the chosen design on every year of weather -> P50 / P90
    yrs, _ = dispatch(load, ghi_years.T, best_s, best_b, gen_kw, p)
    saved = base_fuel - yrs["fuel_l"]
    p50 = float(np.median(saved))
    p90 = float(np.percentile(saved, 10))
    ren = 1 - yrs["gen_to_load"] / yrs["load_kwh"]
    best_capex = best_s * p["solar_cost_per_kw"] + best_b * p["battery_cost_per_kwh"]
    yearly_saving_usd = p50 * price - p["om_fraction"] * best_capex
    payback = best_capex / yearly_saving_usd if yearly_saving_usd > 0 else None

    # 4) "No fuel ship" test: cloudiest week on record, generator off, clinic essentials only
    flat = ghi_years.reshape(-1)
    week = 168
    csum = np.concatenate([[0], np.cumsum(flat)])
    sums = csum[week:] - csum[:-week]
    start = int(np.argmin(sums))
    w_ghi = flat[start:start + week]
    crit = np.full(week, float(critical.max())) if critical.max() > 0 else np.zeros(week)
    _, ctr = dispatch(crit, w_ghi, best_s, best_b, gen_kw, p, gen_available=False, record=True)
    powered = sum(1 for u in ctr["unserved"] if u < 1e-6)
    first_fail = next((h for h, u in enumerate(ctr["unserved"]) if u >= 1e-6), None)

    # 5) A typical week for the chart (median-sunshine week of the typical year)
    _, tr = dispatch(load, ghi_years[typ], best_s, best_b, gen_kw, p, record=True)
    wk = ghi_years[typ][: 52 * week].reshape(52, week).sum(axis=1)
    w = int(np.argsort(wk)[26])
    sl = slice(w * week, (w + 1) * week)
    week_trace = {k: [round(v, 2) for v in vals[sl]] for k, vals in tr.items()}

    r1 = lambda x: round(float(x), 1)
    return {
        "site": {k: site.get(k) for k in ("id", "name", "country", "lat", "lon")},
        "weather": weather_label,
        "years_of_weather": int(n_years),
        "load": {"daily_kwh": r1(daily), "peak_kw": r1(peak), "generator_kw": r1(gen_kw),
                 "critical_kw": round(float(critical.max()), 2)},
        "today": {"diesel_litres_per_year": round(base_fuel), "diesel_cost_per_year": round(base_fuel * price),
                  "lifetime_cost": round(base_npc)},
        "design": {"solar_kw": best_s, "battery_kwh": best_b, "purchase_cost": round(best_capex),
                   "lifetime_cost": round(float(npc[i, j])),
                   "lifetime_saving": round(base_npc - float(npc[i, j])),
                   "litres_saved_p50": round(p50), "litres_saved_p90": round(p90),
                   "percent_diesel_cut_p50": r1(100 * p50 / base_fuel) if base_fuel else 0,
                   "money_saved_per_year_p50": round(p50 * price),
                   "solar_share_percent": r1(100 * float(np.median(ren))),
                   "generator_hours_per_year": round(float(np.median(yrs["gen_hours"]))),
                   "payback_years": r1(payback) if payback else None},
        "no_fuel_ship": {"hours_clinic_powered": powered, "of_hours": week,
                         "days_clinic_powered": r1(powered / 24),
                         "first_outage_hour": first_fail,
                         "cloudiest_week_sunshine_vs_average_percent":
                             r1(100 * sums[start] / (flat.mean() * week)) if flat.mean() > 0 else 0},
        "week_trace": week_trace,
        "sweep": {"solar_kw": S_grid.tolist(), "battery_kwh": B_grid.tolist(),
                  "lifetime_cost": np.round(npc).astype(int).tolist()},
        "assumptions": {**p, "diesel_price_per_litre": price},
    }
