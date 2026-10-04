"""Service, precompute, sensitivity and published-case tests. Owner: Elec B.

    python -m engine.test_service        (also works with pytest)

Prints each test with the sentence to say to a judge.
"""
import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from data.loads import build_load
from data.weather import synthetic_ghi
from engine import service, sim
from engine.service import BadInput, Inputs, build_inputs, compute, evaluate_design, simulate

SMALL = (6, 4)        # a small design grid keeps tests quick; the service itself uses sim.APP_GRID
MOCK = json.loads((Path(__file__).parent.parent / "contracts" / "mock_simresult.json").read_text())


def _reset():
    with service._lock:
        service._cache.clear()


def _small_inputs(years=2, **site_changes):
    """A village on 2 years of synthetic sun: fast enough for many plans."""
    site = {"id": "kadavu", "name": "Test village", "country": "Fiji", "lat": -19.05, "lon": 178.18,
            "households": 80, "has_clinic": True, "has_school": True, "other_kw": 3,
            "generator_kw": 40, "diesel_price_per_litre": 2.0, **site_changes}
    load, critical, _ = build_load(site)
    return Inputs(site, load, critical, synthetic_ghi(site["lat"], site["lon"], n_years=years), "synthetic",
                  None, SMALL, None, {}, [])


def test_inputs_accept_both_contract_spellings():
    """'island_id' / 'diesel_cost_per_liter' (simulation_input.json) mean the same as the plan request's names."""
    i = build_inputs({"island_id": "kadavu", "diesel_cost_per_liter": 1.9, "generator_capacity_kw": 50,
                      "use_live_shape": False, "colour": "blue"}, SMALL)
    assert i.site["id"] == "kadavu" and i.site["diesel_price_per_litre"] == 1.9 and i.site["generator_kw"] == 50
    assert any("colour" in w for w in i.warnings) and not any("use_live_shape" in w for w in i.warnings)


def test_bad_inputs_are_refused_with_a_reason():
    """Nonsense never reaches the engine: each bad request gets a message saying what to fix."""
    bad = [{"site_id": "atlantis"}, {"site_id": "kadavu", "households": -5},
           {"site_id": "kadavu", "has_clinic": "yes"}, {"site_id": "kadavu", "overrides": {"magic": 1}},
           {"site_id": "kadavu", "pv_kw": 50}, {"site_id": "kadavu", "load_kw": [1, 2, 3]},
           {"name": "x", "households": 10}, {"lat": -17, "lon": 168},
           {"site_id": "kadavu", "overrides": {"pv_derate": 1.5}}, {"site_id": "kadavu", "lat": 95},
           {"site_id": "kadavu", "households": 0, "has_clinic": False, "has_school": False, "other_kw": 0},
           "kadavu"]
    for req in bad:
        try:
            build_inputs(req, SMALL)
        except BadInput as e:
            assert str(e), req
        else:
            raise AssertionError(f"accepted bad input {req}")


def test_measured_load_is_repeated_without_changing_daily_energy():
    """24 measured hours become a year: same energy every day, so 365 x the day."""
    day = [5, 4, 4, 4, 5, 6, 8, 9, 9, 8, 8, 8, 8, 8, 8, 9, 10, 14, 18, 20, 18, 14, 9, 6]
    i = build_inputs({"name": "Measured", "lat": -17.7, "lon": 168.3, "load_kw": day, "has_clinic": True}, SMALL)
    assert i.load.shape == (8760,) and abs(i.load.sum() - 365 * sum(day)) < 1e-6
    assert i.critical.max() > 0


def test_fingerprint_changes_with_every_input():
    """The precomputed match is exact: any change of inputs or settings gives a different fingerprint."""
    a = build_inputs({"site_id": "kadavu"}, SMALL).fingerprint
    assert a == build_inputs({"site_id": "kadavu"}, SMALL).fingerprint
    others = [{"site_id": "kadavu", "households": 121}, {"site_id": "kadavu", "overrides": {"solar_cost_per_kw": 2500}},
              {"site_id": "kadavu", "diesel_price_per_litre": 1.71}, {"site_id": "eua"},
              {"site_id": "kadavu", "pv_kw": 50, "battery_kwh": 100}]
    fps = {build_inputs(r, SMALL).fingerprint for r in others}
    assert a not in fps and len(fps) == len(others)


def test_simulate_returns_the_agreed_simresult_then_caches_it():
    """A live answer has the exact SimResult shape (contracts/mock_simresult.json), is valid JSON, and a repeat is instant."""
    _reset()
    req = {"site_id": "kadavu", "households": 100}
    r = simulate(req, grid=SMALL)
    assert r["meta"]["source"] == "live"
    sr = r["simresult"]
    assert set(sr) == set(MOCK)
    for k, v in MOCK.items():
        assert type(sr[k]) is bool if isinstance(v, bool) else isinstance(sr[k], (int, float, str)), k
    assert sr["diesel_litres_saved_p50"] >= sr["diesel_litres_saved_p90"] >= 0
    assert sr["years_simulated"] == 20 and sr["blackout_hours_worst_year"] == 0
    assert r["bank"]["sentence"].startswith("In 9 years out of 10")
    assert len(r["bank"]["litres_saved_each_year"]) == 20
    json.dumps(r, allow_nan=False)                  # no NaN / infinity / numpy types anywhere
    t0 = time.perf_counter()
    again = simulate(req, grid=SMALL)
    assert again["meta"]["source"] == "cache" and time.perf_counter() - t0 < 0.5
    assert again["simresult"] == sr


def test_precomputed_answer_is_served_and_used_as_fallback():
    """Same inputs -> precomputed file at once; a slow or broken live run -> the island's file, with a warning."""
    _reset()
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("ENGINE_PRECOMPUTED_DIR")
        os.environ["ENGINE_PRECOMPUTED_DIR"] = d
        real_compute = service.compute
        try:
            pre = compute(build_inputs({"site_id": "eua"}, SMALL))
            (Path(d) / "eua.json").write_text(json.dumps(pre))
            _reset()
            r = simulate({"site_id": "eua"}, grid=SMALL)
            assert r["meta"]["source"] == "precomputed" and r["simresult"] == pre["simresult"]

            # slow live run: answer straight away from the file, say the changes are not in it
            r = simulate({"site_id": "eua", "households": 95}, timeout=0, grid=SMALL)
            assert r["meta"]["source"] == "precomputed-fallback"
            assert any("NOT in these numbers" in w for w in r["meta"]["warnings"])
            for fut in list(service._inflight.values()):
                fut.result(timeout=120)                 # the live run finishes and lands in the cache
            assert simulate({"site_id": "eua", "households": 95}, grid=SMALL)["meta"]["source"] == "cache"

            # engine crash: still an answer, never a blank screen
            def boom(_):
                raise RuntimeError("simulated bug")
            service.compute = boom
            r = simulate({"site_id": "eua", "households": 96}, grid=SMALL)
            assert r["meta"]["source"] == "precomputed-fallback"
            assert any("simulated bug" in w for w in r["meta"]["warnings"])
            try:
                simulate({"site_id": "kadavu", "households": 96}, grid=SMALL)     # no file for kadavu here
            except service.NotReady:
                pass
            else:
                raise AssertionError("expected NotReady without a precomputed file")
        finally:
            service.compute = real_compute
            if old is None:
                os.environ.pop("ENGINE_PRECOMPUTED_DIR", None)
            else:
                os.environ["ENGINE_PRECOMPUTED_DIR"] = old
            service._load_precomputed()


def test_your_design_check_agrees_with_the_optimiser():
    """Our own winner, checked as 'your design', costs exactly what the optimiser said; no panels = today."""
    i = _small_inputs()
    res, opt = sim.plan(i.site, i.load, i.critical, i.ghi, i.label, None, SMALL)
    w = opt["winner"]
    same = evaluate_design(i, w["solar_kw"], w["battery_kwh"], winner_cost=w["cost"]["total"])
    assert abs(same["extra_lifetime_cost_vs_ours_aud"]) <= 1, same
    assert same["verdict"].startswith("Works")
    assert same["diesel_litres_saved_p50"] == res["design"]["litres_saved_p50"]
    none = evaluate_design(i, 0.0, 0.0, winner_cost=w["cost"]["total"])
    assert none["diesel_litres_saved_p50"] == 0 and abs(none["lifetime_cost_aud"] - res["today"]["lifetime_cost"]) <= 1
    tiny = evaluate_design(i, 5.0, 0.0, winner_cost=w["cost"]["total"])
    assert tiny["verdict"].startswith("Not safe") or tiny["extra_lifetime_cost_vs_ours_aud"] > 0


def test_sensitivity_table_is_consistent():
    """3 x 3 table: the middle cell is the normal plan, regret there is 0, dearer diesel never makes the plan cheaper."""
    from engine.sensitivity import sensitivity, slide_svg, table_md
    i = _small_inputs()
    t = sensitivity(i)
    mid = t["grid"][1][1]
    res, _ = sim.plan(i.site, i.load, i.critical, i.ghi, i.label, None, SMALL)
    assert (mid["pv_kw"], mid["battery_kwh"]) == (res["design"]["solar_kw"], res["design"]["battery_kwh"])
    assert mid["regret_percent"] == 0
    assert all(c["regret_percent"] >= 0 for row in t["grid"] for c in row)
    for j in range(3):                     # down a column diesel gets dearer: best 20-yr cost rises
        col = [t["grid"][k][j]["lifetime_cost_aud"] for k in range(3)]
        assert col == sorted(col), col
    for k in range(3):                     # along a row solar gets dearer: best 20-yr cost rises
        row = [c["lifetime_cost_aud"] for c in t["grid"][k]]
        assert row == sorted(row), row
    assert "<svg" in slide_svg(t) and "|" in table_md(t)


def test_published_case_fuel_curve_and_as_built_system():
    """Ta'u: reported diesel through our fuel curve gives within 5% of the reported 1.3 GWh; as built runs >= 97% solar."""
    from engine.dispatch import dispatch
    from engine.published_case import LITRES_PER_US_GALLON, PUBLISHED, tau_load, tau_weather
    p = dict(sim.DEFAULTS)
    load, litres = tau_load(p)
    assert abs(litres / (PUBLISHED["diesel_gallons_per_year"] * LITRES_PER_US_GALLON) - 1) < 1e-3
    assert abs(load.sum() / PUBLISHED["energy_kwh_per_year"] - 1) < 0.05
    assert abs(load.max() / PUBLISHED["peak_kw"] - 1) < 0.05
    ghi, _ = tau_weather()
    t, _ = dispatch(load, ghi.T, PUBLISHED["pv_kw"], PUBLISHED["battery_kwh"], PUBLISHED["grid_capacity_kw"], p)
    share = 1 - t["gen_to_load"] / t["load_kwh"]
    assert np.median(share) >= 0.97 and t["unserved_hours"].max() == 0


def test_server_plans_reuse_precomputed_answers():
    """The app's own plan path (sim.simulate) answers a default island from the precomputed file at once."""
    from data.weather import get_ghi, get_site
    site = dict(get_site("kadavu"))
    load, critical, _ = build_load(site)
    ghi, label = get_ghi(site["lat"], site["lon"])
    sim._PLANS.clear()
    t0 = time.perf_counter()
    r = sim.simulate(site, load, critical, ghi, label)
    seconds = time.perf_counter() - t0
    pre = service.precomputed("kadavu")
    assert pre is not None and r.get("meta", {}).get("source") == "precomputed", \
        "engine/precomputed is stale or missing: run python -m engine.precompute"
    assert r["design"] == pre["design"] and r["simresult"] == pre["simresult"] and seconds < 1.0
    changed = dict(site, households=site["households"] + 1)          # any change -> computed live
    load2, critical2, _ = build_load(changed)
    assert "meta" not in sim.simulate(changed, load2, critical2, ghi, label, grid=SMALL)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_http_end_to_end():
    """The real web server: /health, POST /simulate (full and ?view=simresult), and a clear 422 for bad input."""
    import uvicorn
    os.environ["ENGINE_WARMUP"] = "0"
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(service.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)

        def call(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        code, h = call("/health")
        assert code == 200 and h["ok"] and h["engine_version"] == service.ENGINE_VERSION
        code, r = call("/simulate?view=simresult", {"site_id": "malekula"})
        assert code == 200 and set(r) == set(MOCK)
        code, r = call("/simulate", {"site_id": "malekula"})
        assert code == 200 and r["simresult"]["site"].startswith("Village on Malekula") and r["meta"]["source"] in (
            "cache", "precomputed")
        code, r = call("/simulate", {"site_id": "atlantis"})
        assert code == 422 and "unknown island" in r["detail"]
    finally:
        server.should_exit = True
        th.join(timeout=10)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    t_all = time.time()
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.1f}s)\n      {t.__doc__.strip()}")
    print(f"\nAll {len(tests)} service tests passed in {time.time() - t_all:.0f}s.")
