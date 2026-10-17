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
from typing import Any

import config

GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
OLLAMA_API = "http://localhost:11434/api/chat"
OLLAMA_HOST = "http://localhost:11434"

GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]
GEMINI_VISION_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]

VISION_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ProviderError(RuntimeError):
    """A provider failed. `fatal` means do not retry this provider at all."""

    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


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
        raise ProviderError(f"gemini network error: {exc}") from exc

    if resp.status_code == 429:
        raise ProviderError(f"gemini quota: {resp.text[:180]}", fatal=True)
    if resp.status_code in (401, 403):
        raise ProviderError(f"gemini auth failed: {resp.text[:180]}", fatal=True)
    if resp.status_code != 200:
        raise ProviderError(f"gemini {resp.status_code}: {resp.text[:180]}")

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
        raise ProviderError(f"groq network error: {exc}") from exc

    if resp.status_code in (401, 403):
        raise ProviderError(f"groq auth failed: {resp.text[:180]}", fatal=True)
    if resp.status_code == 429:
        msg = resp.text
        daily = "tokens per day" in msg.lower() or "tpd" in msg.lower()
        raise ProviderError(f"groq {'daily quota' if daily else 'rate limit'}: "
                            f"{msg[:160]}", fatal=daily)
    if resp.status_code != 200:
        raise ProviderError(f"groq {resp.status_code}: {resp.text[:180]}")

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
             as_json: bool = False) -> str:
    """Call one provider through its model list, with one retry on throttle."""
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
    throttle_retries = 0
    for model in models:
        while True:
            try:
                return fn(prompt, system, model, temperature=temperature,
                          max_tokens=max_tokens, as_json=as_json,
                          images=image_paths)
            except ProviderError as exc:
                last = exc
                if exc.fatal:
                    raise
                throttle_retries += 1
                if throttle_retries <= 1:
                    print(f"[warn] {provider}/{model} throttled; retrying once")
                    time.sleep(3)
                    continue
                raise ProviderError(f"{provider}/{model} failed: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"{provider}/{model} error: {exc}") from exc
    raise ProviderError(f"{provider}: all models failed: {last}")


def chain(prompt: str, system: str, *, vision: bool = False,
          image_paths: list[str] | None = None, temperature: float = 0.85,
          max_tokens: int = 3200, as_json: bool = False,
          allowed: list[str] | None = None) -> str:
    """Try each provider in order. Returns the first success.

    Raises DailyTokenLimitError only when every provider is unavailable, so a
    single exhausted quota no longer kills the run.
    """
    order = [p for p in provider_order() if (allowed is None or p in allowed)]
    errors: list[str] = []

    for provider in order:
        try:
            return generate(provider, prompt, system, vision=vision,
                            image_paths=image_paths, temperature=temperature,
                            max_tokens=max_tokens, as_json=as_json)
        except ProviderError as exc:
            errors.append(f"{provider}: {exc}")
            print(f"[warn] skipping {provider}: {exc}")
            continue

    if any("daily quota" in e for e in errors) or any("quota" in e for e in errors):
        raise config.DailyTokenLimitError(
            "Every configured LLM provider is exhausted or unavailable:\n  "
            + "\n  ".join(errors))
    raise RuntimeError("All LLM providers failed:\n  " + "\n  ".join(errors))