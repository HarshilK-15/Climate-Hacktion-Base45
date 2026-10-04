"""Find the cheapest solar + battery design that never blacks out and keeps the clinic alive
when the fuel ship is late. Owner: Elec B.

    python -m engine.optimise            # Funafuti (brief 3.5): 20 years of NASA weather + Data Sci's mock load
    python -m engine.optimise kadavu     # any site in data/sites.json

How it works (brief 3.5, upgraded):
  1. A grid of 600 designs (30 panel sizes x 20 battery sizes), scaled to the site's own load
     and sun rather than fixed numbers, so a 10 kW village and a 1 MW town both get a sensible grid.
  2. Every design runs on EVERY weather year at once (numpy): 600 x 20 = 12,000 island-years.
  3. A design qualifies only if it has no more blackout hours than today in every one of those
     years (not just year one), and the clinic survives the worst 7 days of sun on record with
     no diesel at all, starting from whatever charge normal running left in the battery.
  4. The cheapest lifetime cost wins, then a 9 x 9 finer grid around it sharpens the answer.
  5. We also find the cheapest design that ignores the clinic: the difference is what keeping
     the clinic alive costs ("the price of resilience").

Cost model (20 years, AUD, prices from data/costs.md; kept simple on purpose):
  lifetime cost = panels + battery (bought in year 0)
                + battery replacement in year 10
                + 20 years of diesel + generator upkeep + solar/battery upkeep,
                  each discounted at 4%/yr (costs.md financing rate) to today's money
  Declared simplifications: diesel price flat (no inflation or price spikes); no resale value
  at year 20; generator replacement not counted (every design keeps the generator, and fewer
  running hours would only make it last longer, so leaving it out is conservative for solar).
"""
import time

import numpy as np

from engine.dispatch import dispatch

HOURS = 8760
SHIP_DAYS = 7          # brief 3.5: the fuel ship is a week late
LOOK_DAYS = 30         # keep going after the 7 days to say how long the clinic would really last
GRID_PV, GRID_BATT = 30, 20   # brief 3.5: ~600 designs
REFINE_POINTS = 7
# ASSUMPTION: designs within 2% of the best 20-year cost are economically the same, because the
# input prices themselves (data/costs.md) are only good to roughly +-10%. Tighten with better prices.
NEAR_OPTIMAL = 0.02


def cost_breakdown(solar_kw, battery_kwh, fuel_l, gen_kwh, load_kwh, price, p):
    """Lifetime cost in today's AUD, split into parts. fuel_l, gen_kwh, load_kwh are per year.

    Works on numbers or numpy arrays (every design at once).
    """
    r, n = p["discount_rate"], int(p["project_years"])
    life = int(p["battery_life_years"])
    years_pv = sum((1 + r) ** -y for y in range(1, n + 1))   # today's value of 1 AUD a year for n years
    solar = solar_kw * p["solar_cost_per_kw"]
    battery = battery_kwh * p["battery_cost_per_kwh"]
    replacement = sum(battery * (1 + r) ** -y for y in range(life, n, life))   # year 10 for 20 years
    diesel = years_pv * fuel_l * price
    gen_upkeep = years_pv * gen_kwh * p["gen_om_per_kwh"]
    upkeep = years_pv * p["om_fraction"] * (solar + battery)
    total = solar + battery + replacement + diesel + gen_upkeep + upkeep
    return {
        "solar": solar,
        "battery": battery,
        "battery_replacement": replacement,
        "diesel": diesel,
        "generator_upkeep": gen_upkeep,
        "solar_battery_upkeep": upkeep,
        "total": total,
        # what each kWh the island uses costs over the project, in today's money
        "cost_per_kwh": total / (years_pv * load_kwh) if load_kwh else 0.0,
    }


def worst_stretch(ghi_years, hours=SHIP_DAYS * 24):
    """The `hours`-long stretch with the least sun in the whole record.

    Returns (start index into the flattened record, its sun as a fraction of an average stretch).
    """
    flat = np.asarray(ghi_years, float).reshape(-1)
    csum = np.concatenate([[0.0], np.cumsum(flat)])
    sums = csum[hours:] - csum[:-hours]
    start = int(np.argmin(sums))
    avg = flat.mean() * hours
    return start, float(sums[start] / avg) if avg > 0 else 0.0


def _need_from(need_kw, hour, n):
    """n hours of demand starting at `hour` of the year: a flat kW, or a slice of an 8760-h load."""
    need = np.asarray(need_kw, float)
    if need.ndim == 0:
        return np.full(n, float(need))
    return need[(hour + np.arange(n)) % HOURS]


def no_fuel_ship(load, need_kw, ghi_years, solar_kw, battery_kwh, gen_kw, p,
                 days=SHIP_DAYS, look_days=LOOK_DAYS, start_soc=None):
    """The late-fuel-ship test, for every design at once.

    The diesel runs out at the start of the darkest week on record. The battery starts that week
    with whatever charge normal running left in it at that hour (not a convenient full battery).
    From then on the generator is off and only `need_kw` is served: the clinic's critical kW,
    or the whole island's 8760-hour load. start_soc: that starting charge if the caller already
    knows it (optimise() records it during the main run). Returns a dict of arrays.
    """
    ghi_years = np.asarray(ghi_years, float)
    start, sun = worst_stretch(ghi_years, days * 24)
    year, hour = divmod(start, HOURS)
    p0 = dict(p)
    if start_soc is not None:
        p0["batt_start_soc"] = start_soc
    elif hour > 0:
        before, _ = dispatch(load[:hour], ghi_years[year, :hour], solar_kw, battery_kwh, gen_kw, p)
        p0["batt_start_soc"] = before["soc_end_frac"]
    flat = ghi_years.reshape(-1)
    week = flat[start:start + days * 24]
    longer = flat[start:start + look_days * 24]
    t7, _ = dispatch(_need_from(need_kw, hour, len(week)), week, solar_kw, battery_kwh, gen_kw, p0,
                     gen_available=False)
    tl, _ = dispatch(_need_from(need_kw, hour, len(longer)), longer, solar_kw, battery_kwh, gen_kw, p0,
                     gen_available=False)
    first_out = np.minimum(tl["first_unserved_hour"], len(longer))
    return {
        "survives": t7["unserved_hours"] == 0,
        "hours_powered": len(week) - t7["unserved_hours"],
        "of_hours": len(week),
        "first_outage_hour": t7["first_unserved_hour"],     # inf = never in the 7 days
        "days_until_first_outage": first_out / 24,          # capped at look_days
        "look_days": look_days,
        "start": start,
        "sun_vs_average": sun,
    }


def _evaluate(S_grid, B_grid, load, critical_kw, ghi_years, gen_kw, price, p, base_blackout=None,
              extra_snapshots=()):
    """Run every (panels, battery) pair on every weather year; cost and test each one.

    base_blackout None: the grid starts at 0 kW / 0 kWh, so its first design IS today's
    diesel-only island and sets the bar.
    """
    S, B = S_grid[:, None], B_grid[None, :]
    start, _ = worst_stretch(ghi_years)
    year, hour = divmod(start, HOURS)
    # one run of every design on every year, noting the battery charge when the fuel ship fails
    t, _ = dispatch(load, ghi_years.T, S[..., None], B[..., None], gen_kw, p,
                    snapshot_hours=(hour, *extra_snapshots))                     # (pv, batt, year)
    cost = cost_breakdown(S, B, t["fuel_l"].mean(axis=2), t["gen_kwh"].mean(axis=2),
                          float(load.sum()), price, p)
    shape = (S_grid.size, B_grid.size)
    cost = {k: np.broadcast_to(v, shape) for k, v in cost.items()}
    ship = no_fuel_ship(load, critical_kw, ghi_years, S, B, gen_kw, p,
                        start_soc=t["soc_snapshots"][hour][..., year])
    worst_blackout = t["unserved_hours"].max(axis=2)
    if base_blackout is None:
        assert S_grid[0] == 0 and B_grid[0] == 0
        base_blackout = worst_blackout[0, 0]
    return {"S": S_grid, "B": B_grid, "t": t, "cost": cost, "ship": ship,
            "worst_blackout": worst_blackout,
            "reliable": worst_blackout <= base_blackout,
            "designs": S_grid.size * B_grid.size, "years": ghi_years.shape[0]}


def _cheapest(ev, need_clinic):
    ok = ev["reliable"] & (ev["ship"]["survives"] if need_clinic else True)
    if not ok.any():
        return None
    i, j = np.unravel_index(np.argmin(np.where(ok, ev["cost"]["total"], np.inf)), ok.shape)
    return int(i), int(j)


def _refine(ev, ij, *args):
    """A finer grid one coarse step either side of the winner."""
    S, B = ev["S"], ev["B"]
    dS, dB = S[1] - S[0], B[1] - B[0]
    s, b = S[ij[0]], B[ij[1]]
    S2 = np.unique(np.round(np.linspace(max(0.0, s - dS), s + dS, REFINE_POINTS), 1))
    B2 = np.unique(np.round(np.linspace(max(0.0, b - dB), b + dB, REFINE_POINTS), 1))
    return _evaluate(S2, B2, *args)


def _design(ev, ij, base, price, p):
    """Everything about one design, as plain numbers."""
    i, j = ij
    t, c, sh = ev["t"], ev["cost"], ev["ship"]
    fuel_years = t["fuel_l"][i, j]
    saved = float(base["fuel_l"]) - fuel_years
    capex = float(c["solar"][i, j] + c["battery"][i, j])
    yearly = (float(np.median(saved)) * price
              + (float(base["gen_kwh"]) - float(t["gen_kwh"][i, j].mean())) * p["gen_om_per_kwh"]
              - p["om_fraction"] * capex)
    first = float(sh["first_outage_hour"][i, j])
    year, hour = divmod(sh["start"], HOURS)
    return {
        "solar_kw": round(float(ev["S"][i]), 1),
        "battery_kwh": round(float(ev["B"][j]), 1),
        "capex": capex,
        "cost": {k: float(v[i, j]) for k, v in c.items()},
        "litres_saved_per_year": saved,
        "litres_saved_p50": float(np.median(saved)),
        "litres_saved_p90": float(np.percentile(saved, 10)),
        "solar_share": float(np.median(1 - t["gen_to_load"][i, j] / t["load_kwh"])),
        "generator_hours": float(np.median(t["gen_hours"][i, j])),
        "blackout_hours_worst_year": int(ev["worst_blackout"][i, j]),
        "payback_years": capex / yearly if yearly > 0 else None,
        "clinic_survives": bool(sh["survives"][i, j]),
        "clinic_hours_powered": int(sh["hours_powered"][i, j]),
        "clinic_first_outage_hour": None if np.isinf(first) else int(first),
        "clinic_days_until_first_outage": float(sh["days_until_first_outage"][i, j]),
        "battery_charge_when_ship_fails": float(t["soc_snapshots"][hour][i, j, year]),
    }


def optimise(load, critical_kw, ghi_years, gen_kw, price, p, n_pv=GRID_PV, n_batt=GRID_BATT, refine=True):
    """Search panel x battery sizes on every weather year. Returns a dict (see module docstring)."""
    t0 = time.perf_counter()
    load = np.asarray(load, float)
    ghi_years = np.asarray(ghi_years, float)
    load_kwh = float(load.sum())
    # Grid scaled to the site: panels that would make a whole day's energy on an average day
    daily = load_kwh / 365
    sun_kwh_m2_day = ghi_years.mean() * 24 / 1000
    pv_one_day = daily / (sun_kwh_m2_day * p["pv_derate"]) if sun_kwh_m2_day > 0 else daily / 4
    # Sizes rounded to 0.1 kW / kWh BEFORE simulating, so every number reported is for exactly the
    # design reported
    S_grid = np.unique(np.round(np.linspace(0, 2.5 * pv_one_day, n_pv), 1))   # 0 .. 2.5 days of panels
    B_grid = np.unique(np.round(np.linspace(0, 2.0 * daily, n_batt), 1))      # 0 .. 2 days of storage
    # The week the app charts: the median-sun week of the median-sun year
    typ = int(np.argsort(ghi_years.sum(axis=1))[ghi_years.shape[0] // 2])
    weeks = ghi_years[typ, :52 * 168].reshape(52, 168).sum(axis=1)
    week_start = int(np.argsort(weeks)[26]) * 168

    coarse = _evaluate(S_grid, B_grid, load, critical_kw, ghi_years, gen_kw, price, p,
                       extra_snapshots=(week_start,))
    t_coarse = time.perf_counter() - t0
    # Today = the grid's first design (0 kW, 0 kWh): the same island on diesel alone
    tz = coarse["t"]
    base = {k: float(tz[k][0, 0, 0]) for k in ("fuel_l", "gen_kwh", "unserved_hours")}
    base_cost = cost_breakdown(0.0, 0.0, base["fuel_l"], base["gen_kwh"], load_kwh, price, p)
    args = (load, critical_kw, ghi_years, gen_kw, price, p, base["unserved_hours"], (week_start,))

    picks, refined = {}, {}
    for name, need_clinic in (("resilient", True), ("cheapest", False)):
        ij = _cheapest(coarse, need_clinic)
        if ij is None:
            continue
        if ij not in refined:     # usually both picks are the same design: refine it once
            refined[ij] = _refine(coarse, ij, *args) if refine else coarse
        ev = refined[ij]
        ij2 = _cheapest(ev, need_clinic) if refine else ij
        if ij2 is None:          # finer grid lost every qualifying design: keep the coarse one
            ev, ij2 = coarse, ij
        picks[name] = _design(ev, ij2, base, price, p)
        picks[name]["_at"] = (ev, ij2)

    winner = picks.get("resilient") or picks["cheapest"]

    # Hour-by-hour trace of the winner's typical week, started from the charge the main run recorded
    ev, (i, j) = winner.pop("_at")
    picks["cheapest"].pop("_at", None)
    sl = slice(week_start, week_start + 168)
    soc0 = ev["t"]["soc_snapshots"][week_start][i, j, typ]
    _, week_trace = dispatch(load[sl], ghi_years[typ, sl], winner["solar_kw"], winner["battery_kwh"],
                             gen_kw, dict(p, batt_start_soc=soc0), record=True)

    # Same money, different priorities: every qualifying design within NEAR_OPTIMAL of the best cost
    need_clinic = "resilient" in picks
    ok = coarse["reliable"] & (coarse["ship"]["survives"] if need_clinic else True)
    total = coarse["cost"]["total"]
    near = ok & (total <= winner["cost"]["total"] * (1 + NEAR_OPTIMAL))
    options = {}
    if near.any():
        capex = coarse["cost"]["solar"] + coarse["cost"]["battery"]
        fuel = coarse["t"]["fuel_l"].mean(axis=2)
        for name, score in (("lowest_purchase_cost", capex), ("most_diesel_free", fuel)):
            i, j = np.unravel_index(np.argmin(np.where(near, score, np.inf)), near.shape)
            options[name] = _design(coarse, (int(i), int(j)), base, price, p)
        Sg, Bg = np.meshgrid(S_grid, B_grid, indexing="ij")
        band = {"solar_kw": (float(Sg[near].min()), float(Sg[near].max())),
                "battery_kwh": (float(Bg[near].min()), float(Bg[near].max())),
                "designs": int(near.sum())}
    else:
        band = None
    # Bonus line for the pitch: the WHOLE island (not just the clinic) with no fuel at all
    island = no_fuel_ship(load, load, ghi_years, winner["solar_kw"], winner["battery_kwh"], gen_kw, p,
                          start_soc=winner["battery_charge_when_ship_fails"])
    winner["island_hours_powered"] = int(island["hours_powered"])
    winner["island_days_until_first_outage"] = float(island["days_until_first_outage"])
    cheapest = picks["cheapest"]
    designs = coarse["designs"] + (len(refined) * REFINE_POINTS ** 2 if refine else 0)
    return {
        "winner": winner,
        "week_trace": week_trace,
        "clinic_guaranteed": "resilient" in picks,
        "cheapest_ignoring_clinic": cheapest,
        "price_of_resilience": winner["cost"]["total"] - cheapest["cost"]["total"],
        "near_optimal_band": band,
        "options": options,
        "today": {"litres_per_year": float(base["fuel_l"]), "cost": base_cost,
                  "blackout_hours_per_year": int(base["unserved_hours"])},
        "lifetime_saving": base_cost["total"] - winner["cost"]["total"],
        "generator_kw": float(gen_kw),
        "diesel_price": float(price),
        "critical_kw": float(critical_kw),
        "worst_week_start": coarse["ship"]["start"],
        "worst_week_sun_vs_average": coarse["ship"]["sun_vs_average"],
        "grid": {"solar_kw": S_grid, "battery_kwh": B_grid, "lifetime_cost": coarse["cost"]["total"],
                 "reliable": coarse["reliable"], "clinic_survives": coarse["ship"]["survives"]},
        "designs_tested": designs,
        "island_years": designs * ghi_years.shape[0],
        "seconds_coarse": t_coarse,
        "seconds": time.perf_counter() - t0,
        "years": ghi_years.shape[0],
    }


# ---------------------------------------------------------------------------------------------
# Command line: python -m engine.optimise [site_id]
# ---------------------------------------------------------------------------------------------

FUNAFUTI = {"id": "funafuti", "name": "Funafuti", "country": "Tuvalu", "lat": -8.52, "lon": 179.20,
            "diesel_price_per_litre": 2.35}   # landed diesel, Tuvalu (data/costs.md section 2)


def _funafuti_load():
    """Data Sci's mock week (data/mock_load_profile.csv, 168 h) repeated for a year."""
    from pathlib import Path
    f = Path(__file__).parent.parent / "data" / "mock_load_profile.csv"
    week = np.loadtxt(f, delimiter=",", skiprows=1, usecols=1)
    return np.resize(week, HOURS), "Data Sci mock week (data/mock_load_profile.csv) repeated"


def _when(start, label):
    """'14-20 Mar 2011' style date for a flat hour index, if the label says which years."""
    import datetime as dt
    import re
    m = re.search(r"(\d{4})-(\d{4})", label)
    year, hour = divmod(start, HOURS)
    d0 = dt.date(2023, 1, 1) + dt.timedelta(days=hour // 24)      # 2023: no 29 Feb, like the data
    d1 = d0 + dt.timedelta(days=SHIP_DAYS - 1)
    if not m:
        return f"{d0:%d %b}-{d1:%d %b}, year {year + 1} of the record"
    return f"{d0:%d %b}-{d1:%d %b} {int(m.group(1)) + year}"


def main(site_id="funafuti"):
    from engine.sim import DEFAULTS, generator_size
    from data.loads import CLINIC_CRITICAL_KW, build_load
    from data.weather import get_ghi, get_site

    if site_id == "funafuti":
        site = dict(FUNAFUTI)
        load, load_note = _funafuti_load()
        critical = CLINIC_CRITICAL_KW     # vaccine fridge + emergency light (data/loads.py ASSUMPTION)
    else:
        site = get_site(site_id)
        if not site:
            raise SystemExit(f"unknown site '{site_id}'")
        load, crit, _ = build_load(site)
        critical, load_note = float(crit.max()), "data/loads.py village model"
    p = dict(DEFAULTS)
    price = float(site.get("diesel_price_per_litre") or p["diesel_price_per_litre"])
    ghi, label = get_ghi(site["lat"], site["lon"])
    gen_kw = generator_size(site, load)
    r = optimise(load, critical, ghi, gen_kw, price, p)

    w, c, today = r["winner"], r["winner"]["cost"], r["today"]
    money = lambda x: f"AUD {x:,.0f}"
    print(f"\nSHIPLESS OPTIMISER - {site['name']}, {site['country']}")
    print(f"  weather    {label}")
    print(f"  load       {load.sum() / 365:,.0f} kWh/day, peak {load.max():.1f} kW ({load_note})")
    print(f"  generator  {gen_kw:.0f} kW     diesel {price:.2f} AUD/L     clinic essentials {critical:.1f} kW")
    print(f"\n  Tested {r['designs_tested']} designs x {r['years']} years = {r['island_years']:,} island-years "
          f"({r['island_years'] * HOURS / 1e6:,.0f} million hourly decisions) in {r['seconds']:.1f} s;\n"
          f"  the first {GRID_PV * GRID_BATT} designs x {r['years']} years took {r['seconds_coarse']:.1f} s")
    print(f"\nWINNER   {w['solar_kw']:.1f} kW of solar panels + {w['battery_kwh']:.1f} kWh of battery")
    print(f"  purchase cost        {money(w['capex'])}")
    print(f"  20-year cost         {money(c['total'])}   vs {money(today['cost']['total'])} staying on diesel"
          f"  ->  saves {money(r['lifetime_saving'])}")
    print(f"    panels {money(c['solar'])} | battery {money(c['battery'])} | battery swap yr 10 "
          f"{money(c['battery_replacement'])} | diesel {money(c['diesel'])} | generator upkeep "
          f"{money(c['generator_upkeep'])} | solar+battery upkeep {money(c['solar_battery_upkeep'])}")
    print(f"  cost of power        {c['cost_per_kwh']:.2f} AUD/kWh   (diesel only: "
          f"{today['cost']['cost_per_kwh']:.2f} AUD/kWh)")
    pb = f"{w['payback_years']:.1f} years" if w["payback_years"] else "never"
    print(f"  diesel saved         {w['litres_saved_p50']:,.0f} L/yr typical (P50), {w['litres_saved_p90']:,.0f} L/yr "
          f"in a bad year (P90) = {100 * w['litres_saved_p50'] / today['litres_per_year']:.0f}% cut; payback {pb}")
    print(f"  CO2 avoided          {w['litres_saved_p50'] * p['co2_kg_per_litre'] / 1000:,.0f} tonnes/yr")
    print(f"  blackout hours       {w['blackout_hours_worst_year']} in the worst of {r['years']} years")

    print(f"\nNO-FUEL-SHIP TEST   darkest week on record: {_when(r['worst_week_start'], label)}, "
          f"{100 * r['worst_week_sun_vs_average']:.0f}% of normal sun")
    print(f"  generator off, clinic only ({critical:.1f} kW), battery as normal running left it "
          f"({100 * w['battery_charge_when_ship_fails']:.0f}% full)")
    if w["clinic_survives"]:
        d = w["clinic_days_until_first_outage"]
        more = f"{LOOK_DAYS}+ days" if d >= LOOK_DAYS else f"{d:.1f} days"
        print(f"  critical_load_survives_7d_no_fuel: TRUE   powered {w['clinic_hours_powered']}/168 h; "
              f"with no fuel at all it would last {more}")
    else:
        print(f"  critical_load_survives_7d_no_fuel: FALSE  powered {w['clinic_hours_powered']}/168 h, "
              f"first outage at hour {w['clinic_first_outage_hour']}")

    d = w["island_days_until_first_outage"]
    lasts = f"{d * 24:.0f} hours" if d < 2 else f"{d:.1f} days"
    print(f"  whole island ({load.sum() / 365:,.0f} kWh/day), same week, no fuel: lights out after {lasts} "
          f"({w['island_hours_powered']}/168 h powered) - so the clinic needs its own protected circuit")

    band, opts = r["near_optimal_band"], r["options"]
    if band and opts:
        span = lambda lo_hi, unit: (f"{lo_hi[0]:.0f} {unit}" if round(lo_hi[0]) == round(lo_hi[1])
                                   else f"{lo_hi[0]:.0f}-{lo_hi[1]:.0f} {unit}")
        print(f"\nSAME MONEY, YOUR CHOICE   {band['designs']} designs cost within {NEAR_OPTIMAL:.0%} of the best over "
              f"20 years (solar {span(band['solar_kw'], 'kW')}, battery {span(band['battery_kwh'], 'kWh')}). "
              f"Prices are only good to ~10%, so pick by priority:")
        rows = (("cheapest over 20 years", w), ("cheapest to buy", opts["lowest_purchase_cost"]),
                ("most diesel-free", opts["most_diesel_free"]))
        for name, o in rows:
            print(f"  {name:24s} {o['solar_kw']:6.1f} kW + {o['battery_kwh']:6.1f} kWh   buy {money(o['capex']):>12s}"
                  f"   20-yr {money(o['cost']['total']):>12s}   diesel cut "
                  f"{100 * o['litres_saved_p50'] / today['litres_per_year']:3.0f}%")

    cheap = r["cheapest_ignoring_clinic"]
    if r["clinic_guaranteed"] and r["price_of_resilience"] > 1:
        print(f"\nPRICE OF RESILIENCE   cheapest design ignoring the clinic: {cheap['solar_kw']:.1f} kW + "
              f"{cheap['battery_kwh']:.1f} kWh, {money(cheap['cost']['total'])}; clinic "
              f"{'survives' if cheap['clinic_survives'] else 'FAILS'} ({cheap['clinic_hours_powered']}/168 h)")
        print(f"  keeping the clinic alive costs {money(r['price_of_resilience'])} over 20 years "
              f"({100 * r['price_of_resilience'] / cheap['cost']['total']:.1f}%)")
    elif r["clinic_guaranteed"]:
        print("\nPRICE OF RESILIENCE   zero: the cheapest design already keeps the clinic alive")
    else:
        print("\n  No design in the grid keeps the clinic alive for 7 days: showing the cheapest reliable one")
    print()
    return r


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "funafuti")
