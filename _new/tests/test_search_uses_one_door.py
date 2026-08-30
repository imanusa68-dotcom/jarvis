# -*- coding: utf-8 -*-
"""
Search and deep research must walk through the one shared door to the model.

Until 09.08.2026 four places built their own google.genai client:
  actions/web_search.py     -- news digest, plain synthesis, compare
  actions/deep_research.py  -- evidence synthesis

Those four were blind: they did not know about the 429 cooldown kept in
core/model_guard.py, they never retried a transient 503, and a missing key
killed the answer instead of degrading it. Everything now goes through
core/aux_model.aux_call, exactly like the agent path and the composer.

Run standalone:  python tests/test_search_uses_one_door.py
"""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.aux_model as aux_model_module
import actions.web_search as ws
import actions.deep_research as dr
from config.loader import get_model

OLD_SDK = "google." + "generativeai"
SDK_CLIENT = "genai." + "Client("

RESULTS = [
    {"title": "First", "snippet": "aaa", "url": "https://a.example", "domain": "a.example"},
    {"title": "Second", "snippet": "bbb", "url": "https://b.example", "domain": "b.example"},
]

EVIDENCE = {
    "sources": [
        {"title": "S1", "url": "https://a.example", "domain": "a.example",
         "content_preview": "first preview", "published_date": None},
        {"title": "S2", "url": "https://b.example", "domain": "b.example",
         "content_preview": "second preview", "published_date": None},
    ]
}


class Door:
    """Stand-in for the shared door. Remembers the question, answers to order."""

    def __init__(self, ok=True, answer=""):
        self.ok = ok
        self.answer = answer
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"prompt": prompt, "key": api_key, "model": model, "caller": caller})
        return self.ok, self.answer


class Mine:
    def __call__(self, *a, **kw):
        raise AssertionError("the model was called where it must not be")


def run(door, fn, key="KEY-FOR-TESTS", key_raises=False):
    """Swap the shared door and the key source, capture what gets printed."""
    saved_door = aux_model_module.aux_call
    saved_keys = [(m, m._get_api_key) for m in (ws, dr)]
    saved_retrieve = ws._retrieve
    saved_rank = ws.deduplicate_and_rank

    def fake_key():
        if key_raises:
            raise RuntimeError("gemini_api_key not found")
        return key

    aux_model_module.aux_call = door
    for m, _ in saved_keys:
        m._get_api_key = fake_key
    ws._retrieve = lambda *a, **kw: []
    ws.deduplicate_and_rank = lambda *a, **kw: []

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            out = fn()
    finally:
        aux_model_module.aux_call = saved_door
        for m, orig in saved_keys:
            m._get_api_key = orig
        ws._retrieve = saved_retrieve
        ws.deduplicate_and_rank = saved_rank
    return out, buf.getvalue()


def digest(door, **kw):
    return run(door, lambda: ws._synthesize_news_digest("news today", RESULTS, date_str="2026-08-09"), **kw)


def synth(door, **kw):
    return run(door, lambda: ws._synthesize_with_gemini("what is x", RESULTS), **kw)


def compare(door, **kw):
    return run(door, lambda: ws._compare_items(["RTX 5060", "RTX 5070"], aspect="price"), **kw)


def research(door, **kw):
    return run(door, lambda: dr._synthesize("what is x", EVIDENCE), **kw)


# ─────────────────────────────────────────────────────────────────────────────
# 1. no second door left in the source
# ─────────────────────────────────────────────────────────────────────────────

def test_the_search_files_never_build_their_own_client():
    for rel in ("actions/web_search.py", "actions/deep_research.py"):
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        assert OLD_SDK not in text, rel + " still mentions the deprecated SDK"
        assert SDK_CLIENT not in text, rel + " still builds its own client"
        assert "from google import genai" not in text, rel + " still imports the SDK"
        assert "from core.aux_model import aux_call" in text, rel + " lost the shared door"


# ─────────────────────────────────────────────────────────────────────────────
# 2. news digest
# ─────────────────────────────────────────────────────────────────────────────

def test_the_digest_walks_through_the_one_door():
    d = Door(True, "Digest text, sir.")
    out, _ = digest(d)
    assert len(d.calls) == 1, "the digest did not use the shared door exactly once"
    assert out == "Digest text, sir."
    assert d.calls[0]["model"] == get_model("aux_light"), "digest role drifted"
    assert d.calls[0]["caller"] == "WebSearch-Digest"


def test_the_digest_prompt_still_carries_its_rules():
    d = Door(True, "ok")
    digest(d)
    prompt = d.calls[0]["prompt"]
    assert "news digest" in prompt.lower(), "the digest task vanished from the prompt"
    assert "2026-08-09" in prompt, "the requested date vanished from the prompt"
    assert "a.example" in prompt, "the sources vanished from the prompt"


def test_a_refused_digest_falls_back_to_plain_text():
    d = Door(False, "[quota-cooldown:65s]")
    out, printed = digest(d)
    assert "News digest" in out, "the refusal left the owner with nothing"
    assert "quota-cooldown" in printed, "the refusal passed in silence"
    assert "First" in out, "the plain fallback lost the found results"


def test_an_empty_digest_is_not_served_as_an_answer():
    d = Door(True, "   ")
    out, _ = digest(d)
    assert "News digest" in out, "an empty model answer was served as a digest"


# ─────────────────────────────────────────────────────────────────────────────
# 3. plain synthesis
# ─────────────────────────────────────────────────────────────────────────────

def test_the_synthesis_walks_through_the_one_door():
    d = Door(True, "Short answer, sir.")
    out, _ = synth(d)
    assert len(d.calls) == 1
    assert out == "Short answer, sir."
    assert d.calls[0]["model"] == get_model("aux_light"), "synthesis role drifted"
    assert d.calls[0]["caller"] == "WebSearch-Synthesis"


def test_a_refused_synthesis_still_shows_the_found_links():
    d = Door(False, "[error:503 UNAVAILABLE]")
    out, printed = synth(d)
    assert "Search results for:" in out, "the refusal left the owner with nothing"
    assert "https://a.example" in out, "the links were lost together with the model"
    assert "503" in printed, "the refusal passed in silence"


# ─────────────────────────────────────────────────────────────────────────────
# 4. compare
# ─────────────────────────────────────────────────────────────────────────────

def test_the_compare_walks_through_the_one_door():
    d = Door(True, "A is cheaper than B, sir.")
    out, _ = compare(d)
    assert len(d.calls) == 1
    assert out == "A is cheaper than B, sir."
    assert d.calls[0]["model"] == get_model("aux_light"), "compare role drifted"
    assert d.calls[0]["caller"] == "WebSearch-Compare"


def test_a_refused_compare_still_names_both_items():
    d = Door(False, "[quota-429:cooldown 30s]")
    out, printed = compare(d)
    assert "RTX 5060" in out and "RTX 5070" in out, "the refusal dropped the items"
    assert "quota-429" in printed, "the refusal passed in silence"


# ─────────────────────────────────────────────────────────────────────────────
# 5. deep research
# ─────────────────────────────────────────────────────────────────────────────

def test_the_research_walks_through_the_one_door():
    payload = json.dumps({"answer": "A", "facts": ["f1", "f2"], "confidence": "high"})
    d = Door(True, payload)
    out, _ = research(d)
    assert len(d.calls) == 1
    assert out["answer"] == "A"
    assert out["facts"] == ["f1", "f2"]
    assert out["confidence"] == "high"
    assert d.calls[0]["model"] == dr.SYNTHESIS_MODEL, "research role drifted"
    assert d.calls[0]["caller"] == "DeepResearch"


def test_a_refusal_is_never_parsed_as_research_json():
    d = Door(False, "[quota-cooldown:65s]")
    out, printed = research(d)
    assert out["confidence"] == "low", "a refusal was reported as a confident answer"
    assert out["uncertainty"].startswith("model unavailable"), out["uncertainty"]
    assert "first preview" in out["answer"], "the gathered sources were thrown away"
    assert "quota-cooldown" in printed, "the refusal passed in silence"
    assert "Synthesis failed" not in printed, "a refusal was disguised as a crash"


def test_research_still_reads_plain_text_when_json_is_absent():
    d = Door(True, "plain sentence without braces")
    out, _ = research(d)
    assert out["answer"] == "plain sentence without braces"
    assert out["confidence"] == "medium"


# ─────────────────────────────────────────────────────────────────────────────
# 6. a missing key must degrade, not kill
# ─────────────────────────────────────────────────────────────────────────────

def test_a_missing_key_never_kills_the_search():
    out, _ = digest(Mine(), key_raises=True)
    assert "News digest" in out, "a missing key killed the digest"

    out, _ = synth(Mine(), key_raises=True)
    assert "Search results for:" in out, "a missing key killed the synthesis"

    out, _ = compare(Mine(), key_raises=True)
    assert "RTX 5060" in out, "a missing key killed the comparison"

    out, _ = research(Mine(), key_raises=True)
    assert out["confidence"] == "low", "a missing key produced a confident answer"


# ─────────────────────────────────────────────────────────────────────────────
# 7. the log must say who burns the quota
# ─────────────────────────────────────────────────────────────────────────────

def test_every_search_caller_has_its_own_name():
    names = []
    for fn in (digest, synth, compare, research):
        d = Door(True, json.dumps({"answer": "a", "facts": [], "confidence": "low"}))
        fn(d)
        names.append(d.calls[0]["caller"])
    assert len(set(names)) == 4, "caller names collided: " + str(names)
    assert "unknown" not in names, "a caller stayed anonymous: " + str(names)


def test_the_key_reaches_the_door_unchanged():
    d = Door(True, "ok")
    digest(d, key="SECRET-53-CHARS")
    assert d.calls[0]["key"] == "SECRET-53-CHARS", "the key was mangled on the way to the door"


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
