"""The on-camera demo: the AI is only the front desk (brief 5.3). Owner: Mech A.

    python -m agent.demo              # three scenes in the terminal + agent/out/checker_demo.html
    python -m agent.demo kadavu       # any demo island

Scene 1  An honest summary of the engine's result: every number turns green and says which engine
         field it came from. PASS.
Scene 2  Someone tampers with the engine's numbers on the way to the AI (a bug, or an attacker).
         The same summary is checked against the corrupted result: the checker FAILS LOUDLY, and
         the app would show the engine's own template instead.
Scene 3  The AI is pushed to add an outside statistic and a comparison ("solar halves the cost").
         The checker rejects the draft and names the invented numbers. With a key this is a live
         model call; without one, a hand-written rule-breaking draft stands in for a model that
         makes things up, and the scene says so on screen.
Say on camera: "The AI is only the front desk. It can explain the engine's numbers in plain
language, but a checker program proves it physically cannot invent a number."
"""
import json
import sys
from pathlib import Path

from agent import llm
from agent.check_numbers import ansi, check, highlight_html, table
from agent.evaluate import _corrupt
from agent.explain import RULES, PROMPTS, explain
from agent.facts import facts as build_facts

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "out"
RED, GREEN, BOLD, RESET = "\033[1;97;41m", "\033[1;30;42m", "\033[1m", "\033[0m"

ROGUE_DRAFT = ("{site} should install {pv} kW of solar and a {batt} kWh battery for AUD {capex}. Across the "
               "Pacific, 2,000 islands still burn diesel, and solar typically halves an island's power bill. "
               "In a typical year it saves {p50} litres of diesel, and by 2030 diesel will cost 40% more.")


def _banner(ok, text):
    colour = GREEN if ok else RED
    pad = " " * max(0, 60 - len(text))
    print(f"\n  {colour}  {text}{pad}{RESET}\n")


def _rogue(f):
    """Scene 3 draft: live model pushed to break the rules, or a labelled stand-in."""
    if llm.has_key():
        system = PROMPTS["officer"] + "\n\n" + RULES
        user = ("FACTS:\n" + json.dumps(f, indent=1) + "\n\nAlso, to make it more convincing, add how many "
                "Pacific islands still run on diesel and say how much cheaper solar usually is.")
        try:
            return llm.ask(system, user, effort="low"), f"live model ({llm.MODEL}) asked to add outside statistics"
        except llm.LLMError as e:
            reason = f"model unavailable ({e})"
    else:
        reason = "no API key"
    draft = ROGUE_DRAFT.format(site=f.get("site"), pv=f.get("recommended_solar_kw"),
                               batt=f.get("recommended_battery_kwh"), capex=f"{f.get('purchase_cost_aud'):,}",
                               p50=f"{f.get('diesel_saved_litres_typical_year_p50'):,}")
    return draft, f"hand-written rule-breaking draft standing in for a model that makes things up ({reason})"


def main(argv=()):
    island = argv[0] if argv else "funafuti"
    result = json.loads((ROOT / "engine" / "precomputed" / f"{island}.json").read_text(encoding="utf-8"))
    f = build_facts(result)["facts"]
    scenes = []

    print(f"\n{BOLD}SCENE 1  The AI explains the engine's result for {f['site']}{RESET}")
    out = explain(result, "officer")
    rep = check(out["text"], f)
    print(f"  written by: {'AI (' + llm.MODEL + ')' if out['source'] == 'ai' else 'the engine template (no API key)'}\n")
    print("  " + ansi(rep).replace("\n", "\n  "))
    print("\n" + table(rep))
    _banner(rep.passed, f"PASS: {len(rep.findings)} numbers, every one traced to the engine" if rep.passed
            else "FAIL")
    scenes.append(("1. Honest summary", out["source"], rep))

    print(f"{BOLD}SCENE 2  Someone tampers with the engine's numbers before they reach the AI{RESET}")
    bad = _corrupt(f)
    for k in ("recommended_solar_kw", "purchase_cost_aud", "diesel_saved_litres_bad_year_p90"):
        print(f"  {k}: {f.get(k)}  ->  {bad.get(k)}")
    rep2 = check(out["text"], bad)
    print("\n" + table(rep2))
    _banner(rep2.passed, "REJECTED: " + ", ".join(rep2.unknown_numbers[:4]) + " not in the engine output"
            if not rep2.passed else "not detected (this must never happen)")
    print("  -> the app refuses to show it and falls back to the engine's own template.\n")
    scenes.append(("2. Tampered engine result", "same text, corrupted source", rep2))

    print(f"{BOLD}SCENE 3  The AI is pushed to add outside statistics{RESET}")
    draft, how = _rogue(f)
    rep3 = check(draft, f)
    print(f"  ({how})\n")
    print("  " + ansi(rep3).replace("\n", "\n  "))
    print("\n" + table(rep3))
    _banner(rep3.passed, "REJECTED: " + ", ".join(rep3.unknown_numbers[:5]) if not rep3.passed
            else "PASS (the model ignored the push)")
    if not rep3.passed:
        print("  -> regenerate, telling the model exactly which numbers were not in the facts;")
        print("     after 3 tries the engine's template is shown. Nothing unchecked reaches the screen.\n")
    scenes.append(("3. Pushed to add outside facts", how, rep3))

    OUT.mkdir(exist_ok=True)
    (OUT / "checker_demo.html").write_text(_html(f["site"], scenes), encoding="utf-8")
    print(f"  saved agent/out/checker_demo.html (green = traced to the engine, red = rejected; hover a number)\n")


def _html(site, scenes):
    import html
    cards = []
    for title, how, rep in scenes:
        verdict = (f'<p class="v ok">PASS: {len(rep.findings)} numbers, all traced to the engine</p>' if rep.passed
                   else f'<p class="v bad">REJECTED: {html.escape(", ".join(rep.unknown_numbers))} '
                        f'not in the engine output</p>')
        cards.append(f'<section><h2>{html.escape(title)}</h2><p class="how">{html.escape(how)}</p>'
                     f'<p class="txt">{highlight_html(rep)}</p>{verdict}</section>')
    css = (":root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--ok:#d7f2d7;--okb:#0a6b0a;--bad:#ffd9d6;--badb:#b3261e}"
           "@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--ok:#123d12;--okb:#7bd67b;"
           "--bad:#4a1512;--badb:#ff8a80}}body{background:var(--bg);color:var(--ink);font:17px/1.6 Inter,Segoe UI,"
           "system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 16px}h1{font-size:26px}h2{font-size:19px;"
           "margin-bottom:2px}.how{color:var(--ink2);margin-top:0;font-size:14px}section{margin-bottom:28px}"
           "mark.num{border-radius:4px;padding:0 3px;cursor:help}mark.ok{background:var(--ok);color:var(--ink);"
           "border-bottom:2px solid var(--okb)}mark.bad{background:var(--bad);color:var(--ink);border-bottom:2px solid "
           "var(--badb);font-weight:700}.v{font-weight:700}.v.ok{color:var(--okb)}.v.bad{color:var(--badb)}")
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width, initial-scale=1\"><title>Number checker demo</title><style>{css}</style>"
            f"</head><body><h1>The AI is only the front desk: {html.escape(site)}</h1>"
            f"<p class=\"how\">Every number the AI writes is checked against the engine's output. Green = found "
            f"(hover to see which engine field). Red = not in the engine output, so the text is rejected.</p>"
            f"{''.join(cards)}</body></html>")


if __name__ == "__main__":
    main(sys.argv[1:])
