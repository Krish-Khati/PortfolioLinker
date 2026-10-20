"""
Tests for the provider fallback chain and the JSON salvage around it.

The failure these cover is real: a GitHub Actions run lost a whole topic to a
Gemini 503 ("high demand") and two replies of malformed JSON. The old chain
gave that model one three-second retry, then abandoned the provider - even
though two more Gemini models were sitting in the list, and even though a
provider that answers with broken JSON was treated as a success.

    python test_fallback.py
"""

from __future__ import annotations

import json
import sys

import config
import generate_post as G
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


_orig_text = providers.TEXT_ADAPTERS.copy()
_orig_backoff = providers.TRANSIENT_BACKOFF
_orig_glitch = G.GLITCH_BACKOFF


def install(stub_map, available_map=None):
    """Replace adapters. Anything NOT named in stub_map gets a neutral stub
    that records the model, so a unit test can never make a real call."""
    providers.TEXT_ADAPTERS.clear()
    for name, (fn, _, defaults, key) in _orig_text.items():
        providers.TEXT_ADAPTERS[name] = (
            stub_map.get(name, lambda p, s, m, **kw: '{"ok": true}'),
            (available_map or {}).get(name, lambda: True),
            defaults, key,
        )


def restore():
    providers.TEXT_ADAPTERS.clear()
    providers.TEXT_ADAPTERS.update(_orig_text)


# never actually sleep in a test
providers.TRANSIENT_BACKOFF = (0, 0)
G.GLITCH_BACKOFF = 0

try:
    print("\n1. a transient 503 does not abandon the provider")
    calls: list[str] = []

    def flaky(prompt, system, model, **kw):
        calls.append(model)
        if model == "gemini-3.5-flash-lite":
            raise providers.ProviderError(
                "gemini 503: high demand", transient=True)
        return '{"ok": true}'

    calls.clear()
    install({"gemini": flaky}, {"gemini": lambda: True,
                                 "groq": lambda: True,
                                 "ollama": lambda: False})
    out = providers.chain("p", "s", allowed=["gemini", "groq"])
    check("the next Gemini model was used", out, '{"ok": true}')
    check_true("the second model was reached",
               "gemini-3.6-flash" in calls)
    check_true("the busy model was retried, not given up on",
               calls.count("gemini-3.5-flash-lite") == 3)

    print("\n2. every model gets its own retry budget")
    calls.clear()

    def two_models(prompt, system, model, **kw):
        calls.append(model)
        if model == "gemini-3.5-flash-lite":
            raise providers.ProviderError("gemini 503", transient=True)
        if model == "gemini-3.6-flash":
            raise providers.ProviderError("gemini 503", transient=True)
        return '{"ok": true}'

    install({"gemini": two_models}, {"gemini": lambda: True,
                                     "groq": lambda: True,
                                     "ollama": lambda: False})
    out = providers.chain("p", "s", allowed=["gemini", "groq"])
    check("third model answered", out, '{"ok": true}')
    check("the budget did not carry over between models",
          calls.count("gemini-3.6-flash"), 3)

    print("\n3. a non-transient failure moves on without burning retries")
    calls.clear()

    def hard_fail(prompt, system, model, **kw):
        calls.append(model)
        raise providers.ProviderError("gemini 400: bad request")

    install({"gemini": hard_fail}, {"gemini": lambda: True,
                                    "groq": lambda: True,
                                    "ollama": lambda: False})
    out = providers.chain("p", "s", allowed=["gemini", "groq"])
    check("fell through to groq", out, '{"ok": true}')
    check("the bad model was tried once each", calls.count("gemini-3.5-flash-lite"), 1)

    print("\n4. a provider that answers with unusable output is not a success")
    calls.clear()

    def garbage_then_good(prompt, system, model, **kw):
        calls.append(model)
        if model == "gemini-3.5-flash-lite":
            return "sorry, I cannot do that"
        return '{"ok": true}'

    install({"gemini": garbage_then_good}, {"gemini": lambda: True,
                                            "groq": lambda: True,
                                            "ollama": lambda: False})
    out = providers.chain("p", "s", allowed=["gemini", "groq"],
                          validate=lambda s: s.startswith("{"))
    check("the next model was used instead", out, '{"ok": true}')
    check_true("the bad reply did not end the chain",
               "gemini-3.6-flash" in calls)

    print("\n5. unusable output from every provider is its own error")
    install({"gemini": lambda p, s, m, **kw: "nope",
             "groq": lambda p, s, m, **kw: "also nope"},
            {"gemini": lambda: True, "groq": lambda: True,
             "ollama": lambda: False})
    e = check_raises("raises NoUsableOutput", providers.NoUsableOutput,
                     lambda: providers.chain("p", "s", allowed=["gemini", "groq"],
                                             validate=lambda s: False))
    check_true("the message names the problem",
               e is not None and "no answer was usable" in str(e))

    print("\n6. a dead provider is not a usability failure")
    def dead(prompt, system, model, **kw):
        raise providers.ProviderError("gemini 400: bad request")

    install({"gemini": dead, "groq": lambda p, s, m, **kw: "nope"},
            {"gemini": lambda: True, "groq": lambda: True,
             "ollama": lambda: False})
    e2 = check_raises("raises RuntimeError, not NoUsableOutput", RuntimeError,
                      lambda: providers.chain("p", "s", allowed=["gemini", "groq"],
                                              validate=lambda s: False))
    check_true("not a NoUsableOutput",
               e2 is not None and not isinstance(e2, providers.NoUsableOutput))

    print("\n7. quota still surfaces as DailyTokenLimitError")
    def quota(prompt, system, model, **kw):
        raise providers.ProviderError("groq daily quota: 200000", fatal=True)

    install({"gemini": quota, "groq": quota},
            {"gemini": lambda: True, "groq": lambda: True,
             "ollama": lambda: False})
    check_raises("raises DailyTokenLimitError",
                 config.DailyTokenLimitError,
                 lambda: providers.chain("p", "s"))

    print("\n8. malformed JSON is repaired instead of wasting a round")
    check("trailing comma is repaired",
          G._parse('{"a": 1,}'), {"a": 1})
    check("trailing comma in an array is repaired",
          G._parse('{"a": [1, 2,]}'), {"a": [1, 2]})
    check("a raw newline inside a string is repaired",
          G._parse('{"a": "line one\nline two"}'),
          {"a": "line one\nline two"})
    check("a comma inside a string is left alone",
          G._parse('{"a": "x, }"}'), {"a": "x, }"})
    check("still rejects prose", G._parse("no json here"), None)
    check("still rejects truncated json", G._parse('{"a": 1'), None)
    check("still rejects a bare array", G._parse("[1, 2, 3]"), None)

    print("\n9. a chain-wide glitch does not consume the round")
    restore()
    _real_chat = config.chat
    seen = {"n": 0}

    def glitchy(prompt, system, **kw):
        seen["n"] += 1
        if seen["n"] == 1:
            raise providers.NoUsableOutput("every provider answered badly")
        return '{"ok": true}'

    try:
        config.chat = glitchy
        out = G._ask_for_json("p", "s", temperature=0.9, max_tokens=100)
        check("the retry returned the good reply", out, '{"ok": true}')
        check("it asked twice", seen["n"], 2)
    finally:
        config.chat = _real_chat

    print("\n10. a permanent glitch is reported, not swallowed")
    def always_bad(prompt, system, **kw):
        raise providers.NoUsableOutput("every provider answered badly")

    try:
        config.chat = always_bad
        check_raises("raises after the retries", providers.NoUsableOutput,
                     lambda: G._ask_for_json("p", "s", temperature=0.9,
                                             max_tokens=100))
    finally:
        config.chat = _real_chat

    print("\n11. the gate only accepts a reply it can read")
    import quality_gate
    check_true("a verdict with scores is accepted",
               quality_gate._verdict_ok(
                   '{"scores": {"ai_slop": {"score": 1}}, "problems": []}'))
    check_true("a reply with no scores is rejected",
               not quality_gate._verdict_ok('{"verdict": "pass"}'))
    check_true("a broken reply is rejected",
               not quality_gate._verdict_ok("not json at all"))
    check_true("a reply with a raw newline is still read",
               quality_gate._verdict_ok(
                   '{"scores": {"ai_slop": {"score": 1}}, "note": "a\nb"}'))

    print("\n12. the exact CI failure no longer wastes the topic")
    # What actually happened on 2026-10-19: Gemini answered with a 503
    # ("high demand"), the fallback replied with malformed JSON, and the
    # round was gone. Here Gemini is down for good and the fallback is the
    # only thing left - the topic must still come out the other end.
    good_post = {
        "title": "A Post That Survived The Outage",
        "slug": "a-post-that-survived-the-outage",
        "lede": "The provider was down and the fallback answered anyway.",
        "sections": [
            {"heading": "First", "paragraphs": ["one [[LINK1]] two"]},
            {"heading": "Second", "paragraphs": ["three four"]},
            {"heading": "Third", "paragraphs": ["five [[LINK2]] six"]},
        ],
        "backlinks": [
            {"id": "LINK1", "anchor": "the finished work",
             "target": "projects", "placement": "body"},
            {"id": "LINK2", "anchor": "where I write it up",
             "target": "blog", "placement": "close"},
        ],
        "image_query": "desk with notebook",
        "alt_text": "a desk with a notebook",
        "meta_description": "x" * 155,
        "tags": ["a", "b", "c"],
    }

    def gemini_down(prompt, system, model, **kw):
        raise providers.ProviderError(
            "gemini 503: high demand", transient=True)

    install({"gemini": gemini_down,
             "groq": lambda p, s, m, **kw: json.dumps(good_post)},
            {"gemini": lambda: True, "groq": lambda: True,
             "ollama": lambda: False})

    _real_validate = G.validate
    _real_length = G.enforce_length
    try:
        G.validate = lambda post: []
        G.enforce_length = lambda post, target=700, max_rounds=3: post
        out = G.generate_post({"topic": "A topic nobody wanted to lose",
                                "angle": "", "image_query": "",
                                "kind": "general"})
        check_true("the post came out", bool(out.get("title")))
        check_true("it carries no errors", not out.get("errors"))
        check("the fallback provider's post was used",
              out.get("title"), good_post["title"])
    finally:
        G.validate = _real_validate
        G.enforce_length = _real_length
finally:
    restore()
    providers.TRANSIENT_BACKOFF = _orig_backoff
    G.GLITCH_BACKOFF = _orig_glitch

print("\n" + "=" * 52)
print(f"  {PASSES} passed, {FAILS} failed")
print("=" * 52)
sys.exit(1 if FAILS else 0)
