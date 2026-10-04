"""P50 / P90 diesel savings: the numbers a bank lends against (brief 3.6). Owner: Elec B.

    python -m engine.p90            # Funafuti: 20 yearly savings, the P90 sentence, the full SimResult
    python -m engine.p90 kadavu     # any site in data/sites.json
    python -m engine.p90 all        # one line per site, with how sure we are of each P90

P50 = the median year's saving (a typical year).
P90 = the saving beaten in 9 years out of 10: numpy.percentile(savings, 10), "10th percentile of
      savings = P90 exceedance". With 20 years it sits between the 2nd- and 3rd-worst year.
The sentence the app shows: "In 9 years out of 10, this island saves at least 30,000 litres of diesel."
The figure in it is rounded DOWN, so the promise is never bigger than the evidence.

Speaking bank, beyond the brief:
  1-year vs 10-year P90  banks quote both. Over 10 years good and bad years average out, so the
                         10-year P90 (of the 10-year average) sits closer to the P50.
  how sure are we        leave-one-out and bootstrap ranges: would a different 20 years change it?
  weather only           this P90 covers weather. Load growth, panel ageing and fuel price are not
                         in it, and we say so.
"""
import math

import numpy as np

# ASSUMPTION: 2000 resamples gives a stable 90% range (more changes it by < 1%).
N_BOOTSTRAP = 2000
LOAN_YEARS = 10   # the multi-year P90 horizon banks commonly quote alongside the 1-year one


def p50_p90(saved_per_year):
    """Median and 10th percentile of yearly litres saved."""
    s = np.asarray(saved_per_year, float)
    return float(np.median(s)), float(np.percentile(s, 10))


def round_down(x, sig=2):
    """Round down to `sig` significant figures: 30,066 -> 30,000; 9,876 -> 9,800. Never rounds up."""
    if x <= 0:
        return 0.0
    step = 10 ** (math.floor(math.log10(x)) - sig + 1)
    return float(math.floor(x / step) * step)


def p90_sentence(saved_per_year, place="this island"):
    """The bank sentence, and the evidence behind it -> (sentence, facts dict)."""
    s = np.asarray(saved_per_year, float)
    p50, p90 = p50_p90(s)
    promise = round_down(p90)
    facts = {"p50": p50, "p90": p90, "promise_litres": promise,
             "years": len(s), "years_at_least_promise": int((s >= promise).sum()),
             "worst_year_litres": float(s.min())}
    return f"In 9 years out of 10, {place} saves at least {promise:,.0f} litres of diesel.", facts


def p90_multi_year(saved_per_year, years=LOAN_YEARS, n_boot=N_BOOTSTRAP, seed=0):
    """P90 of the AVERAGE yearly saving over `years` years (bootstrap over the real years)."""
    s = np.asarray(saved_per_year, float)
    rng = np.random.default_rng(seed)
    means = s[rng.integers(0, len(s), size=(n_boot, years))].mean(axis=1)
    return float(np.percentile(means, 10))


def p90_confidence(saved_per_year, n_boot=N_BOOTSTRAP, seed=0):
    """How much to trust the P90 -> dict of litres.

    loo_min / loo_max: lowest / highest P90 when any single year is left out.
    boot_low / boot_high: 5th / 95th percentile of the P90 over bootstrap resamples.
    """
    s = np.asarray(saved_per_year, float)
    n = len(s)
    p50, p90 = p50_p90(s)
    loo = [np.percentile(np.delete(s, i), 10) for i in range(n)] if n > 2 else [p90]
    rng = np.random.default_rng(seed)
    boot = np.percentile(s[rng.integers(0, n, size=(n_boot, n))], 10, axis=1)
    return {
        "years": n,
        "p50": p50,
        "p90": p90,
        "loo_min": float(min(loo)),
        "loo_max": float(max(loo)),
        "boot_low": float(np.percentile(boot, 5)),
        "boot_high": float(np.percentile(boot, 95)),
    }


def saved_per_year(site, load, ghi_years, solar_kw, battery_kwh, p=None):
    """Run one design on every weather year (fast engine) -> litres saved in each year vs diesel only."""
    from engine.dispatch import dispatch
    from engine.sim import DEFAULTS, HOURS, generator_size
    p = dict(DEFAULTS, **(p or {}))
    gen_kw = generator_size(site, load)
    base, _ = dispatch(load, np.zeros(HOURS), 0.0, 0.0, gen_kw, p)
    yrs, _ = dispatch(load, np.asarray(ghi_years, float).T, solar_kw, battery_kwh, gen_kw, p)
    return float(base["fuel_l"]) - yrs["fuel_l"]


def saved_per_year_brief(load, ghi_years, solar_kw, battery_kwh, gen_kw, price, p):
    """The brief's method, literally: simulate_year() once per weather year, collect litres_saved."""
    from engine.dispatch import simulate_year
    knobs = dict(A=p["fuel_a"], B=p["fuel_b"], soc_min=p["batt_min_soc"], soc_start=p["batt_start_soc"],
                 eff_charge=p["batt_eff_charge"], eff_discharge=p["batt_eff_discharge"],
                 gen_min_frac=p["gen_min_load"], pr=p["pv_derate"], c_rate=p["batt_c_rate"])
    return np.array([simulate_year(load, np.asarray(g, float) / 1000.0, solar_kw, battery_kwh, gen_kw,
                                   price, **knobs)["litres_saved"] for g in ghi_years])


# ---------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------

def _years(label, n):
    """Calendar years from a label like 'NASA POWER hourly, 2005-2024 (20 years)', else 1..n."""
    import re
    m = re.search(r"(\d{4})-(\d{4})", label)
    if m and int(m.group(2)) - int(m.group(1)) + 1 == n:
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    return [f"yr {i + 1}" for i in range(n)]


def _site_inputs(site_id):
    from engine.optimise import FUNAFUTI, _funafuti_load
    from data.loads import CLINIC_CRITICAL_KW, build_load
    from data.weather import get_ghi, get_site
    if site_id == "funafuti":
        site = dict(FUNAFUTI)
        load, _ = _funafuti_load()
        critical = np.full(len(load), CLINIC_CRITICAL_KW)   # data/loads.py ASSUMPTION (vaccine fridge + light)
    else:
        site = get_site(site_id)
        if not site:
            raise SystemExit(f"unknown site '{site_id}'")
        load, critical, _ = build_load(site)
    ghi, label = get_ghi(site["lat"], site["lon"])
    return site, load, critical, ghi, label


def report(site_id="funafuti"):
    """Brief 3.6 done-when: the full SimResult for a site, every field filled, timed."""
    import json
    import time
    from engine import sim

    site, load, critical, ghi, label = _site_inputs(site_id)
    t0 = time.perf_counter()
    result, opt = sim.plan(site, load, critical, ghi, label)
    seconds = time.perf_counter() - t0
    w, sr = opt["winner"], result["simresult"]
    saved = np.asarray(w["litres_saved_per_year"])
    price = opt["diesel_price"]
    years = _years(label, len(saved))
    sentence, f = p90_sentence(saved)
    c = p90_confidence(saved)
    p90_10 = p90_multi_year(saved)

    print(f"\nP50 / P90 - {site['name']}, {site['country']}: {w['solar_kw']:.1f} kW solar + "
          f"{w['battery_kwh']:.1f} kWh battery, on {label}")
    print(f"\n  Diesel saved each year, worst first ({len(saved)} real weather years):")
    lo, hi = saved.min() * 0.98, saved.max()
    order = np.argsort(saved)
    for rank, k in enumerate(order, 1):
        bar = "#" * max(1, int(round(40 * (saved[k] - lo) / (hi - lo)))) if hi > lo else "#" * 40
        mark = ""
        if rank == 2 or rank == 3:
            mark = "  <- P90 sits between these two"
        if rank == (len(saved) + 1) // 2:
            mark = "  <- P50 (middle year)"
        print(f"   {years[k]!s:>6}  {saved[k]:>8,.0f} L  {bar}{mark}")

    print(f"\n  P50  {f['p50']:,.0f} L/yr   typical year (median)")
    print(f"  P90  {f['p90']:,.0f} L/yr   numpy.percentile(savings, 10): '10th percentile of savings = P90 exceedance'")
    print(f"\n  >> \"{sentence}\"")
    print(f"     Evidence: {f['years_at_least_promise']} of {f['years']} real years saved at least "
          f"{f['promise_litres']:,.0f} L; the worst ({years[int(order[0])]}) saved {f['worst_year_litres']:,.0f} L.")
    print(f"     In money: at least AUD {round_down(f['promise_litres'] * price):,.0f} a year of diesel "
          f"not bought (at {price:.2f} AUD/L), "
          f"{round(f['promise_litres'] * result['assumptions']['co2_kg_per_litre'] / 1000)} t CO2.")
    print(f"\n  Bank view")
    rows = (("1-year P90", f"{f['p90']:,.0f} L   (any single year)"),
            (f"{LOAN_YEARS}-year P90 of the average", f"{p90_10:,.0f} L   (good and bad years average out over a loan)"),
            ("drop any one year", f"P90 stays within {c['loo_min']:,.0f} - {c['loo_max']:,.0f} L"),
            ("90% bootstrap range", f"{c['boot_low']:,.0f} - {c['boot_high']:,.0f} L"),
            ("covers", f"weather only ({len(saved)} real years). Not in it: load growth, panel ageing,"),
            ("", "diesel price changes."))
    for name, text in rows:
        print(f"    {name:28s} {text}")

    filled = all(v is not None for v in sr.values())
    print(f"\n  SimResult (brief Chapter 1.4):")
    print("  " + json.dumps(sr, indent=2).replace("\n", "\n  "))
    print(f"\n  every field filled: {'YES' if filled else 'NO'}   built in {seconds:.1f} s (target: under 5 s)"
          f"   [a repeat request is answered from the plan cache in milliseconds]\n")
    return result, opt, seconds


def table():
    """One line per app site: P50, P90 and how sure we are of the P90."""
    from engine.sim import simulate
    from data.loads import build_load
    from data.weather import get_ghi, load_sites

    print(f"{'site':9s} {'years':>5s} {'P50 L':>8s} {'P90 L':>8s}  {'P90 if one year dropped':>23s}"
          f"  {'P90 90% range (bootstrap)':>26s}")
    for site in load_sites():
        load, critical, _ = build_load(site)
        ghi, label = get_ghi(site["lat"], site["lon"])
        d = simulate(site, load, critical, ghi, label)["design"]
        c = p90_confidence(saved_per_year(site, load, ghi, d["solar_kw"], d["battery_kwh"]))
        print(f"{site['id']:9s} {c['years']:5d} {c['p50']:8,.0f} {c['p90']:8,.0f}  "
              f"{c['loo_min']:10,.0f} - {c['loo_max']:<10,.0f}  {c['boot_low']:12,.0f} - {c['boot_high']:<12,.0f}"
              f"  [{label}]")


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "funafuti"
    table() if arg == "all" else report(arg)
