"""The AI "front desk": what the server imports. Owner: Mech A.

The AI is only the front desk. It explains the engine's numbers in plain language and turns a
plain description into form fields, but a checker program proves it cannot invent a number:
    agent/intake.py         description -> SiteInput (every field must quote the user's own words)
    agent/explain.py        engine result -> plain language (every number checked, else template)
    agent/check_numbers.py  the checker
    agent/sms.py            alert -> one GSM-7 text message for a technician
    agent/llm.py            the only file that calls the model (key from the environment)
Without a key everything still works on rules and templates, so the demo never breaks.

This file keeps the names server/app.py already uses (parse_description, explain, alert_sms,
has_key, MODEL) with the same response shapes, plus extra fields the web app can use later.

The key (Gemini by default) goes in a git-ignored file called .env in the project folder:
    GEMINI_API_KEY=your-key-here          (template: agent/env.example)
Check it with: python -m agent.llm --check   (it never prints the key). Details in agent/llm.py.
"""
from agent import explain as _explain
from agent import intake, llm
from agent import sms as _sms
from agent.check_numbers import check

MODEL = llm.MODEL
has_key = llm.has_key


def parse_description(text):
    """Plain description -> {"site": form fields, "source": "ai"|"rules", "note", ...}.

    Same shape as before for web/plan.js. New: "question" (one short follow-up, if something is
    missing), "state" (send it back with the answer to continue: intake.reply), "site_input"
    (when complete), "warnings" (facts that were dropped because the words weren't there)."""
    state = intake.start(text)
    notes = state["warnings"] + state["notes"]
    if state["question"]:
        notes.append("Question: " + state["question"])
    return {"site": intake.form_fields(state), "source": state["source"], "note": " ".join(notes) or None,
            "question": state["question"], "done": state["done"], "ok": state["ok"],
            "site_input": state["site_input"], "warnings": state["warnings"], "state": state}


def explain(result, audience="council"):
    """Engine plan -> {"text", "source", "check": {"passed", "unknown_numbers", ...}, ...}.
    The council version is what the plan screen shows; "officer" is the 120-word funder version."""
    return _explain.explain(result, audience)


def check_numbers(text, result):
    """Old name kept: {"passed", "unknown_numbers"} for any text against any engine result."""
    return check(text, result).to_dict()


def alert_sms(alert, site=None):
    """Alert -> one text message (template; fast enough to run on every poll)."""
    return _sms.alert_sms(alert, site)
