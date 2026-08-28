# -*- coding: utf-8 -*-
"""
The auxiliary path must reach the SDK through exactly one provider layer.

Until 09.08.2026 core/aux_model.py built the SDK client itself. Quotas,
retries, cutting and the spoken failure lines lived in the same file as the
vendor call, so the only way to test the door was to monkeypatch a private
function. Now the vendor call lives in core/provider/gemini.py behind the
contract written in core/provider/base.py.

What is guarded here:
  1. The layer exists as three files and the door keeps its name at home.
  2. The SDK appears in exactly one file of the auxiliary path.
  3. Importing the layer never pulls the SDK (phase 0.7 depends on this).
  4. Model name, key, prompt and picture reach the provider unchanged.
  5. The payload built by a provider is the very object sent by it.
  6. Quotas and retries stay in core/aux_model.py, not in the provider.
  7. Every failure is still audible and still names the caller.
  8. The provider files carry no model names and no forbidden numbers.

Run standalone:  python tests/test_provider_layer.py
"""

import io
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.aux_model as aux_model          # noqa: E402
import core.provider as provider_pkg        # noqa: E402
from core.provider.base import Provider     # noqa: E402

PROVIDER_DIR = ROOT / "core" / "provider"
DOOR = "core/provider/gemini.py"

SDK_CLIENT = "genai." + "Client("
SDK_SEND = ".generate" + "_content("
SDK_IMPORTS = ("from google import genai", "from google.genai import", "import google.genai")

# Live voice and Screen View keep their own clients: another protocol, and the
# provider layer must never be dragged into them.
LIVE_FILES = {
    "core/screen_live_runtime.py",
    "core/screen_live_session.py",
    "core/screen_share_manager.py",
    "actions/screen_processor.py",
}
# Reads the numeric status code out of the SDK error type for live sessions.
# It builds no client and sends no request, so it is not a second door. Named
# here on purpose: a new importer must be added consciously, not silently.
ERROR_CODE_FILES = {"core/session_manager.py"}
AUX_DIRS = ("core", "agent", "actions", "memory")

# Mirrors of two distant scanners, so a careless edit fails here with a clear
# message instead of in tests/test_registry_roles.py or the retry watchdog.
MODEL_NEEDLES = ("gemini-", "gemma-", "text-embedding", "palm-",
                 "gpt-4", "gpt-3", "claude-", "llama-", "mistral-")
FORBIDDEN_NUMBERS = ("12000", "1.7")

# A whole word, not a substring: a helper named _fingerprint() is not a mouth.
# The scanner must catch use, never vocabulary - the same lesson as model_guard.
_PRINT_RE = re.compile(r"(?<![A-Za-z0-9_])print\s*\(")

ERR_503 = RuntimeError("503 UNAVAILABLE: model is overloaded")
ERR_429 = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
ERR_400 = ValueError("400 INVALID_ARGUMENT: prompt is malformed")


def _read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def _aux_files():
    for folder in AUX_DIRS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            yield path, path.relative_to(ROOT).as_posix()


def _provider_files():
    return sorted(PROVIDER_DIR.glob("*.py"))


class FakeProvider(Provider):
    """A provider that records everything, answers by script, never dials out."""

    name = "fake"

    def __init__(self, *script):
        self.script = list(script) or ["ok"]
        self.built = []
        self.sent = []

    def build_payload(self, prompt, image_parts=None):
        self.built.append({"prompt": prompt, "images": image_parts, "by": id(self)})
        return {"made_by": self.name, "prompt": prompt, "images": image_parts}

    def generate(self, model, payload, api_key):
        self.sent.append({"model": model, "payload": payload,
                          "key": api_key, "by": id(self)})
        step = self.script[min(len(self.sent) - 1, len(self.script) - 1)]
        if isinstance(step, BaseException):
            raise step
        return step


def _call(fake, model, caller="test", image_parts=None, prompt="question"):
    """Ask through the real aux_call with a fake provider and no real sleep."""
    saved_provider, saved_sleep = provider_pkg.set_provider(fake), time.sleep
    slept = []
    time.sleep = lambda seconds: slept.append(seconds)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ok, text = aux_model.aux_call(
                prompt, "test-key-53", model=model,
                image_parts=image_parts, caller=caller,
            )
    finally:
        provider_pkg.set_provider(saved_provider)
        time.sleep = saved_sleep
    return ok, text, buf.getvalue(), slept


def test_the_layer_exists_as_three_files():
    for name in ("__init__.py", "base.py", "gemini.py"):
        path = PROVIDER_DIR / name
        assert path.exists(), "the provider layer lost " + name
        assert len(_read(path).strip()) > 0, name + " is empty"


def test_the_door_keeps_its_name_at_home():
    for name in ("_generate", "_build_contents", "_Reply"):
        assert hasattr(aux_model, name), "core/aux_model.py lost " + name
    assert aux_model._generate.__module__ == "core.aux_model", (
        "the door moved out of core/aux_model.py: the retry watchdog patches it there"
    )
    assert aux_model._Reply("hi").text == "hi", "the reply wrapper lost its text field"


def test_the_sdk_lives_in_exactly_one_file_of_the_aux_path():
    """A door means building a client or sending a request, not naming a type."""
    doors, importers = [], []
    door_is_whole = False
    for path, rel in _aux_files():
        src = _read(path)
        if rel == DOOR:
            door_is_whole = SDK_CLIENT in src and SDK_SEND in src
            continue
        if (SDK_CLIENT in src or SDK_SEND in src) and rel not in LIVE_FILES:
            doors.append(rel)
        if any(n in src for n in SDK_IMPORTS) and rel not in LIVE_FILES | ERROR_CODE_FILES:
            importers.append(rel)
    assert not doors, "a second door to the SDK appeared: " + ", ".join(doors)
    assert not importers, "a new file started importing the SDK: " + ", ".join(importers)
    assert door_is_whole, DOOR + " no longer builds and sends: the door is gone"


def test_aux_model_keeps_no_sdk_of_its_own():
    src = _read(ROOT / "core" / "aux_model.py")
    for needle in (SDK_CLIENT,) + SDK_IMPORTS:
        assert needle not in src, "core/aux_model.py still touches the SDK: " + needle
    assert "core.provider" in src, "core/aux_model.py no longer walks through the layer"


def test_importing_the_layer_never_pulls_the_sdk():
    """Module level must stay clean: tests and the offline core depend on it."""
    offenders = []
    for path in _provider_files():
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if line[:1].strip() and ("import google" in line or "from google" in line):
                offenders.append("%s:%d" % (path.name, number))
    assert not offenders, "the SDK is imported at module level: " + ", ".join(offenders)


def test_a_text_question_reaches_the_provider_unchanged():
    fake = FakeProvider("answer")
    ok, text, out, _slept = _call(fake, "test-provider-text", prompt="how are you")

    assert ok and text == "answer", "the answer did not come back: %r" % (text,)
    assert len(fake.sent) == 1, "the provider was asked %d times" % len(fake.sent)
    assert fake.sent[0]["model"] == "test-provider-text", "the model name was mangled"
    assert fake.sent[0]["key"] == "test-key-53", "the key was mangled on the way"
    assert fake.built[0]["prompt"] == "how are you", "the prompt was mangled"
    assert fake.built[0]["images"] is None, "a text question carried a picture"
    assert out == "", "a successful call printed something: %r" % (out,)


def test_the_payload_travels_from_builder_to_sender_untouched():
    fake = FakeProvider("answer")
    _ok, _text, _out, _slept = _call(fake, "test-provider-payload")

    built = fake.built[0]
    sent = fake.sent[0]["payload"]
    assert isinstance(sent, dict) and sent.get("made_by") == "fake", (
        "the sent payload was not the one the provider built: %r" % (sent,)
    )
    assert sent["prompt"] == built["prompt"], "the payload was rebuilt on the way"
    assert built["by"] == fake.sent[0]["by"], (
        "one instance built the payload and another sent it"
    )


def test_the_layer_hands_out_one_and_the_same_provider():
    saved = provider_pkg.set_provider(None)
    try:
        first = provider_pkg.get_provider()
        second = provider_pkg.get_provider()
        assert first is second, "a second provider instance appeared"
        assert isinstance(first, Provider), "the layer handed out something alien"
    finally:
        provider_pkg.set_provider(saved)


def test_a_picture_reaches_the_provider_exactly_as_given():
    picture = [(b"\x89PNG-bytes", "image/png")]
    fake = FakeProvider("seen")
    ok, text, _out, _slept = _call(fake, "test-provider-vision", image_parts=picture)

    assert ok and text == "seen"
    assert fake.built[0]["images"] == picture, "the picture was changed on the way"
    assert fake.built[0]["images"][0][1] == "image/png", "the picture type was lost"


def test_the_real_provider_puts_the_picture_before_the_text():
    """Checked by reading the source: the SDK is absent from this sandbox."""
    src = _read(PROVIDER_DIR / "gemini.py")
    assert "from_bytes" in src and "from_text" in src, "the picture branch disappeared"
    assert src.index("from_bytes") < src.index("from_text"), (
        "the text part now goes before the picture: vision answers will quietly worsen"
    )


def test_a_provider_failure_is_audible_and_names_the_caller():
    fake = FakeProvider(ERR_400)
    ok, text, out, _slept = _call(fake, "test-provider-broken", caller="Provider-Test")

    assert not ok and text.startswith("[error:"), "the failure was dressed up: %r" % (text,)
    assert "Provider-Test" in out, "the log does not say who called: %r" % (out,)
    assert "INVALID_ARGUMENT" in out, "the reason was swallowed: %r" % (out,)
    assert len(fake.sent) == 1, "a permanent error was retried"


def test_a_429_from_the_provider_still_starts_the_cooldown():
    fake = FakeProvider(ERR_429)
    ok, text, out, _slept = _call(fake, "test-provider-quota")

    assert not ok and text.startswith("[quota-429"), (
        "the provider hid the quota error from core/model_guard.py: %r" % (text,)
    )
    assert len(fake.sent) == 1, "a 429 was retried and burned the daily quota"
    assert "429" in out, "the quota stop went silent: %r" % (out,)


def test_a_transient_failure_from_the_provider_is_retried_once():
    fake = FakeProvider(ERR_503, "second try")
    ok, text, out, slept = _call(fake, "test-provider-transient")

    assert ok and text == "second try", "the retry did not save the answer: %r" % (text,)
    assert len(fake.sent) == 2, "the provider was asked %d times" % len(fake.sent)
    assert slept, "the pause between tries disappeared"
    assert "временный отказ" in out, "the first failure went silent: %r" % (out,)


def test_an_empty_answer_stays_an_empty_string():
    fake = FakeProvider("")
    ok, text, _out, _slept = _call(fake, "test-provider-empty")
    assert ok and text == "", "an empty answer changed shape: %r" % (text,)


def test_the_provider_never_touches_quotas_models_or_the_voice():
    """Naming core/model_guard.py in a comment is fine; calling it is not."""
    used_needles = (
        "import core.model_guard", "from core.model_guard", "get_guard(",
        "handle_exception(", "is_available(", "record_429(", "cooldown_remaining(",
    )
    for path in _provider_files():
        src = _read(path)
        for needle in used_needles:
            assert needle not in src, (
                path.name + " reaches into the quota guard: " + needle
            )
        assert "get_model(" not in src, path.name + " asks for a model name itself"
        assert not _PRINT_RE.search(src), (
            path.name + " speaks: the project has one mouth"
        )


def test_the_provider_files_carry_no_model_names_and_no_forbidden_numbers():
    for path in _provider_files():
        low = _read(path).lower()
        for needle in MODEL_NEEDLES:
            assert needle not in low, "%s carries a model name: %s" % (path.name, needle)
        for number in FORBIDDEN_NUMBERS:
            assert number not in low, "%s carries number %s" % (path.name, number)


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print("  PASS  " + name)
            passed += 1
        except AssertionError as e:
            print("  FAIL  " + name + " -- " + str(e))
        except Exception as e:
            print("  ERROR " + name + " -- " + type(e).__name__ + ": " + str(e))
    print("RESULT: %d/%d %s" % (passed, len(tests), "ALL PASS" if passed == len(tests) else "SOME FAILED"))
    sys.exit(0 if passed == len(tests) else 1)
