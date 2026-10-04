"""The engine as a web service (brief 3.7). Owner: Elec B.

Run it from the project folder:
    python -m engine.service                      # port 8001, or $PORT (Render / Railway set it)
    uvicorn engine.service:app --port 8001        # same thing
    (cd engine && uvicorn service:app --port 8001)  # the brief's literal command also works
Then open http://localhost:8001/docs to try every endpoint in the browser.

Port 8001, not the brief's 8000: Mech B's server (python -m server.app) already listens on 8000.
To serve the engine from Mech B's server instead, one line does it:
    from engine.service import router;  app.include_router(router, prefix="/engine")

    POST /simulate               SiteInput -> the full plan, SimResult under "simresult"
    GET  /simulate/{island}      the same for a demo island with default inputs (easy to open in a browser)
    GET  /islands                every demo island's precomputed SimResult + portfolio totals (the map)
    GET  /sensitivity/{island}   diesel price +-30% x solar cost +-30%: the 3 x 3 table
    GET  /sensitivity/{island}/slide.svg   the same table as a 1600 x 900 slide
    GET  /published-case         our engine vs the Ta'u (American Samoa) microgrid as built
    GET  /health                 engine version, which precomputed files are up to date

The demo never depends on live computation:
  1. A request whose inputs exactly match a precomputed file is answered from that file at once
     (meta.source = "precomputed"). Every input and every line of engine code is in the match, so
     a stale file is never served as if it were current.
  2. Anything else runs live (2-9 s on the dev laptop) and is cached (meta.source = "live" / "cache").
  3. If a live run takes longer than ENGINE_TIMEOUT_S or fails, the island's precomputed default
     plan comes back with a warning in meta.warnings (meta.source = "precomputed-fallback"); the
     live run keeps going and lands in the cache for the next click.
Build the files with: python -m engine.precompute
"""
import copy
import hashlib
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parent
ROOT = ENGINE_DIR.parent
if str(ROOT) not in sys.path:            # lets `uvicorn service:app` run from inside engine/
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Body, FastAPI, HTTPException, Response              # noqa: E402
from fastapi.middleware.cors import CORSMiddleware                                  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware                                  # noqa: E402

from data.loads import CLINIC_CRITICAL_KW, build_load                               # noqa: E402
from data.weather import get_ghi, get_site, load_sites                              # noqa: E402
from engine import sim                                                              # noqa: E402
from engine.dispatch import dispatch                                                # noqa: E402
from engine.optimise import FUNAFUTI, _funafuti_load, cost_breakdown, no_fuel_ship  # noqa: E402
from engine.p90 import _years, p90_confidence, p90_multi_year, p90_sentence         # noqa: E402

HOURS = 8760
PRECOMPUTED = ENGINE_DIR / "precomputed"
DEFAULT_PORT = 8001
# ASSUMPTION: a judge clicking "plan" waits ~20 s at most. A live plan takes 2-9 s on the dev
# laptop (engine/README.md, Timing); time it on the demo machine and set ENGINE_TIMEOUT_S to suit.
LIVE_TIMEOUT_S = float(os.environ.get("ENGINE_TIMEOUT_S", "20"))
CACHE_MAX = 64                       # finished answers kept in memory (each ~25 kB)
# Every file whose code changes the numbers. Editing any of them marks the precomputed files stale.
# Line endings are normalised first: git checks files out with CRLF on Windows and LF on Mac/Linux,
# and the same code must give the same version on every machine.
_VERSIONED = ("dispatch.py", "optimise.py", "sim.py", "p90.py", "service.py")
ENGINE_VERSION = hashlib.sha1(b"".join((ENGINE_DIR / f).read_bytes().replace(b"\r\n", b"\n")
                                       for f in _VERSIONED)).hexdigest()[:10]

# SiteInput (brief Chapter 1.3). Names from the plan request (contracts/FORMATS.md) and from
# contracts/simulation_input.json are both accepted.
SITE_FIELDS = {
    # name: (type, low, high): numbers outside [low, high] are refused with a clear message
    "name": (str, None, None),
    "country": (str, None, None),
    "lat": (float, -90, 90),
    "lon": (float, -180, 180),
    "households": (int, 0, 100_000),
    "has_clinic": (bool, None, None),
    "has_school": (bool, None, None),
    "other_kw": (float, 0, 100_000),
    "generator_kw": (float, 0.1, 100_000),
    "diesel_litres_per_month": (float, 0, 10_000_000),
    "diesel_price_per_litre": (float, 0.01, 50),
}
ALIASES = {
    "island_id": "site_id", "island": "site_id", "id": "site_id",
    "diesel_cost_per_liter": "diesel_price_per_litre",
    "generator_capacity_kw": "generator_kw",
    "solar_capacity_kw": "pv_kw", "battery_capacity_kwh": "battery_kwh",
}
# Keys the service itself understands besides SITE_FIELDS
CONTROL_FIELDS = {"site_id", "load_kw", "critical_kw", "overrides", "pv_kw", "battery_kwh", "fresh"}
# Keys the web app may send that mean nothing to the engine: ignored without a warning
QUIET_FIELDS = {"use_live_shape", "source"}


class BadInput(ValueError):
    """The request can't be planned; the message says what to fix (-> HTTP 422)."""


class NotReady(RuntimeError):
    """The live run is still going and there is nothing precomputed to fall back on (-> HTTP 503)."""


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------

def demo_islands():
    """Every island the demo shows: Funafuti (the brief's worked example) + data/sites.json."""
    return ["funafuti"] + [s["id"] for s in load_sites()]


def _base_site(site_id):
    return dict(FUNAFUTI) if site_id == "funafuti" else get_site(site_id)


def _number(name, v, kind, lo, hi):
    if kind is str:
        return str(v)
    if kind is bool:
        if isinstance(v, bool):
            return v
        raise BadInput(f"'{name}' must be true or false, got {v!r}")
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise BadInput(f"'{name}' must be a number, got {v!r}")
    try:
        x = float(v)
    except ValueError:
        raise BadInput(f"'{name}' must be a number, got {v!r}") from None
    if not math.isfinite(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise BadInput(f"'{name}' = {v!r} is outside the sensible range {lo} to {hi}")
    return int(round(x)) if kind is int else x


def _hourly_load(values):
    """24 (one day), 168 (one week) or 8760 (one year) hourly kW -> a full year of 8760 hours."""
    a = np.asarray(values, float)
    if a.ndim != 1 or a.size not in (24, 168, HOURS):
        raise BadInput(f"'load_kw' needs 24, 168 or {HOURS} hourly kW values, got {a.size}")
    if not np.all(np.isfinite(a)) or a.min() < 0:
        raise BadInput("'load_kw' values must be finite and not negative")
    if a.max() <= 0:
        raise BadInput("'load_kw' is all zero: there is nothing to power")
    return np.resize(a, HOURS)       # repeats the day / week; 8760 = 365 x 24 = 52 weeks + 1 day


class Inputs:
    """Everything one plan depends on, checked and ready for the engine."""

    def __init__(self, site, load, critical, ghi, label, overrides, grid, design, notes, warnings):
        self.site, self.load, self.critical = site, load, critical
        self.ghi, self.label, self.overrides, self.grid = ghi, label, overrides, grid
        self.design, self.notes, self.warnings = design, notes, warnings
        key = sim._fingerprint(site, load, critical, ghi, label, overrides, grid)
        extra = json.dumps([ENGINE_VERSION, design], default=float).encode()
        self.fingerprint = hashlib.sha1(key.encode() + extra).hexdigest()[:16]

    @property
    def site_id(self):
        return self.site.get("id")


def build_inputs(req, grid=sim.APP_GRID):
    """SiteInput dict -> Inputs, or BadInput saying exactly what is wrong.

    A demo island by id ("site_id" or "island_id"), optionally with changes ("households": 140),
    or a new place: "lat", "lon" and either "households" or "load_kw" (24/168/8760 hourly kW).
    "overrides": any engine setting from sim.DEFAULTS, e.g. {"solar_cost_per_kw": 2400}.
    "pv_kw" + "battery_kwh": also check this design against ours ("your_design" in the answer).
    """
    if not isinstance(req, dict):
        raise BadInput("send a JSON object, e.g. {\"site_id\": \"kadavu\"}")
    warnings = []
    r = {}
    for k, v in req.items():
        key = ALIASES.get(k, k)
        if v is None or key in QUIET_FIELDS:
            continue
        if key not in SITE_FIELDS and key not in CONTROL_FIELDS:
            warnings.append(f"ignored unknown field '{k}'")
            continue
        r[key] = v

    site_id = r.get("site_id")
    site = _base_site(str(site_id)) if site_id else None
    if site_id and site is None and not ("lat" in r and "lon" in r):
        raise BadInput(f"unknown island '{site_id}'. Demo islands: {', '.join(demo_islands())}; "
                       f"or send lat + lon + households for a new place")
    if site is None:
        site = {"id": str(site_id or "custom"), "name": "Custom site", "country": ""}
    changed = []
    for k, (kind, lo, hi) in SITE_FIELDS.items():
        if k in r:
            v = _number(k, r[k], kind, lo, hi)
            if site.get(k) != v:
                changed.append(k)
            site[k] = v
    if "lat" not in site or "lon" not in site:
        raise BadInput("a new place needs 'lat' and 'lon' (for its weather)")

    # Load: measured values if sent, else Data Sci's village model, else Funafuti's mock week
    notes = {}
    if "load_kw" in r:
        load = _hourly_load(r["load_kw"])
        notes["load"] = f"sent by the caller ({np.asarray(r['load_kw']).size} hourly values, repeated to a year)"
        # data/loads.py ASSUMPTION: the clinic's must-never-fail part (vaccine fridge + emergency light)
        critical = np.full(HOURS, CLINIC_CRITICAL_KW if site.get("has_clinic", True) else 0.0)
    elif site.get("id") == "funafuti" and "households" not in site:
        load, notes["load"] = _funafuti_load()
        critical = np.full(HOURS, CLINIC_CRITICAL_KW)
        ignored = [k for k in changed if k in ("has_clinic", "has_school", "other_kw", "diesel_litres_per_month")]
        if ignored:
            warnings.append(f"Funafuti uses Data Sci's measured-style week; ignored {', '.join(ignored)} "
                            f"(send 'households' or 'load_kw' to model the load instead)")
    else:
        if "households" not in site:
            raise BadInput("a new place needs 'households' (or 'load_kw' with its hourly demand)")
        load, critical, info = build_load(site)
        notes["load"] = f"data/loads.py village model, {info['basis']}"
    if load.sum() <= 0:
        raise BadInput("this site uses no electricity (0 households, no clinic, school or other load)")
    if "critical_kw" in r:
        critical = np.full(HOURS, _number("critical_kw", r["critical_kw"], float, 0, float(load.max())))

    overrides = r.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise BadInput("'overrides' must be an object, e.g. {\"solar_cost_per_kw\": 2400}")
    clean = {}
    for k, v in overrides.items():
        if k not in sim.DEFAULTS:
            raise BadInput(f"unknown override '{k}'. Allowed: {', '.join(sorted(sim.DEFAULTS))}")
        clean[k] = _number(k, v, float, 0, 1e7)
    if "diesel_price_per_litre" in clean:          # sim.plan reads the price from the site
        site["diesel_price_per_litre"] = clean.pop("diesel_price_per_litre")
    for k in ("batt_min_soc", "batt_start_soc", "batt_eff_charge", "batt_eff_discharge",
              "gen_min_load", "pv_derate"):
        if k in clean and not 0 <= clean[k] <= 1:
            raise BadInput(f"override '{k}' is a fraction: between 0 and 1")

    design = None
    if ("pv_kw" in r) != ("battery_kwh" in r):
        raise BadInput("to check your own design send both 'pv_kw' and 'battery_kwh'")
    if "pv_kw" in r:
        design = (round(_number("pv_kw", r["pv_kw"], float, 0, 1e6), 1),
                  round(_number("battery_kwh", r["battery_kwh"], float, 0, 1e7), 1))

    ghi, label = get_ghi(site["lat"], site["lon"])
    if label.startswith("SYNTHETIC"):
        warnings.append("no real weather near this place: using SYNTHETIC sunshine (made up, labelled)")
    elif "away" in label:
        notes["weather"] = "nearest real weather file (see label for distance)"
    return Inputs(site, load, critical, np.asarray(ghi), label, clean or None, tuple(grid), design,
                  notes, warnings)


# ---------------------------------------------------------------------------------------------
# One plan
# ---------------------------------------------------------------------------------------------

def plain(x):
    """numpy numbers / arrays -> plain JSON values; infinity and NaN -> None."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return plain(x.tolist())
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _option(o, today_litres):
    """One design, summarised for the 'same money, your choice' cards."""
    return {"pv_kw": o["solar_kw"], "battery_kwh": o["battery_kwh"],
            "capex_aud": round(o["capex"]), "lifetime_cost_aud": round(o["cost"]["total"]),
            "percent_diesel_cut_p50": round(100 * o["litres_saved_p50"] / today_litres, 1) if today_litres else 0,
            "payback_years": round(o["payback_years"], 1) if o["payback_years"] else None,
            "critical_load_survives_7d_no_fuel": o["clinic_survives"]}


def evaluate_design(inputs, pv_kw, battery_kwh, winner_cost=None):
    """Somebody else's design (e.g. a supplier quote) through the same engine and cost model."""
    site, load, ghi = inputs.site, inputs.load, inputs.ghi
    p = dict(sim.DEFAULTS, **(inputs.overrides or {}))
    price = float(site.get("diesel_price_per_litre") or p["diesel_price_per_litre"])
    gen_kw = sim.generator_size(site, load)
    base, _ = dispatch(load, np.zeros(HOURS), 0.0, 0.0, gen_kw, p)        # today: diesel only
    t, _ = dispatch(load, ghi.T, pv_kw, battery_kwh, gen_kw, p)            # every weather year
    saved = float(base["fuel_l"]) - t["fuel_l"]
    cost = cost_breakdown(pv_kw, battery_kwh, float(t["fuel_l"].mean()), float(t["gen_kwh"].mean()),
                          float(load.sum()), price, p)
    ship = no_fuel_ship(load, float(inputs.critical.max()), ghi, pv_kw, battery_kwh, gen_kw, p)
    worst = int(t["unserved_hours"].max())
    out = {"pv_kw": pv_kw, "battery_kwh": battery_kwh,
           "capex_aud": round(cost["solar"] + cost["battery"]),
           "lifetime_cost_aud": round(cost["total"]),
           "diesel_litres_saved_p50": round(float(np.median(saved))),
           "diesel_litres_saved_p90": round(float(np.percentile(saved, 10))),
           "percent_diesel_cut_p50": round(100 * float(np.median(saved)) / float(base["fuel_l"]), 1),
           "blackout_hours_worst_year": worst,
           "critical_load_survives_7d_no_fuel": bool(ship["survives"]),
           "clinic_hours_powered_of_168": int(ship["hours_powered"])}
    if winner_cost is not None:
        extra = cost["total"] - winner_cost
        out["extra_lifetime_cost_vs_ours_aud"] = round(extra)
        problems = []
        if worst > int(base["unserved_hours"]):
            problems.append(f"it leaves the island without power for {worst} hours in the worst year")
        if not ship["survives"]:
            problems.append(f"the clinic loses power after {int(ship['hours_powered'])} of 168 hours "
                            f"when the fuel ship is late")
        if problems:
            out["verdict"] = "Not safe: " + "; ".join(problems) + "."
        elif extra > 0:
            out["verdict"] = (f"Works, but costs AUD {extra:,.0f} more over 20 years than our design "
                              f"({100 * extra / winner_cost:.1f}%).")
        else:
            out["verdict"] = "Works, and costs no more than our design over 20 years."
    return out


def compute(inputs):
    """Run the engine for one set of inputs -> the full answer (plain JSON values)."""
    t0 = time.perf_counter()
    result, opt = sim.plan(inputs.site, inputs.load, inputs.critical, inputs.ghi, inputs.label,
                           inputs.overrides, inputs.grid)
    w, today_l = opt["winner"], opt["today"]["litres_per_year"]
    saved = np.asarray(w["litres_saved_per_year"])
    years = _years(inputs.label, len(saved))
    sentence, f = p90_sentence(saved)
    conf = p90_confidence(saved)
    worst = int(np.argmin(saved))
    co2 = result["assumptions"]["co2_kg_per_litre"]
    result["bank"] = {
        "sentence": sentence,
        "promise_litres": f["promise_litres"],
        "promise_aud_per_year": round(f["promise_litres"] * opt["diesel_price"]),
        "promise_tonnes_co2_per_year": round(f["promise_litres"] * co2 / 1000),
        "evidence": (f"{f['years_at_least_promise']} of {f['years']} real weather years saved at least "
                     f"{f['promise_litres']:,.0f} L; the worst ({years[worst]}) saved {f['worst_year_litres']:,.0f} L."),
        "p90_10_year_average_litres": round(p90_multi_year(saved)),
        "p90_if_any_one_year_dropped": [round(conf["loo_min"]), round(conf["loo_max"])],
        "p90_bootstrap_90pct_range": [round(conf["boot_low"]), round(conf["boot_high"])],
        "litres_saved_each_year": {str(y): round(float(s)) for y, s in zip(years, saved)},
        "covers": "weather only; not load growth, panel ageing or diesel price changes",
    }
    result["options"] = {"cheapest_over_20_years": _option(w, today_l)}
    for name, o in opt["options"].items():
        result["options"]["cheapest_to_buy" if name == "lowest_purchase_cost" else name] = _option(o, today_l)
    result["options"]["cheapest_ignoring_clinic"] = _option(opt["cheapest_ignoring_clinic"], today_l)
    result["price_of_resilience_aud"] = round(max(0.0, opt["price_of_resilience"]))
    result["near_optimal_band"] = opt["near_optimal_band"]
    result["whole_island_no_fuel"] = {"hours_powered_of_168": w["island_hours_powered"],
                                      "days_until_first_outage": round(w["island_days_until_first_outage"], 2)}
    result["cost_breakdown_aud"] = {k: round(v) for k, v in w["cost"].items() if k != "cost_per_kwh"}
    result["cost_per_kwh_aud"] = {"ours": round(w["cost"]["cost_per_kwh"], 3),
                                  "diesel_only": round(opt["today"]["cost"]["cost_per_kwh"], 3)}
    result["search"] = {"designs_tested": opt["designs_tested"], "island_years": opt["island_years"],
                        "hourly_decisions": opt["island_years"] * HOURS}
    result["load"]["basis"] = inputs.notes.get("load")
    if inputs.design:
        result["your_design"] = evaluate_design(inputs, *inputs.design, winner_cost=w["cost"]["total"])
    result["meta"] = {"source": "live", "seconds": round(time.perf_counter() - t0, 2),
                      "engine_version": ENGINE_VERSION,
                      "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "inputs_fingerprint": inputs.fingerprint, "warnings": list(inputs.warnings)}
    return plain(result)


# ---------------------------------------------------------------------------------------------
# Precomputed files, cache, time limit
# ---------------------------------------------------------------------------------------------

_cache = {}              # fingerprint -> finished answer
_inflight = {}           # fingerprint -> Future of a live run
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="engine")
_pre = {"files": {}, "by_fp": {}, "by_island": {}}


def precomputed_dir():
    return Path(os.environ.get("ENGINE_PRECOMPUTED_DIR", PRECOMPUTED))


def _load_precomputed():
    """Read engine/precomputed/{island}.json, re-reading only files that changed on disk."""
    d = precomputed_dir()
    files = {f: f.stat().st_mtime for f in d.glob("*.json")} if d.is_dir() else {}
    with _lock:
        if files == _pre["files"]:
            return _pre
        by_fp, by_island = {}, {}
        for f in files:
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(r, dict) or "simresult" not in r or "meta" not in r:
                continue     # index.json, sensitivity.json, published_case.json
            by_fp[r["meta"]["inputs_fingerprint"]] = r
            by_island[f.stem] = r
        _pre.update(files=files, by_fp=by_fp, by_island=by_island)
        return _pre


def precomputed(island):
    """The precomputed default plan for an island, or None."""
    r = _load_precomputed()["by_island"].get(island)
    return copy.deepcopy(r) if r else None


def _remember(fp, result):
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))       # forget the oldest
        _cache[fp] = result
        _inflight.pop(fp, None)


def _start(inputs):
    """Start (or join) the live run for these inputs -> Future."""
    fp = inputs.fingerprint
    with _lock:
        fut = _inflight.get(fp)
        if fut is None:
            def run():
                try:
                    res = compute(inputs)
                except BaseException:
                    with _lock:
                        _inflight.pop(fp, None)
                    raise
                _remember(fp, res)
                return res
            fut = _inflight[fp] = _pool.submit(run)
    return fut


def _answer(result, source, warnings=()):
    out = copy.deepcopy(result)
    out["meta"]["source"] = source
    out["meta"]["warnings"] = list(dict.fromkeys(list(out["meta"].get("warnings", [])) + list(warnings)))
    return out


def simulate(req, timeout=None, grid=sim.APP_GRID):
    """SiteInput dict -> the full answer. The brief's `simulate(site)`, with the safety net."""
    inputs = build_inputs(req, grid)
    fp = inputs.fingerprint
    fresh = bool(req.get("fresh")) if isinstance(req, dict) else False
    if not fresh:
        with _lock:
            hit = _cache.get(fp)
        if hit is not None:
            return _answer(hit, "cache", inputs.warnings)
        pre = _load_precomputed()["by_fp"].get(fp)
        if pre is not None:
            return _answer(pre, "precomputed", inputs.warnings)
    timeout = LIVE_TIMEOUT_S if timeout is None else timeout
    fut = _start(inputs)
    try:
        return _answer(fut.result(timeout=timeout), "live", inputs.warnings)
    except FutureTimeout:
        why = f"the live run took longer than {timeout:g} s (it carries on; ask again in a moment)"
    except BadInput:
        raise
    except Exception as e:                                    # engine bug: never a blank demo screen
        why = f"the live run failed ({type(e).__name__}: {e})"
    pre = precomputed(inputs.site_id) if inputs.site_id else None
    if pre is None:
        raise NotReady(f"{why}, and there is no precomputed plan for '{inputs.site_id}'")
    note = "showing the precomputed default plan for this island"
    if pre["meta"]["inputs_fingerprint"] != fp:
        note += ": your changes are NOT in these numbers"
    return _answer(pre, "precomputed-fallback", list(inputs.warnings) + [f"{why}; {note}"])


def freshness():
    """{island: "fresh" | "stale" | "missing"}: does each precomputed file match today's inputs + code?"""
    pre = _load_precomputed()["by_island"]
    out = {}
    for island in demo_islands():
        if island not in pre:
            out[island] = "missing"
            continue
        try:
            fp = build_inputs({"site_id": island}).fingerprint
        except BadInput:
            fp = None
        out[island] = "fresh" if pre[island]["meta"]["inputs_fingerprint"] == fp else "stale"
    return out


def warm_up():
    """Start live runs for demo islands whose precomputed file is stale or missing, so their first
    click is answered from the cache. Runs in the background; call once at start-up."""
    def go():
        for island, state in freshness().items():
            if state != "fresh":
                _start(build_inputs({"site_id": island}))
    threading.Thread(target=go, daemon=True, name="engine-warm-up").start()


def _read_precomputed_file(name):
    f = precomputed_dir() / name
    if not f.exists():
        raise HTTPException(404, f"{name} not built yet: run python -m engine.precompute")
    return json.loads(f.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------

router = APIRouter()

SITE_INPUT_EXAMPLE = {"site_id": "kadavu", "households": 140, "diesel_price_per_litre": 1.85,
                      "pv_kw": 80, "battery_kwh": 150}


@router.post("/simulate")
def simulate_endpoint(site: dict = Body(..., examples=[SITE_INPUT_EXAMPLE, {"site_id": "funafuti"},
                                                      {"name": "My village", "lat": -17.7, "lon": 168.3,
                                                       "households": 60, "has_clinic": True}]),
                      view: str = "full"):
    """SiteInput (brief 1.3) -> plan. `?view=simresult` returns only the flat SimResult (brief 1.4)."""
    try:
        r = simulate(site)
    except BadInput as e:
        raise HTTPException(422, str(e)) from None
    except NotReady as e:
        raise HTTPException(503, str(e)) from None
    return r["simresult"] if view == "simresult" else r


@router.get("/simulate/{island}")
def simulate_island(island: str, view: str = "full"):
    """A demo island with its default inputs (open it in a browser)."""
    return simulate_endpoint({"site_id": island}, view)


@router.get("/islands")
def islands():
    """Every demo island's SimResult + totals, from the precomputed index (instant)."""
    return _read_precomputed_file("index.json")


@router.get("/sensitivity/{island}")
def sensitivity(island: str):
    """Diesel price x solar cost, each -30% / as sourced / +30%: winning design and 20-year cost."""
    tables = _read_precomputed_file("sensitivity.json")["islands"]
    if island not in tables:
        raise HTTPException(404, f"no sensitivity table for '{island}'. Have: {', '.join(tables)}")
    return tables[island]


@router.get("/sensitivity/{island}/slide.svg")
def sensitivity_slide(island: str):
    """The 3 x 3 table as a 1600 x 900 slide (embed with <img src=...>)."""
    f = precomputed_dir() / f"sensitivity_{island}.svg"
    if not f.exists():
        raise HTTPException(404, f"no slide for '{island}': run python -m engine.precompute")
    return Response(f.read_text(encoding="utf-8"), media_type="image/svg+xml")


@router.get("/published-case")
def published_case():
    """How our engine compares with a real, published Pacific microgrid (Ta'u, American Samoa)."""
    return _read_precomputed_file("published_case.json")


@router.get("/health")
def health():
    with _lock:
        cached, running = len(_cache), len(_inflight)
    return {"ok": True, "engine_version": ENGINE_VERSION, "live_timeout_s": LIVE_TIMEOUT_S,
            "precomputed": freshness(), "cached_answers": cached, "runs_in_progress": running}


@asynccontextmanager
async def _lifespan(_app):
    if os.environ.get("ENGINE_WARMUP", "1") != "0":
        warm_up()
    yield


app = FastAPI(title="Shipless engine", version=ENGINE_VERSION, lifespan=_lifespan,
              description="Hour-by-hour island power simulation, design search and P50/P90 savings.")
app.add_middleware(GZipMiddleware, minimum_size=2000)
# The web app may be served from another port or an ngrok / Render address: allow it to call us.
app.add_middleware(CORSMiddleware, allow_methods=["*"], allow_headers=["*"],
                   allow_origins=os.environ.get("ENGINE_CORS_ORIGINS", "*").split(","))
app.include_router(router)


@app.get("/", include_in_schema=False)
def index():
    return {"service": "Shipless engine", "try": ["/docs", "/health", "/islands", "/simulate/funafuti",
                                                  "/sensitivity/funafuti", "/published-case"]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    print(f"\n  Shipless engine on http://localhost:{port}  (try http://localhost:{port}/docs)")
    print(f"  engine version {ENGINE_VERSION}; precomputed: {freshness()}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
