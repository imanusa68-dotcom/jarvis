"""
Stage 0 + Stage 1 safety net for the central security policy.

Stage 0 invariant: the gate must preserve the pre-refactor runtime behaviour
(blocked stays blocked; interactive computer_control depends on the SCREEN toggle).

Stage 1 invariant: every decision carries a PolicyMode (auto/confirm/forbid)
derived from (status, risk). Nothing acts on it yet, so behaviour is unchanged.

Run:  python -m pytest tests/test_security_stage1.py -q
or:   python tests/test_security_stage1.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.security import (
    check_tool_call,
    format_security_block,
    resolve_policy,
    get_policy,
    get_risk,
)
from core.uncertainty_policy import classify_risk, RiskLevel


# ─────────────────────────────────────────────────────────────────────────────
# Stage 0 — behaviour preservation (mirrors the gate logic in main._execute_tool)
# ─────────────────────────────────────────────────────────────────────────────

def _gate(name, args, screen_control):
    """Reproduce main._execute_tool's gate outcome as one of RUN/BLOCKED/SCREEN_OFF."""
    d = check_tool_call(name, args)
    if not d.allowed:
        # format_security_block must always produce a non-empty message
        assert format_security_block(d)
        return "BLOCKED"
    if d.requires_screen_control and not screen_control:
        return "SCREEN_OFF"
    return "RUN"


STAGE0_CASES = [
    ("web_search", {"query": "x"}, False, "RUN"),
    ("open_app", {"app_name": "spotify"}, False, "RUN"),
    ("weather_report", {"city": "x"}, False, "RUN"),
    ("reminder", {"action": "list"}, False, "RUN"),
    ("file_controller", {"action": "read", "path": "x"}, False, "RUN"),
    ("file_controller", {"action": "copy", "path": "x"}, False, "RUN"),
    # delete was unblocked in Stage 2.6: it is now undoable (staged copy) and
    # goes to the Recycle Bin, behind a confirmation. Like write above, the
    # Stage-0 helper only models allow/block, so it reports RUN; the mandatory
    # confirm step is covered in test_stage26_ops.py / test_stage3_files.py.
    ("file_controller", {"action": "delete", "path": "x"}, False, "RUN"),
    # write was unblocked in Stage 3 slice 1 (now allowed → confirm). The Stage-0
    # gate helper only models allow/block, so it reports RUN; the confirm step is
    # covered in test_stage3_files.py / test_security_stage2.py.
    ("file_controller", {"action": "write", "path": "x"}, False, "RUN"),
    ("code_helper", {"action": "explain"}, False, "RUN"),
    ("code_helper", {"action": "run"}, False, "BLOCKED"),
    ("dev_agent", {"description": "x"}, False, "BLOCKED"),
    ("send_message", {"receiver": "a", "message_text": "b", "platform": "tg"}, False, "BLOCKED"),
    ("computer_settings", {"action": "shutdown"}, False, "BLOCKED"),
    ("youtube_video", {"action": "get_info", "url": "x"}, False, "RUN"),
    ("youtube_video", {"action": "play", "query": "x"}, False, "BLOCKED"),
    ("game_updater", {"action": "list"}, False, "RUN"),
    ("game_updater", {"action": "install", "game_name": "x"}, False, "BLOCKED"),
    ("desktop_control", {"action": "list"}, False, "RUN"),
    ("desktop_control", {"action": "wallpaper"}, False, "BLOCKED"),
    ("computer_control", {"action": "screenshot"}, False, "RUN"),
    ("computer_control", {"action": "screen_click", "description": "btn"}, False, "SCREEN_OFF"),
    ("computer_control", {"action": "screen_click", "description": "btn"}, True, "RUN"),
    ("computer_control", {"action": "focus_window", "title": "TG"}, False, "SCREEN_OFF"),
    ("computer_control", {"action": "focus_window", "title": "TG"}, True, "RUN"),
    ("computer_control", {"action": "type", "text": "hi"}, True, "RUN"),
    ("analyze_screen_view", {"question": "x"}, False, "RUN"),
    ("screen_share_control", {"action": "start"}, False, "RUN"),
    ("agent_task", {"goal": "x"}, False, "RUN"),
    ("save_memory", {"category": "notes", "key": "k", "value": "v"}, False, "RUN"),
]


def test_stage0_behaviour_preserved():
    for name, args, sc, expected in STAGE0_CASES:
        assert _gate(name, args, sc) == expected, f"{name}/{args} sc={sc}"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — PolicyMode invariants
# ─────────────────────────────────────────────────────────────────────────────

def test_blocked_is_always_forbid():
    for name, args in [
        ("dev_agent", {"description": "x"}),
        ("send_message", {"receiver": "a"}),
        ("computer_settings", {"action": "shutdown"}),
        ("file_controller", {"action": "organize_desktop", "path": "x"}),
        ("youtube_video", {"action": "play"}),
        ("totally_unknown", {}),
    ]:
        d = check_tool_call(name, args)
        assert not d.allowed
        assert d.policy == "forbid", f"{name} should be forbid, got {d.policy}"


def test_safe_actions_are_auto():
    for name, args in [
        ("web_search", {"query": "x"}),
        ("open_app", {"app_name": "spotify"}),
        ("file_controller", {"action": "read", "path": "x"}),
        ("file_controller", {"action": "copy", "path": "x"}),
        ("reminder", {"action": "list"}),
    ]:
        d = check_tool_call(name, args)
        assert d.allowed and d.policy == "auto", f"{name} -> {d.policy}"


def test_screen_actions_are_auto_not_double_prompted():
    # Interactive computer_control is gated by the SCREEN toggle, not by a
    # confirm prompt on top of it.
    d = check_tool_call("computer_control", {"action": "screen_click", "description": "b"})
    assert d.allowed and d.requires_screen_control and d.policy == "auto"


def test_resolve_policy_mapping():
    assert resolve_policy("blocked", "low") == "forbid"
    assert resolve_policy("requires_screen_control", "high") == "auto"
    assert resolve_policy("allowed", "low") == "auto"
    assert resolve_policy("allowed", "medium") == "auto"
    assert resolve_policy("allowed", "high") == "confirm"
    assert resolve_policy("allowed", "critical") == "confirm"
    # explicit override wins
    assert resolve_policy("allowed", "low", explicit="confirm") == "confirm"


def test_dangerous_cmd_promotes_risk_and_confirm():
    # cmd_control is allowed+medium normally → auto.
    assert get_policy("cmd_control", {"task": "show disk space"}) == "auto"
    # A destructive free-text task should promote to high → confirm.
    assert get_risk("cmd_control", {"task": "delete all logs"}) == "high"
    assert get_policy("cmd_control", {"task": "delete all logs"}) == "confirm"


def test_risk_source_is_single_and_critical_maps_to_fatal():
    # uncertainty_policy must read risk from security (one source).
    assert classify_risk("dev_agent", {"description": "x"}) == RiskLevel.FATAL
    assert classify_risk("web_search", {"query": "x"}) == RiskLevel.LOW
    # Stage 2.6: delete dropped critical -> high (reversible + confirm-gated),
    # but it must stay HIGH, never medium/low.
    assert classify_risk("file_controller", {"action": "delete"}) == RiskLevel.HIGH


if __name__ == "__main__":
    import sys
    fails = 0
    for fn in [
        test_stage0_behaviour_preserved,
        test_blocked_is_always_forbid,
        test_safe_actions_are_auto,
        test_screen_actions_are_auto_not_double_prompted,
        test_resolve_policy_mapping,
        test_dangerous_cmd_promotes_risk_and_confirm,
        test_risk_source_is_single_and_critical_maps_to_fatal,
    ]:
        try:
            fn()
            print(f"  OK   {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print("\nRESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)
