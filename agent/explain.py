"""Explains the engine's results in plain language, and proves every number. Owner: Mech A.

    python -m agent.explain funafuti                    # 120-word summary for a fund officer (brief 5.3)
    python -m agent.explain kadavu --audience council   # the village-council version the app shows
    python -m agent.explain funder                      # one-page portfolio summary -> agent/out/

The AI sees ONLY the fact sheet built from the engine's output (agent/facts.py). Whatever it
writes goes through agent/check_numbers.py:
    pass  -> shown, with every number traceable to an engine field
    fail  -> rejected; the model is told exactly which numbers were not in the facts and tries again
             (up to MAX_ATTEMPTS), then the engine's own template is shown instead
The templates are held to the same checker (test_agent.py runs them on every demo island), so even
the fallback can't contain an unsourced number. Rejected drafts are kept in "attempts" so the app
(and the video) can show a rejection happening.
"""
import json
import sys
from pathlib import Path

from agent import llm
from agent.check_numbers import check, highlight_html
from agent.facts import facts as build_facts, portfolio_facts

ROOT = Path(__file__).resolve().parent.parent
PRECOMPUTED = ROOT / "engine" / "precomputed"
OUT = Path(__file__).resolve().parent / "out"
MAX_ATTEMPTS = 3
# Brief 5.3: 120 words. ASSUMPTION: up to 10% over is still "about 120"; longer drafts are retried.
WORD_LIMITS = {"officer": 132, "council": 220, "funder_note": 90}

RULES = """Rules you must follow:
- Use ONLY numbers that appear in FACTS. Write them as digits. You may round a number (keep at least 2 significant figures, e.g. 30,126 -> 30,000) but never calculate a new one: no totals, differences, ratios or percentages of your own.
- Never write numbers as words, and never use comparison words like "half", "double" or "twice".
- Do not add any outside facts, statistics, dates or prices. Do not mention anything that is not in FACTS.
- Money is in Australian dollars: write "AUD 314,870".
- P50 means a typical year. P90 means the saving is beaten in 9 years out of 10 (a bad year).
- Plain English, short sentences, no jargon, no headings, no bullet points."""

PROMPTS = {
    "officer": "You are writing for a non-technical development-fund officer. Summarise these simulation "
               "results in about 120 words of plain English. Structure: what we recommend building, what it "
               "costs, what the island saves in a typical year (P50) and in a bad year (P90), and whether the "
               "clinic survives a fuel-ship delay of the number of days in FACTS.",
    "council": "You are explaining an island energy plan to the village council. Write 4 short, warm paragraphs "
               "(about 160 words in total): today's diesel use and cost; the solar and battery plan and what it "
               "costs; typical-year and bad-year savings; and what happens to the clinic when the fuel ship is late.",
    "funder_note": "You are writing the opening paragraph of a one-page funding summary for a climate fund that "
                   "invests in a portfolio of island microgrids. About 80 words, formal and plain: what the money "
                   "buys, the diesel and CO2 it saves each year, and that every clinic stays powered through a "
                   "late fuel ship. Use the portfolio totals in FACTS.",
}


def _words(text):
    return len(text.split())


def _fmt(v, money=False):
    """Engine number -> text, written so the checker can trace it (thousands commas, no new rounding)."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = f"{v:,}" if isinstance(v, int) else f"{v:,.1f}" if abs(v) >= 10 else f"{v:g}"
    return f"AUD {s}" if money else s


# ---------------------------------------------------------------------------------------------
# Templates (no AI): the fallback, and the baseline the AI must beat
# ---------------------------------------------------------------------------------------------

def template(f, audience="officer"):
    g = lambda k: f.get(k)
    site = g("site") or "This island"
    parts = []
    if audience == "council":
        if g("diesel_litres_per_year_today") is not None:
            cost = f", costing about {_fmt(g('diesel_cost_per_year_today_aud'), True)}" \
                if g("diesel_cost_per_year_today_aud") is not None else ""
            parts.append(f"{site} burns about {_fmt(g('diesel_litres_per_year_today'))} litres of diesel a year "
                         f"today{cost}.")
        pay = f" It pays for itself in about {_fmt(g('payback_years'))} years." if g("payback_years") else ""
        parts.append(f"The best plan we found is {_fmt(g('recommended_solar_kw'))} kW of solar panels and a "
                     f"{_fmt(g('recommended_battery_kwh'))} kWh battery, costing {_fmt(g('purchase_cost_aud'), True)} "
                     f"to buy and install.{pay}")
        cut = f" ({_fmt(g('percent_less_diesel_typical_year'))}% less)" if g("percent_less_diesel_typical_year") else ""
        parts.append(f"In a typical year it saves {_fmt(g('diesel_saved_litres_typical_year_p50'))} litres of "
                     f"diesel{cut}. Even in a bad year (beaten 9 years out of 10) it still saves at least "
                     f"{_fmt(g('diesel_saved_litres_bad_year_p90'))} litres.")
    else:
        gen = f" alongside the existing {_fmt(g('generator_kw'))} kW diesel generator" if g("generator_kw") else ""
        parts.append(f"For {site} we recommend {_fmt(g('recommended_solar_kw'))} kW of solar panels and a "
                     f"{_fmt(g('recommended_battery_kwh'))} kWh battery{gen}.")
        life = ""
        if g("cost_over_project_life_aud") is not None:
            life = (f", and {_fmt(g('cost_over_project_life_aud'), True)} over {_fmt(g('project_years'))} years "
                    f"including diesel and upkeep")
            if g("diesel_only_cost_over_project_life_aud") is not None:
                life += f", against {_fmt(g('diesel_only_cost_over_project_life_aud'), True)} on diesel alone"
        parts.append(f"It costs {_fmt(g('purchase_cost_aud'), True)} to buy and install{life}.")
        cut = f" ({_fmt(g('percent_less_diesel_typical_year'))}% less)" if g("percent_less_diesel_typical_year") else ""
        parts.append(f"In a typical year (P50) the island burns {_fmt(g('diesel_saved_litres_typical_year_p50'))} "
                     f"fewer litres of diesel{cut}; even in a bad year (P90, beaten 9 years out of 10) it saves "
                     f"{_fmt(g('diesel_saved_litres_bad_year_p90'))} litres.")
        if g("co2_tonnes_avoided_per_year") is not None:
            parts.append(f"That avoids {_fmt(g('co2_tonnes_avoided_per_year'))} tonnes of CO2 a year.")
    days = _fmt(g("fuel_ship_delay_days_tested"))
    years = f" in the cloudiest week of {_fmt(g('weather_years_simulated'))} years" if g("weather_years_simulated") else ""
    if g("clinic_survives_fuel_ship_delay"):
        parts.append(f"If the fuel ship is {days} days late{years}, the clinic's vaccine fridge and emergency "
                     f"light stay on for all {days} days, on solar and battery alone.")
    elif g("clinic_survives_fuel_ship_delay") is False:
        lasts = f" after {_fmt(g('clinic_days_powered_with_no_diesel'))} days" \
            if g("clinic_days_powered_with_no_diesel") is not None else ""
        parts.append(f"If the fuel ship is {days} days late{years}, the clinic's essential power runs out{lasts}, "
                     f"so this plan needs a bigger battery or a protected clinic circuit.")
    return "\n\n".join(parts) if audience == "council" else " ".join(parts)


# ---------------------------------------------------------------------------------------------
# AI with the checker in the loop
# ---------------------------------------------------------------------------------------------

def _ai_text(audience, fact_sheet, feedback, language):
    system = PROMPTS[audience] + "\n\n" + RULES
    if language and language.lower() != "english":
        system += (f"\n- Write in {language}. Keep every number as digits exactly as in FACTS. If you are not "
                   f"confident in a word in {language}, use the English word instead.")
    user = "FACTS:\n" + json.dumps(fact_sheet, indent=1, ensure_ascii=False)
    if feedback:
        user += "\n\n" + feedback
    return llm.ask(system, user, effort="medium")


def explain(result, audience="officer", language="English", use_ai=True):
    """Engine answer -> {text, source, check, html, attempts, ...}. Never returns an unchecked number."""
    sheet = build_facts(result)
    f = sheet["facts"]
    attempts, feedback = [], ""
    if use_ai and llm.has_key():
        for _ in range(MAX_ATTEMPTS):
            try:
                text = _ai_text(audience, f, feedback, language)
            except llm.LLMError as e:
                attempts.append({"text": "", "passed": False, "reason": f"AI unavailable: {e}"})
                break
            model = llm.answered_by()
            rep = check(text, f)
            too_long = _words(text) > WORD_LIMITS[audience]
            attempts.append({"text": text, "passed": rep.passed and not too_long, "words": _words(text),
                             "unknown_numbers": rep.unknown_numbers,
                             "reason": "" if rep.passed and not too_long else
                             ("numbers not in the facts: " + ", ".join(rep.unknown_numbers)) if not rep.passed
                             else f"too long ({_words(text)} words)"})
            if rep.passed and not too_long:
                return _result(text, "ai", audience, rep, attempts, sheet, language, model)
            feedback = (f"Your previous draft used numbers that are not in FACTS: {', '.join(rep.unknown_numbers)}. "
                        f"Rewrite it using only numbers from FACTS." if not rep.passed else
                        f"Your previous draft was {_words(text)} words. Keep it under {WORD_LIMITS[audience]} words.")
    text = template(f, "council" if audience == "council" else "officer")
    return _result(text, "template", audience, check(text, f), attempts, sheet, "English")


def _result(text, source, audience, rep, attempts, sheet, language, model=None):
    return {"text": text, "source": source, "audience": audience, "language": language,
            "model": (model or llm.MODEL) if source == "ai" else None, "words": _words(text),
            "check": rep.to_dict(), "html": highlight_html(rep), "attempts": attempts,
            "rejected_drafts": sum(1 for a in attempts if not a["passed"] and a.get("text")),
            "facts": sheet["facts"], "fact_sources": sheet["sources"]}


# ---------------------------------------------------------------------------------------------
# The funder one-pager (the portfolio "bundle" story)
# ---------------------------------------------------------------------------------------------

def _load(name):
    f = PRECOMPUTED / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def funder_summary(index=None, sensitivity=None, published=None, use_ai=True):
    """One page for a portfolio funder, every number checked -> {markdown, html, check, source}."""
    index = index or _load("index.json")
    sensitivity = sensitivity if sensitivity is not None else _load("sensitivity.json")
    published = published if published is not None else _load("published_case.json")
    pf = portfolio_facts(index, sensitivity, published)
    n = pf["islands"]
    note_src = "template"
    note = (f"This portfolio puts solar panels and batteries on {n} island grids for "
            f"{_fmt(pf['total_purchase_cost_aud'], True)}. In a typical year it saves "
            f"{_fmt(pf['total_diesel_saved_litres_typical_year_p50'])} litres of diesel and "
            f"{_fmt(pf['total_co2_tonnes_avoided_per_year'])} tonnes of CO2, and "
            f"{_fmt(pf['clinics_protected'])} of {n} clinics keep their vaccine fridges powered through a "
            f"{_fmt(pf['fuel_ship_delay_days_tested'])}-day fuel-ship delay in the darkest week on record.")
    if use_ai and llm.has_key():
        cover = {k: v for k, v in pf.items() if k not in ("per_island", "sensitivity", "validation_tau")}
        for _ in range(MAX_ATTEMPTS):
            try:
                draft = _ai_text("funder_note", cover, "", "English")
            except llm.LLMError:
                break
            if check(draft, cover).passed and _words(draft) <= WORD_LIMITS["funder_note"]:
                note, note_src = draft, f"AI ({llm.answered_by() or llm.MODEL}), then checked"
                break
    rows = ["| Island | Solar | Battery | Cost to buy | Diesel saved: typical / bad year | Less diesel | Payback | "
            "Clinic, late fuel ship |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i in pf["per_island"]:
        pay = f"{_fmt(i['payback_years'])} years" if i["payback_years"] else "-"
        rows.append(f"| {i['island']} | {_fmt(i['solar_kw'])} kW | {_fmt(i['battery_kwh'])} kWh | "
                    f"{_fmt(i['purchase_cost_aud'], True)} | {_fmt(i['diesel_saved_litres_p50'])} / "
                    f"{_fmt(i['diesel_saved_litres_p90'])} L | {_fmt(i['percent_less_diesel'])}% | {pay} | "
                    f"{'stays powered' if i['clinic_survives'] else 'NOT protected'} |")
    years = pf["per_island"][0]["weather_years"] if pf["per_island"] else None
    lines = [f"# Shipless: funding summary for {n} Pacific island microgrids", "",
             note, "",
             "## What the money returns each year", "",
             f"- **{_fmt(pf['total_diesel_saved_litres_typical_year_p50'])} litres** of diesel not burned in a "
             f"typical year (P50); at least **{_fmt(pf['total_diesel_saved_litres_sum_of_island_p90s'])} litres** "
             f"in a bad year (the sum of each island's P90, beaten 9 years out of 10, which is conservative for a "
             f"portfolio).",
             f"- **{_fmt(pf['total_money_saved_per_year_aud_p50'], True)}** a year of diesel not bought (typical year).",
             f"- **{_fmt(pf['total_co2_tonnes_avoided_per_year'])} tonnes of CO2** avoided a year.",
             f"- **{_fmt(pf['clinics_protected'])} of {n} clinics** keep their vaccine fridge and emergency light "
             f"powered for {_fmt(pf['fuel_ship_delay_days_tested'])} days with no diesel at all, in the darkest "
             f"week of {_fmt(years)} years of NASA weather.", "",
             "## Island by island", ""] + rows + [""]
    lines += ["## Why you can trust these numbers", ""]
    if years:
        lines.append(f"- Every island is simulated hour by hour on {_fmt(years)} years of real NASA satellite "
                     f"weather, and every design must never black out in any of those years.")
    v = pf.get("validation_tau")
    if v:
        lines.append(f"- Checked against a real island: for Ta'u (American Samoa) our fuel model turns its "
                     f"reported diesel into {_fmt(v['our_kwh_per_year_from_reported_diesel'])} kWh a year of "
                     f"electricity against {_fmt(v['reported_kwh_per_year'])} kWh reported "
                     f"({_fmt(abs(v['difference_percent']))}% apart), and our engine runs its as-built "
                     f"{_fmt(v['as_built_pv_kw'])} kW solar and {_fmt(v['as_built_battery_kwh'])} kWh battery at "
                     f"{_fmt(v['our_solar_share_percent'])}% solar.")
    if pf.get("sensitivity"):
        lines.append(f"- Prices tested {_fmt(pf['sensitivity_price_change_percent'])}% up and down (diesel and "
                     f"solar): solar still beats diesel on every island in every case, by at least "
                     f"{_fmt(pf['lowest_lifetime_saving_any_price_aud'], True)} over {_fmt(pf['project_years'])} "
                     f"years. On {_fmt(pf['islands_robust_to_price_changes'])} of "
                     f"{_fmt(pf['islands_in_sensitivity_test'])} islands the design barely changes; where diesel is "
                     f"cheapest, a big fall in the diesel price would favour a smaller battery, so lock in the "
                     f"fuel price before ordering.")
    lines += ["", "## What these numbers do not include", "",
              "Load growth, panel ageing, diesel price changes over time, cyclone damage and generator "
              "replacement. Household counts and loads are illustrative until each island's own data is in.", ""]
    md = "\n".join(lines)
    rep = check(md, pf)
    footer = (f"\n---\n*Every number above was checked against the Shipless engine's output by "
              f"agent/check_numbers.py: {len(rep.findings)} numbers, "
              f"{'all traced' if rep.passed else str(len(rep.unsourced)) + ' NOT traced'}. "
              f"Opening paragraph written by {note_src}.*\n")
    return {"markdown": md + footer, "html": _page(md + footer), "check": rep.to_dict(),
            "source": "template" if note_src == "template" else "ai"}


def _page(md):
    """Minimal Markdown -> a printable HTML page (headings, bullets, bold, the one table)."""
    import html
    import re
    body, in_table, in_list = [], False, False
    inline = lambda s: re.sub(r"\*(.+?)\*", r"<em>\1</em>", re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>",
                                                                   html.escape(s)))
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("- "):
                continue
            if not in_table:
                body.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) +
                            "</tr></thead><tbody>")
                in_table = True
            else:
                body.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</tbody></table>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.strip() == "---":
            body.append("<hr>")
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    if in_table:
        body.append("</tbody></table>")
    if in_list:
        body.append("</ul>")
    css = ("body{font:15px/1.5 Inter,Segoe UI,system-ui,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;"
           "color:#0b0b0b;background:#fff}h1{font-size:26px;margin-bottom:8px}h2{font-size:18px;margin-top:24px}"
           "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #ddd;padding:6px 8px;"
           "text-align:left}td:not(:first-child){white-space:nowrap}th{background:#f4f4f2}"
           "hr{border:0;border-top:1px solid #ddd;margin-top:24px}"
           "em{color:#52514e}@media print{body{margin:0}}")
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width, initial-scale=1\"><title>Shipless funding summary</title>"
            f"<style>{css}</style></head><body>{''.join(body)}</body></html>")


if __name__ == "__main__":
    from agent.check_numbers import table
    args = sys.argv[1:]
    target = args[0] if args else "funafuti"
    audience = args[args.index("--audience") + 1] if "--audience" in args else "officer"
    if target == "funder":
        out = funder_summary()
        OUT.mkdir(exist_ok=True)
        (OUT / "funder_summary.md").write_text(out["markdown"], encoding="utf-8")
        (OUT / "funder_summary.html").write_text(out["html"], encoding="utf-8")
        print(out["markdown"])
        print(f"\nchecker: {'PASS' if out['check']['passed'] else 'FAIL'} ({out['check']['numbers_checked']} numbers)"
              f" -> agent/out/funder_summary.md and .html")
    else:
        result = _load(f"{target}.json")
        if result is None:
            raise SystemExit(f"no engine/precomputed/{target}.json: run python -m engine.precompute")
        out = explain(result, audience)
        for a in out["attempts"]:
            if not a["passed"]:
                print(f"REJECTED draft: {a['reason']}")
        print(f"\n{out['text']}\n\n[{out['source']}, {out['words']} words]")
        print(table(check(out["text"], out["facts"])))
