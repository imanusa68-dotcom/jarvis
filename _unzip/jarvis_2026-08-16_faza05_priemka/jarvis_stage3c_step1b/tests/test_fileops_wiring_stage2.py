# -*- coding: utf-8 -*-
"""
Stage 2.4b - fileops wired into the file_controller dispatcher.

When the shared FileOps is active, create/write/move/rename become reversible
and the 'undo' action pops the LIFO undo stack. Append and the flag-OFF case
stay on the legacy path. Offline, stdlib only (+ send2trash shim on PYTHONPATH).

Run:  PYTHONPATH=.:/data/shims python tests/test_fileops_wiring_stage2.py
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

# Capture pristine reference at import time (before any test mutates it) so we
# can restore it after every test and never leak into other test modules.
_ORIG_SAFE_ROOTS = fc._safe_roots

try:
    import pytest as _pytest
except Exception:            # standalone run (no pytest) — leaking is harmless
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(autouse=True)
    def _restore_globals():
        """Undo this module's monkeypatching after each test (pytest isolation)."""
        yield
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def _wire():
    tmp = Path(tempfile.mkdtemp(prefix="jv_wire_"))
    conn = store.open_store(tmp / "jarvis.db")
    fo = FileOps(journal=Journal(conn), staging=Staging(root=tmp / "staging"),
                 safe_roots=[tmp])
    fc._safe_roots = lambda: [tmp]   # scope file_controller's guard to tmp
    bridge.set_override(fo)
    return tmp


def test_create_then_undo_removes():
    tmp = _wire()
    r = fc.file_controller({"action": "create_file", "path": str(tmp),
                            "name": "a.txt", "content": "hi"})
    assert "Created" in r, r
    assert (tmp / "a.txt").read_text(encoding="utf-8") == "hi"
    fc.file_controller({"action": "undo"})
    assert not (tmp / "a.txt").exists()   # undo of a create removes it


def test_write_overwrite_then_undo_restores():
    tmp = _wire()
    f = tmp / "a.txt"; f.write_text("v1", encoding="utf-8")
    fc.file_controller({"action": "write", "path": str(f), "content": "v2"})
    assert f.read_text(encoding="utf-8") == "v2"
    fc.file_controller({"action": "undo"})
    assert f.read_text(encoding="utf-8") == "v1"   # old bytes restored


def test_append_stays_legacy():
    tmp = _wire()
    f = tmp / "log.txt"; f.write_text("a", encoding="utf-8")
    fc.file_controller({"action": "write", "path": str(f),
                        "content": "b", "append": True})
    assert f.read_text(encoding="utf-8") == "ab"   # append not routed to replace


def test_move_then_undo():
    tmp = _wire()
    src = tmp / "m.txt"; src.write_text("X", encoding="utf-8")
    (tmp / "sub").mkdir()
    fc.file_controller({"action": "move", "path": str(src),
                        "destination": str(tmp / "sub")})
    assert (tmp / "sub" / "m.txt").exists() and not src.exists()
    fc.file_controller({"action": "undo"})
    assert src.exists() and not (tmp / "sub" / "m.txt").exists()


def test_rename_then_undo():
    tmp = _wire()
    src = tmp / "r1.txt"; src.write_text("Z", encoding="utf-8")
    fc.file_controller({"action": "rename", "path": str(src),
                        "new_name": "r2.txt"})
    assert (tmp / "r2.txt").exists() and not src.exists()
    fc.file_controller({"action": "undo"})
    assert src.exists() and not (tmp / "r2.txt").exists()


def test_flag_off_returns_none():
    import core.feature_flags as ff
    orig = ff.fileops_enabled
    bridge.clear_override()
    bridge.reset()
    try:
        ff.fileops_enabled = lambda: False
        assert bridge.get_fileops() is None   # OFF -> legacy path
    finally:
        ff.fileops_enabled = orig


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
