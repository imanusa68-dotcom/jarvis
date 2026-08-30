"""
Jarvis must stop refusing screen work from memory.

The old bug, seen live on 2026-08-08 23:35: with the SCREEN button ON, Jarvis
kept answering "Screen control is disabled, please enable it" -- and did so
WITHOUT calling any tool. The reason was not a lie and not a broken toggle:
the system prompt is assembled once, at connect (main.py: ds_str = _ds_prompt()),
so the sentence "Screen control ... OFF" was frozen for the whole session while
the real button had already been pressed. tests/test_screen_control_state.py
checks that the sentence is BUILT correctly -- nobody ever checked that the
model could still HEAR it after the toggle moved. It could not.

The fix under test here follows the rule the project already uses for live
state (system_context, resolve_reference): live truth arrives through a TOOL,
not through the frozen prompt.

  * computer_control gains action='screen_status' -- read-only, outside
    INTERACTIVE_ACTIONS, so it answers even while clicking is blocked.
  * both refusal texts (core/gate.py and actions/computer_control.py) become
    ONE string that teaches the model to re-check instead of repeating itself.

No mouse, no model, no network is touched by any test in this file.

Run:  python -m pytest tests/test_screen_status_truth.py -q
or:   python tests/test_screen_status_truth.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actions.computer_control as cc
import core.dialogue_state as ds
import core.gate as gate
import core.security as sec

GOLDEN_PHRASE = "Screen control is currently disabled"


class _Player:
    """Stand-in for the Tk interface: only the two attributes the action reads."""

    def __init__(self, screen_control: bool):
        self.screen_control = screen_control
        self.log: list[str] = []

    def write_log(self, line: str) -> None:
        self.log.append(line)


def _ask(screen_control: bool, action: str = "screen_status", **extra):
    """Call the REAL computer_control, with the mouse and the model booby-trapped."""
    player = _Player(screen_control)

    def _no_mouse(*a, **k):
        raise AssertionError("screen_status touched the mouse")

    def _no_model(*a, **k):
        raise AssertionError("screen_status called the model")

    saved_click = cc._click
    saved_locate = cc._locate
    saved_eye = cc._analyze_screen_for_element
    cc._click = _no_mouse
    cc._locate = _no_model
    cc._analyze_screen_for_element = _no_model
    try:
        params = {"action": action}
        params.update(extra)
        return cc.computer_control(parameters=params, player=player), player
    finally:
        cc._click = saved_click
        cc._locate = saved_locate
        cc._analyze_screen_for_element = saved_eye


# ── The answer tells the truth about the live button ──────────────────────────

def test_toggle_on_is_reported_as_on():
    answer, _ = _ask(True)
    assert "Screen control (clicking/typing on screen): ON" in answer
    assert "OFF. The SCREEN button" not in answer


def test_toggle_off_is_reported_as_off():
    answer, _ = _ask(False)
    assert "Screen control (clicking/typing on screen): OFF" in answer


def test_the_question_is_answerable_while_clicking_is_blocked():
    """The whole point: asking must work exactly when the toggle is OFF."""
    answer, _ = _ask(False)
    assert GOLDEN_PHRASE not in answer, "screen_status was blocked by the guard"
    assert "Screen control (clicking/typing on screen): OFF" in answer


def test_status_is_not_an_interactive_action():
    src = Path(cc.__file__).read_text(encoding="utf-8", errors="replace")
    start = src.index("INTERACTIVE_ACTIONS = {")
    block = src[start:src.index("}", start)]
    assert "screen_status" not in block
    assert "screen_click" in block, "the guard list itself went missing"


def test_when_on_the_model_is_told_to_act_not_to_ask():
    answer, _ = _ask(True)
    assert "RIGHT NOW" in answer
    assert "instead of asking the user to enable" in answer


def test_when_off_the_model_is_told_to_recheck_afterwards():
    answer, _ = _ask(False)
    assert "press the SCREEN button" in answer
    assert "screen_status again" in answer


def test_the_two_screen_features_are_never_confused():
    """Live 23:35: Jarvis reached for screen_share_control (Screen View) instead."""
    off, _ = _ask(False)
    on, _ = _ask(True)
    assert "Do NOT use screen_share_control" in off
    assert "Screen View" in off and "Screen View" in on


def test_screen_view_state_travels_with_the_answer():
    ds.update_screen_share(active=True, source_type="full")
    try:
        answer, _ = _ask(True)
        assert "Screen View (vision streaming, a separate feature): ON" in answer
    finally:
        ds.update_screen_share(active=False, source_type=None)


def test_a_broken_dialogue_state_cannot_kill_the_answer():
    """Screen View is a nice-to-have; the toggle answer must survive without it."""
    saved = ds.get

    def _explode():
        raise RuntimeError("dialogue state unavailable")

    ds.get = _explode
    try:
        answer, _ = _ask(True)
    finally:
        ds.get = saved
    assert "Screen control (clicking/typing on screen): ON" in answer
    assert "unknown" in answer


# ── The refusal stops being a permanent verdict ───────────────────────────────

def test_the_refusal_still_opens_with_the_sentence_golden_dispatch_pins():
    assert gate.SCREEN_OFF_MSG.startswith(GOLDEN_PHRASE)


def test_the_refusal_sends_the_model_to_recheck():
    msg = gate.SCREEN_OFF_MSG
    assert "screen_status" in msg
    assert "THIS MOMENT only" in msg
    assert "Never refuse from memory" in msg
    assert "Do NOT call screen_share_control" in msg


def test_the_refusal_text_exists_only_once():
    """Two copies in two files used to drift; one of them kept lying."""
    assert cc._screen_off_message() == gate.SCREEN_OFF_MSG


def test_interactive_actions_are_still_blocked_with_the_toggle_off():
    answer, _ = _ask(False, action="screen_click", description="OK button")
    assert answer.startswith(GOLDEN_PHRASE)


# ── The gate lets the question through without a ceremony ─────────────────────

def test_the_gate_runs_the_question_even_with_the_toggle_off():
    r = gate.dispatch(
        "computer_control", {"action": "screen_status"}, screen_control=False
    )
    assert r.verdict == "run", f"expected run, got {r.verdict}: {r.message!r}"
    assert r.allowed


def test_the_question_never_asks_for_confirmation():
    """An unlisted action inherits risk 'high' -> policy 'confirm' -> a consent
    ticket for asking a question. It must be registered as allowed/low."""
    params = {"action": "screen_status"}
    assert sec.get_risk("computer_control", params) == "low"
    assert sec.get_policy("computer_control", params) == "auto"
    needs, _reason = sec.needs_confirmation("computer_control", params)
    assert needs is False


def test_clicking_still_needs_the_toggle_at_the_gate():
    """Guard against a fix that quietly opened the door for everything."""
    r = gate.dispatch(
        "computer_control",
        {"action": "screen_click", "description": "OK button"},
        screen_control=False,
    )
    assert r.verdict == "screen_off"
    assert not r.allowed


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK   {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            fails += 1
            print(f"  ERR  {fn.__name__}: {e!r}")
    print(f"\nRESULT: {len(tests) - fails}/{len(tests)} " + ("ALL PASS" if not fails else f"-- {fails} FAILURES"))
    sys.exit(1 if fails else 0)
