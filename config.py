"""
Shared config: .env loading, paths, Groq client, and the verified model list.

Every module imports from here so secrets stay in exactly one place.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

# Windows consoles default to cp1252 and blow up on em-dashes/curly quotes
# coming back from the model. Force UTF-8 before anything prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
LEDGER_PATH = ROOT / "posted_topics.json"
DRAFTS_DIR = ROOT / "drafts"
IMAGES_DIR = ROOT / "images"

# Blogger API v3
BLOGGER_API = "https://blogger.googleapis.com/v3"
BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"

# Model preference order, best-first. Verified live against Groq's /models
# endpoint. Override with GROQ_MODEL in .env.
#
# Order rationale: qwen/qwen3.8-27b is the only vision-capable model we use,
# but on the free tier it is rate-limited on most calls. So TEXT generation
# leads with the OpenAI open-weight models, which are far less throttled, and
# vision is pinned to qwen separately (see VISION_MODELS).
MODEL_FALLBACKS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]

# Vision-capable models. Per console.groq.com/docs/vision:
#   qwen/qwen3.8-27b - 27B multimodal, 131K ctx, 20MB/image, 3 images/request
#
# meta-models/muse-spark-open was also listed as vision-capable but is limited
# preview and not reliably available, so it is not used as a fallback.
VISION_MODELS = ["qwen/qwen3.8-27b"]

# If vision fails because qwen is throttled, fall back to tag-based alt text
# rather than failing the run. Set VISION_STRICT=true to change that.
VISION_STRICT = False

# Groq models that emit a reasoning block; we strip it before parsing.
REASONING_MODELS = {"qwen/qwen3.8-27b"}

# Vision-capable models on Groq. Verified against console.groq.com/docs/vision.
# qwen only: it is the sole model here that accepts image input, and it is
# heavily rate-limited on the free tier, so callers must tolerate failure.
# Documented per-request limits.
MAX_IMAGES_PER_REQUEST = 3
MAX_IMAGE_BYTES = 20 * 1024 * 1024
VISION_IMAGE_TOKENS = 2048

REQUIRED_KEYS = [
    "GROQ_API_KEY",
    "PIXABAY_API_KEY",
    "BLOGGER_CLIENT_ID",
    "BLOGGER_CLIENT_SECRET",
    "BLOGGER_REFRESH_TOKEN",
    "BLOGGER_BLOG_ID",
    "PORTFOLIO_URL",
    "PORTFOLIO_BLOG_PAGE_URL",
    "GITHUB_PROFILE_URL",
]


def _boot() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(
            f"[!] {ENV_PATH} not found. Copy .env.example -> .env and fill it in."
        )
    load_dotenv(ENV_PATH)
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k, "").strip()]
    if missing:
        raise SystemExit(
            "[!] Missing/empty in .env:\n    " + "\n    ".join(missing)
        )
    for d in (DRAFTS_DIR, IMAGES_DIR):
        d.mkdir(exist_ok=True)


_boot()


# ---------------------------------------------------------------- accessors
def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def publish_live() -> bool:
    """True only when the operator has explicitly flipped PUBLISH_LIVE=true."""
    return env("PUBLISH_LIVE", "false").lower() in {"1", "true", "yes"}


def model_name() -> str:
    return env("GROQ_MODEL") or MODEL_FALLBACKS[0]


# ------------------------------------------------------------- groq client
_client = None


def groq_client():
    """Lazily built Groq client. Keeps the API key out of module import time."""
    global _client
    if _client is None:
        from groq import Groq

        _client = Groq(api_key=env("GROQ_API_KEY"))
    return _client


class DailyTokenLimitError(RuntimeError):
    """Raised when the org has exhausted its tokens-per-day allowance.

    Retrying or falling back cannot help: the limit is organisation-wide, so
    every model is dead until the daily reset. Fail fast and say so.
    """


def _is_daily_token_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("tokens per day" in msg or "tpd" in msg
            or "per day (tpd)" in msg or "tokens per day" in msg)


def _is_rate_limit(exc: Exception) -> bool:
    if _is_daily_token_limit(exc):
        return False
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "429" in msg


# Token accounting for the current process, so a run can report what it cost.
USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def _record_usage(resp) -> None:
    u = getattr(resp, "usage", None)
    if not u:
        return
    USAGE["calls"] += 1
    USAGE["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
    USAGE["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0


def usage_report() -> str:
    return (f"{USAGE['calls']} calls, "
            f"{USAGE['prompt_tokens']:,} prompt + "
            f"{USAGE['completion_tokens']:,} completion tokens")


def _strip_reasoning(text: str) -> str:
    import re

    # Qwen-style reasoning models prepend a <think>...</think> block.
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()


def chat(prompt: str, system: str, *, temperature: float = 0.85,
         max_tokens: int = 3200, as_json: bool = False,
         allowed: list[str] | None = None,
         validate: Callable[[str], bool] | None = None) -> str:
    """Text generation across the configured provider chain.

    Order comes from LLM_PROVIDERS in .env (default gemini -> groq -> ollama).
    A provider that is exhausted, misconfigured or offline is skipped and the
    next one is tried; only an all-providers failure raises.

    `validate` is an optional predicate on the raw reply. When it is given, a
    provider whose reply fails it is skipped like a dead provider, so a
    malformed answer does not end the chain with garbage in hand.
    """
    import providers  # local import: providers imports config

    return providers.chain(prompt, system, vision=False, temperature=temperature,
                           max_tokens=max_tokens, as_json=as_json,
                           allowed=allowed, validate=validate)


def chat_vision(prompt: str, image_paths: list[str | Path], system: str, *,
                temperature: float = 0.3, max_tokens: int = 600,
                as_json: bool = False,
                validate: Callable[[str], bool] | None = None) -> str:
    """Vision generation. Only providers with a multimodal model are tried.

    Groq vision is pinned to qwen3.8-27b; Gemini flash models are natively
    multimodal; Ollama accepts images too. Falls back to a clear error so
    callers can degrade to tag-based alt text rather than crash.
    """
    import providers

    return providers.chain(prompt, system, vision=True,
                           image_paths=[str(p) for p in image_paths],
                           temperature=temperature, max_tokens=max_tokens,
                           as_json=as_json, validate=validate)


def usage_report() -> str:
    base = (f"{USAGE['calls']} calls, {USAGE['prompt_tokens']:,} prompt + "
            f"{USAGE['completion_tokens']:,} completion tokens")
    by = USAGE.get("by_provider") or {}
    if by:
        detail = ", ".join(f"{k}: {v['calls']} calls/{v['tokens']:,} tok"
                            for k, v in sorted(by.items()))
        base += f"  [{detail}]"
    return base
