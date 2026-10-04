"""The only place that talks to a language model. Owner: Mech A.

    python -m agent.llm --check         # is the key found, is it safe, does one tiny call work? (never prints the key)
    python -m agent.llm --models        # which models this key can use
    python -m agent.llm --install-hook  # block any git commit that contains an API key (run once per computer)

Every AI feature (intake, explainer, SMS wording) goes through ask(). If there is no key, the
network is down, the model refuses, the hourly limit is reached or the answer is malformed, ask()
raises LLMError and the caller falls back to its built-in rules or template, so the demo never breaks.

WHERE THE KEY GOES (never in code, never in git, never in the browser or the Pico):
    a file called .env in the project folder (next to README.md), one line:
        GEMINI_API_KEY=your-key-here
    .env is listed in .gitignore, so git will not commit it; this module reads it at start-up.
    Or set it in the terminal instead:  Windows PowerShell  $env:GEMINI_API_KEY="..."
                                        Mac/Linux           export GEMINI_API_KEY=...
    A variable already set in the terminal wins over the .env file.

Providers (picked automatically from whichever key is present, or SHIPLESS_PROVIDER=gemini|anthropic):
    Gemini      GEMINI_API_KEY (or GOOGLE_API_KEY, or the brief's LLM_KEY)    default model gemini-3.5-flash
    Anthropic   ANTHROPIC_API_KEY                                             default model claude-opus-5-5
Switches:
    SHIPLESS_MODEL=...                     another model ID
    SHIPLESS_AI=off                        force the templates (e.g. bad venue Wi-Fi during the demo)
    SHIPLESS_AI_MAX_CALLS_PER_HOUR=120     abuse guard: past this, templates until the hour rolls over

Tests swap the model for a fake with set_backend(), so they run offline and cost nothing.
"""
import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
GEMINI_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
ANTHROPIC_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# Gemini 3.5 Flash: stable, and fast enough for a live demo. (Gemini 2.5 models shut down on
# 16 Oct 2026, so don't use them.) Check what your key can use: python -m agent.llm --models
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash", "anthropic": "claude-opus-5-5"}
# ASSUMPTION: a judge waits ~30 s at most for an AI answer; after that the template is shown.
TIMEOUT_S = float(os.environ.get("SHIPLESS_AI_TIMEOUT_S", "30"))
# ASSUMPTION: a demo and a day of testing make well under 120 model calls an hour. If the app is put
# on a public URL (ngrok / Render), this stops a stranger from burning the key's quota.
MAX_CALLS_PER_HOUR = int(os.environ.get("SHIPLESS_AI_MAX_CALLS_PER_HOUR", "120"))
CACHE_MAX = 256                      # identical requests are answered from memory, not re-billed
# Anthropic only: if the model's safety classifier declines, the API retries on its recommended fallback.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """No usable AI answer. Callers fall back to rules / templates. Never contains the key."""


LEAKED = ("Google has disabled this key: it was found somewhere public ('reported as leaked'). It will never "
          "work again. Delete it at https://aistudio.google.com/apikey, create a new one, put the new one "
          "in .env, and don't paste it anywhere else.")


def load_env_file(path=ENV_FILE):
    """KEY=VALUE lines from the git-ignored .env file into the environment. A variable that is
    already set (e.g. in the terminal) is never overwritten. Returns the names it set."""
    loaded = []
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


_ENV_LOADED = load_env_file()
_backend = None                       # tests: a function (system, user, schema, effort) -> text
_lock = threading.Lock()
_calls = deque()                      # times of real model calls in the last hour
_cache = OrderedDict()


def provider():
    """'gemini', 'anthropic', or None if there is no key."""
    chosen = os.environ.get("SHIPLESS_PROVIDER", "").strip().lower()
    if chosen in DEFAULT_MODELS:
        return chosen
    if any(os.environ.get(k) for k in GEMINI_KEYS):
        return "gemini"
    if any(os.environ.get(k) for k in ANTHROPIC_KEYS):
        return "anthropic"
    if os.environ.get("LLM_KEY"):
        return "gemini"              # the brief's generic name; the team's key is a Gemini key
    return None


def current_model():
    return os.environ.get("SHIPLESS_MODEL") or DEFAULT_MODELS[provider() or "gemini"]


MODEL = current_model()              # what the server prints at start-up


def set_backend(fn):
    """Replace the real model with fn(system, user, schema, effort) -> str. None restores the real one."""
    global _backend
    _backend = fn
    with _lock:
        _cache.clear()
        _calls.clear()


def has_key():
    """True if an AI answer can be attempted (a key is set, or a test backend is installed)."""
    if os.environ.get("SHIPLESS_AI", "").lower() == "off":
        return False
    return _backend is not None or provider() is not None


def mode():
    """'ai' or 'rules': what the front desk is running on right now (shown in the UI and logs)."""
    return "ai" if has_key() else "rules"


def _key_for(p):
    names = GEMINI_KEYS + ("LLM_KEY",) if p == "gemini" else ANTHROPIC_KEYS + ("LLM_KEY",)
    for n in names:
        if os.environ.get(n):
            return n, os.environ[n]
    return None, None


def _spend():
    """Abuse guard: at most MAX_CALLS_PER_HOUR real calls per rolling hour."""
    now = time.time()
    with _lock:
        while _calls and now - _calls[0] > 3600:
            _calls.popleft()
        if len(_calls) >= MAX_CALLS_PER_HOUR:
            raise LLMError(f"hourly AI limit reached ({MAX_CALLS_PER_HOUR} calls); using templates")
        _calls.append(now)


def ask(system, user, *, schema=None, effort="low", max_tokens=8192):
    """One model call -> text, or (with a JSON schema) the parsed object. Raises LLMError."""
    if not has_key():
        raise LLMError("AI is off (no key, or SHIPLESS_AI=off)")
    p = "test" if _backend is not None else provider()
    key = hashlib.sha256(json.dumps([p, current_model(), system, user, schema, effort]).encode()).hexdigest()
    with _lock:
        text = _cache.get(key)
    if text is None:
        if _backend is not None:
            text = _backend(system, user, schema, effort)
        else:
            _spend()
            text = (_call_gemini if p == "gemini" else _call_anthropic)(system, user, schema, effort, max_tokens)
        with _lock:
            _cache[key] = text
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
    if schema is None:
        return text.strip()
    try:
        return json.loads(_strip_fences(text))
    except (TypeError, ValueError) as e:
        raise LLMError(f"model returned invalid JSON ({e})") from None


def _strip_fences(text):
    """Some models wrap JSON in ```json fences even when asked not to."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


# ---------------------------------------------------------------------------------------------
# Gemini (Google Gen AI SDK)
# ---------------------------------------------------------------------------------------------

_LEVELS = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}


def _call_gemini(system, user, schema, effort, max_tokens):
    from google import genai
    from google.genai import errors, types
    _, api_key = _key_for("gemini")
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(TIMEOUT_S * 1000)))
    # Try the strict request first (JSON schema + low thinking for speed). If the model rejects one
    # of those options, retry once plainly with the schema written into the instructions; the
    # callers check every answer anyway.
    attempts = [dict(schema=schema, thinking=True), dict(schema=None, thinking=False)]
    last = None
    for a in attempts:
        cfg = {"system_instruction": system, "max_output_tokens": max_tokens,
               # no tools are given to the model; turning this off also silences the SDK's warning
               "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)}
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            if a["schema"] is not None:
                cfg["response_json_schema"] = schema
            else:
                cfg["system_instruction"] = system + "\n\nReply with JSON only, matching this JSON schema:\n" + \
                    json.dumps(schema)
        if a["thinking"]:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=_LEVELS.get(effort, "LOW"))
        try:
            resp = client.models.generate_content(model=current_model(), contents=user,
                                                  config=types.GenerateContentConfig(**cfg))
        except errors.ClientError as e:
            msg = f"{e.status or ''} {e.message or ''}".lower()
            if "leaked" in msg:
                raise LLMError(LEAKED) from None
            if "api key" in msg or "api_key" in msg or e.code in (401, 403):
                raise LLMError("the API key was rejected (or is restricted)") from None
            if e.code == 429:
                raise LLMError("rate limited / quota used up") from None
            if e.code == 404:
                raise LLMError(f"model '{current_model()}' not found: set SHIPLESS_MODEL "
                               f"(see python -m agent.llm --models)") from None
            last = LLMError(f"API error {e.code}")
            continue                                  # 400 on an option: try the plain request
        except errors.APIError as e:
            raise LLMError(f"API error {e.code}") from None
        except Exception as e:                        # network / timeout
            raise LLMError(f"no connection to the AI service ({type(e).__name__})") from None
        cand = (resp.candidates or [None])[0]
        reason = str(getattr(cand, "finish_reason", "") or "").upper()
        if "SAFETY" in reason or "PROHIBITED" in reason or "BLOCK" in reason:
            raise LLMError("the model declined")
        if "MAX_TOKENS" in reason:
            raise LLMError("the answer was cut off")
        text = resp.text
        if not text or not text.strip():
            raise LLMError("empty answer")
        return text
    raise last or LLMError("no answer")


# ---------------------------------------------------------------------------------------------
# Anthropic (official SDK) - kept as an alternative
# ---------------------------------------------------------------------------------------------

def _call_anthropic(system, user, schema, effort, max_tokens):
    import anthropic
    name, api_key = _key_for("anthropic")
    client = anthropic.Anthropic(api_key=api_key) if name != "ANTHROPIC_AUTH_TOKEN" else anthropic.Anthropic()
    output_config = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = client.with_options(timeout=TIMEOUT_S, max_retries=1).beta.messages.create(
            model=current_model(), max_tokens=max(max_tokens, 16000), system=system,
            messages=[{"role": "user", "content": user}], output_config=output_config,
            betas=[ANTHROPIC_FALLBACK_BETA], fallbacks="default")
    except anthropic.AuthenticationError:
        raise LLMError("the API key was rejected") from None
    except anthropic.RateLimitError:
        raise LLMError("rate limited") from None
    except anthropic.APIStatusError as e:
        raise LLMError(f"API error {e.status_code}") from None
    except anthropic.APIConnectionError:
        raise LLMError("no connection to the AI service") from None
    if resp.stop_reason == "refusal":
        raise LLMError("the model declined")
    if resp.stop_reason == "max_tokens":
        raise LLMError("the answer was cut off")
    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text.strip():
        raise LLMError("empty answer")
    return text


# ---------------------------------------------------------------------------------------------
# python -m agent.llm --check : safe self-check (the key is never printed)
# ---------------------------------------------------------------------------------------------

def _mask(secret):
    return f"{secret[:4]}...{secret[-4:]} ({len(secret)} characters)" if secret and len(secret) > 12 else "(set)"


def check():
    import subprocess
    p = provider()
    print(f"\n  provider      {p or 'none: no key found, the app uses rules + templates'}")
    if p:
        name, secret = _key_for(p)
        where = "the .env file" if name in _ENV_LOADED else "the terminal environment"
        print(f"  key           {name} from {where}: {_mask(secret)}")
        print(f"  model         {current_model()}")
    ignored = subprocess.run(["git", "check-ignore", "-q", str(ENV_FILE)], cwd=ROOT).returncode == 0
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=ROOT,
                             capture_output=True).returncode == 0
    print(f"  .env          {'exists' if ENV_FILE.exists() else 'not present'}; git-ignored: "
          f"{'YES' if ignored else 'NO - add .env to .gitignore before putting a key in it!'}"
          f"{'; WARNING: .env IS TRACKED BY GIT, run: git rm --cached .env' if tracked else ''}")
    print(f"  abuse guard   at most {MAX_CALLS_PER_HOUR} calls an hour; identical requests answered from memory")
    if not p or os.environ.get("SHIPLESS_AI", "").lower() == "off":
        print("  test call     skipped\n")
        return
    try:
        t0 = time.perf_counter()
        out = ask("Reply with exactly the word OK.", "Health check.", effort="low", max_tokens=256)
        print(f"  test call     {'OK' if 'ok' in out.lower() else 'answered: ' + out[:40]!r} "
              f"({time.perf_counter() - t0:.1f} s)\n")
    except LLMError as e:
        print(f"  test call     FAILED: {e}\n")


def list_models():
    if provider() != "gemini":
        print("  --models lists Gemini models; set GEMINI_API_KEY first")
        return
    from google import genai
    from google.genai import errors
    client = genai.Client(api_key=_key_for("gemini")[1])
    try:
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if not actions or "generateContent" in actions:
                print(" ", m.name.removeprefix("models/"))
    except errors.APIError as e:
        leaked = "leaked" in f"{e.message or ''}".lower()
        print(f"\n  {LEAKED if leaked else f'Google refused: {e.code} {e.status}'}\n")


HOOK = r"""#!/bin/sh
# Shipless: refuse to commit an API key or the .env file (installed by: python -m agent.llm --install-hook)
if git diff --cached -U0 | grep -E '^\+' | grep -qE 'AIza[0-9A-Za-z_-]{35}|sk-ant-[A-Za-z0-9_-]{20,}'; then
  echo "Blocked by Shipless: this commit contains an API key. Keep keys only in .env (git-ignored)." >&2
  exit 1
fi
if git diff --cached --name-only | grep -qE '(^|/)\.env$'; then
  echo "Blocked by Shipless: .env holds the API key and must never be committed." >&2
  exit 1
fi
"""


def install_hook():
    """A git pre-commit hook on THIS computer that blocks any commit containing an API key.
    Every teammate runs it once in their own copy (hooks are not shared through git)."""
    import subprocess
    hooks = Path(subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT, capture_output=True,
                                text=True).stdout.strip())
    hook = (ROOT / hooks if not hooks.is_absolute() else hooks) / "pre-commit"
    if hook.exists() and "Shipless" not in hook.read_text(encoding="utf-8", errors="ignore"):
        print(f"  {hook} already exists (someone else's hook): not overwritten. Add the lines from "
              f"agent/llm.py HOOK to it by hand.")
        return
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    print(f"  installed {hook}: commits containing an API key or .env are now blocked on this computer")


if __name__ == "__main__":
    if "--install-hook" in sys.argv:
        install_hook()
    elif "--models" in sys.argv:
        list_models()
    else:
        check()
