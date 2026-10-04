"""Our engine vs a real, published Pacific microgrid: Ta'u, American Samoa. Owner: Elec B.

    python -m engine.published_case

Ta'u (pop. ~600, 70 miles east of Tutuila) switched from diesel to a SolarCity / Tesla microgrid in
Nov 2016: 1.41 MW of solar + 6 MWh of Tesla Powerpack batteries, reported to replace ~109,500 US
gallons of diesel a year and run the island on (almost) 100% solar. Diesel generators stay as backup.

What we check, without tuning a single engine number to fit:
  1. FUEL CURVE. Push the reported diesel (109,500 gal = 414,500 L) back through our Cummins fuel
     curve on Ta'u's ~300 kW plant: it implies the island used X kWh a year. A separate published
     figure says ~1,300,000 kWh. Agreement = our litres-per-kWh maths matches a real island.
  2. DISPATCH + SOLAR. Run the as-built 1.41 MW + 6 MWh through our hour-by-hour engine on 20
     years of NASA weather. Published: ~100% solar. Ours: the solar share and diesel saved.
  3. DESIGN SEARCH. What our optimiser would have built for Ta'u, and what the extra size Tesla
     chose buys: the last few percent of diesel (and days of autonomy) cost real money.
Honest limits: no public hourly data for Ta'u exists, so the island's daily pattern is Data Sci's
village shape flattened to the reported peak, and the weather is the nearest 20-year NASA record
(Savai'i, ~320 km west).
"""
import numpy as np

from data.loads import CLINIC_CRITICAL_KW, HOUSEHOLD_SHAPE
from data.weather import get_ghi, get_site
from engine import sim
from engine.dispatch import dispatch
from engine.optimise import cost_breakdown, no_fuel_ship, optimise
from engine.weather_files import _distance_km

HOURS = 8760
LITRES_PER_US_GALLON = 3.785411784

TAU = {"id": "tau", "name": "Ta'u", "country": "American Samoa", "lat": -14.23, "lon": -169.47}

# ---- PUBLISHED (sources below; every number used is one of these) ----
PUBLISHED = {
    "pv_kw": 1410.0,                 # 1,410 kW, 5,328 panels (NREL / DOI; SolarCity)
    "battery_kwh": 6000.0,           # 60 Tesla Powerpacks, 6 MWh
    "diesel_gallons_per_year": 109_500,   # "save in the region of 109,500 gallons a year" (SolarCity)
    "grid_capacity_kw": 300.0,       # "power grid capacity of approximately 300 kW" (NREL 2013, p.5)
    "energy_kwh_per_year": 1_300_000,     # "diesel units which generate 1,300,000 kWh/year" (secondary)
    "peak_kw": 229.0,                # "peak load was 229 kW" (secondary, same source)
    "solar_share": "almost 100%",
    "autonomy_days_claimed": 3,      # "power the island for three days without sun" (SolarCity)
    "recharge_hours_claimed": 7,     # "fully recharge in seven hours" (SolarCity)
}
SOURCES = [
    ("NREL for US DOI, American Samoa Energy Strategies (Dec 2013): Ta'u grid ~300 kW",
     "https://www.doi.gov/sites/default/files/uploads/American-Samoa-Final-Strategic-Energy-Plan.pdf"),
    ("Energy-Storage.news: SolarCity-Tesla completes Ta'u microgrid, 1.4 MW / 6 MWh, ~109,500 gal/yr",
     "https://energy-storage.news/solarcity-tesla-completes-diesel-replacing-utility-scale-microgrid-in-american-samoa"),
    ("pv magazine: Tesla/SolarCity completes PV and battery system to power Samoan island",
     "https://pv-magazine-usa.com/?p=1950"),
    ("Utility Dive: Tesla, SolarCity ready diesel-reducing microgrid in American Samoa",
     "https://www.utilitydive.com/news/tesla-solarcity-ready-diesel-reducing-microgrid-in-american-samoa/431053/"),
    ("Microgrid Projects: Ta'u - diesel backup kept (3 x 500 kW Cummins sets, ASPA FY2024 audit)",
     "https://microgridprojects.com/projects/tau-solarcity-tesla-microgrid-american-samoa"),
    ("Euan Mearns, Solar power on the island of Ta'u (secondary): 1,300,000 kWh/yr, 229 kW peak",
     "https://euanmearns.com/solar-power-on-the-island-of-tau-a-preliminary-appraisal/"),
]
# ASSUMPTION: the island's daily pattern is Data Sci's village shape (data/loads.py), flattened
# just enough to match the reported 229 kW peak (a whole island with shops, school, water pumps and
# the clinic is flatter than houses alone). No hourly Ta'u data is public.
# ASSUMPTION: the clinic's must-never-fail part is data/loads.py CLINIC_CRITICAL_KW (0.4 kW).


def tau_weather():
    """The nearest 20-year NASA record: Savai'i (data/sites.json), ~320 km west of Ta'u."""
    sv = get_site("savaii")
    ghi, label = get_ghi(sv["lat"], sv["lon"])
    km = _distance_km(TAU["lat"], TAU["lon"], sv["lat"], sv["lon"])
    return np.asarray(ghi, float), f"{label} at Savai'i, {km:.0f} km from Ta'u"


def tau_load(p, gen_kw=PUBLISHED["grid_capacity_kw"]):
    """The 8760-hour load whose diesel-only run on our fuel curve burns exactly the reported litres.

    Returns (load kW, litres it burns). The total comes from the DIESEL figure, not from the
    1.3 GWh one, so the 1.3 GWh can be used as an independent check.
    """
    shape = HOUSEHOLD_SHAPE / HOUSEHOLD_SHAPE.mean()
    target_peak = PUBLISHED["peak_kw"] / (PUBLISHED["energy_kwh_per_year"] / HOURS)
    a = (target_peak - 1) / (shape.max() - 1)            # 0 = flat, 1 = village shape
    day = 1 - a + a * shape
    target = PUBLISHED["diesel_gallons_per_year"] * LITRES_PER_US_GALLON

    def litres(avg_kw):
        t, _ = dispatch(np.tile(day * avg_kw, 365), np.zeros(HOURS), 0.0, 0.0, gen_kw, p)
        return float(t["fuel_l"])

    lo, hi = 1.0, gen_kw                                  # bisection: fuel rises with load
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if litres(mid) < target else (lo, mid)
    load = np.tile(day * lo, 365)
    return load, litres(lo)


def _recharge_hours(load, ghi, pv_kw, batt_kwh, gen_kw, p):
    """Hours of daylight to refill the battery from its 20% floor, on a clear day (sunniest 10% of
    days in the record), counted from sunrise."""
    days = ghi.reshape(-1, 24)
    clear = np.argsort(days.sum(axis=1))[int(0.9 * len(days))]     # 90th-percentile sunny day
    sun = days[clear]
    first = int(np.argmax(sun > 0))
    t, tr = dispatch(load[:24], sun, pv_kw, batt_kwh, gen_kw, dict(p, batt_start_soc=p["batt_min_soc"]),
                     gen_available=False, record=True)
    full = [h for h, s in enumerate(tr["soc_pct"]) if s >= 99.5]
    return (full[0] + 1 - first) if full else None


def compare(grid=(30, 20)):
    """The full comparison -> dict of plain numbers, with sources."""
    p = dict(sim.DEFAULTS)
    gen_kw = PUBLISHED["grid_capacity_kw"]
    load, base_litres = tau_load(p, gen_kw)
    ghi, label = tau_weather()
    kwh_year = float(load.sum())
    S, B = PUBLISHED["pv_kw"], PUBLISHED["battery_kwh"]

    # 2. The as-built system, every weather year
    t, _ = dispatch(load, ghi.T, S, B, gen_kw, p)
    saved = base_litres - t["fuel_l"]
    share = 1 - t["gen_to_load"] / t["load_kwh"]
    # Ta'u's "3 days without sun": the darkest 3 days in 20 years, generator off, WHOLE island
    three = no_fuel_ship(load, load, ghi, S, B, gen_kw, p, days=3)
    usable = B * (1 - p["batt_min_soc"]) * p["batt_eff_discharge"]

    # 3. What our optimiser would build for Ta'u (fuel at the portfolio average price, AUD)
    price = p["diesel_price_per_litre"]
    opt = optimise(load, CLINIC_CRITICAL_KW, ghi, gen_kw, price, p, n_pv=grid[0], n_batt=grid[1])
    w = opt["winner"]
    built = cost_breakdown(S, B, float(t["fuel_l"].mean()), float(t["gen_kwh"].mean()), kwh_year, price, p)
    ours_share = w["solar_share"]

    r = lambda x, n=0: round(float(x), n) if n else int(round(float(x)))
    out = {
        "site": "Ta'u, American Samoa", "weather": label, "published": PUBLISHED,
        "sources": [{"what": a, "url": u} for a, u in SOURCES],
        "check_1_fuel_curve": {
            "reported_diesel_litres_per_year": r(PUBLISHED["diesel_gallons_per_year"] * LITRES_PER_US_GALLON),
            "implied_kwh_per_year_from_our_fuel_curve": r(kwh_year),
            "reported_kwh_per_year": PUBLISHED["energy_kwh_per_year"],
            "difference_percent": r(100 * (kwh_year / PUBLISHED["energy_kwh_per_year"] - 1), 1),
            "implied_kwh_per_litre": r(kwh_year / base_litres, 2),
        },
        "check_2_as_built": {
            "pv_kw": S, "battery_kwh": B,
            "solar_share_percent_p50": r(100 * np.median(share), 1),
            "solar_share_percent_worst_year": r(100 * share.min(), 1),
            "diesel_saved_litres_p50": r(np.median(saved)),
            "diesel_saved_gallons_p50": r(np.median(saved) / LITRES_PER_US_GALLON),
            "diesel_saved_vs_reported_percent": r(100 * np.median(saved) / (
                PUBLISHED["diesel_gallons_per_year"] * LITRES_PER_US_GALLON), 1),
            "generator_hours_per_year_p50": r(np.median(t["gen_hours"])),
            "blackout_hours_worst_year": int(t["unserved_hours"].max()),
            "battery_alone_days_at_average_load": r(usable / (kwh_year / 365), 2),
            "darkest_3_days_in_record_whole_island_hours_powered_of_72": int(three["hours_powered"]),
            "darkest_3_days_sun_vs_average_percent": r(100 * three["sun_vs_average"], 1),
            "recharge_hours_clear_day_from_20pct": _recharge_hours(load, ghi, S, B, gen_kw, p),
            "lifetime_cost_aud_our_cost_model": r(built["total"]),
        },
        "check_3_our_design": {
            "pv_kw": w["solar_kw"], "battery_kwh": w["battery_kwh"],
            "solar_share_percent_p50": r(100 * ours_share, 1),
            "percent_diesel_cut_p50": r(100 * w["litres_saved_p50"] / base_litres, 1),
            "lifetime_cost_aud": r(w["cost"]["total"]),
            "as_built_costs_more_aud": r(built["total"] - w["cost"]["total"]),
            "as_built_costs_more_percent": r(100 * (built["total"] / w["cost"]["total"] - 1), 1),
            "diesel_price_used_aud_per_litre": price,
        },
        "load_model": {"kwh_per_day": r(kwh_year / 365), "average_kw": r(kwh_year / HOURS, 1),
                       "peak_kw": r(load.max(), 1), "generator_kw": gen_kw,
                       "note": "total from the reported diesel via our fuel curve; daily shape = "
                               "data/loads.py village pattern flattened to the reported 229 kW peak"},
    }
    c1, c2, c3 = out["check_1_fuel_curve"], out["check_2_as_built"], out["check_3_our_design"]
    out["summary"] = [
        f"Fuel curve: Ta'u's reported {PUBLISHED['diesel_gallons_per_year']:,} gal/yr of diesel implies "
        f"{c1['implied_kwh_per_year_from_our_fuel_curve']:,} kWh/yr on our fuel curve; the separately "
        f"reported figure is {PUBLISHED['energy_kwh_per_year']:,} kWh ({c1['difference_percent']:+.1f}%).",
        f"As built (1.41 MW + 6 MWh): our engine gives {c2['solar_share_percent_p50']}% solar in a typical "
        f"year ({c2['solar_share_percent_worst_year']}% in the worst of 20), saving "
        f"{c2['diesel_saved_gallons_p50']:,} gal/yr; published: almost 100% solar, ~109,500 gal/yr.",
        f"Our optimiser would build {c3['pv_kw']:.0f} kW + {c3['battery_kwh']:.0f} kWh for a "
        f"{c3['percent_diesel_cut_p50']:.0f}% diesel cut; on our prices the as-built system costs "
        f"{c3['as_built_costs_more_percent']:.0f}% more over 20 years to remove the last "
        f"{100 - c3['percent_diesel_cut_p50']:.0f}% of diesel. A fair choice for an island that wanted to "
        f"stop fuel shipments; the engine puts a price on it.",
        f"Where we differ: the battery alone covers {c2['battery_alone_days_at_average_load']:.1f} days of "
        f"average load, not the advertised 3; in the darkest 3 days of 20 years (starting from the charge "
        f"normal running left, generator off) the whole island stays powered "
        f"{c2['darkest_3_days_in_record_whole_island_hours_powered_of_72']} of 72 hours "
        f"({c2['darkest_3_days_sun_vs_average_percent']:.0f}% of normal sun). Refill from 20% on a clear "
        f"day: {c2['recharge_hours_clear_day_from_20pct']} h from sunrise (advertised: 7 h).",
    ]
    return out


if __name__ == "__main__":
    import json
    res = compare()
    print("\nPUBLISHED CASE - Ta'u, American Samoa (SolarCity / Tesla, 2016)")
    print(f"  weather: {res['weather']}\n")
    for line in res["summary"]:
        print("  - " + line)
    print("\n" + json.dumps({k: res[k] for k in ("check_1_fuel_curve", "check_2_as_built", "check_3_our_design")},
                            indent=2))
