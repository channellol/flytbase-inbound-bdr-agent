"""Gemini wrapper: JSON-only responses, retries with backoff, no key leakage."""
from __future__ import annotations

import json
import os
import re
import threading
import time

from core.log import log
from core.web import backoff_sleep

_env = __import__("pathlib").Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
_client = None
_last_call = 0.0
MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "1"))
# stronger model for the two customer-facing writing steps (emails, AE handoff)
WRITER_MODEL = os.environ.get("GEMINI_WRITER_MODEL", "gemini-3.5-flash")
_lock = threading.Lock()
# if a model is overloaded (503), rotate to the next one instead of waiting it out
FALLBACKS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACKS", "gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.7-flash").split(",") if m.strip()]


class MissingKey(RuntimeError):
    pass


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise MissingKey("GEMINI_API_KEY is not set. Export it and run again.")
        from google import genai

        _client = genai.Client(api_key=key)
    return _client


def check_key() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        raise MissingKey("GEMINI_API_KEY is not set. Export it and run again.")


def scrub(obj):
    """Remove em/en dashes from every string the model returns (house style)."""
    if isinstance(obj, str):
        s = obj.replace(" — ", ", ").replace("—", ", ").replace("–", "-")
        return s
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)


SYSTEM = (
    "You are one step in an automated inbound sales pipeline for FlytBase, a drone autonomy "
    "software company. Respond with valid JSON only, matching the requested shape. "
    "Never invent facts, numbers, names or URLs. If something is not supported by the "
    "provided material, say 'not found' or 'unknown'. Never use em dashes."
)


def ask_json(prompt: str, stage: str, purpose: str, temperature: float = 0.2, retries: int = 8, model: str | None = None):
    """Call Gemini and return parsed JSON. Logs purpose and timing, never the key."""
    global _last_call
    from google.genai import types

    client = _get_client()
    chain = [model or MODEL] + [m for m in FALLBACKS if m != (model or MODEL)]
    idx = 0
    for attempt in range(retries):
        with _lock:  # space out request starts across threads (rate limits)
            wait = MIN_INTERVAL - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.time()
        t0 = time.time()
        use = chain[idx % len(chain)]
        thinking = (types.ThinkingConfig(thinking_level=os.environ.get("GEMINI_THINKING", "low"))
                    if use.startswith("gemini-3") else None)
        try:
            resp = client.models.generate_content(
                model=use,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    temperature=temperature,
                    response_mime_type="application/json",
                    thinking_config=thinking,
                ),
            )
            data = _parse_json(resp.text or "")
            if isinstance(data, list):  # some models wrap the object in a list
                if len(data) == 1 and isinstance(data[0], dict):
                    data = data[0]
                else:
                    raise json.JSONDecodeError("expected a JSON object", "", 0)
            log(f"LLM ok: {purpose} ({time.time() - t0:.1f}s, {use})", stage=stage)
            return scrub(data)
        except json.JSONDecodeError:
            log(f"LLM returned invalid JSON for {purpose}, retrying", level="warn", stage=stage)
        except Exception as e:
            msg = str(e)
            if os.environ.get("GEMINI_API_KEY", "@@") in msg:
                msg = msg.replace(os.environ["GEMINI_API_KEY"], "***")
            code = "429" if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) else type(e).__name__
            log(f"LLM error on {purpose} ({code}, {use}): {msg[:120]}", level="warn", stage=stage)
            if "API_KEY_INVALID" in msg or "PERMISSION_DENIED" in msg:
                raise
            if "503" in msg or "UNAVAILABLE" in msg or "404" in msg or code == "429":
                idx += 1  # model busy, retired or rate-limited: try the next model in the chain
                log(f"switching {purpose} to {chain[idx % len(chain)]}", level="warn", stage=stage)
                time.sleep(1 + attempt)
            else:
                backoff_sleep(attempt, base=2, cap=60)
    raise RuntimeError(f"LLM failed after {retries} attempts: {purpose}")


def ask_writer(prompt: str, stage: str, purpose: str, temperature: float = 0.4):
    """Customer-facing writing: try the stronger model, fall back to the default if it is busy."""
    try:
        return ask_json(prompt, stage, purpose, temperature, retries=2, model=WRITER_MODEL)
    except Exception as e:
        log(f"Writer model busy ({str(e)[:60]}), falling back to {MODEL}", level="warn", stage=stage)
        return ask_json(prompt, stage, purpose + " (fallback)", temperature)
