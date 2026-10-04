"""Front-desk tests: run offline with a fake model, so they're free and repeatable. Owner: Mech A.

    python -m agent.test_agent        (also works with pytest)

The first line of each test's docstring is the sentence to say to a judge.
"""
import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from agent import agent, explain as ex, intake, llm, sms
from agent.check_numbers import check, check_list, extract_numbers, highlight_html
from agent.evaluate import _corrupt
from agent.facts import facts as build_facts

ROOT = Path(__file__).resolve().parent.parent
ISLANDS = ["funafuti", "nanumea", "kadavu", "eua", "malekula", "abaiang", "savaii"]
PLAN = {i: json.loads((ROOT / "engine" / "precomputed" / f"{i}.json").read_text(encoding="utf-8")) for i in ISLANDS}
F = build_facts(PLAN["funafuti"])["facts"]


@contextlib.contextmanager
def fake_model(fn):
    """Swap the real model for fn(system, user, schema, effort) -> text."""
    old = os.environ.pop("SHIPLESS_AI", None)
    llm.set_backend(fn)
    try:
        yield
    finally:
        llm.set_backend(None)
        if old is not None:
            os.environ["SHIPLESS_AI"] = old


@contextlib.contextmanager
def no_ai():
    """Force rules + templates even if the person running the tests has a key set."""
    old = os.environ.get("SHIPLESS_AI")
    os.environ["SHIPLESS_AI"] = "off"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SHIPLESS_AI", None)
        else:
            os.environ["SHIPLESS_AI"] = old


# ------------------------------------------------------------------------------------ the checker

def test_templates_pass_the_checker_on_every_island():
    """Even our fallback text is held to the rule: every number in it is traced to the engine, on every island."""
    for island, plan in PLAN.items():
        f = build_facts(plan)["facts"]
        for audience in ("officer", "council"):
            rep = check(ex.template(f, audience), f)
            assert rep.passed, (island, audience, rep.unknown_numbers)
            assert len(rep.findings) >= 8


def test_checker_catches_invented_numbers_however_written():
    """An invented number is caught whether it's digits, words, '1.2 million', 'halves', a year or a percentage."""
    f = {"pv_kw": 62.0, "years": 20, "litres_p90": 30126}
    for bad in ("It saves 31,500 litres.", "It saves thirty-one thousand litres.", "That is 1.2 million dollars.",
                "Solar halves the cost.", "Diesel doubles by then.", "By 2030 it pays off.",
                "It uses 20% less diesel.", "About 300,000 litres."):
        rep = check(bad, f)
        assert not rep.passed, bad


def test_honest_rounding_passes_dishonest_rounding_fails():
    """Rounding 30,126 to 30,000 is honest; rounding 314,870 to 300,000 changes the claim and fails."""
    src = {"p90": 30126, "capex": 314870, "life": 566052, "cut_percent": 91.6, "first_year": 2005, "derate": 0.8}
    assert check("at least 30,000 litres", src).passed
    assert check("AUD 566k over the project", src).passed
    assert check("0.57 million", src).passed
    assert check("about 92% less diesel", src).passed
    assert check("an 80% performance ratio", src).passed
    assert not check("AUD 300,000 to buy", src).passed           # 1 significant figure
    assert not check("2,000 islands", src).passed                 # a year can't be 'rounded'


def test_names_and_the_p90_definition_are_not_quantities():
    """P90, CO2 and COP31 are names, and '9 years out of 10' is the P90 definition: none count as claims."""
    text = "The P50 and P90 savings cut CO2 ahead of COP31: beaten in 9 years out of 10, nine years in ten."
    assert extract_numbers(text) == []
    assert check(text, {}).passed


def test_money_symbols_scales_and_times_are_read_correctly():
    """'$314,870', 'AUD 2.9M', 'A$1.95/L' and '12:00' are each read as the number a person sees."""
    vals = {n.text: n.value for n in extract_numbers("$314,870 then AUD 2.9M at A$1.95/L, 12:00, 566k")}
    assert vals["314,870"] == 314870 and vals["2.9M"] == 2.9e6 and vals["1.95"] == 1.95
    assert vals["12:00"] == 12 and vals["566k"] == 566000


def test_brief_interface_returns_the_bad_numbers():
    """The brief's check(ai_text, simresult_json): an empty list means PASS, otherwise the bad numbers."""
    sr = (ROOT / "contracts" / "mock_simresult.json").read_text()
    assert check_list("We recommend 850 kW and 2400 kWh, saving 61,000 litres.", sr) == []
    assert check_list("We recommend 900 kW.", sr) == [900.0]


def test_tampered_engine_result_fails_loudly_on_every_island():
    """If anything corrupts the engine's numbers on the way to the AI, the checker rejects the text, every time."""
    for island, plan in PLAN.items():
        f = build_facts(plan)["facts"]
        rep = check(ex.template(f), _corrupt(f))
        assert not rep.passed and len(rep.unknown_numbers) >= 4, island


def test_report_shows_where_every_number_came_from():
    """Every traced number names its engine field, so a judge can follow it back (and red ones are marked)."""
    rep = check("62 kW and 999 kWh", {"design": {"solar_kw": 62.0}})
    d = rep.to_dict()
    assert d["numbers"][0]["source"] == "design.solar_kw" and d["numbers"][1]["status"] == "unsourced"
    h = highlight_html(rep)
    assert 'class="num ok"' in h and 'class="num bad"' in h and "design.solar_kw" in h


# ------------------------------------------------------------------------------------ the explainer

def test_ai_draft_with_an_invented_number_is_rejected_then_fixed():
    """A draft with an invented number is rejected, the model is told which number, and the fixed draft is shown."""
    good = ex.template(F)
    calls = []

    def model(system, user, schema, effort):
        calls.append(user)
        return "It saves 45,000 litres a year." if len(calls) == 1 else good

    with fake_model(model):
        out = ex.explain(PLAN["funafuti"], "officer")
    assert out["source"] == "ai" and out["check"]["passed"] and out["rejected_drafts"] == 1
    assert "45,000" in calls[1]                       # the feedback names the bad number


def test_ai_that_keeps_inventing_falls_back_to_the_template():
    """If the AI keeps inventing numbers, nothing it wrote is shown: the engine's template is."""
    with fake_model(lambda *a: "Solar halves diesel use by 2030."):
        out = ex.explain(PLAN["kadavu"], "officer")
    assert out["source"] == "template" and out["check"]["passed"]
    assert out["rejected_drafts"] == ex.MAX_ATTEMPTS


def test_ai_unavailable_falls_back_to_the_template():
    """No key, no network or a refusal: the demo still shows a checked explanation."""
    def down(*a):
        raise llm.LLMError("no connection")
    with fake_model(down):
        out = ex.explain(PLAN["eua"], "council")
    assert out["source"] == "template" and out["check"]["passed"]


def test_the_ai_only_ever_sees_the_engine_fact_sheet():
    """The AI receives only the fact sheet built from the engine's output: no hourly data, no other facts."""
    seen = {}

    def model(system, user, schema, effort):
        seen["facts"] = json.loads(user.split("FACTS:\n", 1)[1])
        return ex.template(F)

    with fake_model(model):
        ex.explain(PLAN["funafuti"], "officer")
    assert seen["facts"] == json.loads(json.dumps(F))
    assert "week_trace" not in json.dumps(seen["facts"]) and "sweep" not in json.dumps(seen["facts"])


def test_officer_summary_is_about_120_words():
    """The fund-officer summary stays near the brief's 120 words on every island."""
    with no_ai():
        for plan in PLAN.values():
            assert ex.explain(plan, "officer")["words"] <= ex.WORD_LIMITS["officer"]


def test_funder_summary_passes_its_own_check():
    """The one-page funder summary checks itself: every number in it is traced to the engine."""
    with no_ai():
        out = ex.funder_summary()
    assert out["check"]["passed"] and out["check"]["numbers_checked"] > 50
    assert "<table>" in out["html"] and "all traced" in out["markdown"]


# ------------------------------------------------------------------------------------ the intake

def test_brief_example_is_understood_without_questions():
    """'Funafuti, about 1200 households, the clinic fridge must never go off, $2.40 a litre' needs no follow-up."""
    with no_ai():
        s = intake.run("Funafuti, about 1200 households, the clinic fridge must never go off, "
                       "diesel costs us $2.40 a litre.")
    assert s["ok"] and not s["turns"]
    assert s["site_input"] == {"site_id": "funafuti", "households": 1200, "has_clinic": True,
                               "diesel_price_per_litre": 2.4}


def test_intake_drops_facts_the_person_never_said():
    """If the AI claims a fact the person never wrote, code drops it and says so: the intake can't invent either."""
    def model(system, user, schema, effort):
        out = {k: None for k in intake.SCHEMA["properties"]}
        out.update(language="en", place_name="Kadavu", place_quote="Kadavu",
                   households=1500, households_quote="1500 households",        # not in the text
                   generator_size=90, generator_quote="a 45 kW generator", generator_unit="kW")  # wrong number
        return json.dumps(out)

    with fake_model(model):
        s = intake.start("Our village on Kadavu has a 45 kW generator and lots of homes.")
    assert s["extracted"]["households"] is None and s["extracted"]["generator_size"] is None
    assert len(s["warnings"]) == 2 and s["asking"] == "households"


def test_intake_asks_at_most_three_questions():
    """A description it can't understand gets at most 3 short questions, then hands over to the form."""
    with no_ai():
        s = intake.run("asdf qwerty", ["", "", "", "", ""])
    assert s["done"] and not s["ok"] and len(s["turns"]) == intake.MAX_ROUNDS


def test_coordinates_never_come_from_the_ai():
    """The AI can't supply coordinates: the schema has no field for them; places come from our own list."""
    assert not {"lat", "lon", "latitude", "longitude"} & set(intake.SCHEMA["properties"])
    with no_ai():
        s = intake.run("We live on Vatoa with 85 homes.", ["Suva"])
    assert s["ok"] and s["site_input"]["lat"] == -18.14 and s["site_input"]["lon"] == 178.44


def test_conversions_are_done_by_code_not_the_ai():
    """'1,200 litres a week' and '70 kVA' are converted by code with written-down factors."""
    with no_ai():
        a = intake.run("Abaiang: 100 households, we burn 1,200 litres of diesel a week.")
        b = intake.run("Savai'i, 150 homes and a 70 kVA generator.")
    assert a["site_input"]["diesel_litres_per_month"] == 5200.0
    assert b["site_input"]["generator_kw"] == 56.0


def test_dollar_prices_are_only_used_when_they_are_aud():
    """'$2.40' on Funafuti is AUD (Tuvalu uses it); 'FJ$2.50' is asked about and not used if it isn't AUD."""
    with no_ai():
        tuv = intake.run("Funafuti, 1200 households, diesel $2.40 a litre.")
        fij = intake.run("Kadavu village, 120 homes, diesel is FJ$2.50 a litre.", ["no"])
    assert tuv["site_input"]["diesel_price_per_litre"] == 2.4 and not tuv["turns"]
    assert "Australian dollars" in fij["turns"][0]["question"]
    assert "diesel_price_per_litre" not in fij["site_input"]


def test_a_million_households_is_questioned_not_believed():
    """An absurd number ('a million households') gets a follow-up question instead of a plan."""
    with no_ai():
        s = intake.run("We are a village of a million households on Kadavu.", ["about 300"])
    assert s["turns"][0]["field"] == "households_check" and s["site_input"]["households"] == 300


def test_every_accepted_site_input_runs_in_the_engine():
    """Anything the intake accepts has already passed the engine's own input checker, so the plan will run."""
    from engine.service import build_inputs
    cases = json.loads((Path(__file__).parent / "eval" / "intake_cases.json").read_text(encoding="utf-8"))["cases"]
    with no_ai():
        for c in cases:
            s = intake.run(c["text"], c["answers"])
            if s["ok"]:
                build_inputs(s["site_input"])      # raises if the engine would refuse it


# ------------------------------------------------------------------------------------ SMS

def test_every_alert_fits_one_text_message():
    """Every alert becomes ONE text message: 160 GSM-7 characters, site named, numbers from the alert only."""
    for a in sms.EXAMPLES:
        for site in ("Funafuti", "Village on Savaiʻi", "A very long village name on a far outer island"):
            text = sms.alert_sms(a, site)
            m = sms.measure(text)
            assert m["encoding"] == "GSM-7" and m["segments"] == 1, (text, m)
            assert check(text, a["facts"]).passed, text


def test_ai_sms_that_invents_a_number_is_not_sent():
    """An AI SMS that adds 'battery #2' (a number not in the alert) is rejected and the template is sent."""
    a = sms.EXAMPLES[1]
    with fake_model(lambda *x: "Shipless (Funafuti): generator on at 12:00 in full sun. Check inverter + battery #2."):
        out = sms.compose(a, "Funafuti")
    assert out["source"] == "template" and any("2" in p for p in out["ai_problems"])
    sms._ai_cache.clear()
    with fake_model(lambda *x: "Shipless (Funafuti): generator running at 12:00 in full sun. Check the inverter."):
        out = sms.compose(a, "Funafuti")
    assert out["source"] == "ai" and out["segments"] == 1
    sms._ai_cache.clear()


def test_okina_does_not_split_the_message():
    """'Savaiʻi' with an okina would force a 70-character UCS-2 text; it is cleaned to GSM-7 first."""
    assert sms.measure("Savaiʻi")["encoding"] == "UCS-2"
    assert sms.measure(sms.gsm_clean("Savaiʻi"))["encoding"] == "GSM-7"


def test_sms_without_twilio_is_demo_mode_and_never_logs_the_number():
    """Without Twilio settings nothing is sent, the app says it's a demo, and the phone number is masked."""
    saved = {k: os.environ.pop(k, None) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM",
                                                   "ACCOUNT_SID", "AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
                                                   "MY_PHONE_NUMBER", "SMS_TO", "DEMO_PHONE_SECRET")}
    try:
        r = sms.send_sms("+6881234567", "test")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    assert r["sent"] is False and r["mode"] == "demo" and "1234" not in r["to"]


# ------------------------------------------------------------------------------------ keys, abuse, providers

_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "LLM_KEY",
             "SHIPLESS_PROVIDER", "SHIPLESS_MODEL", "SHIPLESS_AI", "SHIPLESS_TEST_OTHER")


@contextlib.contextmanager
def env(**values):
    """Run with exactly these key-related variables set (others removed), then restore everything."""
    saved = {k: os.environ.pop(k, None) for k in _KEY_VARS}
    os.environ.update({k: v for k, v in values.items() if v is not None})
    try:
        yield
    finally:
        for k in _KEY_VARS:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]


def test_key_file_is_read_but_never_overrides_the_terminal():
    """The key can live in the git-ignored .env file; a variable set in the terminal always wins."""
    with env(SHIPLESS_TEST_OTHER="from-terminal"), tempfile.TemporaryDirectory() as d:
        f = Path(d) / ".env"
        f.write_text("# comment\nGEMINI_API_KEY=\"test-key-123\"\nexport SHIPLESS_TEST_OTHER=from-file\n",
                     encoding="utf-8")
        loaded = llm.load_env_file(f)
        assert loaded == ["GEMINI_API_KEY"] and os.environ["GEMINI_API_KEY"] == "test-key-123"
        assert os.environ["SHIPLESS_TEST_OTHER"] == "from-terminal"


def test_gemini_is_used_when_its_key_is_present():
    """With a Gemini key the app uses Gemini; with none it runs on rules; a setting can force the provider."""
    with env():
        assert llm.provider() is None and not llm.has_key()
    with env(GEMINI_API_KEY="x" * 39):
        assert llm.provider() == "gemini" and llm.current_model().startswith("gemini-")
    with env(GEMINI_API_KEY="x" * 39, SHIPLESS_PROVIDER="anthropic"):
        assert llm.provider() == "anthropic"
    with env(GEMINI_API_KEY="x" * 39, SHIPLESS_AI="off"):
        assert not llm.has_key()


def test_hourly_cap_stops_a_stranger_burning_the_key():
    """If the app is public, someone hammering it can't spend the key: past the hourly cap, templates are used."""
    llm.set_backend(None)               # also clears the call counter
    old = llm.MAX_CALLS_PER_HOUR
    llm.MAX_CALLS_PER_HOUR = 2
    try:
        llm._spend()
        llm._spend()
        try:
            llm._spend()
        except llm.LLMError as e:
            assert "hourly" in str(e)
        else:
            raise AssertionError("cap not enforced")
    finally:
        llm.MAX_CALLS_PER_HOUR = old
        llm.set_backend(None)


def test_out_of_quota_model_hands_over_to_the_fallback_model():
    """When the main model's daily free quota runs out, the fallback model answers, and the empty one isn't retried."""
    tried = []

    def once(client, errors, types, model, *rest):
        tried.append(model)
        if model == "gemini-3.5-flash":
            raise llm._OutOfQuota(3600)
        return "OK"

    real = llm._gemini_once
    llm._gemini_once = once
    llm._exhausted.clear()
    try:
        with env(GEMINI_API_KEY="x" * 39, SHIPLESS_MODEL="gemini-3.5-flash"):
            assert llm._call_gemini("s", "u", None, "low", 100) == "OK" and llm.answered_by() == "gemini-3.5-flash-lite"
            llm._call_gemini("s", "u2", None, "low", 100)
    finally:
        llm._gemini_once = real
        llm._exhausted.clear()
    assert tried == ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]


def test_identical_requests_are_answered_from_memory():
    """The same question twice costs one model call, not two."""
    calls = []
    with fake_model(lambda *a: calls.append(1) or "OK"):
        assert llm.ask("s", "u") == llm.ask("s", "u") == "OK"
    assert len(calls) == 1


def test_model_answers_are_converted_to_the_right_types():
    """'1200' becomes 1200 and the string 'false' becomes False, never True: no silent type bugs."""
    out = intake._coerce({"households": "1,200", "has_clinic": "false", "has_school": "yes",
                          "diesel_price": "2.40", "generator_size": "forty", "place_name": "Kadavu"})
    assert out["households"] == 1200 and out["has_clinic"] is False and out["has_school"] is True
    assert out["diesel_price"] == 2.4 and out["generator_size"] is None and out["place_name"] == "Kadavu"


def test_no_api_key_is_in_any_committed_file():
    """No API key is in any file git tracks, and the .env file that holds the key is git-ignored."""
    import re
    import subprocess
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    patterns = re.compile(r"AIza[0-9A-Za-z_\-]{35}|sk-ant-[A-Za-z0-9_\-]{20,}")
    # the real secrets in .env, whatever shape they have (new Google keys start "AQ.")
    secrets = []
    if llm.ENV_FILE.exists():
        for line in llm.ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)", line)   # settings, not comments
            value = m.group(2).strip().strip("'\"") if m else ""
            if m and re.search(r"KEY|TOKEN|SECRET|SID|PHONE", m.group(1).upper()) and len(value) >= 8:
                secrets.append(value)              # API keys, the Twilio token and SID, phone numbers
    leaks = []
    for name in files:
        p = ROOT / name
        if p.suffix.lower() in (".png", ".jpg", ".npz", ".pdf", ".ico") or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if patterns.search(text) or any(s in text for s in secrets):
            leaks.append(name)                 # the file name only: the key is never printed
    assert not leaks, f"API key found in tracked files: {leaks}"
    assert subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT).returncode == 0
    assert ".env" not in files


def test_free_push_alert_needs_no_phone_number():
    """Alerts can go out as a free push notification: no phone number, no payment, topic kept in .env."""
    calls = []
    real, saved = sms._http_post, os.environ.pop("NTFY_TOPIC_SECRET", None)
    sms._http_post = lambda url, **kw: calls.append((url, kw)) or type("R", (), {"status_code": 200})()
    try:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
            topic, new = sms.setup_push(f)
            assert new and topic.startswith("shipless-") and len(topic) >= 25
            assert f"NTFY_TOPIC_SECRET={topic}" in f.read_text(encoding="utf-8")
        r = sms.notify("Shipless (Funafuti): demand jumped.", "Shipless alert")
    finally:
        sms._http_post = real
        os.environ.pop("NTFY_TOPIC_SECRET", None)
        if saved is not None:
            os.environ["NTFY_TOPIC_SECRET"] = saved
    assert r["sent"] and r["mode"] == "push" and topic not in r["to"]        # the topic is never echoed in full
    assert calls[0][0].endswith("/" + topic) and calls[0][1]["data"] == b"Shipless (Funafuti): demand jumped."


# ------------------------------------------------------------------------------------ the server's interface

def test_server_interface_is_unchanged():
    """server/app.py keeps working: same function names, same response shapes as before."""
    with no_ai():
        d = agent.parse_description("We have 60 houses, a clinic and a school on Kadavu. 40 kW generator.")
        e = agent.explain(PLAN["kadavu"])
    for k in ("households", "generator_kw", "diesel_litres_per_month", "diesel_price_per_litre", "other_kw",
              "has_clinic", "has_school"):
        assert k in d["site"]
    assert d["source"] in ("ai", "rules") and d["site"]["has_clinic"] in (True, False, None)
    assert d["site"]["site_id"] == "kadavu"            # the web page selects the island from this
    assert isinstance(e["text"], str) and e["check"]["passed"] is True and e["check"]["unknown_numbers"] == []
    assert len(agent.alert_sms(sms.EXAMPLES[0])) <= 160 and isinstance(agent.MODEL, str)


# ------------------------------------------------------------------------------------ fact-checking our own docs

def test_factcheck_flags_unsourced_numbers_in_our_own_documents():
    """We run the same checker on our own pitch: a made-up statistic is flagged, an engine number passes."""
    from agent.factcheck import scan
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "pitch.md"
        p.write_text("# Pitch\n\nFunafuti needs 62 kW of solar. We cut diesel by 31.2% and reach 4,321 islands.\n\n"
                     "```\nport 8000\n```\n", encoding="utf-8")
        res = scan([str(p)])[str(p)]
    by = {x["number"]: x["status"] for x in res}
    assert by["62"] == "sourced" and by["31.2%"] == "FLAGGED IN SHEET" and by["4,321"] == "UNSOURCED"
    assert "8000" not in by                                 # code blocks are not claims


def test_fact_check_sheet_has_the_brief_columns():
    """The fact-check sheet has the brief's columns (claim, number, source link, checked by, date) on every row."""
    from agent.factcheck import sheet_rows
    rows = sheet_rows()
    assert len(rows) >= 30
    for r in rows:
        for col in ("claim", "number", "source link", "checked by", "date", "status"):
            assert r[col].strip(), (col, r)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    t_all = time.time()
    for t in tests:
        t0 = time.time()
        t()
        print(f"PASS  {t.__name__}  ({time.time() - t0:.1f}s)\n      {t.__doc__.strip().splitlines()[0]}")
    print(f"\nAll {len(tests)} front-desk tests passed in {time.time() - t_all:.0f}s.")
