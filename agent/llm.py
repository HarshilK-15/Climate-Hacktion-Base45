"""The only place that talks to the language model. Owner: Mech A.

Every AI feature (intake, explainer, SMS wording) goes through ask(). If there is no key, the
network is down, the model refuses or the answer is malformed, ask() raises LLMError and the
caller falls back to its built-in rules or template, so the demo never breaks.

Keys (never in code, never in the repo):
    ANTHROPIC_API_KEY=...        the Anthropic SDK's standard variable
    LLM_KEY=...                  the brief's name for it (used if ANTHROPIC_API_KEY is not set)
Switches:
    SHIPLESS_AI=off              force the templates (e.g. bad venue Wi-Fi during the demo)
    SHIPLESS_MODEL=...           model ID (default claude-opus-5-5)

Tests swap the model for a fake with set_backend(), so they run offline and cost nothing.
"""
import json
import os

MODEL = os.environ.get("SHIPLESS_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5-5"
# ASSUMPTION: a judge waits ~30 s at most for an AI answer; after that the template is shown.
TIMEOUT_S = float(os.environ.get("SHIPLESS_AI_TIMEOUT_S", "30"))
# Server-side fallback: if the model's safety classifier declines, the API re-runs the request on
# Anthropic's recommended fallback model instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_backend = None   # tests: a function (system, user, schema, effort) -> text


class LLMError(RuntimeError):
    """No usable AI answer (no key, network, refusal, truncation, bad JSON). Callers fall back."""


def set_backend(fn):
    """Replace the real model with fn(system, user, schema, effort) -> str. None restores the real one."""
    global _backend
    _backend = fn


def has_key():
    """True if an AI answer can be attempted (a key is set, or a test backend is installed)."""
    if os.environ.get("SHIPLESS_AI", "").lower() == "off":
        return False
    if _backend is not None:
        return True
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "LLM_KEY", "ANTHROPIC_AUTH_TOKEN"))


def mode():
    """'ai' or 'rules': what the front desk is running on right now (shown in the UI and logs)."""
    return "ai" if has_key() else "rules"


def _client():
    import anthropic     # imported here so the app still starts if the SDK is missing (templates only)
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_KEY")
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def ask(system, user, *, schema=None, effort="low", max_tokens=16000):
    """One model call -> text, or (with a JSON schema) the parsed object. Raises LLMError."""
    if not has_key():
        raise LLMError("AI is off (no key, or SHIPLESS_AI=off)")
    if _backend is not None:
        text = _backend(system, user, schema, effort)
    else:
        text = _call_anthropic(system, user, schema, effort, max_tokens)
    if schema is None:
        return text.strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError) as e:
        raise LLMError(f"model returned invalid JSON ({e})") from None


def _call_anthropic(system, user, schema, effort, max_tokens):
    import anthropic
    output_config = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = _client().with_options(timeout=TIMEOUT_S, max_retries=1).beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
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
