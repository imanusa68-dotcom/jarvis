"""
Slice A — action journal (memory of what Jarvis did).

A1: dialogue_state keeps a short rolling journal of recent actions with a
concrete target, injected into context so follow-ups ("change that file")
resolve. Survives reconnect (reset) — exactly when live history is lost.

A2: file create/write results carry the FULL path, so the live conversation
history carries the exact target within a session.

These exercise dialogue_state + file_controller directly (no Tkinter / no Live).

Run:  python -m pytest tests/test_action_journal.py -q
or:   python tests/test_action_journal.py
"""

import importlib
import tempfile
from pathlib import Path

import core.dialogue_state as ds
import actions.file_controller as fc


def _fresh():
    importlib.reload(ds)
    return ds


# ─────────────────────────────────────────────────────────────────────────────
# A1 — journal behaviour
# ─────────────────────────────────────────────────────────────────────────────

def test_journal_records_and_shows_in_prompt():
    d = _fresh()
    d.record_action("file_controller", "create_file", "Created file: a.txt (full path: C:/Users/x/Desktop/a.txt)")
    block = d.format_for_prompt()
    assert "Recent actions" in block
    assert "create_file" in block
    assert "a.txt" in block


def test_journal_is_a_ring_buffer():
    d = _fresh()
    for i in range(20):
        d.record_action("open_app", None, f"opened app{i}")
    journal = d.get()["action_journal"]
    assert len(journal) == d._JOURNAL_MAX, len(journal)
    # Only the most recent survive.
    assert journal[-1]["summary"].endswith("app19")
    assert all("app0:" not in e["summary"] for e in journal)


def test_journal_marks_success_and_failure():
    d = _fresh()
    d.record_action("file_controller", "write", "ok", ok=True)
    d.record_action("file_controller", "delete", "blocked", ok=False)
    block = d.format_for_prompt()
    assert "✓" in block and "✗" in block


def test_journal_survives_reset_reconnect():
    # Reconnect is exactly when live history is lost → journal must persist.
    d = _fresh()
    d.record_action("file_controller", "create_file", "Created file: note.txt")
    d.reset()
    journal = d.get()["action_journal"]
    assert len(journal) == 1
    assert "note.txt" in d.format_for_prompt()


def test_empty_tool_is_ignored():
    d = _fresh()
    d.record_action("", None, "noise")
    assert d.get()["action_journal"] == []


def test_journal_does_not_disturb_screen_or_memory_fields():
    d = _fresh()
    d.set_screen_control(True)
    d.record_action("open_app", None, "opened spotify")
    snap = d.get()
    assert snap["screen_control_active"] is True
    assert len(snap["action_journal"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# A2 — rich results carry the full path
# ─────────────────────────────────────────────────────────────────────────────

class _TempRoots:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "Desktop"
        root.mkdir(parents=True, exist_ok=True)
        self._orig = fc._safe_roots
        fc._safe_roots = lambda: [root]  # type: ignore
        return root

    def __exit__(self, *exc):
        fc._safe_roots = self._orig  # type: ignore
        self._tmp.cleanup()


def test_create_result_contains_full_path():
    with _TempRoots() as root:
        target = root / "привет.txt"
        r = fc.create_file(str(target), content="hi")
        assert "full path:" in r
        assert str(target) in r


def test_write_result_contains_full_path():
    with _TempRoots() as root:
        target = root / "log.txt"
        r = fc.write_file(str(target), content="x")
        assert str(target) in r


if __name__ == "__main__":
    import sys
    tests = [
        test_journal_records_and_shows_in_prompt,
        test_journal_is_a_ring_buffer,
        test_journal_marks_success_and_failure,
        test_journal_survives_reset_reconnect,
        test_empty_tool_is_ignored,
        test_journal_does_not_disturb_screen_or_memory_fields,
        test_create_result_contains_full_path,
        test_write_result_contains_full_path,
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
