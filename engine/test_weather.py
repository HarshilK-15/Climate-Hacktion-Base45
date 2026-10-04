"""Real-weather tests (brief 3.4). Owner: Elec B.

Run from the project folder:  python -m engine.test_weather
                          or:  python -m pytest engine/test_weather.py

1. The loader refuses files that would silently give wrong answers.
2. The engine's physics still holds on every real island and every real year.
3. A full plan runs on real weather for every site in the app.
"""
import json
import tempfile
from pathlib import Path

import numpy as np

from engine.dispatch import TECH_DEFAULTS, dispatch, simulate_year
from engine.p90 import p90_confidence, saved_per_year
from engine.sim import simulate
from engine.weather_files import PLACES, WEATHER_DIR, load_ghi_csv, nearest_weather
from data.loads import build_load
from data.weather import _cache_file, get_ghi, load_sites

CONTRACTS = Path(__file__).parent.parent / "contracts"
# The full-plan tests check the plan's shape and P90, not the search: a small grid keeps them quick.
# Search quality is tested in test_optimise.py.
TEST_GRID = (6, 4)
# ASSUMPTION: plausible yearly sunshine for the tropical Pacific, kWh/m2/yr. Wide on purpose:
# it catches unit slips (x1000) and half-empty files, not real climate differences.
# Data Sci: replace with the range from Global Solar Atlas for the portfolio.
PLAUSIBLE_KWH_M2_YR = (1400, 2600)


def _write(text):
    d = tempfile.mkdtemp()
    f = Path(d) / "x_ghi.csv"
    f.write_text(text)
    return f


def _day(peak_wm2):
    """24 hours of sunshine: dark nights, a sine-shaped day peaking at noon."""
    return np.r_[np.zeros(6), peak_wm2 * np.sin(np.linspace(0, np.pi, 13))[:12], np.zeros(6)]


def _cached_sites():
    """Sites with Data Sci's 20-year download (python -m data.weather fetch) -> (site, ghi, years)."""
    out = []
    for site in load_sites():
        f = _cache_file(site["lat"], site["lon"])
        if f.exists():
            d = np.load(f)
            out.append((site, d["ghi"].astype(float), d["years"].tolist()))
    return out


def _real_sites():
    """Every app site with the real weather file nearest to it (skips sites with none)."""
    out = []
    for site in load_sites():
        ghi, label, info = nearest_weather(site["lat"], site["lon"])
        if info.get("place"):
            out.append((site, ghi, label))
    return out


# ---------------------------------------------------------------------------------------------
# 1. The loader
# ---------------------------------------------------------------------------------------------

def test_reads_brief_format_one_column_kw():
    """A bare one-column kW/m2 file (the brief's format) is read and converted to W/m2."""
    vals = np.tile(_day(0.9), 365 * 2)
    g, info = load_ghi_csv(_write("\n".join(f"{v:.4f}" for v in vals)))
    assert g.shape == (2, 8760)
    assert abs(g.max() - 900) < 1
    assert info["n_years"] == 2


def test_reads_data_sci_format_and_drops_leap_day():
    """Data Sci's timestamp,ghi_wm2 files are read, and 29 February is dropped so every year is 8760 h."""
    import datetime as dt
    t0 = dt.datetime(2024, 1, 1)
    day = _day(800)
    rows = ["timestamp,ghi_wm2"]
    for h in range(8784):                          # 2024 is a leap year
        t = t0 + dt.timedelta(hours=h)
        rows.append(f"{t:%Y-%m-%dT%H:00:00Z},{day[t.hour]:.2f}")
    g, info = load_ghi_csv(_write("\n".join(rows)), lon=179.2)
    assert g.shape == (1, 8760)
    assert info["years"] == ["2024"]
    assert any("local solar time" in w for w in info["warnings"])
    assert any("P90" in w for w in info["warnings"])


def test_refuses_bad_files():
    """Files with gaps, NASA's -999 missing code, impossible sunshine or part-years are refused, not guessed."""
    good = np.tile(_day(800), 365)
    bad_files = {
        "missing code": np.where(np.arange(8760) == 100, -999.0, good),
        "gap": np.where(np.arange(8760) == 100, np.nan, good),
        "part year": good[:8000],
    }
    for name, vals in bad_files.items():
        try:
            load_ghi_csv(_write("ghi_wm2\n" + "\n".join(str(v) for v in vals)))
        except ValueError:
            continue
        raise AssertionError(f"accepted a file with a {name}")
    try:                                           # W/m2 numbers in a column labelled kW/m2
        load_ghi_csv(_write("ghi_kwm2\n" + "\n".join(str(v) for v in good)))
    except ValueError:
        pass
    else:
        raise AssertionError("accepted W/m2 numbers labelled as kW/m2")


def test_far_from_any_file_falls_back_to_synthetic():
    """A site far from every weather file gets synthetic sun, clearly labelled, so nobody is ever blocked."""
    g, label, info = nearest_weather(51.5, 0.0)    # London: thousands of km from the Pacific files
    assert g.shape[1] == 8760 and "SYNTHETIC" in label and info["place"] is None


# ---------------------------------------------------------------------------------------------
# 2. Real weather files
# ---------------------------------------------------------------------------------------------

def test_every_real_weather_file_is_sane():
    """Every NASA file loads as whole 8760-hour years, with plausible yearly sunshine peaking around midday."""
    files = [(p, WEATHER_DIR / f"{p}_ghi.csv") for p in PLACES]
    present = [(p, f) for p, f in files if f.exists()]
    assert present, f"no weather files in {WEATHER_DIR}: ask Data Sci (python data/download_power.py)"
    for place, f in present:
        g, info = load_ghi_csv(f, lon=PLACES[place][1])
        assert g.shape[1] == 8760 and g.shape[0] >= 1, place
        lo, hi = PLAUSIBLE_KWH_M2_YR
        assert all(lo <= k <= hi for k in info["kwh_per_m2_per_year"]), (place, info["kwh_per_m2_per_year"])
        assert 10 <= info["peak_hour"] <= 14, (place, info["peak_hour"])


def test_physics_holds_on_every_real_island_and_year():
    """On all 120 real island-years (6 sites x 20 years): energy balances, the battery stays in limits, more solar never adds diesel."""
    S = np.array([0, 20, 50, 100, 160])[:, None, None]
    B = np.array([0, 60, 200, 400])[None, :, None]
    # The 20-year per-site cache if downloaded, else the 2-year nearest-city files
    sites = [(s, g, None) for s, g, _ in _cached_sites()] or _real_sites()
    assert sites
    for site, ghi, _ in sites:
        load, _, _ = build_load(site)
        t, _ = dispatch(load, ghi.T, S, B, float(site["generator_kw"]), TECH_DEFAULTS)
        supplied = t["solar_direct"] + t["batt_out"] + t["gen_to_load"] + t["unserved"]
        assert np.allclose(supplied, t["load_kwh"], rtol=1e-9), site["id"]
        assert np.all(t["soc_min_frac"] >= TECH_DEFAULTS["batt_min_soc"] - 1e-9), site["id"]
        assert np.all(np.diff(t["fuel_l"], axis=0) <= 1e-6), site["id"]
        assert np.all(t["fuel_l"][-1] < t["fuel_l"][0]), site["id"]   # real sun really saves diesel


def test_two_engines_agree_on_real_weather():
    """The readable loop and the fast engine agree on real NASA weather, not just made-up sun."""
    for site, ghi, _ in _real_sites():
        load, _, _ = build_load(site)
        gen = float(site["generator_kw"])
        a = simulate_year(load, ghi[0] / 1000.0, 50, 150, gen, diesel_price=2.0)
        b, _ = dispatch(load, ghi[0], 50, 150, gen, TECH_DEFAULTS)
        assert abs(a["litres"] - float(b["fuel_l"])) <= 1e-6 * a["litres"], site["id"]
        assert a["gen_hours"] == int(b["gen_hours"]), site["id"]


def test_full_plan_runs_on_real_weather_for_every_site():
    """A complete plan (design search, P50/P90, no-fuel-ship week) runs on real weather for every site in the app."""
    mock = json.loads((CONTRACTS / "mock_simresult.json").read_text())
    for site, ghi, label in _real_sites():
        load, critical, _ = build_load(site)
        r = simulate(site, load, critical, ghi, label, grid=TEST_GRID)
        d, sr = r["design"], r["simresult"]
        assert set(sr) == set(mock), site["id"]
        assert sr["years_simulated"] == ghi.shape[0]
        assert d["litres_saved_p50"] >= d["litres_saved_p90"] > 0, site["id"]
        assert d["lifetime_cost"] <= r["today"]["lifetime_cost"], site["id"]


# ---------------------------------------------------------------------------------------------
# 3. Twenty years at each site's exact location (data/cache, from python -m data.weather fetch)
#    These pass with a SKIP note if nobody has downloaded the cache on this machine.
# ---------------------------------------------------------------------------------------------

def test_twenty_years_of_weather_per_site():
    """Every site has at least 10 years of real NASA weather at its exact location, enough for a real P90."""
    cached = _cached_sites()
    if not cached:
        print("      SKIP: no data/cache yet (run: python -m data.weather fetch)")
        return
    lo, hi = PLAUSIBLE_KWH_M2_YR
    for site, ghi, years in cached:
        assert ghi.shape[0] >= 10 and ghi.shape[1] == 8760, site["id"]
        assert not np.isnan(ghi).any() and ghi.min() >= 0, site["id"]
        assert all(lo <= y <= hi for y in ghi.sum(axis=1) / 1000), site["id"]
        label = get_ghi(site["lat"], site["lon"])[1]
        assert label.startswith("NASA POWER") and "SYNTHETIC" not in label, site["id"]


def test_nearest_city_file_has_the_same_yearly_sun_as_the_site():
    """Borrowing weather from a city up to 464 km away gets yearly sunshine within 8% of the site's own: fine for sizing."""
    cached = _cached_sites()
    if not cached:
        print("      SKIP: no data/cache yet (run: python -m data.weather fetch)")
        return
    for site, ghi, years in cached:
        near, _, info = nearest_weather(site["lat"], site["lon"])
        if not info.get("place") or not isinstance(info["years"], list):
            continue
        for i, y in enumerate(int(v) for v in info["years"]):
            if y in years:
                own = ghi[years.index(y)].sum() / 1000
                assert abs(info["kwh_per_m2_per_year"][i] - own) <= 0.08 * own, (site["id"], y)


def test_full_plan_on_twenty_years_gives_a_real_p90():
    """On 20 real years the P90 saving is solid: no single year moves it by 2%, and resampling the years keeps it within 8%."""
    cached = _cached_sites()
    if not cached:
        print("      SKIP: no data/cache yet (run: python -m data.weather fetch)")
        return
    for site, ghi, years in cached:
        load, critical, _ = build_load(site)
        r = simulate(site, load, critical, ghi, "NASA POWER", grid=TEST_GRID)
        d = r["design"]
        assert r["simresult"]["years_simulated"] == len(years) >= 10
        assert d["litres_saved_p50"] > d["litres_saved_p90"] > 0, site["id"]
        # Measured on 2005-2024: leave-one-out moves P90 < 1%, bootstrap range within about 4.3%.
        c = p90_confidence(saved_per_year(site, load, ghi, d["solar_kw"], d["battery_kwh"]))
        assert c["loo_max"] - c["loo_min"] <= 0.02 * c["p90"], (site["id"], c)
        assert c["boot_low"] >= 0.92 * c["p90"] and c["boot_high"] <= 1.08 * c["p90"], (site["id"], c)


if __name__ == "__main__":
    import time
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.2f}s)\n      \"{t.__doc__.strip().splitlines()[0]}\"")
    print(f"\nAll {len(tests)} weather tests passed.")
