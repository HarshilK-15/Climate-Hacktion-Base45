"""Engine tests: how we know the engine is right. Owner: Elec B.

Run from the project folder:  python -m engine.test_engine   (prints the spoken sentence per test)
                          or:  python -m pytest engine/test_engine.py

The first line of each test's docstring is the sentence to say to a judge.
"""
import csv
from pathlib import Path

import numpy as np

from engine.dispatch import (FUEL_A_L_PER_KWH, FUEL_B_L_PER_H_PER_KW_RATED, TECH_DEFAULTS,
                             dispatch, fuel_litres, simulate_year)
from engine.p90 import p50_p90, p90_confidence
from engine.weather_files import nearest_weather
from data.loads import build_load

CONTRACTS = Path(__file__).parent.parent / "contracts"
# Cummins C55 D5e prime rating (40 kW): litres/hour at 1/4, 1/2, 3/4, full load
CUMMINS_PRIME = [(10.0, 3.8), (20.0, 6.4), (30.0, 9.4), (40.0, 12.0)]

# Real NASA sunshine for Funafuti (data/out/funafuti_ghi.csv), or synthetic if Data Sci's file is missing
_GHI, WEATHER_LABEL, _ = nearest_weather(-8.52, 179.20)
SUN_W = _GHI[0]                                       # W/m2, what dispatch() takes
SUN_KW = SUN_W / 1000.0                               # kW/m2, what simulate_year() takes
NO_SUN = np.zeros(8760)
SOC_FLOOR_PCT = TECH_DEFAULTS["batt_min_soc"] * 100


def _random_island(seed):
    """A random but plausible island: noisy load, day/night sun with random cloud, W/m2."""
    rng = np.random.default_rng(seed)
    load = rng.uniform(2, 30, 8760)
    day = np.r_[np.zeros(6), np.sin(np.linspace(0, np.pi, 12)), np.zeros(6)]
    ghi = np.tile(day, 365) * rng.uniform(0, 1100, 8760)
    gen = float(rng.choice([20, 40, 60, 100]))
    return load, ghi, gen


# ---------------------------------------------------------------------------------------------
# The four tests from the brief (3.3)
# ---------------------------------------------------------------------------------------------

def test_zero_solar_matches_hand_calc():
    """With no sun, the diesel bill matches a hand calculation to a millionth of a litre.

    10 kW for 24 h on a 30 kW set: 0.27 x 240 kWh + 0.05 x 30 kW x 24 h = 100.8 L.
    (The brief used a 40 kW set, but 10 kW is below its 30% floor of 12 kW, so the set
    really makes 12 kW: that case is test_generator_never_runs_below_30_percent.)
    """
    r = simulate_year([10.0] * 24, [0.0] * 24, pv_kw=0, batt_kwh=0, gen_kw=30,
                      diesel_price=2.0, A=0.27, B=0.05)
    assert abs(r["litres"] - (0.27 * 240 + 0.05 * 30 * 24)) < 1e-6
    assert r["gen_hours"] == 24 and r["blackout_hours"] == 0


def test_battery_never_empties_below_minimum():
    """Under a spiky load the battery never drops below its 20% floor or goes above 100%, in any hour."""
    rng = np.random.default_rng(3)
    load = rng.uniform(2, 10, 8760)
    load[rng.random(8760) < 0.05] = 60.0          # 5% of hours spike past the generator
    r = simulate_year(load, SUN_KW, pv_kw=40, batt_kwh=100, gen_kw=30, diesel_price=2.0, record=True)
    soc = r["trace"]["soc_pct"]
    assert min(soc) >= SOC_FLOOR_PCT - 1e-9
    assert max(soc) <= 100 + 1e-9
    assert min(soc) < SOC_FLOOR_PCT + 1           # the floor really was reached, so it was tested
    assert r["blackout_hours"] > 0                  # the spikes really were too big to cover


def test_more_solar_never_increases_diesel():
    """Adding solar panels never increases diesel use: checked from 0 to 400 kW of panels."""
    load = np.random.default_rng(1).uniform(5, 15, 8760)
    ghi = np.random.default_rng(2).uniform(0, 1, 8760)
    litres = [simulate_year(load, ghi, pv_kw=pv, batt_kwh=100, gen_kw=40, diesel_price=2.0)["litres"]
              for pv in (0, 25, 50, 100, 200, 400)]
    assert all(b <= a + 1e-6 for a, b in zip(litres, litres[1:])), litres
    assert litres[-1] < litres[0]


def test_no_blackouts_when_system_huge():
    """A system much bigger than the load has zero blackout hours all year."""
    ghi = ([0.8] * 12 + [0.0] * 12) * 365
    r = simulate_year([5.0] * 8760, ghi, pv_kw=200, batt_kwh=500, gen_kw=50, diesel_price=2.0)
    assert r["blackout_hours"] == 0 and r["unserved_kwh"] == 0


# ---------------------------------------------------------------------------------------------
# Physics that must always hold
# ---------------------------------------------------------------------------------------------

def test_energy_balance():
    """Every kWh of demand is met by solar, battery or diesel, or counted as unserved: nothing appears or vanishes."""
    r = simulate_year(np.full(8760, 20.0), SUN_KW, 30.0, 60.0, 40.0, diesel_price=2.0)
    supplied = r["solar_used_kwh"] + r["batt_out_kwh"] + r["gen_to_load_kwh"] + r["unserved_kwh"]
    assert abs(supplied - r["load_kwh"]) < 1e-6 * r["load_kwh"]


def test_physics_holds_on_224_random_designs():
    """On 8 random islands x 28 designs: energy balances, the battery stays in limits, no flow goes negative, more solar never adds diesel."""
    S = np.array([0, 10, 25, 50, 80, 120, 160])[:, None]
    B = np.array([0, 50, 150, 300])[None, :]
    for seed in range(8):                           # 8 islands x 7 x 4 designs = 224 runs
        load, ghi, gen = _random_island(seed)
        t, _ = dispatch(load, ghi, S, B, gen, TECH_DEFAULTS)
        supplied = t["solar_direct"] + t["batt_out"] + t["gen_to_load"] + t["unserved"]
        assert np.allclose(supplied, t["load_kwh"], rtol=1e-9), seed
        assert np.all(t["soc_min_frac"] >= TECH_DEFAULTS["batt_min_soc"] - 1e-9), seed
        for k in ("fuel_l", "gen_kwh", "solar_direct", "batt_out", "unserved", "curtailed", "gen_dumped"):
            assert np.all(t[k] >= -1e-9), (seed, k)
        assert np.all(t["gen_kwh"] <= gen * 8760 + 1e-6)
        assert np.all(np.diff(t["fuel_l"], axis=0) <= 1e-6), seed


def test_bigger_battery_costs_at_most_a_fraction_of_a_percent_more_diesel():
    """A bigger battery is never more than 0.2% worse on diesel: our simple hour-by-hour rules are honest about not being perfect.

    Unlike solar, battery size is not strictly monotone: with a 30% generator floor and an
    "engine on" fuel cost, a bigger battery can shift a generator start and add an hour or two
    of running. Fuzzing found at most +0.09%, so the design search tries sizes instead of assuming.
    """
    S = np.array([0, 40, 80, 120])[:, None]
    B = np.linspace(0, 400, 11)[None, :]
    for seed in range(8):
        load, ghi, gen = _random_island(seed)
        f = dispatch(load, ghi, S, B, gen, TECH_DEFAULTS)[0]["fuel_l"]
        assert np.all(np.diff(f, axis=1) <= 0.002 * f[:, 1:]), seed
        assert np.all(f[:, -1] <= f[:, 0])           # but a big battery always beats none


# ---------------------------------------------------------------------------------------------
# The generator and fuel curve
# ---------------------------------------------------------------------------------------------

def test_fuel_curve_matches_datasheet():
    """Our diesel curve matches all four points on the Cummins datasheet to within 0.3 L/h."""
    for kw_out, litres in CUMMINS_PRIME:
        assert abs(fuel_litres(kw_out, 40.0, 1.0) - litres) <= 0.3


def test_idle_generator_still_burns_fuel():
    """A generator that is on but making nothing still burns fuel: B x rated kW every hour."""
    assert abs(fuel_litres(0.0, 40.0, 1.0) - FUEL_B_L_PER_H_PER_KW_RATED * 40.0) < 1e-12


def test_generator_never_runs_below_30_percent():
    """The generator never runs below 30% load: a 5 kW island on a 40 kW set burns fuel for 12 kW."""
    r = simulate_year(np.full(8760, 5.0), NO_SUN, 0.0, 0.0, 40.0, diesel_price=2.0)
    per_hour = FUEL_A_L_PER_KWH * 12.0 + FUEL_B_L_PER_H_PER_KW_RATED * 40.0   # 4.312 L
    assert abs(r["litres"] - per_hour * 8760) < 1e-6
    assert abs(r["gen_dumped_kwh"] - 7.0 * 8760) < 1e-6
    assert r["blackout_hours"] == 0


def test_forced_generator_output_is_stored_not_wasted():
    """When the 30% floor forces extra power, the battery soaks it up, so the generator runs 40% fewer hours."""
    load = np.full(8760, 5.0)
    no_batt = simulate_year(load, NO_SUN, 0.0, 0.0, 40.0, diesel_price=2.0)
    batt = simulate_year(load, NO_SUN, 0.0, 50.0, 40.0, diesel_price=2.0)
    assert batt["gen_hours"] < 0.6 * no_batt["gen_hours"]
    assert batt["litres"] < no_batt["litres"]
    assert batt["blackout_hours"] == 0


def test_blackout_hours_when_generator_too_small():
    """If the generator is too small and there is no sun, every hour is a blackout and the shortfall is counted exactly."""
    r = simulate_year(np.full(8760, 50.0), NO_SUN, 0.0, 0.0, 30.0, diesel_price=2.0)
    assert r["blackout_hours"] == 8760
    assert abs(r["unserved_kwh"] - 20.0 * 8760) < 1e-6


def test_battery_drains_exactly_to_its_floor():
    """With no sun and no generator, the battery gives exactly its usable energy, then stops."""
    r = simulate_year(np.full(8760, 2.0), NO_SUN, 0.0, 100.0, 0.0, diesel_price=2.0)
    usable = (TECH_DEFAULTS["batt_start_soc"] - TECH_DEFAULTS["batt_min_soc"]) * 100.0
    assert abs(r["batt_out_kwh"] - usable * TECH_DEFAULTS["batt_eff_discharge"]) < 1e-6
    assert r["gen_hours"] == 0


# ---------------------------------------------------------------------------------------------
# Money, baselines, and the two engines agreeing
# ---------------------------------------------------------------------------------------------

def test_diesel_only_design_saves_nothing():
    """The diesel-only design saves exactly zero litres against the baseline."""
    r = simulate_year(np.full(8760, 10.0), SUN_KW, 0.0, 0.0, 20.0, diesel_price=2.0)
    assert r["litres"] == r["baseline_litres"]
    assert r["litres_saved"] == 0.0 and r["money_saved"] == 0.0


def test_solar_and_battery_save_diesel_and_money():
    """Solar plus battery saves diesel, and the money saved is exactly litres times the price."""
    r = simulate_year(np.full(8760, 10.0), SUN_KW, 30.0, 60.0, 20.0, diesel_price=2.10)
    assert r["litres_saved"] > 0
    assert abs(r["money_saved"] - 2.10 * r["litres_saved"]) < 1e-9
    assert 0 < r["renewable_share"] <= 1


def test_simple_loop_matches_vectorised_engine():
    """Two separate implementations, a readable loop and the fast numpy engine, agree on every total for every design."""
    site = {"id": "t", "households": 80, "has_clinic": True, "has_school": True, "other_kw": 3}
    island, _, _ = build_load(site)
    pairs = {"litres": "fuel_l", "gen_hours": "gen_hours", "blackout_hours": "unserved_hours",
             "solar_used_kwh": "solar_direct", "batt_out_kwh": "batt_out", "gen_kwh": "gen_kwh",
             "gen_to_load_kwh": "gen_to_load", "unserved_kwh": "unserved",
             "curtailed_kwh": "curtailed", "gen_dumped_kwh": "gen_dumped"}
    designs = [(0, 0, 60), (40, 0, 60), (0, 150, 60), (60, 200, 60), (120, 400, 60),
               (60, 200, 0), (60, 200, 15)]   # incl. no generator and an undersized one
    for pv, batt, gen in designs:
        a = simulate_year(island, SUN_KW, pv, batt, gen, diesel_price=2.0)
        b, _ = dispatch(island, SUN_W, pv, batt, gen, TECH_DEFAULTS)
        for ka, kb in pairs.items():
            assert abs(a[ka] - float(b[kb])) <= 1e-6 * max(1.0, abs(a[ka])), (pv, batt, gen, ka)


def test_reads_the_agreed_contract_files():
    """The engine runs straight off the team's agreed file formats (contracts/load_profile.csv, solar_ghi.csv)."""
    def column(name, col):
        with open(CONTRACTS / name, newline="") as f:
            return [float(row[col]) for row in csv.DictReader(f)]
    load, ghi = column("load_profile.csv", "load_kw"), column("solar_ghi.csv", "ghi_kwm2")
    assert len(load) == len(ghi) > 0
    r = simulate_year(load, ghi, pv_kw=5, batt_kwh=10, gen_kw=10, diesel_price=2.0)
    supplied = r["solar_used_kwh"] + r["batt_out_kwh"] + r["gen_to_load_kwh"] + r["unserved_kwh"]
    assert abs(supplied - sum(load)) < 1e-9
    assert r["blackout_hours"] == 0


# ---------------------------------------------------------------------------------------------
# P50 / P90
# ---------------------------------------------------------------------------------------------

def test_p90_is_the_saving_we_beat_nine_years_in_ten():
    """P90 is the saving we beat in 9 years out of 10; P50 is the typical year."""
    saved = np.arange(1000.0, 3000.0, 100.0)       # 20 years: 1000, 1100, ... 2900 litres
    p50, p90 = p50_p90(saved)
    assert p50 == 1950.0
    assert abs(p90 - 1190.0) < 1e-9                # numpy linear interpolation at the 10th percentile
    assert np.mean(saved >= p90) >= 0.9


def test_p90_confidence_brackets_the_p90():
    """We say how sure we are of the P90: dropping any year, or resampling the years, keeps it in a stated range."""
    saved = np.random.default_rng(4).normal(30000, 1500, 20)
    c = p90_confidence(saved)
    assert c["loo_min"] <= c["p90"] <= c["loo_max"]
    assert c["boot_low"] <= c["p90"] <= c["boot_high"]
    assert c["p90"] <= c["p50"]
    steady = p90_confidence(np.full(20, 30000.0))  # identical years: no uncertainty at all
    assert steady["boot_low"] == steady["boot_high"] == 30000.0


if __name__ == "__main__":
    import time
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    print(f"Weather: {WEATHER_LABEL}" + chr(10))
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.2f}s)\n      \"{t.__doc__.strip().splitlines()[0]}\"")
    print(f"\nAll {len(tests)} engine tests passed.")
