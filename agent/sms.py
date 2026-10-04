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
out only if the Twilio settings are in .env (a free Twilio trial works; it can only text numbers you
have verified with Twilio). Say on camera which one the audience is seeing.
    ACCOUNT_SID / AUTH_TOKEN / TWILIO_PHONE_NUMBER   (or TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM)
    MY_PHONE_NUMBER                                  where demo texts go, in +61... format

FREE ALTERNATIVE (what the demo uses: Twilio trial accounts can only send Twilio's own canned texts):
a push notification through ntfy (https://ntfy.sh): free, no account, and no phone number at all.
    python -m agent.sms --setup-push      # makes a secret topic name, saves NTFY_TOPIC_SECRET in .env
    then on the phone: install the "ntfy" app, tap +, enter that topic name, Subscribe.
The topic name works like a password (anyone who knows it can read or send alerts to it), so it
lives only in the git-ignored .env, where the git hook guards it. With a topic set, alerts go there
instead of SMS. On camera, say it's a push notification standing in for an SMS.

    python -m agent.sms --send            # one example alert to your phone (push, or SMS if no topic)
    python -m agent.sms --watch           # send each new alert from the running app (python -m server.app)
        --site "Funafuti"   name the site in the text      --ai   AI wording (checked; uses Gemini quota)
The watcher texts at most one alert of each type every SMS_COOLDOWN_S (600 s) and SMS_MAX_TEXTS (5)
per run, so a noisy sensor can't flood your phone or use up the trial credit. Your number is never
printed in full, and the web page never sees it.
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


def _env(*names):
    """The first of these settings that is set (the .env file is loaded by agent.llm)."""
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


def twilio_settings():
    return {"sid": _env("TWILIO_ACCOUNT_SID", "ACCOUNT_SID"), "token": _env("TWILIO_AUTH_TOKEN", "AUTH_TOKEN"),
            "from": _env("TWILIO_FROM", "TWILIO_PHONE_NUMBER"),
            "to": _env("SMS_TO", "MY_PHONE_NUMBER", "DEMO_PHONE_SECRET")}


def mask(text):
    """Hide every phone-number-like digit run except its last 3 digits: +61412345678 -> +61*******678."""
    return re.sub(r"\+?\d[\d ]{6,}\d", lambda m: re.sub(r"\d(?=(?:\D*\d){3})", "*", m.group(0)), str(text))


def send_sms(to, text):
    """Send a real SMS through Twilio if it is configured; otherwise say it's demo mode.
    to=None sends to MY_PHONE_NUMBER. The phone number is never logged or returned in full."""
    cfg = twilio_settings()
    to = to or cfg["to"]
    if not (cfg["sid"] and cfg["token"] and cfg["from"] and to):
        return {"sent": False, "mode": "demo", "to": mask(to or ""),
                "note": "Twilio not configured: shown as a phone-style notification in the app only"}
    try:
        r = _http_post(f"https://api.twilio.com/2010-04-01/Accounts/{cfg['sid']}/Messages.json",
                       auth=(cfg["sid"], cfg["token"]), data={"To": to, "From": cfg["from"], "Body": text})
    except Exception as e:
        return {"sent": False, "mode": "twilio", "to": mask(to), "error": f"no connection ({type(e).__name__})"}
    if r.status_code >= 300:
        try:
            body = r.json()
            why = f"Twilio {r.status_code} (code {body.get('code')}): {mask(body.get('message', ''))}"
        except ValueError:
            why = f"Twilio {r.status_code}"
        return {"sent": False, "mode": "twilio", "to": mask(to), "error": why}
    return {"sent": True, "mode": "twilio", "to": mask(to), "sid": r.json().get("sid")}


NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")


def _http_post(url, **kwargs):
    import requests                                 # tests replace this function, so they never send anything
    return requests.post(url, timeout=15, **kwargs)


def send_push(text, title="Shipless alert"):
    """A push notification to the phone subscribed to NTFY_TOPIC_SECRET (free; no phone number used)."""
    topic = _env("NTFY_TOPIC_SECRET", "NTFY_TOPIC")
    if not topic:
        return {"sent": False, "mode": "demo", "note": "no ntfy topic: run python -m agent.sms --setup-push"}
    try:
        r = _http_post(f"{NTFY_SERVER}/{topic}", data=text.encode("utf-8"),
                       headers={"Title": title.encode("utf-8"), "Priority": "high", "Tags": "warning"})
    except Exception as e:
        return {"sent": False, "mode": "push", "error": f"no connection ({type(e).__name__})"}
    if r.status_code >= 300:
        return {"sent": False, "mode": "push", "error": f"ntfy {r.status_code}"}
    return {"sent": True, "mode": "push", "to": f"ntfy topic ...{topic[-4:]}"}


def notify(text, title="Shipless alert"):
    """Deliver an alert to the demo phone: push if an ntfy topic is set, else SMS via Twilio, else demo."""
    if _env("NTFY_TOPIC_SECRET", "NTFY_TOPIC"):
        return send_push(text, title)
    return send_sms(None, text)


def setup_push(env_file=None):
    """Make a long random topic name and save it as NTFY_TOPIC_SECRET in the git-ignored .env."""
    import secrets
    from agent.llm import ENV_FILE
    env_file = env_file or ENV_FILE
    existing = _env("NTFY_TOPIC_SECRET")
    if existing:
        return existing, False
    topic = "shipless-" + secrets.token_urlsafe(18).replace("_", "").replace("-", "")[:22].lower()
    with open(env_file, "a", encoding="utf-8") as f:
        f.write(f"\n# Push notifications for the alert demo (python -m agent.sms --setup-push)\nNTFY_TOPIC_SECRET={topic}\n")
    os.environ["NTFY_TOPIC_SECRET"] = topic
    return topic, True


def watch(base="http://localhost:8000", site=None, use_ai=False, every_s=5):
    """Text each new alert from the running app to MY_PHONE_NUMBER, with a cooldown and a cap."""
    import time
    import requests
    cooldown = float(os.environ.get("SMS_COOLDOWN_S", "600"))
    cap = int(os.environ.get("SMS_MAX_TEXTS", "5"))
    last, sent = {}, 0
    where = ("a push notification (ntfy)" if _env("NTFY_TOPIC_SECRET", "NTFY_TOPIC")
             else mask(twilio_settings()["to"] or "nobody (demo mode)"))
    print(f"  watching {base}/api/alerts: alerts go to {where}; "
          f"one per alert type every {cooldown:.0f} s, at most {cap} this run. Ctrl+C to stop.")
    while sent < cap:
        try:
            alerts = requests.get(f"{base}/api/alerts", timeout=10).json()
        except (requests.RequestException, ValueError):
            print("  (app not reachable: is python -m server.app running?)")
            time.sleep(every_s)
            continue
        for a in alerts:
            kind = a.get("type")
            if kind in (None, "no_data") or time.time() - last.get(kind, 0) < cooldown:
                continue
            msg = compose(a, site, use_ai=use_ai)
            r = notify(msg["text"], f"Shipless alert: {a.get('title') or kind}")
            last[kind] = time.time()
            sent += r["sent"]
            print(f"  {kind}: {'sent' if r['sent'] else 'NOT sent'} -> {r.get('to', '')}  "
                  f"{r.get('error') or r.get('note') or ''}")
            print(f"     \"{msg['text']}\"")
            if sent >= cap:
                break
        time.sleep(every_s)
    print(f"  stopped after {sent} texts (SMS_MAX_TEXTS={cap})")


EXAMPLES = [
    {"type": "low_load", "facts": {"average_watts": 640, "generator_watts": 3000, "percent_of_size": 21, "minutes": 5}},
    {"type": "diesel_in_sunshine", "facts": {"average_watts": 1850, "local_hour": 12}},
    {"type": "spike", "facts": {"watts_now": 2950, "typical_watts": 410}},
    {"type": "sensor_offline", "facts": {"seconds_since_last_reading": 240, "last_watts": 380}},
    {"type": "no_data", "facts": {"hint": "Start the Pico or tools/fake_device.py"}},
]

if __name__ == "__main__":
    use_ai = "--ai" in sys.argv
    site = sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else None
    if "--watch" in sys.argv:
        watch(site=site, use_ai=use_ai)
        raise SystemExit
    if "--setup-push" in sys.argv:
        topic, new = setup_push()
        print(f"\n  {'Saved a new' if new else 'Using your existing'} topic in .env (NTFY_TOPIC_SECRET).\n"
              f"  On your phone: install the 'ntfy' app, tap +, type this topic name, Subscribe:\n\n"
              f"      {topic}\n\n  Then run: python -m agent.sms --send\n")
        raise SystemExit
    if "--send" in sys.argv:
        r = compose(EXAMPLES[2], site=site or "Funafuti", use_ai=use_ai)
        out = notify(r["text"], "Shipless alert: Sudden jump in demand")
        print(f"  {'SENT' if out['sent'] else 'NOT SENT'} ({out['mode']}) to {out.get('to', '')}  "
              f"{out.get('sid') or out.get('error') or out.get('note') or ''}\n  \"{r['text']}\"")
        raise SystemExit(0 if out["sent"] else 1)
    for a in EXAMPLES:
        r = compose(a, site="Village on Savaiʻi", use_ai=use_ai)
        print(f"[{a['type']}] {r['length']} chars, {r['encoding']}, {r['segments']} text(s), {r['source']}")
        print(f"   {r['text']}")
        if r["ai_problems"]:
            print(f"   AI draft rejected: {'; '.join(r['ai_problems'])}")
