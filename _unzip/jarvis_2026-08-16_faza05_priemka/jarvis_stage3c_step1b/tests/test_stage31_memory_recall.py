"""
Stage 3.1 - memory must be USABLE, not merely stored.

Stage 3.0 made memory survive restarts (durability of the bytes).
This file guards the other half of the promise: a fact that was saved must
actually reach the prompt, so Jarvis can answer with it.

The bug this pins down, reported from a real session:
  - user said "remember I'm into AI automation"
  - the model chose category "hobbies" (not in the hardcoded whitelist)
  - update_memory happily wrote it to long_term.json  -> file looked perfect
  - format_memory_for_prompt rendered only 7 known categories -> fact dropped
  - after restart Jarvis insisted "there is nothing saved about your hobbies"

So the data was safe on disk and still functionally lost. Durability without
recall is theatre. These tests treat a silently unrendered fact as data loss.
"""

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]


def _fresh_state_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="jarvis-stage31-"))
    os.environ["JARVIS_STATE_DIR"] = str(d)
    return d


def _memory_module():
    import memory.memory_manager as mm

    importlib.reload(mm)
    mm._migrated_for = None
    return mm


# ---------------------------------------------------------------- rendering


def test_unknown_category_still_reaches_the_prompt():
    """The exact reported failure: an invented category must not vanish."""
    mm = _memory_module()
    out = mm.format_memory_for_prompt(
        {"hobbies": {"ai_automation": {"value": "AI automation"}}}
    )
    assert "AI automation" in out, f"fact dropped from prompt: {out!r}"
    assert "Hobbies" in out, f"category heading missing: {out!r}"


def test_known_categories_are_unchanged():
    """The fix must not disturb the categories that already worked."""
    mm = _memory_module()
    out = mm.format_memory_for_prompt(
        {
            "identity": {"name": {"value": "Rdrr"}},
            "preferences": {"favorite_color": {"value": "green"}},
        }
    )
    assert "Rdrr" in out
    assert "green" in out
    # A known category must never be printed twice by the generic pass.
    assert out.count("green") == 1, f"duplicated rendering: {out!r}"


def test_known_and_unknown_categories_coexist():
    mm = _memory_module()
    out = mm.format_memory_for_prompt(
        {
            "preferences": {"favorite_color": {"value": "green"}},
            "hobbies": {"ai_automation": {"value": "AI automation"}},
            "skills": {"python": {"value": "advanced"}},
        }
    )
    for expected in ("green", "AI automation", "advanced"):
        assert expected in out, f"{expected!r} missing from {out!r}"


def test_plain_string_values_are_rendered():
    """Older entries were bare strings, not {'value': ...} dicts."""
    mm = _memory_module()
    out = mm.format_memory_for_prompt({"hobbies": {"chess": "plays chess"}})
    assert "plays chess" in out


def test_empty_or_malformed_unknown_categories_are_ignored():
    """No stray headings, and no crash on junk shapes."""
    mm = _memory_module()
    out = mm.format_memory_for_prompt(
        {
            "hobbies": {},
            "junk": "not-a-dict",
            "blanks": {"k": {"value": ""}},
            "preferences": {"favorite_color": {"value": "green"}},
        }
    )
    assert "green" in out
    assert "Hobbies" not in out
    assert "Junk" not in out
    assert "Blanks" not in out


def test_completely_empty_memory_produces_no_block():
    mm = _memory_module()
    assert mm.format_memory_for_prompt({}) == ""
    assert mm.format_memory_for_prompt({"hobbies": {}}) == ""
    assert mm.format_memory_for_prompt(None) == ""


# ------------------------------------------------------- end-to-end recall


def test_saved_fact_survives_restart_and_reaches_the_prompt():
    """Full reproduction: save -> 'restart' -> the fact is in the prompt."""
    _fresh_state_dir()
    mm = _memory_module()

    mm.update_memory({"preferences": {"favorite_color": {"value": "green"}}})
    mm.update_memory({"hobbies": {"ai_automation": {"value": "AI automation"}}})

    # Simulate a restart: brand new module state, same durable state dir.
    mm2 = _memory_module()
    prompt = mm2.format_memory_for_prompt(mm2.load_memory())

    assert "green" in prompt, "colour lost after restart"
    assert "AI automation" in prompt, "hobby lost after restart - the reported bug"


def test_remember_helper_normalises_unknown_category_but_keeps_the_fact():
    """remember() folds junk categories into notes; the fact must still show."""
    _fresh_state_dir()
    mm = _memory_module()
    mm.remember("ai_automation", "AI automation", category="totally_made_up")
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "AI automation" in prompt


def test_forget_removes_a_fact_from_the_prompt():
    """Recall and forgetting are the same surface; both must be honest."""
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory({"hobbies": {"ai_automation": {"value": "AI automation"}}})
    assert "AI automation" in mm.format_memory_for_prompt(mm.load_memory())

    mm.forget("ai_automation", category="hobbies")
    assert "AI automation" not in mm.format_memory_for_prompt(mm.load_memory())


# ------------------------------------------------------------ prompt rules


def test_behaviour_prompt_forbids_denying_memory():
    """
    The second half of the bug was not code: Jarvis said "I cannot access your
    personal information" while the memory block sat in its own prompt. Pin the
    instruction so a future prompt edit cannot quietly delete it.
    """
    text = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
        encoding="utf-8"
    )
    assert "LONG-TERM MEMORY IS YOURS" in text
    assert "WHAT YOU KNOW ABOUT THIS PERSON" in text
    assert "cannot access your personal information" in text.lower().replace(
        "i cannot", "cannot"
    )


def test_prompt_header_matches_what_the_rules_reference():
    """
    The prompt tells the model to read a block by name. If the header text ever
    drifts, the instruction silently points at nothing - so tie them together.
    """
    mm = _memory_module()
    block = mm.format_memory_for_prompt(
        {"identity": {"name": {"value": "Rdrr"}}}
    )
    text = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
        encoding="utf-8"
    )
    assert "WHAT YOU KNOW ABOUT THIS PERSON" in block
    assert "WHAT YOU KNOW ABOUT THIS PERSON" in text
