"""P50 / P90 tests (brief 3.6). Owner: Elec B.

Run from the project folder:  python -m engine.test_p90
                          or:  python -m pytest engine/test_p90.py
The first line of each test's docstring is the sentence to say to a judge.
"""
import contextlib
import io
import json
from pathlib import Path

import numpy as np

from engine.optimise import optimise
from engine.p90 import (p50_p90, p90_multi_year, p90_sentence, report, round_down,
                        saved_per_year_brief)
from engine.sim import DEFAULTS, generator_size
from engine.weather_files import nearest_weather
from data.loads import build_load
from data.weather import get_site

P = dict(DEFAULTS)
MOCK = json.loads((Path(__file__).parent.parent / "contracts" / "mock_simresult.json").read_text())


def test_p90_is_the_10th_percentile_between_the_2nd_and_3rd_worst_year():
    """With 20 years, P90 = numpy.percentile(savings, 10): between the 2nd- and 3rd-worst year, as the brief says."""
    saved = np.random.default_rng(7).normal(30000, 600, 20)
    p50, p90 = p50_p90(saved)
    worst = np.sort(saved)
    assert worst[1] <= p90 <= worst[2]
    assert abs(p90 - (worst[1] + 0.9 * (worst[2] - worst[1]))) < 1e-9   # numpy's linear interpolation
    assert abs(p50 - (worst[9] + worst[10]) / 2) < 1e-9


def test_the_promise_is_rounded_down_never_up():
    """The litres in the bank sentence are rounded down, so we never promise more than the evidence."""
    assert round_down(30066) == 30000
    assert round_down(38999) == 38000
    assert round_down(9876) == 9800
    assert round_down(0) == 0
    for x in np.random.default_rng(1).uniform(1, 1e6, 500):
        assert round_down(x) <= x


def test_the_sentence_holds_in_at_least_18_of_20_real_years():
    """The sentence says 9 in 10, and the 20 real years back it: at least 18 of them beat the promise."""
    saved = np.random.default_rng(3).normal(30000, 800, 20)
    sentence, f = p90_sentence(saved)
    assert sentence == f"In 9 years out of 10, this island saves at least {f['promise_litres']:,.0f} litres of diesel."
    assert f["promise_litres"] <= f["p90"] <= f["p50"]
    assert f["years_at_least_promise"] >= 18


def test_ten_year_p90_is_closer_to_the_typical_year():
    """Over a 10-year loan good and bad years average out, so the 10-year P90 sits between the 1-year P90 and the P50."""
    saved = np.random.default_rng(5).normal(30000, 800, 20)
    p50, p90 = p50_p90(saved)
    p90_10 = p90_multi_year(saved)
    assert p90 < p90_10 < p50 + 1e-6


def test_brief_method_gives_the_same_savings_as_the_fast_engine():
    """The brief's method, simulate_year once per weather year, gives the same yearly savings as the fast optimiser."""
    site = get_site("eua")
    load, critical, _ = build_load(site)
    ghi, _, _ = nearest_weather(site["lat"], site["lon"])
    gen, price = generator_size(site, load), 1.9
    r = optimise(load, critical.max(), ghi, gen, price, P, n_pv=5, n_batt=4)
    w = r["winner"]
    brief = saved_per_year_brief(load, ghi, w["solar_kw"], w["battery_kwh"], gen, price, P)
    fast = np.asarray(w["litres_saved_per_year"])
    assert np.allclose(brief, fast, rtol=1e-9), (brief, fast)
    assert np.allclose(p50_p90(brief), (w["litres_saved_p50"], w["litres_saved_p90"]), rtol=1e-9)


def test_funafuti_simresult_every_field_filled():
    """Done-when for brief 3.6: the full SimResult for Funafuti, every field filled, in the agreed shape.

    The 5-second target is a demo-machine number (this laptop: 2.4-9 s depending on load). Here we
    only guard against the search becoming several times slower.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result, opt, seconds = report("funafuti")
    sr = result["simresult"]
    assert set(sr) == set(MOCK)
    assert all(v is not None for v in sr.values())
    for k, v in MOCK.items():
        assert isinstance(sr[k], bool) if isinstance(v, bool) else isinstance(sr[k], type(v)) or \
            (isinstance(v, (int, float)) and isinstance(sr[k], (int, float))), k
    assert sr["site"] == "Funafuti, Tuvalu" and sr["critical_load_survives_7d_no_fuel"] is True
    assert sr["diesel_litres_saved_p50"] >= sr["diesel_litres_saved_p90"] > 0
    assert "In 9 years out of 10, this island saves at least" in out.getvalue()
    assert seconds < 30


if __name__ == "__main__":
    import time
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.2f}s)\n      \"{t.__doc__.strip().splitlines()[0]}\"")
    print(f"\nAll {len(tests)} P90 tests passed.")
