# -*- coding: utf-8 -*-
"""
Stage 2.2 - journal: saga journal + undo stack on top of core.store.

Covers the durable action journal (superset of the RAM ring buffer), the saga
lifecycle (intent -> complete), the LIFO undo stack, idempotency and crash
recovery (open_intents). Pure stdlib - runs offline as a script or via pytest.

Run:  python -m pytest tests/test_journal_stage2.py -q
or:   python tests/test_journal_stage2.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from core import store
from core.journal import Journal, JOURNAL_MAX


def _journal():
    d = tempfile.mkdtemp(prefix="jv_journal_")
    conn = store.open_store(Path(d) / "jarvis.db")
    return Journal(conn)


def test_record_action_and_recent_order():
    j = _journal()
    j.record_action("file_controller", "write", "/tmp/a.txt")
    j.record_action("open_app", None, "Notepad", ok=False)
    recent = j.recent_actions()
    assert len(recent) == 2
    # most recent last
    assert recent[0]["summary"] == "file_controller/write: /tmp/a.txt"
    assert recent[1]["summary"] == "open_app: Notepad"
    assert recent[1]["ok"] is False


def test_record_action_ignores_empty_tool():
    j = _journal()
    assert j.record_action("", "x", "nope") is None
    assert j.recent_actions() == []


def test_recent_actions_limit():
    j = _journal()
    for i in range(JOURNAL_MAX + 5):
        j.record_action("t", None, f"a{i}")
    recent = j.recent_actions()
    assert len(recent) == JOURNAL_MAX  # hot slice capped
    assert recent[-1]["summary"] == f"t: a{JOURNAL_MAX + 4}"  # newest last


def test_format_for_prompt_marks():
    j = _journal()
    assert j.format_for_prompt() == ""  # empty -> no block
    j.record_action("t", None, "ok one", ok=True)
    j.record_action("t", None, "bad one", ok=False)
    out = j.format_for_prompt()
    assert out.startswith("Recent actions you performed")
    assert "\u2713 t: ok one" in out
    assert "\u2717 t: bad one" in out


def test_saga_lifecycle_and_undo():
    j = _journal()
    inverse = {"op": "move", "from": "/dst/b.txt", "to": "/src/b.txt"}
    sid = j.begin_intent("file_controller", "move", intent={"to": "/dst/b.txt"},
                         inverse=inverse, label="Moved b.txt")
    # before complete: it is an open intent (crash-recovery candidate)
    assert [s["id"] for s in j.open_intents()] == [sid]
    assert j.peek_undo() is None  # not undoable until complete

    j.complete(sid)
    assert j.open_intents() == []  # no longer open
    top = j.peek_undo()
    assert top is not None and top["saga_id"] == sid
    assert top["inverse"] == inverse  # JSON round-trips to the same dict

    popped = j.undo_last()
    assert popped["saga_id"] == sid
    assert popped["inverse"] == inverse
    assert j.get_saga(sid)["status"] == "compensated"
    # stack now empty and stays empty (idempotent)
    assert j.undo_last() is None
    assert j.peek_undo() is None


def test_complete_is_idempotent():
    j = _journal()
    sid = j.begin_intent("t", "x", inverse={"a": 1}, label="L")
    j.complete(sid)
    j.complete(sid)  # second call must not double-push undo
    assert len(j.open_undo_entries()) == 1
    assert j.get_saga(sid)["status"] == "done"


def test_no_inverse_means_not_undoable():
    j = _journal()
    sid = j.begin_intent("t", "read", inverse=None, label="read-only")
    j.complete(sid)
    assert j.peek_undo() is None  # nothing to undo for a no-inverse op
    assert j.get_saga(sid)["status"] == "done"


def test_undo_is_lifo():
    j = _journal()
    a = j.begin_intent("t", "1", inverse={"n": 1}, label="A"); j.complete(a)
    b = j.begin_intent("t", "2", inverse={"n": 2}, label="B"); j.complete(b)
    assert j.undo_last()["saga_id"] == b  # newest first
    assert j.undo_last()["saga_id"] == a
    assert j.undo_last() is None


def test_mark_failed_keeps_it_off_undo_stack():
    j = _journal()
    sid = j.begin_intent("t", "x", inverse={"a": 1}, label="L")
    j.mark_failed(sid, detail="disk full")
    assert j.get_saga(sid)["status"] == "failed"
    assert j.open_intents() == []
    assert j.peek_undo() is None


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
