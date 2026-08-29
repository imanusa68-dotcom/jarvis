"""
Stage 3.1 - forgetting must be REAL, not a polite lie.

Reported from a live session: the user said "remove my night schedule from
memory". Jarvis had no forget tool, so it faked deletion by calling save_memory
with value "schedule updated, disregard previous". It told the user "deleted"
while the stale fact lived on -- and worse, corrupted into junk that poisons
future answers. Same class as Invariant 10: the write path existed, the UNWRITE
path did not exist on the model's surface.

These tests pin down that forgetting actually removes the fact, is honest about
what happened, and survives a restart. They cannot exercise the LLM, so they
prove the mechanism (forget()) and the tool/prompt wiring that routes to it.
"""

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]


def _fresh_state_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="jarvis-stage31-forget-"))
    os.environ["JARVIS_STATE_DIR"] = str(d)
    return d


def _memory_module():
    import memory.memory_manager as mm

    importlib.reload(mm)
    mm._migrated_for = None
    return mm


# -------------------------------------------------------------- mechanism


def test_forget_removes_the_fact_for_real():
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory({"habits": {"work_schedule": {"value": "works at night"}}})
    assert "works at night" in mm.format_memory_for_prompt(mm.load_memory())

    res = mm.forget("work_schedule", "habits")
    assert res.startswith("Forgotten:"), res
    assert "works at night" not in mm.format_memory_for_prompt(mm.load_memory())


def test_forget_survives_restart():
    """A forgotten fact must not resurrect after a restart."""
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory({"habits": {"work_schedule": {"value": "works at night"}}})
    mm.forget("work_schedule", "habits")

    mm2 = _memory_module()  # simulate restart, same durable dir
    assert "works at night" not in mm2.format_memory_for_prompt(mm2.load_memory())


def test_forget_finds_the_key_even_with_wrong_category():
    """
    The model guesses categories badly. Saved under 'habits', asked to forget
    from 'notes' -> must still be removed, or Jarvis reports success on nothing.
    """
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory({"habits": {"work_schedule": {"value": "works at night"}}})

    res = mm.forget("work_schedule", "notes")  # wrong category on purpose
    assert res.startswith("Forgotten:"), res
    assert "habits/work_schedule" in res
    assert "works at night" not in mm.format_memory_for_prompt(mm.load_memory())


def test_forget_finds_the_key_with_no_category():
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory({"hobbies": {"ai_automation": {"value": "AI automation"}}})
    res = mm.forget("ai_automation")  # no category at all
    assert res.startswith("Forgotten:"), res
    assert "AI automation" not in mm.format_memory_for_prompt(mm.load_memory())


def test_forget_is_honest_when_nothing_matches():
    """No fake success: forgetting an unknown key must say 'Not found'."""
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory({"preferences": {"favorite_color": {"value": "green"}}})
    res = mm.forget("sister_name", "relationships")
    assert res.startswith("Not found"), res
    # And it must not have disturbed the real fact.
    assert "green" in mm.format_memory_for_prompt(mm.load_memory())


def test_forget_does_not_touch_other_facts():
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory(
        {
            "preferences": {"favorite_color": {"value": "green"}},
            "habits": {"work_schedule": {"value": "works at night"}},
        }
    )
    mm.forget("work_schedule", "habits")
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "green" in prompt
    assert "works at night" not in prompt


def test_the_reported_bug_end_to_end():
    """
    Full reproduction of the session: save night schedule, then the OLD broken
    behaviour (overwrite with 'disregard') vs the FIX (forget). Prove the fix
    leaves nothing behind after a restart.
    """
    _fresh_state_dir()
    mm = _memory_module()
    mm.update_memory(
        {"habits": {"work_schedule": {"value": "works at night"}}}
    )
    # The fix: a real deletion, not a save_memory overwrite.
    mm.forget("work_schedule", "habits")

    mm2 = _memory_module()
    data = mm2.load_memory()
    # The category may remain as an empty dict, but the key must be gone and
    # NO junk value like 'disregard' may survive anywhere.
    blob = json.dumps(data, ensure_ascii=False).lower()
    assert "work_schedule" not in blob
    assert "disregard" not in blob
    assert "works at night" not in blob


# ------------------------------------------------------------ wiring


def test_forget_memory_tool_is_declared():
    """The mechanism is useless if the model is never offered the tool."""
    text = (REPO / "main.py").read_text(encoding="utf-8")
    assert '"name": "forget_memory"' in text
    # And it must be handled, not just declared.
    assert 'if name == "forget_memory":' in text
    # The handler must route to the real forget, not to save_memory.
    assert "forget as _forget_fact" in text


def test_save_memory_description_warns_against_fake_forgetting():
    text = (REPO / "main.py").read_text(encoding="utf-8")
    assert "forget_memory" in text
    assert "disregard" in text.lower()


def test_behaviour_prompt_teaches_real_forgetting():
    text = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
        encoding="utf-8"
    )
    assert "TO FORGET, ACTUALLY FORGET" in text
    assert "forget_memory" in text
