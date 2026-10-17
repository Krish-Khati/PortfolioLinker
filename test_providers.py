"""
Tests for the provider chain in providers.py.

These stub the adapters so no network call is made. They check the behaviour
that matters: a dead provider must not kill a run, and a run must fail loudly
only when nothing is left.

    python test_providers.py
"""

from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

import config
import providers

PASSES, FAILS = 0, 0


def check(label, got, want):
    global PASSES, FAILS
    if got == want:
        PASSES += 1
        print(f"  ok    {label}")
    else:
        FAILS += 1
        print(f"  FAIL  {label}\n          got : {got!r}\n          want: {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


def check_raises(label, exc_type, fn):
    try:
        fn()
    except exc_type as e:
        check_true(label, True)
        return e
    except Exception as e:  # noqa: BLE001
        check(label, type(e).__name__, exc_type.__name__)
        return None
    check(label, "no raise", exc_type.__name__)
    return None


print("\n1. order comes from LLM_PROVIDERS")
_real_env = config.env
config.env = lambda k, d="": {
    "LLM_PROVIDERS": "ollama, gemini , groq",
}.get(k, _real_env(k, d))
check("parsed, trimmed, lowercased", providers.provider_order(),
      ["ollama", "gemini", "groq"])
config.env = lambda k, d="": {
    "LLM_PROVIDERS": "",
}.get(k, _real_env(k, d))
check("empty falls back to default", providers.provider_order(),
      ["gemini", "groq", "ollama"])
config.env = _real_env

print("\n2. a dead provider is skipped, not fatal")
calls: list[str] = []


def stub_ok(prompt, system, model, **kw):
    calls.append(model)
    return '{"ok": true}'


_orig_text = providers.TEXT_ADAPTERS.copy()


def install(stub_map, available_map=None):
    """Replace adapters. Anything NOT named in stub_map gets a neutral stub that
    records the model, so a unit test can never make a real network call."""
    providers.TEXT_ADAPTERS.clear()
    for name, (fn, _, defaults, key) in _orig_text.items():
        providers.TEXT_ADAPTERS[name] = (
            stub_map.get(name, stub_ok),
            (available_map or {}).get(name, lambda: True),
            defaults, key,
        )


try:
    def boom(prompt, system, model, **kw):
        raise providers.ProviderError("gemini quota", fatal=True)

    calls.clear()
    install({"gemini": boom, "groq": stub_ok}, {"gemini": lambda: True,
                                                 "groq": lambda: True,
                                                 "ollama": lambda: False})
    out = providers.chain("p", "s", allowed=["gemini", "groq", "ollama"])
    check("fell through to groq", out, '{"ok": true}')
    check("tried gpt-oss-120b first", calls[0], "openai/gpt-oss-120b")

    print("\n3. all providers dead -> loud failure")
    def dead(prompt, system, model, **kw):
        raise providers.ProviderError("nope", fatal=True)

    install({n: dead for n in ("gemini", "groq", "ollama")},
            {n: (lambda: True) for n in ("gemini", "groq", "ollama")})
    e = check_raises("raises when nothing works", RuntimeError,
                     lambda: providers.chain("p", "s"))
    check_true("error names every provider",
               e is not None and all(n in str(e) for n in
                                     ("gemini", "groq", "ollama")))

    print("\n4. quota exhaustion surfaces as DailyTokenLimitError")
    def quota(prompt, system, model, **kw):
        raise providers.ProviderError("groq daily quota: 200000", fatal=True)

    install({"gemini": quota, "groq": quota},
            {"gemini": lambda: True, "groq": lambda: True,
             "ollama": lambda: False})
    check_raises("raises DailyTokenLimitError",
                 config.DailyTokenLimitError,
                 lambda: providers.chain("p", "s"))

    print("\n5. an unconfigured provider is fatal, not retried")
    install({"gemini": boom}, {"gemini": lambda: False, "groq": lambda: True,
                                "ollama": lambda: False})
    calls.clear()
    out = providers.chain("p", "s", allowed=["gemini", "groq", "ollama"])
    check("skipped unconfigured gemini and used groq", out, '{"ok": true}')

    print("\n6. model list comes from env, never hardcoded")
    config.env = lambda k, d="": {"GEMINI_MODEL": "gemini-custom"}.get(
        k, _real_env(k, d))
    check("override honoured", providers.models_for("gemini"),
          ["gemini-custom"])
    config.env = lambda k, d="": {"OLLAMA_MODEL": "llama3.1:8b"}.get(
        k, _real_env(k, d))
    check("ollama model from env", providers.models_for("ollama"),
          ["llama3.1:8b"])
    config.env = lambda k, d="": {"OLLAMA_MODEL": ""}.get(k, _real_env(k, d))
    check("ollama with no model configured", providers.models_for("ollama"), [])
    config.env = _real_env

    print("\n7. vision model lists differ from text lists")
    check("groq vision is qwen only", providers.models_for("groq", vision=True),
          ["qwen/qwen3.8-27b"])
    check_true("gemini vision non-empty",
               bool(providers.models_for("gemini", vision=True)))
    check_true("groq text excludes qwen-first",
               providers.models_for("groq")[0] == "openai/gpt-oss-120b")

    print("\n8. groq refuses images on a non-vision model")
    def groq_img(prompt, system, model, **kw):
        return "{}"
    install({"groq": groq_img}, {"groq": lambda: True})
    img = Path(tempfile.mkdtemp()) / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    # force a non-vision model id so the guard is what we are testing
    config.env = lambda k, d="": {"GROQ_VISION_MODEL": "openai/gpt-oss-120b"}.get(
        k, _real_env(k, d))
    e = check_raises("raises for non-vision model", providers.ProviderError,
                     lambda: providers.generate(
                         "groq", "p", "s", vision=True,
                         image_paths=[str(img)], temperature=0.3,
                         max_tokens=100, as_json=True))
    check_true("explains the vision restriction",
               e is not None and "cannot take images" in str(e))
    config.env = _real_env

    print("\n9. images are validated before they are sent")
    providers._encode_image  # exists
    check_raises("missing file is fatal", providers.ProviderError,
                 lambda: providers._encode_image("does/not/exist.jpg"))
    big = Path(tempfile.mkdtemp()) / "big.jpg"
    big.write_bytes(b"x" * 4096)
    real_cap = config.MAX_IMAGE_BYTES
    config.MAX_IMAGE_BYTES = 1024          # shrink the cap for the test
    try:
        e2 = check_raises("oversize rejected before upload",
                          providers.ProviderError,
                          lambda: providers._encode_image(big))
        check_true("size limit named", e2 is not None and "MB" in str(e2))
    finally:
        config.MAX_IMAGE_BYTES = real_cap

    print("\n10. usage is attributed per provider")
    config.USAGE.clear()
    config.USAGE.update({"prompt_tokens": 0, "completion_tokens": 0,
                         "calls": 0, "by_provider": {}})
    providers._record("gemini", 100, 50)
    providers._record("gemini", 10, 5)
    providers._record("groq", 200, 100)
    check("total calls", config.USAGE["calls"], 3)
    check("total completion tokens", config.USAGE["completion_tokens"], 155)
    check("per-provider gemini calls",
          config.USAGE["by_provider"]["gemini"]["calls"], 2)
    check("per-provider groq tokens",
          config.USAGE["by_provider"]["groq"]["tokens"], 300)
    check_true("usage_report names providers",
               "gemini" in config.usage_report()
               and "groq" in config.usage_report())
finally:
    providers.TEXT_ADAPTERS.clear()
    providers.TEXT_ADAPTERS.update(_orig_text)
    config.env = _real_env

print("\n" + "=" * 52)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 52)
sys.exit(1 if FAILS else 0)