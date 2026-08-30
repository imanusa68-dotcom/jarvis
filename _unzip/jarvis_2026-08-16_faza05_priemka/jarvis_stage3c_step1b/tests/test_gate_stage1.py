# -*- coding: utf-8 -*-
"""
Stage 1 — the single execution gate (core/gate.py).

These tests pin the ONE funnel both execution paths use:
  • interactive (main._execute_tool): a human is present → confirm-policy
    actions return verdict="confirm"; the SCREEN toggle is honoured.
  • autonomous (agent.executor._call_tool): no human → fail-closed. confirm
    and screen-gated actions are DENIED, and confirmed=true cannot self-approve.

The gate never bypasses core/security.py; it adapts the same decision to the
caller's mode and audits 100% of calls.

Run:  python -m pytest tests/test_gate_stage1.py -q
or:   python tests/test_gate_stage1.py
"""

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.gate import dispatch, GateResult, SCREEN_OFF_MSG
from core import audit_log
from core.safe_json import STATE_DIR_ENV


# ── Interactive mode ────────────────────────────────────────────────────

def test_interactive_auto_runs():
    for tool, args in [
        ("web_search", {"query": "x"}),
        ("open_app", {"app_name": "spotify"}),
        ("file_controller", {"action": "read", "path": "x"}),
        ("file_controller", {"action": "copy", "path": "x"}),
        ("cmd_control", {"task": "show disk space"}),
    ]:
        g = dispatch(tool, args, mode="interactive")
        assert g.verdict == "run" and g.allowed, f"{tool} -> {g.verdict}"
        assert g.message == ""


def test_interactive_blocked():
    for tool, args in [
        ("dev_agent", {"description": "x"}),
        ("totally_unknown_tool", {}),
        ("youtube_video", {"action": "play", "query": "x"}),
    ]:
        g = dispatch(tool, args, mode="interactive")
        assert g.verdict == "blocked" and not g.allowed, f"{tool} -> {g.verdict}"
        assert g.message.startswith("SECURITY"), g.message


def test_interactive_confirm():
    for tool, args in [
        ("file_controller", {"action": "write", "path": "x", "name": "a.txt", "content": "y"}),
        ("file_controller", {"action": "move", "path": "x", "name": "a", "new_path": "y"}),
        # Stage 2.6: delete is reachable now that it is undoable + Recycle Bin,
        # but it must ALWAYS come through the confirmation gate.
        ("file_controller", {"action": "delete", "path": "x"}),
        ("cmd_control", {"task": "delete all logs"}),
    ]:
        g = dispatch(tool, args, mode="interactive")
        assert g.verdict == "confirm", f"{tool} -> {g.verdict}"
        assert "CONFIRMATION_REQUIRED" in g.message


def test_interactive_confirmed_flag_runs():
    """The LEGACY path, pinned explicitly to the legacy mode.

    This test used to read whatever `durable_consent_enabled` happened to be on
    the machine running it, so switching the mode on a workstation turned it
    red without a single line of source changing. A test that depends on
    ambient state is not testing the code, it is testing the machine - so the
    mode is now stated out loud here and restored afterwards.
    """
    import core.gate as _gate

    original = _gate._consent_enabled
    _gate._consent_enabled = lambda: False
    try:
        g = dispatch(
            "file_controller",
            {"action": "write", "path": "x", "name": "a.txt", "content": "y",
             "confirmed": True},
            mode="interactive",
        )
        assert g.verdict == "run", g.verdict
    finally:
        _gate._consent_enabled = original


def test_interactive_screen_toggle():
    off = dispatch("computer_control", {"action": "screen_click", "description": "b"},
                   mode="interactive", screen_control=False)
    assert off.verdict == "screen_off" and off.message == SCREEN_OFF_MSG
    on = dispatch("computer_control", {"action": "screen_click", "description": "b"},
                  mode="interactive", screen_control=True)
    assert on.verdict == "run", on.verdict


# ── Autonomous mode (fail-closed) ─────────────────────────────────────

def test_autonomous_auto_runs():
    for tool, args in [
        ("web_search", {"query": "x"}),
        ("file_controller", {"action": "read", "path": "x"}),
        ("cmd_control", {"task": "show system info"}),
    ]:
        g = dispatch(tool, args, mode="autonomous")
        assert g.verdict == "run", f"{tool} -> {g.verdict}"


def test_autonomous_confirm_is_denied():
    # write is confirm in interactive; autonomous has no human → deny (blocked).
    g = dispatch("file_controller",
                 {"action": "write", "path": "x", "name": "a.txt", "content": "y"},
                 mode="autonomous")
    assert g.verdict == "blocked" and not g.allowed, g.verdict
    assert g.message.startswith("SECURITY")


def test_autonomous_confirmed_cannot_self_approve():
    # A hallucinated confirmed=true must NOT let an unattended plan run a
    # confirm-gated action.
    g = dispatch("file_controller",
                 {"action": "write", "path": "x", "name": "a.txt", "content": "y",
                  "confirmed": True},
                 mode="autonomous")
    assert g.verdict == "blocked", g.verdict


def test_autonomous_dangerous_cmd_denied():
    g = dispatch("cmd_control", {"task": "delete all logs"}, mode="autonomous")
    assert g.verdict == "blocked", g.verdict


def test_autonomous_screen_action_denied():
    g = dispatch("computer_control", {"action": "screen_click", "description": "b"},
                 mode="autonomous")
    assert g.verdict == "screen_off" and not g.allowed
    assert g.message.startswith("SECURITY")


def test_autonomous_blocked_stays_blocked():
    for tool, args in [
        ("file_controller", {"action": "delete", "path": "x"}),
        ("totally_unknown_tool", {}),
    ]:
        g = dispatch(tool, args, mode="autonomous")
        assert g.verdict == "blocked", f"{tool} -> {g.verdict}"


# ── Audit — 100% of calls are recorded with a verdict ──────────────────────

@contextlib.contextmanager
def _temp_home():
    """Point ALL durable state at a throwaway directory for one test.

    Step 32 (phase 0): the audit journal moved to ~/.jarvis/logs and its path
    is resolved at CALL time, so a test must redirect the state directory
    itself. Asserting against the real journal would both pollute the owner's
    records and make the test depend on his machine.
    """
    tmp = tempfile.mkdtemp(prefix="jv_gate_audit_")
    saved = os.environ.get(STATE_DIR_ENV)
    os.environ[STATE_DIR_ENV] = tmp
    audit_log.reset()
    try:
        yield Path(tmp) / "logs" / "gate-audit.jsonl"
    finally:
        if saved is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = saved
        audit_log.reset()
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_writes_a_verdict_line():
    with _temp_home() as journal:
        dispatch("web_search", {"query": "marker-abc"}, mode="interactive")
        dispatch("file_controller", {"action": "delete", "path": "x"}, mode="autonomous")
        assert audit_log.path() == journal
        assert journal.exists(), "audit log was not created"
        lines = [l for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 2
        rec = json.loads(lines[-1])
        for key in ("ts", "tool", "verdict", "risk", "policy", "mode", "param_keys"):
            assert key in rec, f"missing {key} in audit record"
        assert rec["verdict"] in ("run", "blocked", "screen_off", "confirm")
        # values must never be logged — only keys
        assert isinstance(rec["param_keys"], list)


if __name__ == "__main__":
    import sys
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
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print("\nRESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)
