"""The fact sheet: the only thing the AI is allowed to see. Owner: Mech A.

facts(result) turns the engine's answer into a small, flat dictionary with plain names and units
("diesel_saved_litres_bad_year_p90": 30126). No AI is involved and nothing new is calculated:
every value is copied from the engine's output (or is an engine constant), and `sources` says
which engine field each one came from, so a checked number can be traced all the way back.

Accepts any engine answer: the flat SimResult (contracts/mock_simresult.json, brief 1.4), the
server's plan (POST /api/plan) or the engine service's answer (POST /simulate, which adds "bank").
portfolio_facts() does the same for engine/precomputed/index.json (the funder summary).
"""
import re

from engine.optimise import SHIP_DAYS
from engine.p90 import round_down
from engine.sim import DEFAULTS

# (fact name, where it comes from in a plan, where it comes from in a flat SimResult)
_PLAN_FIELDS = [
    ("recommended_solar_kw", "design.solar_kw", "pv_kw"),
    ("recommended_battery_kwh", "design.battery_kwh", "battery_kwh"),
    ("generator_kw", "load.generator_kw", "generator_kw"),
    ("purchase_cost_aud", "design.purchase_cost", "capex_aud"),
    ("cost_over_project_life_aud", "design.lifetime_cost", "lifetime_cost_aud"),
    ("diesel_only_cost_over_project_life_aud", "today.lifetime_cost", None),
    ("saving_over_project_life_aud", "design.lifetime_saving", None),
    ("diesel_litres_per_year_today", "today.diesel_litres_per_year", None),
    ("diesel_cost_per_year_today_aud", "today.diesel_cost_per_year", None),
    ("diesel_saved_litres_typical_year_p50", "design.litres_saved_p50", "diesel_litres_saved_p50"),
    ("diesel_saved_litres_bad_year_p90", "design.litres_saved_p90", "diesel_litres_saved_p90"),
    ("percent_less_diesel_typical_year", "design.percent_diesel_cut_p50", None),
    ("money_saved_per_year_aud_p50", "design.money_saved_per_year_p50", None),
    ("payback_years", "design.payback_years", None),
    ("solar_share_percent", "design.solar_share_percent", None),
    ("generator_hours_per_year", "design.generator_hours_per_year", None),
    ("co2_tonnes_avoided_per_year", "simresult.co2_tonnes_saved_per_year", "co2_tonnes_saved_per_year"),
    ("blackout_hours_worst_year", "simresult.blackout_hours_worst_year", "blackout_hours_worst_year"),
    ("clinic_survives_fuel_ship_delay", "simresult.critical_load_survives_7d_no_fuel",
     "critical_load_survives_7d_no_fuel"),
    ("clinic_days_powered_with_no_diesel", "no_fuel_ship.days_clinic_powered", None),
    ("cloudiest_week_sun_percent_of_normal", "no_fuel_ship.cloudiest_week_sunshine_vs_average_percent", None),
    ("weather_years_simulated", "years_of_weather", "years_simulated"),
    ("daily_demand_kwh", "load.daily_kwh", None),
    ("clinic_critical_load_kw", "load.critical_kw", None),
    ("diesel_price_aud_per_litre", "assumptions.diesel_price_per_litre", None),
    ("project_years", "assumptions.project_years", None),
    ("promise_litres_9_years_in_10", "bank.promise_litres", None),
    ("p90_10_year_average_litres", "bank.p90_10_year_average_litres", None),
    ("price_of_resilience_aud", "price_of_resilience_aud", None),
    ("whole_island_hours_powered_of_168_no_diesel", "whole_island_no_fuel.hours_powered_of_168", None),
]


def _get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def is_flat_simresult(result):
    return isinstance(result, dict) and "pv_kw" in result and "design" not in result


def facts(result):
    """Engine answer -> {"facts": {...}, "sources": {fact: engine field}}."""
    flat = is_flat_simresult(result)
    out, src = {}, {}
    if flat:
        out["site"] = result.get("site")
    else:
        s = result.get("site") or {}
        out["site"] = ", ".join(str(x) for x in (s.get("name"), s.get("country")) if x) or \
            _get(result, "simresult.site")
    src["site"] = "site"
    for name, plan_path, flat_key in _PLAN_FIELDS:
        path = flat_key if flat else plan_path
        if path is None:
            continue
        v = result.get(path) if flat else _get(result, path)
        if v is not None:
            out[name], src[name] = v, path
    # Engine constants the sentences need (not island facts; listed so the checker can see them)
    out["fuel_ship_delay_days_tested"], src["fuel_ship_delay_days_tested"] = SHIP_DAYS, "engine.optimise.SHIP_DAYS"
    if "project_years" not in out:
        out["project_years"], src["project_years"] = DEFAULTS["project_years"], "engine.sim.DEFAULTS"
    p90 = out.get("diesel_saved_litres_bad_year_p90")
    if "promise_litres_9_years_in_10" not in out and p90:
        out["promise_litres_9_years_in_10"] = round_down(p90)
        src["promise_litres_9_years_in_10"] = "engine.p90.round_down(P90)"
    label = result.get("weather") if not flat else None
    m = re.search(r"(\d{4})-(\d{4})", label or "")
    if m:
        out["weather_first_year"], out["weather_last_year"] = int(m.group(1)), int(m.group(2))
        src["weather_first_year"] = src["weather_last_year"] = "weather (label)"
    if label:
        out["weather_source"] = label
    bank = result.get("bank") or {}
    each = bank.get("litres_saved_each_year") or {}
    if each:
        worst = min(each, key=each.get)
        if worst.isdigit():
            out["worst_weather_year"], src["worst_weather_year"] = int(worst), "bank.litres_saved_each_year"
        out["litres_saved_in_worst_year"], src["litres_saved_in_worst_year"] = each[worst], "bank.litres_saved_each_year"
    return {"facts": out, "sources": src}


def portfolio_facts(index, sensitivity=None, published=None):
    """engine/precomputed/index.json (+ sensitivity.json, published_case.json) -> fact sheet for funders."""
    t = index["totals"]
    out = {
        "islands": t["islands"],
        "total_purchase_cost_aud": t["capex_aud"],
        "total_diesel_saved_litres_typical_year_p50": t["diesel_litres_saved_p50"],
        "total_diesel_saved_litres_sum_of_island_p90s": t["diesel_litres_saved_p90"],
        "total_co2_tonnes_avoided_per_year": t["co2_tonnes_saved_per_year"],
        "total_money_saved_per_year_aud_p50": t["money_saved_per_year_p50"],
        "clinics_protected": t["clinics_protected"],
        "project_years": DEFAULTS["project_years"],
        "fuel_ship_delay_days_tested": SHIP_DAYS,
        "per_island": [{
            "island": f"{i['name']}, {i['country']}",
            "solar_kw": i["simresult"]["pv_kw"], "battery_kwh": i["simresult"]["battery_kwh"],
            "purchase_cost_aud": i["simresult"]["capex_aud"],
            "diesel_saved_litres_p50": i["simresult"]["diesel_litres_saved_p50"],
            "diesel_saved_litres_p90": i["simresult"]["diesel_litres_saved_p90"],
            "percent_less_diesel": i["percent_diesel_cut_p50"], "payback_years": i["payback_years"],
            "clinic_survives": i["simresult"]["critical_load_survives_7d_no_fuel"],
            "weather_years": i["simresult"]["years_simulated"],
        } for i in index["islands"]],
    }
    if sensitivity:
        from engine.sensitivity import FACTORS
        out["sensitivity_price_change_percent"] = round(100 * (max(FACTORS) - 1))
        out["sensitivity"] = {k: {"max_regret_percent": v["max_regret_percent"],
                                  "min_lifetime_saving_aud": v["min_lifetime_saving_aud"],
                                  "robust": v["robust"]} for k, v in sensitivity["islands"].items()}
        # counts of the engine's own per-island flags (engine/sensitivity.py "robust")
        out["islands_in_sensitivity_test"] = len(out["sensitivity"])
        out["islands_robust_to_price_changes"] = sum(v["robust"] for v in out["sensitivity"].values())
        out["lowest_lifetime_saving_any_price_aud"] = min(v["min_lifetime_saving_aud"]
                                                          for v in out["sensitivity"].values())
    if published:
        c1, c2 = published["check_1_fuel_curve"], published["check_2_as_built"]
        out["validation_tau"] = {
            "reported_kwh_per_year": c1["reported_kwh_per_year"],
            "our_kwh_per_year_from_reported_diesel": c1["implied_kwh_per_year_from_our_fuel_curve"],
            "difference_percent": c1["difference_percent"],
            "as_built_pv_kw": c2["pv_kw"], "as_built_battery_kwh": c2["battery_kwh"],
            "our_solar_share_percent": c2["solar_share_percent_p50"],
        }
    return out
