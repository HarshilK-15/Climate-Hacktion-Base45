"""Engine tests. Run from the project folder:  python -m engine.test_sim
(Also works with pytest.)  Owner: Elec B."""
import numpy as np

from engine.sim import DEFAULTS, dispatch, simulate
from data.weather import synthetic_ghi
from data.loads import build_load

P = dict(DEFAULTS)
SUN = synthetic_ghi(-17.0, 178.0, n_years=2)


def test_energy_balance():
    """Every kWh of demand is met by solar, battery, generator or counted as unserved."""
    load = np.full(8760, 20.0)
    tot, _ = dispatch(load, SUN[0], 30.0, 60.0, 40.0, P)
    supplied = tot["solar_direct"] + tot["batt_out"] + tot["gen_to_load"] + tot["unserved"]
    assert abs(float(supplied) - tot["load_kwh"]) < 1e-6 * tot["load_kwh"]


def test_battery_stays_in_limits():
    load = np.full(8760, 15.0)
    _, tr = dispatch(load, SUN[0], 40.0, 80.0, 30.0, P, record=True)
    assert min(tr["soc_pct"]) >= P["batt_min_soc"] * 100 - 1e-6
    assert max(tr["soc_pct"]) <= 100 + 1e-6


def test_diesel_only_matches_hand_calculation():
    """Constant 10 kW on a 20 kW generator: 0.08*20 + 0.25*10 = 4.1 L every hour."""
    load = np.full(8760, 10.0)
    tot, _ = dispatch(load, np.zeros(8760), 0.0, 0.0, 20.0, P)
    assert abs(float(tot["fuel_l"]) - 4.1 * 8760) < 1e-6


def test_more_solar_never_burns_more_diesel():
    load = np.full(8760, 12.0)
    S = np.array([0, 10, 20, 40, 80], float)
    tot, _ = dispatch(load, SUN[0], S, 0.0, 30.0, P)
    assert np.all(np.diff(tot["fuel_l"]) <= 1e-6)


def test_unserved_when_generator_too_small():
    load = np.full(8760, 50.0)
    tot, _ = dispatch(load, np.zeros(8760), 0.0, 0.0, 30.0, P)
    assert abs(float(tot["unserved"]) - 20.0 * 8760) < 1e-6


def test_full_simulation_runs_and_saves_diesel():
    site = {"id": "t", "name": "Test", "households": 80, "has_clinic": True, "has_school": True,
            "other_kw": 3, "generator_kw": 60, "diesel_price_per_litre": 2.0}
    load, critical, _ = build_load(site)
    r = simulate(site, load, critical, SUN, "synthetic")
    d = r["design"]
    assert d["litres_saved_p50"] >= d["litres_saved_p90"] >= 0
    assert d["lifetime_cost"] <= r["today"]["lifetime_cost"]
    assert len(r["week_trace"]["load"]) == 168


if __name__ == "__main__":
    import time
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.2f}s)")
    print(f"\nAll {len(tests)} engine tests passed.")
