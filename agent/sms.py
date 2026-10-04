"""Alert -> a text message a local solar technician can act on (brief 5.4). Owner: Mech A.

    python -m agent.sms                 # every alert type, as the technician would receive it
    python -m agent.sms --ai            # the AI-worded versions next to the templates

Every message names the site, the fault and the first thing to check, in plain words, and fits ONE
text message: 160 characters of the GSM-7 SMS alphabet. That alphabet matters on Pacific phones: a
single character outside it (a curly quote, an emoji, the okina in "Savaiʻi") silently switches the
whole message to UCS-2, where one text holds only 70 characters and the rest arrives in pieces.
Names are cleaned (okina -> ') and every message is measured before it is sent.

alert_sms(alert) is the fast template (no AI): the server calls it for every alert on every poll.
compose(alert, use_ai=True) asks the AI for better wording, then checks it like everything else:
GSM-7 only, 160 characters, site named, and every number traced to the alert's facts. Fails -> template.

Sending: demo mode shows the message as a phone-style notification in the web app. A real SMS goes
out only if TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM are set (a free Twilio trial
works). Say on camera which one the audience is seeing.
"""
import os
import re
import sys
import unicodedata

from agent import llm
from agent.check_numbers import check

SMS_LIMIT = 160            # GSM-7 characters in one text message
UCS2_LIMIT = 70            # characters in one text message once any non-GSM character is present
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
           "abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = set("^{}\\[~]|€")   # allowed, but each takes 2 of the 160

# What a technician should look at first, per alert type (guard/rules.py). Plain words, no jargon.
FAULTS = {
    "low_load": ("generator running at only {percent_of_size}% load for {minutes} min, wasting fuel",
                 "battery charge and inverter. The battery should carry a load this small"),
    "diesel_in_sunshine": ("generator running at {local_hour}:00 in full sun",
                           "inverter and battery, then the solar panel breaker"),
    "spike": ("demand jumped to {watts_now} W (normally {typical_watts} W)",
              "for a fault, or a big new load just switched on"),
    "sensor_offline": ("no signal from the sensor for {seconds_since_last_reading} s",
                       "the sensor's power and Wi-Fi"),
    "no_data": ("waiting for the first reading from the sensor", "that the sensor is switched on"),
}

SYSTEM = """Rewrite this alert as an SMS for a local solar technician on a Pacific island.
- Under 160 characters. Plain words, no jargon, no emoji, plain ASCII punctuation only.
- Start with "Shipless (<site>):". Name the fault, then the first thing to check (FIRST_CHECK).
- Use only numbers from ALERT. Never add a number of your own (no part numbers, battery numbers or times).
Reply with the SMS text only."""

_ai_cache = {}


def gsm_clean(text):
    """Replace characters that would force UCS-2 with their GSM-7 lookalikes."""
    for a, b in (("ʻ", "'"), ("’", "'"), ("‘", "'"), ("`", "'"), ("“", '"'), ("”", '"'), ("–", "-"),
                 ("—", "-"), ("…", "..."), (" ", " ")):
        text = text.replace(a, b)
    out = []
    for ch in text:
        if ch in GSM7 or ch in GSM7_EXT:
            out.append(ch)
        else:   # accents the GSM alphabet lacks: ā -> a
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base if base and all(c in GSM7 for c in base) else "?")
    return "".join(out)


def measure(text):
    """How the phone network will see it -> {encoding, length, segments}."""
    if all(c in GSM7 or c in GSM7_EXT for c in text):
        n = sum(2 if c in GSM7_EXT else 1 for c in text)
        return {"encoding": "GSM-7", "length": n, "segments": 1 if n <= SMS_LIMIT else -(-n // 153)}
    n = len(text)
    return {"encoding": "UCS-2", "length": n, "segments": 1 if n <= UCS2_LIMIT else -(-n // 67)}


def _site(alert, site=None):
    name = site or alert.get("site") or alert.get("site_name") or "your site"
    return gsm_clean(str(name).replace("Village on ", ""))


def alert_sms(alert, site=None):
    """Template SMS: always GSM-7, always one text message, numbers only from the alert's facts."""
    f, kind = alert.get("facts") or {}, alert.get("type")
    name = _site(alert, site)
    if kind in FAULTS:
        fault, first = FAULTS[kind]
        try:
            fault = fault.format(**f)
        except (KeyError, IndexError):
            fault = alert.get("title", kind)
    else:
        fault, first = alert.get("title") or "check the system", "the system log"
    for text in (f"Shipless ({name}): {fault}. First check {first}.",
                 f"Shipless ({name[:20]}): {fault}. Check {first.split('.')[0]}.",
                 f"Shipless ({name[:20]}): {fault}."):
        text = gsm_clean(text)
        if measure(text)["segments"] == 1:
            return text
    return gsm_clean(f"Shipless: {fault}")[:SMS_LIMIT]


def validate(text, alert, site=None):
    """Is an SMS draft safe to send? -> list of problems (empty = OK)."""
    problems = []
    m = measure(text)
    if m["encoding"] != "GSM-7":
        problems.append("has characters outside the SMS alphabet (would split into 70-character parts)")
    if m["segments"] != 1:
        problems.append(f"{m['length']} characters: more than one text message")
    if _site(alert, site).lower()[:12] not in text.lower():
        problems.append("does not name the site")
    rep = check(text, alert.get("facts") or {})
    if not rep.passed:
        problems.append("numbers not in the alert: " + ", ".join(rep.unknown_numbers))
    return problems


def compose(alert, site=None, use_ai=True):
    """Best SMS for an alert -> {text, source, encoding, length, segments, problems_with_ai_draft}."""
    template = alert_sms(alert, site)
    out = {"text": template, "source": "template", "ai_draft": None, "ai_problems": []}
    key = (alert.get("type"), repr(sorted((alert.get("facts") or {}).items())), site)
    if use_ai and llm.has_key():
        if key not in _ai_cache:
            fault = FAULTS.get(alert.get("type"), ("", "the system"))[1]
            user = (f"SITE: {_site(alert, site)}\nALERT: {alert}\nFIRST_CHECK: {fault}\n"
                    f"A good template version: {template}")
            try:
                draft = gsm_clean(llm.ask(SYSTEM, user, effort="low", max_tokens=4000).strip().strip('"'))
                _ai_cache[key] = (draft, validate(draft, alert, site))
            except llm.LLMError as e:
                _ai_cache[key] = (None, [f"AI unavailable: {e}"])
        draft, problems = _ai_cache[key]
        out.update(ai_draft=draft, ai_problems=problems)
        if draft and not problems:
            out.update(text=draft, source="ai")
    out.update(measure(out["text"]))
    return out


def send_sms(to, text):
    """Send a real SMS through Twilio if it is configured; otherwise say it's demo mode.
    The phone number is never logged in full."""
    sid, token, sender = (os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))
    masked = re.sub(r"\d(?=\d{3})", "*", str(to))
    if not (sid and token and sender):
        return {"sent": False, "mode": "demo", "to": masked,
                "note": "Twilio not configured: shown as a phone-style notification in the app only"}
    import requests
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, token),
                      data={"To": to, "From": sender, "Body": text}, timeout=15)
    if r.status_code >= 300:
        return {"sent": False, "mode": "twilio", "to": masked, "error": f"Twilio {r.status_code}"}
    return {"sent": True, "mode": "twilio", "to": masked, "sid": r.json().get("sid")}


EXAMPLES = [
    {"type": "low_load", "facts": {"average_watts": 640, "generator_watts": 3000, "percent_of_size": 21, "minutes": 5}},
    {"type": "diesel_in_sunshine", "facts": {"average_watts": 1850, "local_hour": 12}},
    {"type": "spike", "facts": {"watts_now": 2950, "typical_watts": 410}},
    {"type": "sensor_offline", "facts": {"seconds_since_last_reading": 240, "last_watts": 380}},
    {"type": "no_data", "facts": {"hint": "Start the Pico or tools/fake_device.py"}},
]

if __name__ == "__main__":
    use_ai = "--ai" in sys.argv
    for a in EXAMPLES:
        r = compose(a, site="Village on Savaiʻi", use_ai=use_ai)
        print(f"[{a['type']}] {r['length']} chars, {r['encoding']}, {r['segments']} text(s), {r['source']}")
        print(f"   {r['text']}")
        if r["ai_problems"]:
            print(f"   AI draft rejected: {'; '.join(r['ai_problems'])}")
