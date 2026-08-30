"""
Stage 2 safety net — the confirm-gate mechanism.

Stage 2 invariant: a tool whose resolved PolicyMode is "confirm" must NOT run
until the model re-calls it with confirmed=true. The gate enforces the flag
itself; the prompt instruction is only UX.

Because no real tool is currently "confirm" (all working tools are auto, all
dangerous ones are forbid), we prove the mechanism on a SYNTHETIC policy entry
injected for the duration of the test, then removed. Real behaviour is untouched.

Run:  python -m pytest tests/test_security_stage2.py -q
or:   python tests/test_security_stage2.py
"""

import core.security as sec
from core.security import (
    needs_confirmation,
    format_confirmation_request,
    get_policy,
    check_tool_call,
)


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic confirm tool — injected temporarily so we can exercise the cycle.
# ─────────────────────────────────────────────────────────────────────────────

def _with_synthetic_confirm_tool(fn):
    """Run fn() with a temporary allowed+high (→confirm) tool in the policy."""
    name = "_synthetic_confirm_tool"
    sec.SECURITY_POLICY[name] = sec.ToolPolicy(
        status="allowed", planner_visible=True, risk="high",
        reason="synthetic confirm tool for tests",
    )
    try:
        fn(name)
    finally:
        sec.SECURITY_POLICY.pop(name, None)


def test_synthetic_tool_resolves_to_confirm():
    def check(name):
        assert get_policy(name, {}) == "confirm", get_policy(name, {})
    _with_synthetic_confirm_tool(check)


def test_confirm_required_when_not_confirmed():
    def check(name):
        need, reason = needs_confirmation(name, {})
        assert need is True
        assert reason  # non-empty human reason
        msg = format_confirmation_request(name, reason)
        assert msg.startswith("CONFIRMATION_REQUIRED:")
    _with_synthetic_confirm_tool(check)


def test_no_confirm_once_confirmed_true():
    def check(name):
        for flag in (True, "true", "yes", "1"):
            need, _ = needs_confirmation(name, {"confirmed": flag})
            assert need is False, f"confirmed={flag!r} should skip confirmation"
    _with_synthetic_confirm_tool(check)


def test_gate_passes_through_check_tool_call_when_confirmed():
    def check(name):
        # confirmed=true: the allow/block gate still says allowed (confirm is a
        # separate, later gate), and needs_confirmation is now False.
        d = check_tool_call(name, {"confirmed": True})
        assert d.allowed
        assert needs_confirmation(name, {"confirmed": True})[0] is False
    _with_synthetic_confirm_tool(check)


# ─────────────────────────────────────────────────────────────────────────────
# Everyday safe tools must NEVER ask for confirmation (guards confirm-fatigue).
# As Stage 3 unblocks dangerous actions, only those should become confirm.
# ─────────────────────────────────────────────────────────────────────────────

def test_everyday_safe_tools_never_confirm():
    real_calls = [
        ("web_search", {"query": "x"}),
        ("open_app", {"app_name": "spotify"}),
        ("file_controller", {"action": "read", "path": "x"}),
        ("file_controller", {"action": "copy", "path": "x"}),
        ("file_controller", {"action": "create_file", "path": "desktop"}),  # Stage 3: auto
        ("reminder", {"action": "list"}),
        ("browser_control", {"action": "go_to", "url": "x"}),
        ("cmd_control", {"task": "show disk space"}),
        ("computer_control", {"action": "screenshot"}),
        ("game_updater", {"action": "list"}),
        ("weather_report", {"city": "x"}),
    ]
    for name, args in real_calls:
        need, _ = needs_confirmation(name, args)
        assert need is False, f"{name} unexpectedly requires confirmation"


def test_stage3_write_now_confirms():
    # Stage 3 slice 1: writing a file is a real confirm action now.
    need, reason = needs_confirmation("file_controller", {"action": "write", "path": "desktop"})
    assert need is True and reason


def test_confirmed_flag_does_not_break_blocked_tools():
    # A blocked tool stays blocked even if confirmed=true is passed.
    d = check_tool_call("dev_agent", {"description": "x", "confirmed": True})
    assert not d.allowed


if __name__ == "__main__":
    import sys
    tests = [
        test_synthetic_tool_resolves_to_confirm,
        test_confirm_required_when_not_confirmed,
        test_no_confirm_once_confirmed_true,
        test_gate_passes_through_check_tool_call_when_confirmed,
        test_everyday_safe_tools_never_confirm,
        test_stage3_write_now_confirms,
        test_confirmed_flag_does_not_break_blocked_tools,
    ]
    fails = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK   {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print("\nRESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)
