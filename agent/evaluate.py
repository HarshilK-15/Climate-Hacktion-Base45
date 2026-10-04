"""Accuracy tests for the front desk (brief 5.2 + 5.5). Owner: Mech A.

    python -m agent.evaluate                 # with a key: the AI; without: the rules + templates
    python -m agent.evaluate --regenerate    # rebuild the 20 test SimResults from the engine (~1 min)

Writes agent/eval/RESULTS.md (the numbers for the README) and agent/eval/results.json.

1. Intake: 20 descriptions (agent/eval/intake_cases.json: good, vague, a Pacific language,
   adversarial). Pass = the intake ends as expected (ok / not ok) with every expected field right,
   after at most 3 follow-up questions answered from the script. Cases 1-15 are the brief's 5.2 set.
2. Explainer: 20 real engine results (agent/eval/simresults.json: the 7 demo islands + 13 variations
   run through the engine). For each: does the summary pass the checker first try? Within 3 tries?
   Within the word limit? Then the tamper test: the same summary checked against a corrupted copy
   of the engine result must FAIL, every time. That is the "fail loudly on camera" guarantee.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from agent import intake, llm
from agent.check_numbers import check
from agent.explain import WORD_LIMITS, explain
from agent.facts import facts as build_facts

HERE = Path(__file__).resolve().parent / "eval"
ROOT = Path(__file__).resolve().parent.parent
VARIATIONS = [   # 13 extra plans, so the explainer is tested on 20 different engine results
    {"site_id": "kadavu", "households": 80}, {"site_id": "kadavu", "households": 160},
    {"site_id": "eua", "diesel_price_per_litre": 2.4}, {"site_id": "nanumea", "households": 120},
    {"site_id": "malekula", "households": 40}, {"site_id": "malekula", "households": 90},
    {"site_id": "abaiang", "other_kw": 10}, {"site_id": "savaii", "households": 200},
    {"site_id": "funafuti", "households": 600, "has_clinic": True},
    {"name": "Village near Suva", "lat": -18.14, "lon": 178.44, "households": 70, "has_clinic": True},
    {"name": "Village near Apia", "lat": -13.85, "lon": -171.75, "households": 50, "has_clinic": True},
    {"name": "Village near Port Vila", "lat": -17.73, "lon": 168.32, "households": 100, "has_school": True},
    {"site_id": "kadavu", "diesel_litres_per_month": 4000},
]
_KEEP = ("simresult", "site", "weather", "years_of_weather", "load", "today", "design", "no_fuel_ship",
         "assumptions", "bank", "options", "price_of_resilience_aud", "whole_island_no_fuel")


def regenerate():
    """Build agent/eval/simresults.json from the engine (precomputed islands + VARIATIONS)."""
    from engine.service import build_inputs, compute, demo_islands
    out = []
    for island in demo_islands():
        r = json.loads((ROOT / "engine" / "precomputed" / f"{island}.json").read_text(encoding="utf-8"))
        out.append({"label": island, **{k: r[k] for k in _KEEP if k in r}})
    for req in VARIATIONS:
        t0 = time.perf_counter()
        r = compute(build_inputs(req))
        out.append({"label": json.dumps(req), **{k: r[k] for k in _KEEP if k in r}})
        print(f"  engine: {req}  ({time.perf_counter() - t0:.1f} s)")
    (HERE / "simresults.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _corrupt(fact_sheet):
    """Tamper with the numbers that matter most, as a bug or an attacker between engine and AI might."""
    bad = dict(fact_sheet)
    for k in ("recommended_solar_kw", "recommended_battery_kwh", "purchase_cost_aud",
              "diesel_saved_litres_typical_year_p50", "diesel_saved_litres_bad_year_p90",
              "promise_litres_9_years_in_10", "percent_less_diesel_typical_year", "co2_tonnes_avoided_per_year"):
        if isinstance(bad.get(k), (int, float)) and not isinstance(bad.get(k), bool):
            bad[k] = round(bad[k] * 1.37 + 7, 1)
    return bad


def run_intake():
    cases = json.loads((HERE / "intake_cases.json").read_text(encoding="utf-8"))["cases"]
    rows = []
    for c in cases:
        s = intake.run(c["text"], c["answers"])
        si = s["site_input"] or {}
        wrong = []
        if s["ok"] != c["expect_ok"]:
            wrong.append(f"ok={s['ok']} (expected {c['expect_ok']})")
        for k, v in c["expect"].items():
            got = si.get(k)
            if v is None:
                if got is not None:
                    wrong.append(f"{k}={got} (should be empty)")
            elif isinstance(v, bool) or isinstance(v, str):
                if got != v:
                    wrong.append(f"{k}={got} (expected {v})")
            elif got is None or abs(float(got) - v) > 0.01 * abs(v) + 1e-9:
                wrong.append(f"{k}={got} (expected {v})")
        rows.append({"id": c["id"], "kind": c["kind"], "passed": not wrong, "valid_site_input": s["ok"],
                     "questions_asked": len(s["turns"]), "source": s["source"], "problems": wrong,
                     "warnings": s["warnings"]})
    return rows


def run_explain():
    sims = json.loads((HERE / "simresults.json").read_text(encoding="utf-8"))
    rows = []
    for r in sims:
        out = explain(r, "officer")
        f = build_facts(r)["facts"]
        tampered = check(out["text"], _corrupt(f))
        first = out["attempts"][0]["passed"] if out["attempts"] else out["source"] == "template"
        rows.append({"label": r["label"], "source": out["source"], "passed_first_try": bool(first),
                     "passed": out["check"]["passed"], "attempts": len(out["attempts"]),
                     "rejected_drafts": out["rejected_drafts"], "words": out["words"],
                     "within_word_limit": out["words"] <= WORD_LIMITS["officer"],
                     "numbers_checked": out["check"]["numbers_checked"],
                     "tamper_detected": not tampered.passed, "tamper_flagged": tampered.unknown_numbers})
    return rows


def report(intake_rows, explain_rows):
    mode = llm.mode()
    first15 = [r for r in intake_rows if r["id"] <= 15]
    n = lambda rows, key: sum(1 for r in rows if r[key])
    lines = [f"# Front-desk accuracy results", "",
             f"Run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} with `python -m agent.evaluate`. "
             f"Mode: **{'AI (' + llm.MODEL + ')' if mode == 'ai' else 'rules + templates (no API key)'}**.", "",
             "## Intake: description -> SiteInput", "",
             f"- Brief 5.2 set (cases 1-15): **{n(first15, 'passed')}/15** correct "
             f"(valid SiteInput: {n(first15, 'valid_site_input')}/15; target 12/15)",
             f"- Full set (5.5): **{n(intake_rows, 'passed')}/20** correct "
             f"(valid SiteInput: {n(intake_rows, 'valid_site_input')}/20)", "",
             "| # | kind | result | questions | problems |", "| --- | --- | --- | --- | --- |"]
    for r in intake_rows:
        lines.append(f"| {r['id']} | {r['kind']} | {'pass' if r['passed'] else 'FAIL'} | {r['questions_asked']} | "
                     f"{'; '.join(r['problems']) or '-'} |")
    lines += ["", "## Explainer + number checker (20 engine results)", "",
              f"- Passed the checker first try: **{n(explain_rows, 'passed_first_try')}/20**",
              f"- Passed in the end (AI or template fallback): **{n(explain_rows, 'passed')}/20**",
              f"- Within the 120-word limit: {n(explain_rows, 'within_word_limit')}/20",
              f"- AI drafts rejected by the checker: {sum(r['rejected_drafts'] for r in explain_rows)}",
              f"- **Tamper test: corrupted engine result detected {n(explain_rows, 'tamper_detected')}/20**", "",
              "| result | source | words | numbers | first try | tamper caught |", "| --- | --- | --- | --- | --- | --- |"]
    for r in explain_rows:
        lines.append(f"| {r['label'][:48]} | {r['source']} | {r['words']} | {r['numbers_checked']} | "
                     f"{'yes' if r['passed_first_try'] else 'no'} | {'yes' if r['tamper_detected'] else 'NO'} |")
    if mode != "ai":
        lines += ["", "**No API key was set for this run**, so these are the rules and templates the app falls "
                      "back to. Run again with `ANTHROPIC_API_KEY` set to score the AI itself."]
    return "\n".join(lines) + "\n"


def main(argv):
    if "--regenerate" in argv or not (HERE / "simresults.json").exists():
        regenerate()
    t0 = time.perf_counter()
    i_rows, e_rows = run_intake(), run_explain()
    md = report(i_rows, e_rows)
    (HERE / "RESULTS.md").write_text(md, encoding="utf-8")
    (HERE / "results.json").write_text(json.dumps({"mode": llm.mode(), "model": llm.MODEL, "intake": i_rows,
                                                    "explain": e_rows}, indent=1), encoding="utf-8")
    print(md)
    print(f"({time.perf_counter() - t0:.0f} s) -> agent/eval/RESULTS.md")


if __name__ == "__main__":
    main(sys.argv[1:])
