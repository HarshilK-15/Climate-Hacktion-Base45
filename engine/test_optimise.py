"""Optimiser and cost-model tests (brief 3.5). Owner: Elec B.

Run from the project folder:  python -m engine.test_optimise
                          or:  python -m pytest engine/test_optimise.py
The first line of each test's docstring is the sentence to say to a judge.
"""
import contextlib
import io

import numpy as np

from engine.dispatch import simulate_year
from engine.optimise import (HOURS, NEAR_OPTIMAL, cost_breakdown, main, no_fuel_ship, optimise,
                             worst_stretch)
from engine.sim import DEFAULTS, generator_size
from engine.weather_files import nearest_weather
from data.loads import build_load
from data.weather import get_site

P = dict(DEFAULTS)
YEARS_PV = sum(1.04 ** -y for y in range(1, 21))   # today's value of 1 AUD a year for 20 years at 4%


def test_cost_model_matches_hand_calculation():
    """The 20-year cost matches a hand calculation, line by line: panels, battery, battery swap, diesel, upkeep."""
    c = cost_breakdown(10.0, 20.0, fuel_l=1000.0, gen_kwh=500.0, load_kwh=10000.0, price=2.0, p=P)
    assert c["solar"] == 10 * 2800 and c["battery"] == 20 * 850
    assert abs(c["battery_replacement"] - 17000 / 1.04 ** 10) < 1e-6
    assert abs(c["diesel"] - YEARS_PV * 1000 * 2.0) < 1e-6
    assert abs(c["generator_upkeep"] - YEARS_PV * 500 * 0.08) < 1e-6
    assert abs(c["solar_battery_upkeep"] - YEARS_PV * 0.015 * (28000 + 17000)) < 1e-6
    parts = sum(c[k] for k in ("solar", "battery", "battery_replacement", "diesel",
                                "generator_upkeep", "solar_battery_upkeep"))
    assert abs(c["total"] - parts) < 1e-6
    assert abs(c["cost_per_kwh"] - c["total"] / (YEARS_PV * 10000)) < 1e-12


def test_battery_is_replaced_every_battery_life():
    """The battery is bought again every 10 years of its life: once in a 20-year project, twice if it lasts 7."""
    one = cost_breakdown(0.0, 100.0, 0.0, 0.0, 1.0, 2.0, P)["battery_replacement"]
    assert abs(one - 85000 / 1.04 ** 10) < 1e-6
    two = cost_breakdown(0.0, 100.0, 0.0, 0.0, 1.0, 2.0, dict(P, battery_life_years=7))["battery_replacement"]
    assert abs(two - 85000 * (1.04 ** -7 + 1.04 ** -14)) < 1e-6


def test_finds_the_darkest_week_in_the_whole_record():
    """The no-fuel-ship test uses the darkest 7 days anywhere in 20 years, even across a year boundary."""
    ghi = np.full((3, HOURS), 500.0)
    ghi[1, 5000:5168] = 0.0                     # a planted black week in year 2
    start, sun = worst_stretch(ghi)
    assert start == HOURS + 5000 and sun == 0.0
    ghi = np.full((2, HOURS), 500.0)
    ghi[0, -24:] = ghi[1, :144] = 0.0          # 1 day at the end of year 1 + 6 days of year 2
    assert worst_stretch(ghi)[0] == HOURS - 24


def test_clinic_survival_matches_hand_calculation():
    """With no sun, a 200 kWh battery keeps a 0.4 kW clinic going exactly 142 hours; 250 kWh lasts the week."""
    dark = np.zeros((1, HOURS))
    load = np.full(HOURS, 5.0)
    # usable = (50% start - 20% floor) x 200 kWh x 0.95 out = 57 kWh -> 57 / 0.4 = 142.5 h
    r = no_fuel_ship(load, 0.4, dark, 0.0, np.array([200.0, 250.0]), 10.0, P)
    assert list(r["survives"]) == [False, True]
    assert r["first_outage_hour"][0] == 142 and np.isinf(r["first_outage_hour"][1])
    assert r["hours_powered"][0] == 142


def test_battery_starts_the_bad_week_as_normal_running_left_it():
    """No convenient full battery: if normal running left it empty when the ship fails, the clinic fails at once."""
    ghi = np.full((1, HOURS), 50.0)             # dim sun all year: panels never out-produce the load
    ghi[0, 4000:4168] = 0.0                     # the darkest week starts at hour 4000
    load = np.full(HOURS, 5.0)
    r = no_fuel_ship(load, 0.4, ghi, 1.0, 100.0, 10.0, P)
    assert r["start"] == 4000
    assert r["first_outage_hour"] == 0          # battery was already at its floor
    at_start = np.full((1, HOURS), 50.0)
    at_start[0, :168] = 0.0                     # same dark week, but at hour 0: battery still at 50%
    full = no_fuel_ship(load, 0.4, at_start, 1.0, 100.0, 10.0, P)
    assert full["start"] == 0 and full["first_outage_hour"] > 0   # a 50% start would have hidden the problem


def test_recorded_start_charge_matches_a_rerun():
    """The battery charge recorded during the main run equals re-running the year up to the bad week."""
    from engine.dispatch import dispatch
    site = get_site("eua")
    load, critical, _ = build_load(site)
    ghi = np.asarray(nearest_weather(site["lat"], site["lon"])[0], float)
    S, B = np.array([0.0, 40, 90])[:, None], np.array([0.0, 80, 200])[None, :]
    year, hour = divmod(worst_stretch(ghi)[0], HOURS)
    t, _ = dispatch(load, ghi.T, S[..., None], B[..., None], 35.0, P, snapshot_hours=(hour,))
    rerun, _ = dispatch(load[:hour], ghi[year, :hour], S, B, 35.0, P)
    assert np.array_equal(t["soc_snapshots"][hour][..., year], rerun["soc_end_frac"])


def test_optimiser_matches_brute_force_with_the_simple_loop():
    """The fast optimiser picks exactly the design a slow brute force with the readable engine picks."""
    site = get_site("eua")
    load, _, _ = build_load(site)
    ghi, _, _ = nearest_weather(site["lat"], site["lon"])
    gen, price = generator_size(site, load), 1.9
    r = optimise(load, 0.0, ghi, gen, price, P, n_pv=4, n_batt=4, refine=False)
    base = simulate_year(load, np.zeros(HOURS), 0, 0, gen, price)
    best, best_cost = None, np.inf
    for s in r["grid"]["solar_kw"]:
        for b in r["grid"]["battery_kwh"]:
            runs = [simulate_year(load, g / 1000.0, s, b, gen, price) for g in ghi]
            if max(x["blackout_hours"] for x in runs) > base["blackout_hours"]:
                continue
            cost = cost_breakdown(s, b, np.mean([x["litres"] for x in runs]),
                                  np.mean([x["gen_kwh"] for x in runs]), float(load.sum()), price, P)["total"]
            if cost < best_cost:
                best, best_cost = (round(float(s), 1), round(float(b), 1)), cost
    w = r["winner"]
    assert (w["solar_kw"], w["battery_kwh"]) == best
    assert abs(w["cost"]["total"] - best_cost) <= 1e-6 * best_cost


def test_winner_is_reliable_keeps_the_clinic_alive_and_beats_diesel():
    """The winner has no more blackouts than today in any year, keeps the clinic alive, and costs less than diesel."""
    site = get_site("malekula")
    load, critical, _ = build_load(site)
    ghi, _, _ = nearest_weather(site["lat"], site["lon"])
    r = optimise(load, critical.max(), ghi, generator_size(site, load), 2.0, P, n_pv=12, n_batt=8)
    w = r["winner"]
    assert r["clinic_guaranteed"] and w["clinic_survives"] and w["clinic_hours_powered"] == 168
    assert w["blackout_hours_worst_year"] <= r["today"]["blackout_hours_per_year"]
    assert w["cost"]["total"] < r["today"]["cost"]["total"]
    assert r["price_of_resilience"] >= -1e-6
    for o in r["options"].values():            # "same money" options really are within 2%
        assert o["cost"]["total"] <= (1 + NEAR_OPTIMAL) * w["cost"]["total"] + 1e-6
        assert o["clinic_survives"]


def test_finer_grid_never_makes_the_answer_worse():
    """Refining around the winner can only find an equal or cheaper design."""
    site = get_site("eua")
    load, critical, _ = build_load(site)
    ghi, _, _ = nearest_weather(site["lat"], site["lon"])
    args = (load, critical.max(), ghi, generator_size(site, load), 1.9, P)
    coarse = optimise(*args, n_pv=8, n_batt=6, refine=False)["winner"]["cost"]["total"]
    fine = optimise(*args, n_pv=8, n_batt=6, refine=True)["winner"]["cost"]["total"]
    assert fine <= coarse + 1e-6


def test_same_plan_twice_is_instant_and_safe():
    """Asking for the same plan twice returns the stored answer instantly, and a changed input is recomputed."""
    import time
    from engine.sim import simulate
    site = get_site("eua")
    load, critical, _ = build_load(site)
    ghi, label, _ = nearest_weather(site["lat"], site["lon"])
    first = simulate(site, load, critical, ghi, label, grid=(5, 4))
    t0 = time.perf_counter()
    again = simulate(dict(site), load.copy(), critical, ghi, label, grid=(5, 4))
    assert time.perf_counter() - t0 < 0.5 and again == first
    again["design"]["solar_kw"] = -1                 # a caller editing its copy...
    assert simulate(site, load, critical, ghi, label, grid=(5, 4)) == first   # ...can't corrupt the store
    dearer = simulate(dict(site, diesel_price_per_litre=3.0), load, critical, ghi, label, grid=(5, 4))
    assert dearer["today"]["diesel_cost_per_year"] > first["today"]["diesel_cost_per_year"]


def test_funafuti_prints_winner_cost_and_verdict():
    """Done-when for brief 3.5: optimise.py prints the winning sizes, cost and 7-day verdict for Funafuti."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = main("funafuti")
    text = out.getvalue()
    for must in ("WINNER", "kW of solar panels", "kWh of battery", "20-year cost",
                 "critical_load_survives_7d_no_fuel", "island-years"):
        assert must in text, must
    assert r["winner"]["clinic_survives"]


if __name__ == "__main__":
    import time
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.2f}s)\n      \"{t.__doc__.strip().splitlines()[0]}\"")
    print(f"\nAll {len(tests)} optimiser tests passed.")
