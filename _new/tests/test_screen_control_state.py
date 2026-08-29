"""
Regression test for the SCREEN-control desync bug.

Bug: with the SCREEN button ON, Jarvis still told the user "screen control is
disabled" because the live toggle state was never injected into the model's
context — the model guessed.

Fix: dialogue_state carries screen_control_active, set from ui.py, and
format_for_prompt() always reports ON/OFF so the model never guesses.

These tests exercise dialogue_state directly (no Tkinter / no live session).

Run:  python -m pytest tests/test_screen_control_state.py -q
or:   python tests/test_screen_control_state.py
"""

import importlib
import core.dialogue_state as ds


def _fresh():
    """Reload dialogue_state so each test starts from a clean module state."""
    importlib.reload(ds)
    return ds


def test_default_is_off_and_reported():
    d = _fresh()
    block = d.format_for_prompt()
    assert "Screen control (clicking/typing on screen): OFF" in block


def test_on_is_reported_and_tells_model_not_to_ask():
    d = _fresh()
    d.set_screen_control(True)
    block = d.format_for_prompt()
    assert "Screen control (clicking/typing on screen): ON" in block
    assert "already on" in block.lower()
    # Must NOT simultaneously claim it is off.
    assert "screen control (clicking/typing on screen): off" not in block.lower()


def test_toggle_off_again():
    d = _fresh()
    d.set_screen_control(True)
    d.set_screen_control(False)
    block = d.format_for_prompt()
    assert "Screen control (clicking/typing on screen): OFF" in block


def test_survives_reset_reconnect():
    # The physical button does not un-press on reconnect → state must survive.
    d = _fresh()
    d.set_screen_control(True)
    d.reset()
    assert d.get()["screen_control_active"] is True
    assert "Screen control (clicking/typing on screen): ON" in d.format_for_prompt()


def test_screen_view_fields_untouched():
    # The fix must not disturb the separate Screen VIEW (vision) feature.
    d = _fresh()
    d.set_screen_control(True)
    snap = d.get()
    assert snap["screen_share_active"] is False
    assert snap["screen_share_source_type"] is None
    # And toggling Screen View does not flip Screen Control.
    d.update_screen_share(active=True, source_type="full")
    snap2 = d.get()
    assert snap2["screen_share_active"] is True
    assert snap2["screen_control_active"] is True  # unchanged by the View toggle


if __name__ == "__main__":
    import sys
    tests = [
        test_default_is_off_and_reported,
        test_on_is_reported_and_tells_model_not_to_ask,
        test_toggle_off_again,
        test_survives_reset_reconnect,
        test_screen_view_fields_untouched,
    ]
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
    print("\nRESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)
