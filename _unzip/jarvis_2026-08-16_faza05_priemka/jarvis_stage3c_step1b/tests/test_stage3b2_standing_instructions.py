"""Stage 3B.2a - a standing instruction must outlive the session.

MEASURED ON 2026-07-25 (live, on the user's machine)
----------------------------------------------------
The user gave three standing instructions:

    "explain technical things without jargon and without condescension"
    "warn me what exactly you will touch before deleting or moving anything"
    "don't translate English terms, I'm used to the originals"

Jarvis answered "understood", "accepted", "noted" - and saved NOTHING. The live
log contains no save_memory call at all. At the same time it correctly ignored
four throwaway lines ("I'm tired today", "open notepad", "what time is it",
"seriously?").

That looked like a working filter, but it was a blind spot: the tool contract
only ever described "a personal fact about the user", so a rule about BEHAVIOUR
fell outside memory entirely. Every such rule evaporated at restart and the user
had to teach it again - which is precisely the difference between an assistant
that merely answers and one that knows you.

These tests pin the contract. They deliberately check the wording the model is
given, because that wording IS the mechanism here - there is no code path that
can force the decision.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MAIN = (REPO / "main.py").read_text(encoding="utf-8")
BEHAVIOR = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
    encoding="utf-8")


def _memory_module():
    os.environ["JARVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="jarvis-stage3b2-")
    import memory.memory_manager as mm
    importlib.reload(mm)
    mm._migrated_for = None
    return mm


# -- the tool contract --------------------------------------------------------

def test_save_memory_asks_for_standing_instructions_too():
    lowered = MAIN.lower()
    assert "standing instruction" in lowered, \
        "save_memory must name standing instructions as savable"


def test_save_memory_warns_that_agreeing_is_not_enough():
    """The exact failure seen live: agreement without a save."""
    lowered = MAIN.lower()
    assert "until restart" in lowered, \
        "the tool must say that spoken agreement dies at restart"


def test_save_memory_offers_a_category_for_behaviour_rules():
    assert "communication_habits" in MAIN, \
        "there must be a category for how-to-treat-me rules"


def test_save_memory_still_refuses_throwaway_lines():
    """Widening the contract must not turn memory into a transcript."""
    lowered = MAIN.lower()
    for throwaway in ("passing moods", "one-time commands"):
        assert throwaway in lowered, \
            f"the tool must still exclude {throwaway}"


# -- the behaviour prompt -----------------------------------------------------

def test_the_prompt_treats_behaviour_rules_as_memory():
    assert "STANDING INSTRUCTIONS ARE MEMORY" in BEHAVIOR


def test_the_prompt_names_saying_understood_as_a_failure():
    lowered = BEHAVIOR.lower()
    assert "quiet failure" in lowered, \
        "the prompt must call bare agreement what it is: a silent failure"


def test_the_prompt_gives_a_usable_test_for_what_to_keep():
    lowered = BEHAVIOR.lower()
    assert "still be true next week" in lowered, \
        "the model needs one concrete rule of thumb, not a vague instruction"


def test_the_prompt_lists_things_that_must_not_be_saved():
    lowered = BEHAVIOR.lower()
    for throwaway in ("i'm tired today", "open\n    notepad", "what time is it"):
        assert throwaway.replace("\n    ", " ") in " ".join(lowered.split()), \
            f"the prompt must name {throwaway!r} as NOT memory"


# -- the storage side actually accepts it -------------------------------------

def test_communication_habits_is_a_real_category_not_a_typo():
    mm = _memory_module()
    assert "communication_habits" in mm._empty_memory(), \
        "the category named in the tool must exist in storage"


def test_a_standing_instruction_survives_a_save_and_reaches_the_prompt():
    """End to end on the storage side: save it, reload, see it in the block."""
    mm = _memory_module()
    mm.remember("answer_length", "keep answers short and to the point",
                "communication_habits")

    block = mm.format_memory_for_prompt(mm.load_memory())
    assert "keep answers short" in block, \
        "a saved behaviour rule must come back in the next session's prompt"


def test_a_standing_instruction_can_be_taken_back():
    """A rule the user drops must really go - same honesty bar as Stage 3.1."""
    mm = _memory_module()
    mm.remember("jargon", "explain without jargon", "communication_habits")
    assert "Forgotten" in mm.forget("jargon")

    block = mm.format_memory_for_prompt(mm.load_memory())
    assert "explain without jargon" not in block
