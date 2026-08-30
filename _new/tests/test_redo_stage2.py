# -*- coding: utf-8 -*-
"""
Stage 2.4e - symmetric N-level UNDO <-> REDO.

The live bug: after undoing one step too many (down through the file's creation,
which DELETES it), 'верни как было' had no way forward - undo was a one-way street.
These tests lock the new behaviour: every undo can be walked forward with redo,
including re-creating a file whose creation was undone.

Run:  PYTHONPATH=.:/data/shims python tests/test_redo_stage2.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from core import store
from core.journal import Journal
from core.staging import Staging
from core.fileops import FileOps
import actions.file_controller as fc
import actions.fileops_bridge as bridge

_ORIG_SAFE_ROOTS = fc._safe_roots

try:
    import pytest as _pytest
except Exception:
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(autouse=True)
    def _restore_globals():
        yield
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def _fo(wire):
    conn = store.open_store(wire / "jarvis.db")
    return FileOps(journal=Journal(conn), staging=Staging(root=wire / "staging"),
                   safe_roots=[wire])


def _wire_fc():
    wire = Path(tempfile.mkdtemp(prefix="jv_redo_"))
    fo = _fo(wire)
    fc._safe_roots = lambda: [wire]
    bridge.set_override(fo)
    return wire


# ---- Pure FileOps level -----------------------------------------------------

def test_redo_reapplies_overwrite_at_fileops_level():
    wire = Path(tempfile.mkdtemp(prefix="jv_redo_"))
    fo = _fo(wire)
    try:
        f = wire / "a.txt"
        fo.replace_file(f, "V1")
        fo.replace_file(f, "V2")
        assert f.read_text(encoding="utf-8") == "V2"
        assert fo.undo_last()["ok"]
        assert f.read_text(encoding="utf-8") == "V1"
        r = fo.redo_last()
        assert r and r["ok"]
        assert f.read_text(encoding="utf-8") == "V2", "redo must re-apply the overwrite"
    finally:
        pass


def test_new_write_clears_redo_stack():
    wire = Path(tempfile.mkdtemp(prefix="jv_redo_"))
    fo = _fo(wire)
    f = wire / "a.txt"
    fo.replace_file(f, "V1")
    fo.replace_file(f, "V2")
    fo.undo_last()                 # back to V1, redo now offers V2
    fo.replace_file(f, "V3")       # NEW forward action -> redo must be cleared
    assert fo.redo_last() is None, "a new write must invalidate redo"
    assert f.read_text(encoding="utf-8") == "V3"


# ---- Full dispatcher / voice-path level -------------------------------------

def test_over_undo_then_redo_all_restores_including_recreated_file():
    """Reproduce the user's exact session and prove 'redo' fixes it."""
    wire = _wire_fc()
    try:
        f = wire / "test.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "привет"})     # create
        fc.file_controller({"action": "write", "path": str(f), "content": "версия2"})    # overwrite
        fc.file_controller({"action": "write", "path": str(f), "content": "привет3"})    # overwrite
        # Over-undo all the way down (3rd undo deletes the file).
        assert "восстановлено" in fc.file_controller({"action": "undo", "path": str(f)})  # -> версия2
        assert f.read_text(encoding="utf-8") == "версия2"
        assert "восстановлено" in fc.file_controller({"action": "undo", "path": str(f)})  # -> привет
        assert f.read_text(encoding="utf-8") == "привет"
        assert "удал" in fc.file_controller({"action": "undo", "path": str(f)}).lower()  # -> deleted
        assert not f.exists()
        # 'verni kak bylo - vse izmeneniya verni' => redo x3 walks all the way forward.
        m1 = fc.file_controller({"action": "redo", "path": str(f)})
        assert "возвращено" in m1.lower(), m1
        assert f.exists() and f.read_text(encoding="utf-8") == "привет", "redo must RECREATE the deleted file"
        fc.file_controller({"action": "redo", "path": str(f)})
        assert f.read_text(encoding="utf-8") == "версия2"
        fc.file_controller({"action": "redo", "path": str(f)})
        assert f.read_text(encoding="utf-8") == "привет3", "all changes restored"
        # Nothing left to redo.
        assert "Нечего" in fc.file_controller({"action": "redo", "path": str(f)})
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def test_undo_redo_pingpong_is_repeatable():
    wire = _wire_fc()
    try:
        f = wire / "p.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "A"})
        fc.file_controller({"action": "write", "path": str(f), "content": "B"})
        for _ in range(3):
            fc.file_controller({"action": "undo", "path": str(f)})
            assert f.read_text(encoding="utf-8") == "A"
            fc.file_controller({"action": "redo", "path": str(f)})
            assert f.read_text(encoding="utf-8") == "B"
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def test_redo_empty_returns_notice():
    wire = _wire_fc()
    try:
        msg = fc.file_controller({"action": "redo", "path": str(wire / "nope.txt")})
        assert "Нечего" in msg, msg
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()



def test_undo_message_reports_actual_content():
    """Anti-hallucination: the undo result must carry the file's REAL content."""
    wire = _wire_fc()
    try:
        f = wire / "c.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "aaa"})
        fc.file_controller({"action": "write", "path": str(f), "content": "bbb"})
        msg = fc.file_controller({"action": "undo", "path": str(f)})
        assert "aaa" in msg, msg
        assert f.read_text(encoding="utf-8") == "aaa"
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()


def test_undo_of_create_message_says_deleted_with_no_content():
    wire = _wire_fc()
    try:
        f = wire / "only.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "solo"})
        msg = fc.file_controller({"action": "undo", "path": str(f)})
        assert "удал" in msg.lower(), msg
        assert "solo" not in msg, "must not claim deleted file still has content"
        assert not f.exists()
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()


def test_history_shows_forward_and_back_with_content_previews():
    """The user's 'verni versiyu so stihom' case: timeline exposes each version."""
    wire = _wire_fc()
    try:
        f = wire / "h.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "one"})
        fc.file_controller({"action": "write", "path": str(f), "content": "two"})
        fc.file_controller({"action": "write", "path": str(f), "content": "three"})
        fc.file_controller({"action": "undo", "path": str(f)})   # now: two; forward: three
        h = fc.file_controller({"action": "history", "path": str(f)})
        assert "two" in h, h        # current content shown
        assert "three" in h, h      # forward (redo) preview
        assert "one" in h, h        # back (undo) preview
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()


def test_history_empty_returns_notice():
    wire = _wire_fc()
    try:
        msg = fc.file_controller({"action": "history", "path": str(wire / "x.txt")})
        assert isinstance(msg, str) and msg
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()



def test_undo_is_scoped_to_named_file():
    """Per-file scoping: undoing file A must NOT revert unrelated file B
    (reproduces the report.txt cross-file bug)."""
    wire = _wire_fc()
    try:
        a = wire / "a.txt"; b = wire / "b.txt"
        fc.file_controller({"action": "write", "path": str(a), "content": "a1"})
        fc.file_controller({"action": "write", "path": str(b), "content": "b1"})
        fc.file_controller({"action": "write", "path": str(b), "content": "b2"})
        # b.txt is the GLOBAL top of the stack; undo targeting a.txt must skip it.
        fc.file_controller({"action": "undo", "path": str(a)})
        assert b.read_text(encoding="utf-8") == "b2"   # b untouched
        assert not a.exists()                            # a's creation undone
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()


def test_history_is_scoped_to_named_file():
    wire = _wire_fc()
    try:
        a = wire / "a.txt"; b = wire / "b.txt"
        fc.file_controller({"action": "write", "path": str(a), "content": "aaa"})
        fc.file_controller({"action": "write", "path": str(b), "content": "bbb"})
        h = fc.file_controller({"action": "history", "path": str(a)})
        assert "bbb" not in h, h   # b's version must not leak into a's timeline
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()


def test_new_session_scopes_unscoped_but_keeps_named_file():
    """Hybrid: after new_session, UNSCOPED 'undo last' sees nothing from the
    prior session, but a NAMED file's timeline is still fully navigable."""
    wire = Path(tempfile.mkdtemp(prefix="jv_redo_"))
    fo = _fo(wire)
    fo.new_session()
    f = wire / "s.txt"
    fo.replace_file(f, "one")
    fo.replace_file(f, "two")
    # simulate a restart: brand-new session id on the SAME journal
    fo.new_session()
    assert fo.undo_last() is None          # unscoped -> nothing from prior session
    h = fo.history(path=str(f))
    assert h["back"]                        # named file -> timeline survives
    assert fo.undo_last(str(f)) is not None # named file -> still undoable


def test_create_over_existing_requires_confirmation():
    wire = _wire_fc()
    try:
        f = wire / "t.txt"
        fc.file_controller({"action": "create_file", "path": str(f), "content": "first"})
        msg = fc.file_controller({"action": "create_file", "path": str(f), "content": "second"})
        assert ("подтвержд" in msg.lower()) or ("перезаписать" in msg.lower()), msg
        assert f.read_text(encoding="utf-8") == "first"   # NOT overwritten yet
        fc.file_controller({"action": "create_file", "path": str(f), "content": "second", "confirmed": True})
        assert f.read_text(encoding="utf-8") == "second"  # confirmed -> overwritten
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS; bridge.clear_override(); bridge.reset()



def _reopen_fo(wire):
    """Simulate closing + reopening the app on the SAME db (new process)."""
    from core import store
    from core.journal import Journal
    from core.staging import Staging
    from core.fileops import FileOps
    conn = store.open_store(wire / "jarvis.db")
    fo = FileOps(journal=Journal(conn), staging=Staging(root=wire / "staging"),
                 safe_roots=[wire])
    fo.new_session()
    return fo


def test_named_file_undo_redo_survive_restart():
    """THE dialogue-2 bug: after restart, undo of a NAMED file must use the
    journal (not the legacy .bak) so redo still works."""
    wire = Path(tempfile.mkdtemp(prefix="jv_redo_"))
    fo = _fo(wire)
    fo.new_session()
    f = wire / "test.txt"
    fo.replace_file(f, "привет")   # create
    fo.replace_file(f, "версиз2")  # overwrite
    # --- restart ---
    fo2 = _reopen_fo(wire)
    # history for the NAMED file is remembered across the restart
    h = fo2.history(path=str(f))
    assert h["back"], "named-file history must survive restart"
    # undo the named file -> journal path, and it must be redoable
    u = fo2.undo_last(str(f))
    assert u and u["ok"]
    assert f.read_text(encoding="utf-8") == "привет"
    r = fo2.redo_last(str(f))
    assert r and r["ok"], "redo must work after a cross-session undo"
    assert f.read_text(encoding="utf-8") == "версиз2"


def test_unscoped_undo_is_limited_to_current_session():
    """Unscoped 'undo last' must NOT reach into a previous session's edits
    (the report.txt cross-session bug)."""
    wire = Path(tempfile.mkdtemp(prefix="jv_redo_"))
    fo = _fo(wire)
    fo.new_session()
    old = wire / "report.txt"
    fo.replace_file(old, "from prior run")
    # --- restart, no new edits this session ---
    fo2 = _reopen_fo(wire)
    assert fo2.undo_last() is None, "unscoped undo must see nothing from a prior session"
    # but the named file is still reachable across the restart
    assert fo2.undo_last(str(old)) is not None


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
