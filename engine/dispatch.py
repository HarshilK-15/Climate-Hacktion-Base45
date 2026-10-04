"""Hour-by-hour dispatch: solar first, then battery, then diesel as the last resort.

Owner: Elec B.

One hour, in order (brief section 3.2):
  1. Solar serves the load first.
  2. Spare solar charges the battery (round trip loses ~10% as heat: 0.95 in x 0.95 out).
  3. If solar is short, the battery discharges, down to a safe minimum charge.
  4. If still short, the diesel generator starts, but never below 30% of its rated power.
     When that floor makes it produce more than the island needs, the extra first takes over
     from the battery (no round-trip loss), then charges it; only the rest is wasted.
  5. Anything still unserved is a blackout hour (the number we must drive to zero).

The same rules are written twice:
  simulate_year()  a plain loop for one design: easy to read and check by hand
  dispatch()       numpy version that runs thousands of designs / weather years at once (sim.py)
test_engine.py runs both on the same inputs and checks they agree.

Diesel fuel curve (linear, the standard generator model):
    litres = A x (kWh produced) + B x (rated kW) x (hours running)
A is the fuel per unit of energy. B is the fuel burned just for being switched on, which
is why running a big generator for a tiny load is so wasteful.
"""
import numpy as np

# ---- Generator fuel curve (SOURCED) ----
# Source: Cummins C55 D5e generator set data sheet D-6280-EN (50 Hz, prime 50 kVA / 40 kW)
#   https://yorpower.com/wp-content/uploads/2025/07/C55D5E.pdf
# Prime-rating fuel use at 1/4, 1/2, 3/4, full load: 3.8, 6.4, 9.4, 12.0 L/h.
# Least-squares fit of L/h = A*kW_out + B*kW_rated over those 4 points.
# (Standby column gives A=0.278, B=0.023, so the fit is stable.)
# Cross-check: FW Power fuel chart, 32-80 kW rows, gives A ~0.27-0.31, B ~0-0.016
#   https://fwpower.co.uk/wp-content/uploads/2018/12/Diesel-Generator-Fuel-Consumption-Chart-in-Litres.pdf
FUEL_A_L_PER_KWH = 0.276              # litres per kWh produced
FUEL_B_L_PER_H_PER_KW_RATED = 0.025   # litres per hour per kW of generator size, while running

# ---- Technical settings shared by simulate_year() and dispatch() (sim.py DEFAULTS uses these) ----
TECH_DEFAULTS = {
    # ASSUMPTION: performance ratio (dust, heat, wiring, inverter). Brief 3.2 says ~0.80;
    # replace with the installer's PV design report for the site.
    "pv_derate": 0.80,
    # ASSUMPTION: LiFePO4 one-way efficiencies, 0.95 x 0.95 = ~90% round trip (brief: "lose ~10%").
    # Replace with the chosen battery's datasheet round-trip efficiency.
    "batt_eff_charge": 0.95,
    "batt_eff_discharge": 0.95,
    # ASSUMPTION: never below 20% charge (brief code says 0.15; 0.20 is the more cautious
    # choice for battery life). Replace with the supplier's warranty depth-of-discharge.
    "batt_min_soc": 0.20,
    # ASSUMPTION: max charge/discharge power = 0.5 x capacity. Replace with the inverter rating.
    "batt_c_rate": 0.5,
    # ASSUMPTION: the year starts half full; changes yearly totals by well under 0.1%.
    "batt_start_soc": 0.5,
    # Brief 3.2: running a diesel below ~30% load causes wet stacking and wrecks it.
    # TODO(Data Sci): add the manufacturer source link to data/costs.md.
    "gen_min_load": 0.30,
    "fuel_a": FUEL_A_L_PER_KWH,
    "fuel_b": FUEL_B_L_PER_H_PER_KW_RATED,
}


def fuel_litres(kwh, rated_kw, hours, a=FUEL_A_L_PER_KWH, b=FUEL_B_L_PER_H_PER_KW_RATED):
    """Diesel burned by a generator of rated_kw that runs for `hours` and produces `kwh`."""
    return a * kwh + b * rated_kw * hours


def _year_loop(load_kw, ghi_kwm2, pv_kw, batt_kwh, gen_kw, A, B, soc_min, soc_start,
               eff_charge, eff_discharge, gen_min_frac, pr, c_rate, record=False):
    """The five dispatch steps, one hour at a time. Returns yearly totals (+ hourly trace if record)."""
    # float64 throughout: weather often arrives as float32, and numpy keeps float32 maths
    load_kw, ghi_kwm2 = np.asarray(load_kw, float).tolist(), np.asarray(ghi_kwm2, float).tolist()
    pv_kw, batt_kwh, gen_kw = float(pv_kw), float(batt_kwh), float(gen_kw)
    soc = soc_start * batt_kwh            # battery state of charge, in kWh
    soc_floor = soc_min * batt_kwh
    p_max = c_rate * batt_kwh             # battery power limit, kW
    gen_floor = gen_min_frac * gen_kw
    litres = gen_kwh = gen_to_load = solar_used = batt_out = 0.0
    unserved_kwh = curtailed = gen_dumped = 0.0
    gen_hours = blackout_hours = 0
    trace = {k: [] for k in ("load", "solar", "battery", "diesel", "soc_pct", "unserved")} if record else None

    for L, g in zip(load_kw, ghi_kwm2):
        solar = pv_kw * g * pr            # kWh produced this hour (1-hour steps: kW == kWh)

        # 1. solar serves the load first
        served = min(L, solar)
        excess = solar - served
        deficit = L - served
        solar_used += served

        # 2. spare solar charges the battery, limited by its power rating and free room
        charged = min(excess, p_max, max(batt_kwh - soc, 0.0) / eff_charge)
        soc += charged * eff_charge
        curtailed += excess - charged

        # 3. battery covers what it can, never below soc_floor
        from_batt = min(deficit, max(min(p_max, (soc - soc_floor) * eff_discharge), 0.0))
        soc -= from_batt / eff_discharge
        deficit -= from_batt

        # 4. generator covers the rest, at >= gen_min_frac of its rating
        covered = gen_to_load_h = 0.0
        if deficit > 1e-9 and gen_kw > 0:
            out = min(max(deficit, gen_floor), gen_kw)
            covered = min(out, deficit)
            spare = out - covered         # forced by the minimum-load rule
            back = min(spare, from_batt)  # first: hand the battery's share back to the generator
            from_batt -= back
            soc += back / eff_discharge
            spare -= back
            stored = min(spare, max(p_max - charged, 0.0), max(batt_kwh - soc, 0.0) / eff_charge)
            soc += stored * eff_charge    # then: charge the battery with what's left
            gen_dumped += spare - stored
            litres += A * out + B * gen_kw    # energy term + "engine on" term
            gen_kwh += out
            gen_to_load_h = covered + back
            gen_to_load += gen_to_load_h
            gen_hours += 1
        batt_out += from_batt

        # 5. anything left is a blackout
        unserved = deficit - covered
        unserved_kwh += unserved
        if unserved > 1e-6:
            blackout_hours += 1

        if record:
            trace["load"].append(L)
            trace["solar"].append(served)
            trace["battery"].append(from_batt)
            trace["diesel"].append(gen_to_load_h)
            trace["soc_pct"].append(100.0 * soc / batt_kwh if batt_kwh > 0 else 100.0)
            trace["unserved"].append(unserved)

    return {
        "litres": litres,
        "gen_hours": gen_hours,
        "blackout_hours": blackout_hours,
        "solar_used_kwh": solar_used,
        "batt_out_kwh": batt_out,
        "gen_kwh": gen_kwh,
        "gen_to_load_kwh": gen_to_load,
        "unserved_kwh": unserved_kwh,
        "curtailed_kwh": curtailed,
        "gen_dumped_kwh": gen_dumped,
        "load_kwh": float(np.sum(load_kw)),
        **({"trace": trace} if record else {}),
    }


def simulate_year(load_kw, ghi_kwm2, pv_kw, batt_kwh, gen_kw, diesel_price,
                  A=FUEL_A_L_PER_KWH, B=FUEL_B_L_PER_H_PER_KW_RATED,
                  soc_min=TECH_DEFAULTS["batt_min_soc"],
                  soc_start=TECH_DEFAULTS["batt_start_soc"],
                  eff_charge=TECH_DEFAULTS["batt_eff_charge"],
                  eff_discharge=TECH_DEFAULTS["batt_eff_discharge"],
                  gen_min_frac=TECH_DEFAULTS["gen_min_load"],
                  pr=TECH_DEFAULTS["pv_derate"],
                  c_rate=TECH_DEFAULTS["batt_c_rate"],
                  record=False):
    """One design, one year of weather.

    load_kw, ghi_kwm2: 8760 hourly values (kW load; kW/m2 sun on a flat panel)
    pv_kw, batt_kwh, gen_kw: the design. diesel_price: per litre (AUD in this project).
    pr: 'performance ratio', real panels deliver ~80% of ideal after heat/dirt/wiring.
    record=True adds r["trace"]: hourly load, solar, battery, diesel, soc_pct, unserved.
    Returns yearly totals. The baseline is the same island and generator with diesel only,
    run through the same rules, so it includes the 30% floor and never runs the generator
    in hours with no demand.
    """
    knobs = dict(A=A, B=B, soc_min=soc_min, soc_start=soc_start, eff_charge=eff_charge,
                 eff_discharge=eff_discharge, gen_min_frac=gen_min_frac, pr=pr, c_rate=c_rate)
    r = _year_loop(load_kw, ghi_kwm2, pv_kw, batt_kwh, gen_kw, record=record, **knobs)
    base = _year_loop(load_kw, np.zeros(len(load_kw)), 0.0, 0.0, gen_kw, **knobs)
    r["baseline_litres"] = base["litres"]
    r["litres_saved"] = base["litres"] - r["litres"]
    r["diesel_cost"] = r["litres"] * diesel_price
    r["money_saved"] = r["litres_saved"] * diesel_price
    r["renewable_share"] = 1 - r["gen_to_load_kwh"] / r["load_kwh"] if r["load_kwh"] else 0.0
    return r


def dispatch(load, ghi, solar_kw, battery_kwh, gen_kw, p, gen_available=True, record=False,
             snapshot_hours=()):
    """Same rules as simulate_year(), vectorised over designs and weather years.

    load: (H,) demand in kW.  ghi: (H,) or (H, Y) sunshine in W/m2.
    solar_kw / battery_kwh: numbers or numpy arrays (to test many designs at once).
    p: settings dict with the TECH_DEFAULTS keys.
    snapshot_hours: also return tot["soc_snapshots"][h], the battery charge (fraction) at the start
    of each hour h listed.
    Returns totals (numpy arrays) and, if record=True, an hourly trace.
    """
    load = np.asarray(load, float)
    ghi = np.asarray(ghi, float)            # float64 even if weather arrives as float32
    S = np.asarray(solar_kw, float)
    B = np.asarray(battery_kwh, float)
    shape = np.broadcast(S, B, np.asarray(ghi[0], float)).shape
    zeros = np.zeros(shape)
    B = np.broadcast_to(B, shape)
    soc = B * p["batt_start_soc"]
    bmin = B * p["batt_min_soc"]
    plim = B * p["batt_c_rate"]
    ec, ed = p["batt_eff_charge"], p["batt_eff_discharge"]
    minload = p["gen_min_load"] * gen_kw
    pr = p["pv_derate"]
    gen_on = gen_available and gen_kw > 0

    has_b = B > 0
    inv_b = np.where(has_b, 1.0 / np.where(has_b, B, 1.0), 0.0)   # 1/B, or 0 with no battery

    tot = {k: zeros.copy() for k in (
        "gen_kwh", "gen_to_load", "solar_direct", "batt_out", "unserved",
        "unserved_hours", "curtailed", "gen_dumped", "gen_hours")}
    hours_before_first = zeros.copy()      # hours that passed before the first blackout
    soc_low = soc.copy()                   # lowest charge seen, kWh
    snaps = set(snapshot_hours)
    tot["soc_snapshots"] = {0: soc * inv_b + ~has_b} if 0 in snaps else {}
    trace = {k: [] for k in ("load", "solar", "battery", "diesel", "soc_pct", "unserved")} if record else None

    for t in range(len(load)):
        L = load[t]
        if ghi[t].any():
            pv = S * (ghi[t] / 1000.0) * pr     # same arithmetic as simulate_year(), so both agree exactly
            direct = np.minimum(pv, L)
            surplus = pv - direct
            deficit = L - direct

            ch = np.minimum(np.minimum(surplus, plim), np.maximum(B - soc, 0) / ec)
            soc = soc + ch * ec
            tot["curtailed"] += surplus - ch
            tot["solar_direct"] += direct
        else:                                   # night in every weather year: no solar maths needed
            direct = ch = 0.0
            deficit = L

        avail = np.maximum(np.minimum(plim, (soc - bmin) * ed), 0)
        dis = np.minimum(deficit, avail)
        soc = soc - dis / ed
        rest = deficit - dis

        if gen_on:
            on = rest > 1e-9
            out = np.where(on, np.minimum(np.maximum(rest, minload), gen_kw), 0.0)
            covered = np.minimum(out, rest)
            spare = out - covered          # forced by minimum load
            back = np.minimum(spare, dis)  # first replace battery discharge, then store
            dis = dis - back
            soc = soc + back / ed
            spare = spare - back
            ch2 = np.minimum(np.minimum(spare, np.maximum(plim - ch, 0)), np.maximum(B - soc, 0) / ec)
            soc = soc + ch2 * ec
            tot["gen_dumped"] += spare - ch2
            tot["gen_kwh"] += out
            tot["gen_hours"] += on
            to_load = covered + back
        else:
            covered = to_load = zeros
        uns = rest - covered

        tot["gen_to_load"] += to_load
        tot["batt_out"] += dis
        tot["unserved"] += uns
        tot["unserved_hours"] += uns > 1e-6
        hours_before_first += tot["unserved_hours"] == 0
        soc_low = np.minimum(soc_low, soc)
        if t + 1 in snaps:
            tot["soc_snapshots"][t + 1] = soc * inv_b + ~has_b

        if record:
            frac = soc * inv_b + ~has_b
            trace["load"].append(float(L))
            trace["solar"].append(float(direct))
            trace["battery"].append(float(dis))
            trace["diesel"].append(float(to_load))
            trace["soc_pct"].append(float(frac * 100))
            trace["unserved"].append(float(uns))

    # Linear fuel curve, so the yearly total follows from the totals (same as summing every hour)
    tot["fuel_l"] = p["fuel_a"] * tot["gen_kwh"] + p["fuel_b"] * gen_kw * tot["gen_hours"]
    tot["soc_min_frac"] = soc_low * inv_b + ~has_b       # 1.0 where there is no battery
    tot["soc_end_frac"] = soc * inv_b + ~has_b            # battery charge after the last hour
    tot["first_unserved_hour"] = np.where(tot["unserved_hours"] > 0, hours_before_first, np.inf)
    tot["load_kwh"] = float(np.sum(load))
    return tot, trace
