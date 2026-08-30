# -*- coding: utf-8 -*-
"""
Stage 2.4d - truthful undo messages.

When a deep rewind reaches a file's ORIGINAL creation, undoing it DELETES the
file. Previously the message still said 'восстановлено прежнее состояние', so
the model told the user the file was still there. These tests lock the accurate
messages so the model can narrate honestly.

Run:  PYTHONPATH=.:/data/shims python tests/test_undo_delete_message_stage2.py
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


def _wire():
    wire = Path(tempfile.mkdtemp(prefix="jv_msg_"))
    conn = store.open_store(wire / "jarvis.db")
    fo = FileOps(journal=Journal(conn), staging=Staging(root=wire / "staging"),
                 safe_roots=[wire])
    fc._safe_roots = lambda: [wire]
    bridge.set_override(fo)
    return wire


def test_undo_of_creation_reports_file_deleted_and_removes_it():
    wire = _wire()
    try:
        f = wire / "a.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "V1"})  # create
        fc.file_controller({"action": "write", "path": str(f), "content": "V2"})  # overwrite
        # First undo: content rollback -> must say 'восстановлено', file stays.
        msg1 = fc.file_controller({"action": "undo", "path": str(f)})
        assert "восстановлено" in msg1, msg1
        assert f.read_text(encoding="utf-8") == "V1"
        # Second undo: reaches the creation -> must say 'файл удалён', file gone.
        msg2 = fc.file_controller({"action": "undo", "path": str(f)})
        assert "удал" in msg2.lower(), msg2
        assert not f.exists()
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def test_undo_of_rename_reports_move_reverted():
    wire = _wire()
    try:
        src = wire / "old.txt"
        fc.file_controller({"action": "write", "path": str(src), "content": "DATA"})
        fc.file_controller({"action": "rename", "path": str(src), "new_name": "new.txt"})
        assert (wire / "new.txt").exists()
        msg = fc.file_controller({"action": "undo", "path": str(wire / "new.txt")})
        assert "перемещ" in msg.lower() or "переимен" in msg.lower(), msg
        assert (wire / "old.txt").exists() and not (wire / "new.txt").exists()
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
