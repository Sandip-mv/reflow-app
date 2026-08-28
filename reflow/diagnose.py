"""
diagnose.py — the ONE place an LLM is used to interpret text.

Job: turn a messy gateway error string into a structured cause the rest of
the system can act on.

    "GATEWAY_ERROR: bank_error_code=U69 desc=payer psp unavailable"
        -> {"cause": "issuer_downtime", "confidence": 0.86}

Three things worth noticing:

1. PII is redacted BEFORE the text leaves the process. Card numbers, phone
   numbers, emails and UPI handles never reach a third-party API.
2. If the LLM times out, errors, or returns something we cannot parse, we
   fall back to a deterministic keyword classifier and mark the result
   low-confidence. The pipeline never crashes on a model failure.
3. The LLM classifies. It does NOT decide whether to move money. That
   decision lives in policy.py, which is plain, auditable code.

Set ANTHROPIC_API_KEY to run against the real model. Without it the module
runs in offline mode using the fallback classifier, so the whole project is
still reproducible on a machine with no network and no key.
"""

import json
import os
import re
import urllib.request
import urllib.error

KNOWN_CAUSES = [
    "issuer_downtime",
    "insufficient_funds",
    "checkout_abandon",
    "network_timeout",
    "expired_instrument",
    "mandate_expired",
    "hard_decline",
]

SYSTEM_PROMPT = (
    "You classify Indian payment gateway failure strings.\n"
    "Reply with JSON only, no prose, no markdown fences.\n"
    'Shape: {"cause": <one of the allowed values>, "confidence": <0.0-1.0>}\n'
    "Allowed values: " + ", ".join(KNOWN_CAUSES) + "\n"
    "Use hard_decline only for permanent issuer refusals (stolen card, "
    "account closed, do not honour). Use checkout_abandon when the customer "
    "failed to complete authentication."
)

# --- PII redaction ---------------------------------------------------------

_PATTERNS = [
    (re.compile(r"\b\d{12,19}\b"), "[CARD_REDACTED]"),
    (re.compile(r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b[\w.\-]+@[\w\-]+\.[a-z]{2,}\b", re.I), "[EMAIL_REDACTED]"),
    (re.compile(r"\b[\w.\-]{3,}@(?:okhdfcbank|ybl|paytm|upi|oksbi|apl)\b", re.I),
     "[VPA_REDACTED]"),
]


def redact(text: str) -> str:
    """Strip anything that could identify a customer."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def contains_pii(text: str) -> bool:
    return any(p.search(text) for p, _ in _PATTERNS)


# --- Fallback classifier ---------------------------------------------------

_KEYWORDS = [
    ("hard_decline", ["stolen", "do not honour", "do_not_honour", "account closed",
                      "permanent decline", "code=43", "u16", "code 04"]),
    ("insufficient_funds", ["insufficient", "low balance", "not sufficient",
                            "code=51", "u30", "116", "balance less"]),
    ("expired_instrument", ["expired card", "card_expired", "code=54",
                            "past expiry", "re-collect"]),
    ("mandate_expired", ["mandate", "standing instruction", "si_failed", "umn"]),
    ("checkout_abandon", ["3ds", "otp", "abandon", "dropped off", "did not complete",
                          "did not approve", "session expired", "challenge"]),
    ("network_timeout", ["timeout", "hang up", "no response", "status_unknown",
                         "30000ms", "reversal"]),
    ("issuer_downtime", ["psp unavailable", "switch inoperative", "offline",
                         "unreachable", "u69", "code=91", "maintenance", "5003"]),
]


def classify_by_rules(raw_error: str):
    """Deterministic backup. Also used to grade the LLM's added value."""
    low = raw_error.lower()
    for cause, needles in _KEYWORDS:
        if any(n in low for n in needles):
            return cause, 0.55
    return "unknown", 0.0


# --- LLM path --------------------------------------------------------------

def _call_llm(clean_error: str, timeout: float = 20.0):
    # REFLOW_BREAK_LLM points at a dead port to test graceful degradation
    # (see run.py --break-llm). Otherwise talks to local Ollama — no API
    # key, no network dependency, nothing to leak in a public repo.
    port = 1 if os.environ.get("REFLOW_BREAK_LLM") else 11434

    body = json.dumps({
        "model": "qwen2.5:3b",
        "system": SYSTEM_PROMPT,
        "prompt": clean_error,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }).encode()

    req = urllib.request.Request(
        f"http://localhost:{port}/api/generate",
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    return payload.get("response", "")


def _parse(text: str):
    """Models sometimes wrap JSON in fences or prose. Recover what we can."""
    if not text:
        return None
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    cause = data.get("cause")
    if cause not in KNOWN_CAUSES:
        return None
    conf = data.get("confidence", 0.5)
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.5
    return cause, conf


# Cache keyed on the redacted error text. Identical gateway strings are
# identical conditions, so this is correct behaviour, not just a speedup —
# it also cuts real model calls from ~3000/run to the number of unique
# error templates that actually exist.
_CACHE = {}


def diagnose(raw_error: str) -> dict:
    """
    Returns:
        {"cause": str, "confidence": float, "source": "llm"|"fallback",
         "redacted_input": str}
    """
    clean = redact(raw_error)
    assert not contains_pii(clean), "PII escaped redaction — refusing to send"

    if clean in _CACHE:
        return _CACHE[clean]

    try:
        text = _call_llm(clean)
        parsed = _parse(text) if text is not None else None
        if parsed:
            cause, conf = parsed
            result = {"cause": cause, "confidence": conf, "source": "llm",
                      "redacted_input": clean}
            _CACHE[clean] = result
            return result
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass   # model unreachable or misbehaving — degrade, do not crash

    cause, conf = classify_by_rules(clean)
    result = {"cause": cause, "confidence": conf, "source": "fallback",
              "redacted_input": clean}
    _CACHE[clean] = result
    return result