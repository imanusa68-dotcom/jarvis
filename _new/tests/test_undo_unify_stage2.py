# -*- coding: utf-8 -*-
"""
Stage 2.4c - undo unification / drift regression.

Reproduces the real Windows failure that a single-file sandbox run masked:
when the cached FileOps kept STALE safe roots (from an earlier test/session in
a long-lived process), a write silently fell back to the legacy single-slot
backup, but 'undo' still trusted the GLOBAL fileops undo stack and popped an
unrelated entry -> the file was never restored.

Guards:
  A) fileops_bridge refreshes safe roots on the cached instance, so writes and
     undo take the SAME path and undo restores the correct file. A stale legacy
     backup must never shadow the newer fileops version.
  B) multi-level undo actually walks back several steps (the capability the
     model must stop denying).

Run:  PYTHONPATH=.:/data/shims python tests/test_undo_unify_stage2.py
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
_ORIG_RESOLVE = fc._resolve_path
_ORIG_BACKUP = fc._backup_dir

try:
    import pytest as _pytest
except Exception:
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(autouse=True)
    def _restore_globals():
        yield
        fc._safe_roots = _ORIG_SAFE_ROOTS
        fc._resolve_path = _ORIG_RESOLVE
        fc._backup_dir = _ORIG_BACKUP
        bridge.clear_override()
        bridge.reset()


def test_undo_after_stale_fileops_cache_restores_original():
    """Guard A: a stale cached FileOps must not break undo of a later write,
    and a stale legacy backup must not shadow the newer fileops version."""
    # Prime the cached instance with a throwaway root (an 'earlier test').
    stale = tempfile.TemporaryDirectory()
    stale_root = Path(stale.name) / "Desktop"
    stale_root.mkdir(parents=True, exist_ok=True)
    fc._safe_roots = lambda: [stale_root]
    bridge.reset()
    bridge.get_fileops()  # caches an instance scoped to stale_root

    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    root = base / "Desktop"
    root.mkdir(parents=True, exist_ok=True)
    fc._safe_roots = lambda: [root]
    fc._resolve_path = lambda p: root if p == "desktop" else Path(p).expanduser()
    fc._backup_dir = lambda: base / "backups"
    try:
        full = str(root / "note.txt")
        fc.file_controller({"action": "create_file", "path": "desktop", "name": "note.txt"})
        fc.write_file(full, content="ORIGINAL")   # also leaves a stale (empty) legacy backup
        fc.file_controller({"action": "write", "path": full, "name": "note.txt",
                            "content": "NEW"})
        assert (root / "note.txt").read_text(encoding="utf-8") == "NEW"
        fc.file_controller({"action": "undo", "path": full})
        assert (root / "note.txt").read_text(encoding="utf-8") == "ORIGINAL"
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        fc._resolve_path = _ORIG_RESOLVE
        fc._backup_dir = _ORIG_BACKUP
        bridge.clear_override()
        bridge.reset()
        tmp.cleanup()
        stale.cleanup()


def test_multilevel_undo_walks_back_several_steps():
    """Guard B: undo is N-level - two undos step back two versions."""
    wire = Path(tempfile.mkdtemp(prefix="jv_multi_"))
    conn = store.open_store(wire / "jarvis.db")
    fo = FileOps(journal=Journal(conn), staging=Staging(root=wire / "staging"),
                 safe_roots=[wire])
    fc._safe_roots = lambda: [wire]
    bridge.set_override(fo)
    try:
        f = wire / "a.txt"
        fc.file_controller({"action": "write", "path": str(f), "content": "V1"})
        fc.file_controller({"action": "write", "path": str(f), "content": "V2"})
        fc.file_controller({"action": "write", "path": str(f), "content": "V3"})
        assert f.read_text(encoding="utf-8") == "V3"
        fc.file_controller({"action": "undo", "path": str(f)})
        assert f.read_text(encoding="utf-8") == "V2"   # back one step
        fc.file_controller({"action": "undo", "path": str(f)})
        assert f.read_text(encoding="utf-8") == "V1"   # back another step
    finally:
        fc._safe_roots = _ORIG_SAFE_ROOTS
        bridge.clear_override()
        bridge.reset()


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
