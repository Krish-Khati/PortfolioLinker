"""
Provider adapters for LLM calls.

Three providers, plain REST, no extra dependencies:

  gemini  Google AI Studio free tier. Multimodal. ~500 req/day on flash-lite.
  groq    Fast, but 200,000 tokens/day org-wide on the free tier.
  ollama  Local, no quota at all. Last resort.

Every adapter exposes the same two calls:
    generate(prompt, system, model, temperature, max_tokens, as_json)
    generate_vision(prompt, images, system, ...)   -> returns None if unsupported

Model names are never hardcoded into the call path - they come from .env so
they can be changed without touching code when a provider retires a model.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

import config

GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
OLLAMA_API = "http://localhost:11434/api/chat"
OLLAMA_HOST = "http://localhost:11434"

GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]
GEMINI_VISION_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]

VISION_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ProviderError(RuntimeError):
    """A provider failed.

    Three failures need three different responses, so they are told apart:

    fatal     - this provider is done (no key, auth rejected, quota gone).
                Do not call it again; move to the next provider.
    transient - this looks temporary (503 "high demand", a timeout). Wait a
                little and try the SAME model again.
    unusable  - the provider answered, but the answer was not usable (most
                often malformed JSON). Another model may answer better, so
                keep walking down the list instead of handing it back.
    """

    def __init__(self, message: str, *, fatal: bool = False,
                 transient: bool = False, unusable: bool = False):
        super().__init__(message)
        self.fatal = fatal
        self.transient = transient
        self.unusable = unusable


class NoUsableOutput(RuntimeError):
    """Every provider answered and no answer was usable.

    This is deliberately NOT the same as "every provider died". The providers
    are alive - they just all replied with something unusable. The right
    response is to wait a moment and ask again, so it gets its own type and
    the caller can retry the chain instead of writing the topic off.
    """


# How hard one model is tried before the next model in the list is tried.
#
# A 503 from a free-tier model is a queue, not a rejection. Gemini's own body
# says so: "This model is currently experiencing high demand. Spikes in demand
# are usually temporary. Please try again later." The old code gave that ONE
# three-second retry and then abandoned the provider, so a topic could be
# lost to a busy minute.
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF = (4, 10)

# Markers for failures that look temporary when the adapter could not say so
# itself (a generic exception, a wrapped error).
TRANSIENT_MARKERS = (
    "503", "502", "504", "500", "high demand", "overloaded", "temporarily",
    "timeout", "timed out", "connection reset", "connection aborted",
    "connection refused", "unavailable",
)


def is_transient(exc: Exception) -> bool:
    """True when the failure looks like a hiccup rather than a rejection."""
    msg = str(exc).lower()
    return any(marker in msg for marker in TRANSIENT_MARKERS)


def _backoff(attempt: int) -> int:
    """Seconds to wait before the attempt-th retry of one model."""
    return TRANSIENT_BACKOFF[min(attempt - 1, len(TRANSIENT_BACKOFF) - 1)]


# ------------------------------------------------------------------- helpers
def _strip_reasoning(text: str) -> str:
    import re

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    # gpt-oss and qwen both sometimes emit a leading analysis block
    text = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.S | _RE.I)
    return text.strip()


_RE = __import__("re")

_PRIORITIES = [("gemini", 0), ("resource_exhausted", 0), ("429", 0)]


def _is_quota(exc: Any) -> bool:
    msg = str(exc).lower()
    return ("tokens per day" in msg or "tpd" in msg
            or "rate limit" in msg or "429" in msg
            or "resource_exhausted" in msg or "resource exhausted" in msg)


def _record(provider: str, prompt_tok: int, out_tok: int) -> None:
    config.USAGE["calls"] += 1
    config.USAGE["prompt_tokens"] += prompt_tok
    config.USAGE["completion_tokens"] += out_tok
    config.USAGE.setdefault("by_provider", {})
    b = config.USAGE["by_provider"].setdefault(
        provider, {"calls": 0, "tokens": 0})
    b["calls"] += 1
    b["tokens"] += prompt_tok + out_tok


def _encode_image(path: str | Path) -> tuple[str, str]:
    p = Path(path)
    if not p.exists():
        raise ProviderError(f"image not found: {p}", fatal=True)
    if p.stat().st_size > config.MAX_IMAGE_BYTES:
        raise ProviderError(
            f"{p.name} is {p.stat().st_size / 1e6:.1f} MB, over the "
            f"{config.MAX_IMAGE_BYTES / 1e6:.0f} MB limit", fatal=True)
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    if mime not in VISION_MIME:
        raise ProviderError(f"unsupported image type {mime}", fatal=True)
    return base64.b64encode(p.read_bytes()).decode(), mime


def _candidates(env_key: str, defaults: list[str]) -> list[str]:
    override = config.env(env_key)
    return [override] if override else list(defaults)


# ================================================================== gemini
def gemini_generate(prompt: str, system: str, model: str, *, temperature: float,
                    max_tokens: int, as_json: bool, images=None) -> str:
    key = config.env("GEMINI_API_KEY")
    if not key:
        raise ProviderError("GEMINI_API_KEY not set", fatal=True)

    gen: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    if as_json:
        gen["responseMimeType"] = "application/json"

    parts: list[dict[str, Any]] = []
    if images:
        for path in images[:config.MAX_IMAGES_PER_REQUEST]:
            data, mime = _encode_image(path)
            parts.append({"inline_data": {"mime_type": mime, "data": data}})
    parts.append({"text": prompt})

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    import requests

    try:
        resp = requests.post(
            f"{GEMINI_API}/{model}:generateContent",
            params={"key": key}, json=body, timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"gemini network error: {exc}",
                            transient=True) from exc

    if resp.status_code == 429:
        raise ProviderError(f"gemini quota: {resp.text[:180]}", fatal=True)
    if resp.status_code in (401, 403):
        raise ProviderError(f"gemini auth failed: {resp.text[:180]}", fatal=True)
    if resp.status_code != 200:
        # 5xx is Gemini saying "busy, try again". It is not a rejection of
        # the request and not a reason to give the topic up.
        raise ProviderError(f"gemini {resp.status_code}: {resp.text[:180]}",
                            transient=resp.status_code in (500, 502, 503, 504))

    data = resp.json()
    # blocked candidates come back 200 with no usable parts
    usage = data.get("usageMetadata") or {}
    _record("gemini", usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0))

    chunks: list[str] = []
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if "text" in part:
                chunks.append(part["text"])
    out = _strip_reasoning("".join(chunks))
    if not out:
        raise ProviderError("gemini returned an empty completion")
    if as_json and not out.lstrip().startswith("{"):
        print("[warn] gemini ignored JSON mime type; parsing leniently")
    return out


def gemini_available() -> bool:
    return bool(config.env("GEMINI_API_KEY"))


# ==================================================================== groq
def groq_generate(prompt: str, system: str, model: str, *, temperature: float,
                  max_tokens: int, as_json: bool, images=None) -> str:
    key = config.env("GROQ_API_KEY")
    if not key:
        raise ProviderError("GROQ_API_KEY not set", fatal=True)

    if images:
        # Groq vision is restricted to qwen3.8-27b; handled by the caller.
        if model not in config.VISION_MODELS:
            raise ProviderError(
                f"{model} cannot take images on Groq", fatal=True)
        content: list[dict[str, Any]] = []
        for path in images[:config.MAX_IMAGES_PER_REQUEST]:
            data, mime = _encode_image(path)
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"}})
        content.append({"type": "text", "text": prompt})
        user_msg = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": prompt}

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, user_msg],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if as_json:
        payload["response_format"] = {"type": "json_object"}

    import requests

    try:
        resp = requests.post(GROQ_API, json=payload, timeout=180,
                             headers={"Authorization": f"Bearer {key}"})
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"groq network error: {exc}",
                            transient=True) from exc

    if resp.status_code in (401, 403):
        raise ProviderError(f"groq auth failed: {resp.text[:180]}", fatal=True)
    if resp.status_code == 429:
        msg = resp.text
        daily = "tokens per day" in msg.lower() or "tpd" in msg.lower()
        # A daily quota is gone until midnight and retrying is pointless. A
        # plain rate limit is a queue: wait and try the same model again.
        raise ProviderError(f"groq {'daily quota' if daily else 'rate limit'}: "
                            f"{msg[:160]}", fatal=daily, transient=not daily)
    if resp.status_code != 200:
        raise ProviderError(f"groq {resp.status_code}: {resp.text[:180]}",
                            transient=resp.status_code in (500, 502, 503, 504))

    data = resp.json()
    u = data.get("usage") or {}
    _record("groq", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))

    out = _strip_reasoning(
        ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    if not out:
        raise ProviderError("groq returned an empty completion")
    if as_json and not out.lstrip().startswith("{"):
        print("[warn] groq ignored JSON mode; parsing leniently")
    return out


def groq_available() -> bool:
    return bool(config.env("GROQ_API_KEY"))


# ================================================================== ollama
def ollama_available(timeout: float = 1.5) -> bool:
    import requests

    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def ollama_generate(prompt: str, system: str, model: str, *, temperature: float,
                    max_tokens: int, as_json: bool, images=None) -> str:
    import requests

    msg: dict[str, Any] = {"role": "user", "content": prompt}
    if images:
        encoded = []
        for path in images[:config.MAX_IMAGES_PER_REQUEST]:
            data, _ = _encode_image(path)
            encoded.append(data)
        msg["images"] = encoded

    payload: dict[str, Any] = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [msg],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if as_json:
        payload["format"] = "json"

    try:
        resp = requests.post(OLLAMA_API, json=payload, timeout=900)
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"ollama not reachable: {exc}", fatal=True) from exc
    if resp.status_code != 200:
        raise ProviderError(f"ollama {resp.status_code}: {resp.text[:180]}")

    data = resp.json()
    _record("ollama", data.get("prompt_eval_count", 0),
            data.get("eval_count", 0))
    out = _strip_reasoning((data.get("message") or {}).get("content") or "")
    if not out:
        raise ProviderError("ollama returned an empty completion")
    return out


# ================================================================ registry
TEXT_ADAPTERS = {
    "gemini": (gemini_generate, gemini_available, GEMINI_MODELS, "GEMINI_MODEL"),
    "groq": (groq_generate, groq_available, config.MODEL_FALLBACKS, "GROQ_MODEL"),
    "ollama": (ollama_generate, ollama_available, None, "OLLAMA_MODEL"),
}

VISION_ADAPTERS = {
    "gemini": (gemini_generate, gemini_available, GEMINI_VISION_MODELS,
               "GEMINI_VISION_MODEL"),
    "groq": (groq_generate, groq_available, config.VISION_MODELS,
             "GROQ_VISION_MODEL"),
    "ollama": (ollama_generate, ollama_available, None, "OLLAMA_MODEL"),
}


DEFAULT_PROVIDERS = ["gemini", "groq", "ollama"]


def provider_order() -> list[str]:
    raw = config.env("LLM_PROVIDERS")
    order = [p.strip().lower() for p in raw.split(",") if p.strip()] if raw else []
    return order or list(DEFAULT_PROVIDERS)


def models_for(provider: str, vision: bool = False) -> list[str]:
    table = VISION_ADAPTERS if vision else TEXT_ADAPTERS
    if provider not in table:
        return []
    _, _, defaults, env_key = table[provider]
    if defaults is None:
        m = config.env(env_key)
        return [m] if m else []
    return _candidates(env_key, defaults)


def generate(provider: str, prompt: str, system: str, *, vision: bool = False,
             image_paths: list[str] | None = None,
             temperature: float = 0.85, max_tokens: int = 3200,
             as_json: bool = False,
             validate: Callable[[str], bool] | None = None) -> str:
    """Call one provider through its model list, with one retry on throttle.

    Every model in the list gets its own retry budget, and a model that
    answers badly does not end the chain: the next model is tried, and if the
    whole provider is exhausted the caller moves to the next provider.

    The old version raised out of the model loop on the first non-fatal
    failure, so GEMINI_MODELS' second and third entries were never used at
    all, and the single retry it did allow was spent on the first model. A
    503 on gemini-3.5-flash-lite therefore took down the whole provider
    even though two more Gemini models were sitting right there in the list.

    `validate` is an optional predicate on the raw reply. When it is given, a
    provider whose answer fails it is treated as a failure, so a reply of
    malformed JSON moves on to the next model instead of being returned as if
    it were a success.
    """
    table = VISION_ADAPTERS if vision else TEXT_ADAPTERS
    if provider not in table:
        raise ProviderError(f"unknown provider {provider!r}", fatal=True)
    fn, available, _defaults, _k = table[provider]

    if not available():
        raise ProviderError(f"{provider} is not configured", fatal=True)

    models = models_for(provider, vision=vision)
    if not models:
        raise ProviderError(f"no model configured for {provider}", fatal=True)

    last: Exception | None = None
    unusable_count = 0
    dead_count = 0

    for model in models:
        attempt = 0
        while True:
            try:
                out = fn(prompt, system, model, temperature=temperature,
                         max_tokens=max_tokens, as_json=as_json,
                         images=image_paths)
            except ProviderError as exc:
                last = exc
                if exc.fatal:
                    raise
                attempt += 1
                if exc.transient and attempt <= TRANSIENT_RETRIES:
                    delay = _backoff(attempt)
                    print(f"[warn] {provider}/{model} transient failure "
                          f"({exc}); retrying in {delay}s")
                    time.sleep(delay)
                    continue
                dead_count += 1
                print(f"[warn] {provider}/{model} failed: {exc}")
                break
            except Exception as exc:  # noqa: BLE001
                last = ProviderError(f"{provider}/{model} error: {exc}")
                attempt += 1
                if is_transient(exc) and attempt <= TRANSIENT_RETRIES:
                    delay = _backoff(attempt)
                    print(f"[warn] {provider}/{model} transient failure "
                          f"({exc}); retrying in {delay}s")
                    time.sleep(delay)
                    continue
                dead_count += 1
                print(f"[warn] {provider}/{model} failed: {last}")
                break

            # The provider answered. Hold it to the caller's standard: a model
            # that replies with malformed JSON is not a success, and returning
            # it ends the chain before the next model or provider ever gets
            # a turn.
            if validate is not None and not validate(out):
                last = ProviderError(
                    f"{provider}/{model} returned unusable output",
                    unusable=True)
                unusable_count += 1
                print(f"[warn] {provider}/{model} returned unusable output; "
                      "trying the next model")
                break

            return out

    raise ProviderError(f"{provider}: all models failed: {last}",
                        unusable=(dead_count == 0 and unusable_count > 0))


def chain(prompt: str, system: str, *, vision: bool = False,
          image_paths: list[str] | None = None, temperature: float = 0.85,
          max_tokens: int = 3200, as_json: bool = False,
          allowed: list[str] | None = None,
          validate: Callable[[str], bool] | None = None) -> str:
    """Try each provider in order. Returns the first usable success.

    `validate` is an optional predicate on the raw reply, passed down to
    every provider. A provider whose answer fails it is skipped exactly like
    a dead provider, so one bad reply no longer ends the chain with garbage.

    Raises DailyTokenLimitError only when every provider is unavailable, so a
    single exhausted quota no longer kills the run, and NoUsableOutput when
    every provider answered but no answer was usable.
    """
    order = [p for p in provider_order() if (allowed is None or p in allowed)]
    errors: list[str] = []
    all_unusable = True

    for provider in order:
        try:
            return generate(provider, prompt, system, vision=vision,
                            image_paths=image_paths, temperature=temperature,
                            max_tokens=max_tokens, as_json=as_json,
                            validate=validate)
        except ProviderError as exc:
            errors.append(f"{provider}: {exc}")
            print(f"[warn] skipping {provider}: {exc}")
            if not exc.unusable:
                all_unusable = False
            continue

    if any("daily quota" in e for e in errors) or any("quota" in e for e in errors):
        raise config.DailyTokenLimitError(
            "Every configured LLM provider is exhausted or unavailable:\n  "
            + "\n  ".join(errors))
    if all_unusable and errors:
        raise NoUsableOutput(
            "Every provider answered, but no answer was usable:\n  "
            + "\n  ".join(errors))
    raise RuntimeError("All LLM providers failed:\n  " + "\n  ".join(errors))