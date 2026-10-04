"""The front desk: a plain description of an island -> a SiteInput the engine can run. Owner: Mech A.

    python -m agent.intake "Funafuti, about 1200 households, the clinic fridge must never go off, diesel costs us $2.40 a litre."
    python -m agent.intake            # type a description, answer the follow-up questions

    state = start(text)                   # read the description
    while not state["done"]:
        state = reply(state, input(state["question"]))   # at most 3 short follow-up questions
    state["site_input"]                   # what POST /simulate or /api/plan takes; None if not ok

How it stays honest (the intake can't invent a number either):
  1. The AI (or, without a key, simple rules) extracts facts as JSON to a fixed schema, and for
     every fact it must copy the exact words from the user's text that state it.
  2. Code checks every quote really is in the user's words and contains the number. A fact without
     real words behind it is dropped, and the user is told.
  3. The AI never converts or calculates: code turns "600 litres a week" into litres a month and
     kVA into kW, with the factors written below.
  4. The AI never gives coordinates: the place is looked up in our own list of islands with real
     weather; if it isn't there, we ask for the nearest one (or latitude and longitude).
  5. Plausibility windows catch "a million households": the user is asked, not believed.
  6. The final SiteInput is passed through the engine's own input checker (engine.service), so
     anything this returns as ok is guaranteed to run.
"""
import re
import sys
import unicodedata
from difflib import get_close_matches

from agent import llm
from agent.check_numbers import extract_numbers

MAX_ROUNDS = 3   # brief 5.2: at most 3 follow-up questions
# ASSUMPTION: plausibility windows for a single island mini-grid. Outside them we ask the user to
# confirm rather than refuse; the engine's own hard limits (engine/service.py SITE_FIELDS) still apply.
LIMITS = {
    "households": (1, 20_000),
    "generator_kw": (1, 20_000),
    "diesel_litres_per_month": (10, 2_000_000),
    "diesel_price_per_litre": (0.3, 10.0),
    "other_kw": (0, 5_000),
}
# Calendar averages to turn a stated amount into litres per month (arithmetic, not data)
PER_MONTH = {"day": 365 / 12, "week": 52 / 12, "month": 1.0, "year": 1 / 12}
# kVA -> kW at power factor 0.8: the standard generator rating, e.g. Cummins C55 D5e = 50 kVA / 40 kW
# (the datasheet behind engine/dispatch.py's fuel curve)
KW_PER_KVA = 0.8
# Countries that use the Australian dollar, so "$" there means AUD (the engine works in AUD)
AUD_COUNTRIES = {"tuvalu", "kiribati", "nauru", "australia"}

_NULLABLE = lambda t: {"anyOf": [{"type": t}, {"type": "null"}]}
_PERIODS = {"anyOf": [{"type": "string", "enum": ["day", "week", "month", "year"]}, {"type": "null"}]}
_UNITS = {"anyOf": [{"type": "string", "enum": ["kW", "kVA"]}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "place_name": _NULLABLE("string"), "place_quote": _NULLABLE("string"),
        "country": _NULLABLE("string"),
        "households": _NULLABLE("integer"), "households_quote": _NULLABLE("string"),
        "people": _NULLABLE("integer"), "people_quote": _NULLABLE("string"),
        "has_clinic": _NULLABLE("boolean"), "clinic_quote": _NULLABLE("string"),
        "has_school": _NULLABLE("boolean"), "school_quote": _NULLABLE("string"),
        "other_load_kw": _NULLABLE("number"), "other_load_quote": _NULLABLE("string"),
        "generator_size": _NULLABLE("number"), "generator_unit": _UNITS, "generator_quote": _NULLABLE("string"),
        "diesel_amount_litres": _NULLABLE("number"), "diesel_period": _PERIODS, "diesel_quote": _NULLABLE("string"),
        "diesel_price": _NULLABLE("number"), "price_currency": _NULLABLE("string"), "price_quote": _NULLABLE("string"),
        "language": {"type": "string"},
    },
    "additionalProperties": False,
}
SCHEMA["required"] = list(SCHEMA["properties"])

SYSTEM = """You read a short description of a Pacific island community's electricity and extract the facts as JSON matching the schema.

Rules:
- Use null for anything not stated. Never guess, estimate or calculate a number.
- Copy each number exactly as the person gave it, with the unit and time period they used. Do not convert weeks to months, people to households or kVA to kW: the program does that.
- For every fact you fill in, copy the exact words from the text that state it into the matching *_quote field, character for character, in the original language.
- Never output coordinates. Give the place name exactly as written; the program looks it up.
- has_clinic is true if there is a clinic, health centre, nurse station, hospital or a medicine or vaccine fridge; false only if the text says there is none; otherwise null. has_school works the same way.
- other_load_kw is only for a stated size in kW of an extra daytime load (ice plant, freezer, water pump, workshop).
- price_currency is the currency as written ("$", "AUD", "FJ$", "tala", "pa'anga", "vatu"), or null.
- The text may be in English or a Pacific language (for example Bislama, Fijian, Samoan, Tongan, Tuvaluan or Gilbertese). language is the language it is written in.
- When the text contains follow-up questions and answers, the latest answer is the person's final word on that fact."""


# ---------------------------------------------------------------------------------------------
# Places we have real weather for (the AI never supplies coordinates)
# ---------------------------------------------------------------------------------------------

def _norm(s):
    """'Savaiʻi, Samoa' -> 'savaii samoa': lower case, no accents, okina or punctuation."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['`]", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def places():
    """name variants -> place. Demo islands (data/sites.json + Funafuti) and the cities in
    engine/weather_files.PLACES (Data Sci's NASA files), whose coordinates come from those files."""
    from data.weather import load_sites
    from engine.optimise import FUNAFUTI
    from engine.weather_files import PLACES
    out = {}
    for s in [FUNAFUTI] + load_sites():
        island = s["name"].replace("Village on ", "")
        p = {"site_id": s["id"], "name": s["name"], "country": s["country"], "lat": None, "lon": None}
        for alias in (s["id"], island, s["name"]):
            out[_norm(alias)] = p
    cities = {"suva": ("Suva", "Fiji"), "nukualofa": ("Nuku'alofa", "Tonga"), "port_vila": ("Port Vila", "Vanuatu"),
              "tarawa": ("Tarawa", "Kiribati"), "apia": ("Apia", "Samoa")}
    for key, (lat, lon) in PLACES.items():
        if key in cities and _norm(key.replace("_", " ")) not in out:
            name, country = cities[key]
            p = {"site_id": None, "name": f"Near {name}", "country": country, "lat": lat, "lon": lon}
            out[_norm(name)] = out[_norm(key.replace("_", " "))] = p
    return out


def known_place_names():
    seen, names = set(), []
    for p in places().values():
        n = p["name"].replace("Village on ", "").replace("Near ", "")
        if n not in seen:
            seen.add(n)
            names.append(n)
    return names


def resolve_place(name):
    """A place name as written -> place dict, or None if we have no weather for it."""
    if not name:
        return None
    pl, key = places(), _norm(name)
    if key in pl:
        return pl[key]
    for alias, p in sorted(pl.items(), key=lambda kv: -len(kv[0])):   # "Funafuti, Tuvalu", "on Kadavu island"
        if re.search(rf"\b{re.escape(alias)}\b", key):
            return p
    close = get_close_matches(key, list(pl), n=1, cutoff=0.85)       # small spelling slips: "Savaii"
    return pl[close[0]] if close else None


# ---------------------------------------------------------------------------------------------
# Extraction: AI to the schema, or rules without a key
# ---------------------------------------------------------------------------------------------

def _blank():
    return {k: None for k in SCHEMA["properties"]} | {"language": "unknown"}


_NUM = r"\d[\d,]*(?:\.\d+)?"


def _first_number(s):
    nums = [n for n in extract_numbers(s) if n.kind in ("number", "word", "percent")]
    return nums[0].value if nums else None


def _period(s):
    s = s.lower()
    for word, period in (("day", "day"), ("daily", "day"), ("night", "day"), ("week", "week"),
                         ("fortnight", None), ("month", "month"), ("year", "year"), ("annual", "year")):
        if word in s:
            return period
    return None


def rules_extract(text):
    """Simple patterns (no AI): English only. The fallback when there is no key."""
    t, low = text, text.lower()
    out = _blank()
    out["language"] = "en"
    # place: a name we know anywhere in the text, else "on/at/in <Capitalised Name>"
    nt = _norm(t)
    for alias in sorted(places(), key=len, reverse=True):
        if len(alias) > 2 and re.search(rf"\b{re.escape(alias)}\b", nt):
            out["place_name"] = out["place_quote"] = alias
            break
    if not out["place_name"]:
        m = re.search(r"\b(?:on|at|in|from)\s+((?:[A-Z][\w'ʻ’-]+)(?:\s+[A-Z][\w'ʻ’-]+)?)", t)
        if m and m.group(1).split()[0] not in ("The", "Our", "We", "A", "Monday", "January"):
            out["place_name"], out["place_quote"] = m.group(1), m.group(1)
    # counts: "<number> households/homes/...", number words allowed ("about twelve hundred homes")
    for field, nouns in (("households", r"households?|homes?|houses?|families|dwellings"),
                         ("people", r"people|residents|inhabitants|persons|population")):
        m = re.search(rf"((?:{_NUM}|[a-z-]+(?:\s+(?:hundred|thousand))?)\s+(?:{nouns}))\b", t, re.I)
        if m:
            v = _first_number(m.group(1))
            if v is not None:
                out[field], out[field + "_quote"] = int(v), m.group(1)
    for field, words, flag in (("has_clinic", r"clinic|health (?:centre|center|post)|nurse|hospital|dispensary|"
                                              r"vaccine|medicine fridge", "clinic_quote"),
                               ("has_school", r"school", "school_quote")):
        m = re.search(rf"\b({words})s?\b", low)
        if m:
            neg = re.search(rf"\b(?:no|without|(?:do|does|did)n'?t have an?|there is no|there's no)\s+"
                            rf"(?:\w+\s+)?(?:{words})s?\b", low)
            out[field] = not bool(neg)
            out[flag] = t[(neg or m).start():(neg or m).end()]
    # kW / kVA mentions: a generator if the words around it say so, else an extra daytime load
    for m in re.finditer(rf"({_NUM})\s*(kva|kw)\b", t, re.I):
        around = low[max(0, m.start() - 50): m.end() + 50]
        v = float(m.group(1).replace(",", ""))
        if re.search(r"generator|genset|gen-set|diesel", around) and out["generator_size"] is None:
            out["generator_size"], out["generator_unit"] = v, "kVA" if m.group(2).lower() == "kva" else "kW"
            out["generator_quote"] = m.group(0)
        elif re.search(r"ice|freez|pump|workshop|cold|mill|fridge", around) and out["other_load_kw"] is None \
                and m.group(2).lower() == "kw":
            out["other_load_kw"], out["other_load_quote"] = v, m.group(0)
    m = re.search(rf"({_NUM})\s*(?:l|litres?|liters?|ltrs?)\b([^.;]{{0,40}})", t, re.I)
    if m:
        out["diesel_amount_litres"] = float(m.group(1).replace(",", ""))
        out["diesel_period"] = _period(m.group(2))
        out["diesel_quote"] = m.group(0).strip()
    for m in _PRICE.finditer(t):
        per = re.match(r"\s*(?:/|per|a|an|each)\s*(?:l|litres?|liters?)\b", t[m.end():m.end() + 20], re.I)
        if per:
            out["diesel_price"] = float((m.group("v1") or m.group("v2")).replace(",", ""))
            out["price_currency"] = m.group("c1") or m.group("c2")
            out["price_quote"] = t[m.start():m.end() + per.end()]
            break
    return out


_PRICE = re.compile(rf"(?P<c1>A\$|AU\$|AUD|US\$|USD|FJ\$|FJD|NZ\$|NZD|T\$|WS\$|\$)\s*(?P<v1>{_NUM})"
                    rf"|(?P<v2>{_NUM})\s*(?P<c2>AUD|USD|FJD|NZD|tala|pa'anga|vatu|dollars?)", re.I)


def _coerce(raw):
    """Model output -> the schema's types. Some models send "1200" or "true" as strings, and
    bool("false") is True in Python, so every value is converted explicitly; anything that doesn't
    convert becomes None (unknown) rather than a guess."""
    out = _blank()
    for k, spec in SCHEMA["properties"].items():
        v = raw.get(k) if isinstance(raw, dict) else None
        kinds = [s.get("type") for s in spec.get("anyOf", [spec])]
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "unknown", "n/a"):
            v = None
        if v is None:
            out[k] = None if "null" in kinds else out.get(k)
        elif "boolean" in kinds:
            out[k] = v if isinstance(v, bool) else {"true": True, "yes": True, "false": False,
                                                     "no": False}.get(str(v).strip().lower())
        elif "integer" in kinds or "number" in kinds:
            try:
                n = float(str(v).replace(",", "").strip())
                out[k] = int(round(n)) if "integer" in kinds else n
            except ValueError:
                out[k] = None
        else:
            out[k] = str(v)
    return out


def ai_extract(conversation):
    return _coerce(llm.ask(SYSTEM, conversation, schema=SCHEMA, effort="low", max_tokens=8000))


# ---------------------------------------------------------------------------------------------
# Checking what was extracted
# ---------------------------------------------------------------------------------------------

def _plain_text(s):
    s = unicodedata.normalize("NFKC", s or "")
    for a, b in (("’", "'"), ("‘", "'"), ("ʻ", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return " ".join(s.lower().split())


_EVIDENCE = [  # (value field, quote field, is the value a number that must appear in the quote?)
    ("households", "households_quote", True), ("people", "people_quote", True),
    ("has_clinic", "clinic_quote", False), ("has_school", "school_quote", False),
    ("other_load_kw", "other_load_quote", True), ("generator_size", "generator_quote", True),
    ("diesel_amount_litres", "diesel_quote", True), ("diesel_price", "price_quote", True),
    ("place_name", "place_quote", False),
]


def verify_evidence(raw, user_text):
    """Drop every fact whose quote is not really in the user's words. -> (raw, warnings)"""
    said, warnings = _plain_text(user_text), []
    for field, qfield, numeric in _EVIDENCE:
        v, q = raw.get(field), raw.get(qfield)
        if v is None or (field.startswith("has_") and v is False and not q):
            continue
        if field == "place_name":                       # names compared without accents / okina
            ok = any(x and _norm(x) in _norm(user_text) for x in (q, v))
        else:
            ok = bool(q) and _plain_text(q) in said
        if ok and numeric:
            ok = any(abs(n.value - float(v)) < 1e-6 for n in extract_numbers(q))
        if not ok:
            warnings.append(f"Ignored {field.replace('_', ' ')} = {v}: those words aren't in what you wrote.")
            raw[field] = None
            if field == "generator_size":
                raw["generator_unit"] = None
            if field == "diesel_amount_litres":
                raw["diesel_period"] = None
    return raw, warnings


# ---------------------------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------------------------

def _user_text(state):
    return "\n".join([state["text"]] + [t["answer"] for t in state["turns"]])


def _conversation(state):
    lines = [f"Description: {state['text']}"]
    for t in state["turns"]:
        lines += [f"Follow-up question: {t['question']}", f"Answer: {t['answer']}"]
    return "\n".join(lines)


_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|right|aud|australian|a\$|sure|ok|okay)\b", re.I)
_NO = re.compile(r"^\s*(?:no|nope|not|nah)\b", re.I)
_COORDS = re.compile(r"(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)")


def _apply_answer(state, field, answer, raw):
    """The user's direct answer to our question wins for that field (parsed by code, not the AI)."""
    n = _first_number(answer)
    if field in ("households", "households_check") and n is not None:
        raw["households"], raw["households_quote"] = int(n), answer
    elif field == "diesel_period":
        raw["diesel_period"] = _period(answer) or raw.get("diesel_period")
    elif field in ("generator_kw", "generator_check") and n is not None:
        raw["generator_size"], raw["generator_unit"], raw["generator_quote"] = n, "kW", answer
    elif field == "price_check" and n is not None:
        raw["diesel_price"], raw["price_quote"] = n, answer
    elif field == "currency":
        if _YES.search(answer):
            state["currency_confirmed"] = True
        elif _NO.search(answer):
            state["currency_confirmed"] = False
    elif field == "place":
        m = _COORDS.search(answer)
        p = resolve_place(answer)
        if p:
            raw["place_name"], raw["place_quote"] = p["name"].replace("Village on ", ""), answer
        elif m and -90 <= float(m.group(1)) <= 90 and -180 <= float(m.group(2)) <= 180:
            state["coords"] = (float(m.group(1)), float(m.group(2)))
            raw["place_name"] = raw.get("place_name") or "Custom site"
        elif answer.strip():
            raw["place_name"], raw["place_quote"] = answer.strip().rstrip("."), answer


def _problems(state, raw, place):
    """What still stops a plan -> ordered list of (field, question, required?)."""
    out = []
    names = ", ".join(known_place_names())
    if not place and not state.get("coords"):
        if raw.get("place_name"):
            out.append(("place", f"I don't have weather records for {raw['place_name']} yet. Which of these is "
                                 f"nearest: {names}? (Or send its latitude and longitude.)", True))
        else:
            out.append(("place", f"Which island is this? I have real weather for {names}.", True))
    hh, litres = raw.get("households"), raw.get("diesel_amount_litres")
    # A demo island already has a household count; a new place needs one to shape its daily demand
    # (data/loads.py builds the load from households, then scales it to the diesel bought).
    new_place = not (place and place["site_id"])
    if hh is None and (litres is None or new_place):
        if raw.get("people"):
            out.append(("households", f"You mentioned about {raw['people']:,} people. Roughly how many households "
                                      f"(homes) is that?", True))
        else:
            out.append(("households", "Roughly how many households (homes) are on the power grid?", True))
    elif hh is not None and not LIMITS["households"][0] <= hh <= LIMITS["households"][1]:
        if state.get("households_seen") == hh:      # asked once and they insist: let the engine decide
            pass
        else:
            out.append(("households_check", f"{hh:,} households is far bigger than an island power grid usually "
                                            f"is. Roughly how many homes are connected?", True))
    if litres is not None and not raw.get("diesel_period"):
        out.append(("diesel_period", f"Is that {litres:,.0f} litres of diesel a week, a month or a year?", False))
    gen = raw.get("generator_size")
    if gen is not None and not LIMITS["generator_kw"][0] <= gen <= LIMITS["generator_kw"][1]:
        out.append(("generator_check", f"Is the generator really {gen:,.0f} {raw.get('generator_unit') or 'kW'}? "
                                       f"What size is it in kW?", False))
    price = raw.get("diesel_price")
    if price is not None:
        if not LIMITS["diesel_price_per_litre"][0] <= price <= LIMITS["diesel_price_per_litre"][1]:
            out.append(("price_check", f"Is diesel really {price} a litre? What do you pay per litre?", False))
        elif _currency(state, raw, place) is None:
            out.append(("currency", f"Is the {raw.get('price_currency') or ''}{price:.2f} per litre in Australian "
                                    f"dollars? (The plan is worked out in AUD.)", False))
    return out


def _currency(state, raw, place):
    """'AUD' if the price can be used as AUD, False if it can't, None if we need to ask."""
    cur = (raw.get("price_currency") or "").strip().upper()
    if state.get("currency_confirmed") is not None:
        return "AUD" if state["currency_confirmed"] else False
    if cur in ("AUD", "A$", "AU$"):
        return "AUD"
    country = _norm((place or {}).get("country") or raw.get("country") or "")
    if cur in ("$", "", "DOLLAR", "DOLLARS") and country in AUD_COUNTRIES:
        return "AUD"
    return None


def _site_input(state, raw, place, warnings):
    si = {}
    if place and place["site_id"]:
        si["site_id"] = place["site_id"]
    else:
        lat, lon = state.get("coords") or ((place or {}).get("lat"), (place or {}).get("lon"))
        si.update(name=(place or {}).get("name") or raw.get("place_name") or "Custom site",
                  country=(place or {}).get("country") or raw.get("country") or "", lat=lat, lon=lon)
    if raw.get("households") is not None:
        si["households"] = int(raw["households"])
    for field in ("has_clinic", "has_school"):
        if raw.get(field) is not None:
            si[field] = bool(raw[field])
        elif "site_id" not in si:
            si[field] = False
            if field == "has_clinic":
                warnings.append("No clinic mentioned: tick the clinic box if there is one.")
    if raw.get("other_load_kw") is not None:
        si["other_kw"] = float(raw["other_load_kw"])
    if raw.get("generator_size") is not None:
        kw = raw["generator_size"] * (KW_PER_KVA if raw.get("generator_unit") == "kVA" else 1.0)
        si["generator_kw"] = round(kw, 1)
        if raw.get("generator_unit") == "kVA":
            warnings.append(f"Generator {raw['generator_size']:g} kVA counted as {kw:g} kW (power factor 0.8).")
    if raw.get("diesel_amount_litres") is not None and raw.get("diesel_period"):
        si["diesel_litres_per_month"] = round(raw["diesel_amount_litres"] * PER_MONTH[raw["diesel_period"]], 1)
    elif raw.get("diesel_amount_litres") is not None:
        warnings.append("Diesel amount not used: we don't know if it was per week, month or year.")
    if raw.get("diesel_price") is not None:
        if _currency(state, raw, place) == "AUD":
            si["diesel_price_per_litre"] = float(raw["diesel_price"])
        else:
            warnings.append("Diesel price not used: it isn't in Australian dollars and the engine has no exchange "
                            "rate, so the regional average from data/costs.md is used.")
    return si


def _engine_check(si):
    """The engine's own input checker: if this passes, the plan will run. -> error message or None."""
    from engine.service import BadInput, build_inputs
    try:
        build_inputs(si)
        return None
    except BadInput as e:
        return str(e)


def _step(state):
    text = _user_text(state)
    raw, source = None, "rules"
    if llm.has_key():
        try:
            raw, source = ai_extract(_conversation(state)), "ai"
        except llm.LLMError as e:
            state["notes"].append(f"AI unavailable ({e}); used simple rules.")
    if raw is None:
        raw = rules_extract(state["text"])
        for t in state["turns"]:                       # answers that weren't tied to a question
            extra = rules_extract(t["answer"])
            for k, v in extra.items():
                if v is not None and raw.get(k) is None and k != "language":
                    raw[k] = v
    raw, dropped = verify_evidence(raw, text)
    for t in state["turns"]:
        _apply_answer(state, t["field"], t["answer"], raw)
    place = resolve_place(raw.get("place_name"))
    state["source"], state["extracted"] = source, raw
    state["warnings"] = dropped
    problems = _problems(state, raw, place)
    if raw.get("households") is not None:
        state["households_seen"] = raw["households"]
    final = state["rounds"] >= MAX_ROUNDS
    required = [p for p in problems if p[2]]
    if problems and not final:
        field, question, _ = problems[0]
        state.update(question=question, asking=field, done=False, ok=False, site_input=None)
        return state
    warnings = list(dropped)
    si = _site_input(state, raw, place, warnings)
    error = None if required else _engine_check(si)
    state["warnings"] = warnings
    state["notes"] = list(dict.fromkeys(state["notes"]))
    if required or error:
        state.update(question=None, asking=None, done=True, ok=False, site_input=None,
                     missing=[p[0] for p in required] or ["engine"], error=error)
    else:
        state.update(question=None, asking=None, done=True, ok=True, site_input=si, missing=[])
    return state


def form_fields(state):
    """What has been understood so far, as the plan form's fields, even if a question is still open
    (the web form is filled now; the person checks it before running)."""
    raw = state.get("extracted") or _blank()
    place = resolve_place(raw.get("place_name"))
    si = state.get("site_input") or _site_input(state, raw, place, [])
    price = si.get("diesel_price_per_litre")
    if price is None and raw.get("diesel_price") is not None and _currency(state, raw, place) is not False:
        price = raw["diesel_price"]      # shown for the person to confirm; currency question still open
    # has_clinic / has_school: True, False, or None when the person didn't say (keep the island's own setting)
    return {"site_id": si.get("site_id"), "name": si.get("name"), "households": si.get("households"),
            "has_clinic": si.get("has_clinic"), "has_school": si.get("has_school"),
            "other_kw": si.get("other_kw"), "generator_kw": si.get("generator_kw"),
            "diesel_litres_per_month": si.get("diesel_litres_per_month"), "diesel_price_per_litre": price}


def start(text):
    """Read a description -> state. If state['done'] is False, show state['question'] and call reply()."""
    state = {"text": text, "turns": [], "rounds": 0, "notes": [], "warnings": [], "question": None,
             "asking": None, "done": False, "ok": False, "site_input": None, "missing": []}
    return _step(state)


def reply(state, answer):
    """The user's answer to state['question'] -> the next state."""
    if state.get("done"):
        return state
    state["turns"].append({"question": state["question"], "field": state["asking"], "answer": str(answer)})
    state["rounds"] += 1
    return _step(state)


def run(text, answers=()):
    """Non-interactive: description + scripted answers (tests and the accuracy eval)."""
    state, answers = start(text), list(answers)
    while not state["done"]:
        state = reply(state, answers.pop(0) if answers else "")
    return state


if __name__ == "__main__":
    import json
    text = " ".join(sys.argv[1:]) or input("Describe the island: ")
    s = start(text)
    while not s["done"]:
        s = reply(s, input(f"\n{s['question']}\n> "))
    print(f"\nread with: {s['source']}   ok: {s['ok']}")
    for w in s["warnings"] + s["notes"]:
        print("  note:", w)
    print(json.dumps(s["site_input"], indent=2) if s["ok"] else f"  could not finish: missing {s['missing']} "
          f"{s.get('error') or ''}")
