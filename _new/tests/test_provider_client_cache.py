# -*- coding: utf-8 -*-
"""
The provider must reuse one client and must never hang forever.

Until 10.08.2026 core/provider/gemini.py built a brand new SDK client on every
single call: a new connection pool and a new TLS handshake for every question
the assistant asked. It also passed no request timeout at all, so a stuck
request could hold a worker thread until the network gave up on its own.

What is guarded here:
  1. The same key builds the client once; another key replaces it.
  2. The raw key never lands in the cache - only its digest does.
  3. Many threads still build exactly one client.
  4. The timeout comes from config/registry.yaml, never from the code.
  5. A missing or broken timeout value means the old behaviour, not a crash.
  6. An SDK that refuses the timeout still gets a working client.
  7. A failed request drops the cached client; the error still travels up.

The SDK is not installed in the sandbox and there is no network here, so the
vendor is replaced by a fake module. That is a real limitation and it is said
out loud: this file proves the plumbing, not the live behaviour of the SDK.

Run standalone:  python tests/test_provider_client_cache.py
"""

import re
import sys
import threading
import types as pytypes
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.provider.gemini as gemini        # noqa: E402

KEY_ONE = "key-one-for-the-cache-test"
KEY_TWO = "key-two-for-the-cache-test"
MODEL = "cache-test-model"


class FakeState:
    """Everything the fake vendor remembers between calls."""

    built = []          # kwargs of every successful client build
    refused = 0         # how many times the timeout was rejected
    refuse_timeout = False
    sent = []           # (model, payload) of every request
    error = None        # exception to raise instead of answering
    reply = "ok"

    @classmethod
    def reset(cls):
        cls.built = []
        cls.refused = 0
        cls.refuse_timeout = False
        cls.sent = []
        cls.error = None
        cls.reply = "ok"


class _FakeReply:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def generate_content(self, model=None, contents=None):
        FakeState.sent.append((model, contents))
        if FakeState.error is not None:
            raise FakeState.error
        return _FakeReply(FakeState.reply)


class FakeClient:
    def __init__(self, api_key=None, http_options=None):
        if http_options is not None and FakeState.refuse_timeout:
            FakeState.refused += 1
            raise TypeError("unexpected keyword argument 'http_options'")
        FakeState.built.append({"api_key": api_key, "http_options": http_options})
        self.api_key = api_key
        self.http_options = http_options
        self.models = _FakeModels()


@contextmanager
def fake_sdk():
    """Put a fake vendor into sys.modules and always take it back out."""
    names = ("google", "google.genai")
    saved = {name: sys.modules.get(name) for name in names}
    google_mod = pytypes.ModuleType("google")
    genai_mod = pytypes.ModuleType("google.genai")
    genai_mod.Client = FakeClient
    google_mod.genai = genai_mod
    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mod
    FakeState.reset()
    gemini.reset_clients()
    gemini._TIMEOUT_REFUSED = False
    gemini._TIMEOUT_REASON = ""
    try:
        yield FakeState
    finally:
        for name in names:
            if saved[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved[name]
        gemini.reset_clients()
        gemini._TIMEOUT_REFUSED = False
        gemini._TIMEOUT_REASON = ""


@contextmanager
def timeout_of(value):
    """Pretend the registry says exactly this about the timeout."""
    from config import loader
    original = loader.get_limit

    def fake_get_limit(role, name, default=None):
        if role == gemini.TIMEOUT_ROLE and name == gemini.TIMEOUT_LIMIT:
            return value
        return original(role, name, default)

    loader.get_limit = fake_get_limit
    gemini.reset_clients()
    try:
        yield
    finally:
        loader.get_limit = original
        gemini.reset_clients()


def _read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


# --------------------------------------------------------------------------
# 1. One client per key
# --------------------------------------------------------------------------

def test_the_same_key_builds_the_client_only_once():
    with fake_sdk() as state:
        first = gemini._client_for(KEY_ONE)
        second = gemini._client_for(KEY_ONE)
        assert first is second, "the client is rebuilt on every call again"
        assert len(state.built) == 1, "built %d clients instead of one" % len(state.built)


def test_another_key_replaces_the_cached_client():
    with fake_sdk() as state:
        first = gemini._client_for(KEY_ONE)
        second = gemini._client_for(KEY_TWO)
        assert first is not second, "a new key kept the old client"
        assert len(state.built) == 2, "a new key did not build a client"
        again = gemini._client_for(KEY_TWO)
        assert again is second, "the new client was not cached"
        assert len(state.built) == 2, "the new key rebuilt its client"


def test_the_cache_holds_exactly_one_client():
    with fake_sdk():
        gemini._client_for(KEY_ONE)
        gemini._client_for(KEY_TWO)
        clients = [v for k, v in gemini._CACHE.items() if k == "client"]
        assert len(clients) == 1, "the cache grew: keys pile up over time"


def test_the_raw_key_never_lands_in_the_cache():
    with fake_sdk():
        gemini._client_for(KEY_ONE)
        printed = repr(gemini._CACHE.get("mark"))
        assert KEY_ONE not in printed, "the raw key is stored in the cache"
        assert gemini._fingerprint(KEY_ONE) in printed, "the digest is missing"
        assert gemini._fingerprint(KEY_ONE) != gemini._fingerprint(KEY_TWO), (
            "two different keys share one digest"
        )


def test_many_threads_build_exactly_one_client():
    with fake_sdk() as state:
        seen = []
        start = threading.Event()

        def worker():
            start.wait()
            seen.append(gemini._client_for(KEY_ONE))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()
        assert len(seen) == 20, "a worker thread lost its client"
        assert len(set(id(c) for c in seen)) == 1, "threads got different clients"
        assert len(state.built) == 1, "%d clients built at once" % len(state.built)


# --------------------------------------------------------------------------
# 2. The timeout
# --------------------------------------------------------------------------

def test_the_timeout_reaches_the_sdk_in_milliseconds():
    with fake_sdk() as state:
        with timeout_of(2):
            gemini._client_for(KEY_ONE)
        assert state.built, "no client was built"
        options = state.built[-1]["http_options"]
        assert isinstance(options, dict), "the timeout was not passed at all"
        assert options.get("timeout") == 2000, (
            "the timeout is not in milliseconds: %r" % (options,)
        )


def test_the_timeout_lives_in_the_registry_and_not_in_the_code():
    registry = _read(ROOT / "config" / "registry.yaml")
    assert "timeout_seconds" in registry, "config/registry.yaml lost the timeout"
    source = _read(ROOT / "core" / "provider" / "gemini.py")
    assert "get_limit" in source, "the provider stopped reading the registry"
    hardcoded = re.search(r"(?i)timeout\w*\s*=\s*[0-9]", source)
    assert not hardcoded, "a timeout number is written into the code"


def test_a_missing_timeout_keeps_the_old_behaviour():
    with fake_sdk() as state:
        with timeout_of(None):
            assert gemini._timeout_ms() is None, "a missing limit invented a timeout"
            gemini._client_for(KEY_ONE)
        assert state.built[-1]["http_options"] is None, (
            "a client was given a timeout nobody asked for"
        )


def test_a_broken_timeout_value_is_ignored_quietly():
    for bad in ("soon", "", 0, -5, True, [], {}):
        with fake_sdk() as state:
            with timeout_of(bad):
                assert gemini._timeout_ms() is None, "bad value accepted: %r" % (bad,)
                gemini._client_for(KEY_ONE)
            assert state.built[-1]["http_options"] is None, (
                "bad value %r still reached the SDK" % (bad,)
            )


def test_a_changed_timeout_rebuilds_the_client():
    with fake_sdk() as state:
        with timeout_of(2):
            first = gemini._client_for(KEY_ONE)
        with timeout_of(3):
            second = gemini._client_for(KEY_ONE)
        assert first is not second, "the client kept the old timeout"
        assert len(state.built) == 2, "the timeout change did not rebuild"


def test_an_sdk_that_refuses_the_timeout_still_gets_a_client():
    with fake_sdk() as state:
        state.refuse_timeout = True
        with timeout_of(2):
            client = gemini._client_for(KEY_ONE)
            assert client is not None, "the refusal killed the whole call"
            assert state.refused == 1, "the timeout was never offered"
            assert state.built[-1]["http_options"] is None, "the retry kept the timeout"
            ms, refused, reason = gemini.timeout_status()
            assert ms == 2000, "the status forgot the requested timeout"
            assert refused is True, "the refusal is invisible from outside"
            assert reason, "the refusal has no reason to show"


# --------------------------------------------------------------------------
# 3. Requests through the cached client
# --------------------------------------------------------------------------

def test_two_questions_travel_through_one_client():
    with fake_sdk() as state:
        provider = gemini.GeminiProvider()
        assert provider.generate(MODEL, "first", KEY_ONE) == "ok"
        assert provider.generate(MODEL, "second", KEY_ONE) == "ok"
        assert len(state.built) == 1, "the second question rebuilt the client"
        assert [p for _, p in state.sent] == ["first", "second"], "the payload changed"
        assert [m for m, _ in state.sent] == [MODEL, MODEL], "the model name changed"


def test_a_failed_request_drops_the_cached_client_and_still_raises():
    with fake_sdk() as state:
        provider = gemini.GeminiProvider()
        state.error = RuntimeError("503 UNAVAILABLE: model is overloaded")
        raised = None
        try:
            provider.generate(MODEL, "first", KEY_ONE)
        except RuntimeError as exc:
            raised = exc
        assert raised is not None, "the failure was swallowed inside the provider"
        assert "503" in str(raised), "the failure lost its reason on the way up"
        assert not gemini._CACHE, "a client that just failed stayed in the cache"
        state.error = None
        assert provider.generate(MODEL, "second", KEY_ONE) == "ok"
        assert len(state.built) == 2, "the retry reused the broken client"


def test_an_empty_answer_is_still_an_empty_string():
    with fake_sdk() as state:
        state.reply = None
        provider = gemini.GeminiProvider()
        assert provider.generate(MODEL, "first", KEY_ONE) == "", (
            "an empty answer stopped being an empty string"
        )


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print("  PASS  " + name)
            passed += 1
        except AssertionError as exc:
            print("  FAIL  " + name + " -- " + str(exc))
        except Exception as exc:
            print("  ERROR " + name + " -- " + type(exc).__name__ + ": " + str(exc))
    print("RESULT: %d/%d %s" % (passed, len(tests),
                                "ALL PASS" if passed == len(tests) else "SOME FAILED"))
    sys.exit(0 if passed == len(tests) else 1)
