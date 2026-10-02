"""The AI "front desk". Owner: Mech A.

The AI never invents numbers: it only (1) turns a plain description into form fields
that a person confirms, and (2) explains the engine's results in plain language.
Every number in an explanation is checked against the engine's output.
Without an API key, simple built-in rules and templates are used instead, so the
demo always works.

Set the key before starting the server (keep it out of git!):
  Mac/Linux:  export ANTHROPIC_API_KEY=sk-ant-...
  Windows:    set ANTHROPIC_API_KEY=sk-ant-...
"""
import json
import os
import re

import requests

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
API_URL = "https://api.anthropic.com/v1/messages"
FIELDS = {
    "name": "short name for the place, e.g. 'Village on Kadavu'",
    "households": "number of homes (integer)",
    "has_clinic": "true if there is a clinic / health post / nurse station",
    "has_school": "true if there is a school",
    "other_kw": "extra daytime load in kW (ice plant, freezer, workshop, water pump); estimate if described, else 0",
    "generator_kw": "diesel generator size in kW, or null if not given",
    "diesel_litres_per_month": "litres of diesel bought per month, or null if not given",
    "diesel_price_per_litre": "price per litre in USD, or null if not given",
}


def has_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def call_claude(system, user, max_tokens=900):
    r = requests.post(API_URL, timeout=45, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, json={"model": MODEL, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


# ------------------------------------------------------------------ 1. description -> fields
def parse_description(text):
    if has_key():
        try:
            system = ("You convert a community's plain description of their island into JSON. "
                      "Reply with ONLY a JSON object, no other text, with exactly these keys: "
                      + json.dumps(FIELDS) + ". Use null when something is not stated. Never guess "
                      "a generator size, diesel amount or price that was not given.")
            raw = call_claude(system, text, 400)
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            return {"site": _clean(data), "source": "ai"}
        except Exception as e:
            fallback = _rules_parse(text)
            fallback["note"] = f"AI unavailable ({type(e).__name__}); used simple rules"
            return fallback
    return _rules_parse(text)


def _num(pattern, text):
    m = re.search(pattern, text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _rules_parse(text):
    t = text.lower()
    site = {
        "households": _num(r"(\d[\d,]*)\s*(?:houses|homes|households|families|dwellings)", t),
        "has_clinic": any(w in t for w in ("clinic", "health", "nurse", "hospital", "vaccine")),
        "has_school": "school" in t,
        "generator_kw": _num(r"(\d[\d,.]*)\s*kw\s*(?:diesel\s*)?(?:generator|genset)", t),
        "diesel_litres_per_month": _num(r"(\d[\d,]*)\s*(?:l|litres|liters)\b[^.]*?(?:month|monthly)", t),
        "diesel_price_per_litre": _num(r"\$\s*(\d+(?:\.\d+)?)\s*(?:/|per|a)\s*(?:l|litre|liter)", t),
        "other_kw": 5.0 if any(w in t for w in ("ice plant", "freezer", "cold store", "workshop")) else 0.0,
    }
    return {"site": _clean(site), "source": "rules"}


def _clean(d):
    out = {}
    for k in FIELDS:
        v = d.get(k)
        if k in ("has_clinic", "has_school"):
            out[k] = bool(v)
        elif k == "name":
            out[k] = v or None
        else:
            try:
                out[k] = None if v in (None, "") else float(v)
            except (TypeError, ValueError):
                out[k] = None
    if out.get("households") is not None:
        out["households"] = int(out["households"])
    return out


# ------------------------------------------------------------------ 2. results -> plain language
SAFE_NUMBERS = {1, 2, 3, 7, 9, 10, 20, 24, 35, 50, 90, 100, 168, 2035}


def _allowed_numbers(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("week_trace", "sweep"):
                _allowed_numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _allowed_numbers(v, acc)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        acc.add(float(obj))
    return acc


def check_numbers(text, result):
    allowed = _allowed_numbers(result) | {float(x) for x in SAFE_NUMBERS}
    unknown = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        x = float(m.replace(",", ""))
        if not any(abs(x - a) <= max(0.51, 0.01 * abs(a)) for a in allowed):
            unknown.append(m)
    return {"passed": not unknown, "unknown_numbers": unknown}


def template_explanation(r):
    d, t, n = r["design"], r["today"], r["no_fuel_ship"]
    name = r["site"].get("name") or "This island"
    pay = f" It pays for itself in about {d['payback_years']} years." if d.get("payback_years") else ""
    return (
        f"{name} burns about {t['diesel_litres_per_year']:,} litres of diesel a year today, "
        f"costing about ${t['diesel_cost_per_year']:,}.\n\n"
        f"The best plan we found is {d['solar_kw']} kW of solar panels and a {d['battery_kwh']} kWh battery, "
        f"costing about ${d['purchase_cost']:,} to buy and install.{pay}\n\n"
        f"In a typical year it saves {d['litres_saved_p50']:,} litres of diesel "
        f"({d['percent_diesel_cut_p50']}% less) and about ${d['money_saved_per_year_p50']:,}. "
        f"Even in a bad year (9 years out of 10 are better) it still saves at least "
        f"{d['litres_saved_p90']:,} litres. The sun would supply {d['solar_share_percent']}% of the power.\n\n"
        f"In the cloudiest week of the last {r['years_of_weather']} years, with no diesel at all, "
        f"the clinic's vaccine fridge and emergency light stay on for {n['days_clinic_powered']} of 7 days."
    )


def explain(result):
    if has_key():
        system = ("You explain an island energy plan to a village council in plain, warm English "
                  "(short sentences, no jargon, 4 short paragraphs). Use ONLY numbers that appear in "
                  "the JSON. Do not round them differently, do not calculate new numbers, do not add "
                  "facts. Mention: today's diesel use and cost; the solar and battery plan and its cost; "
                  "typical and bad-year (P90, '9 years out of 10') savings; the cloudiest-week clinic test.")
        slim = {k: v for k, v in result.items() if k not in ("week_trace", "sweep", "assumptions")}
        for _ in range(2):
            try:
                text = call_claude(system, json.dumps(slim), 700).strip()
            except Exception:
                break
            chk = check_numbers(text, result)
            if chk["passed"]:
                return {"text": text, "source": "ai", "check": chk}
    text = template_explanation(result)
    return {"text": text, "source": "template", "check": check_numbers(text, result)}


# ------------------------------------------------------------------ 3. alert -> text message
def alert_sms(alert):
    f = alert.get("facts", {})
    k = alert.get("type")
    if k == "sensor_offline":
        return f"Shipless: no signal from the generator sensor for {f.get('seconds_since_last_reading')} s. Please check its power and Wi-Fi."
    if k == "spike":
        return f"Shipless: demand jumped to {f.get('watts_now')} W (usually {f.get('typical_watts')} W). Check for a fault or a big new load."
    if k == "low_load":
        return (f"Shipless: generator has run at only {f.get('percent_of_size')}% of its size for {f.get('minutes')} min. "
                f"This wastes fuel - the battery should cover small loads like this.")
    if k == "diesel_in_sunshine":
        return f"Shipless: generator running at {f.get('local_hour')}:00 in full sun. Check the solar panels and battery settings."
    if k == "no_data":
        return "Shipless: waiting for the first reading from the sensor."
    return f"Shipless alert: {alert.get('title')}"
