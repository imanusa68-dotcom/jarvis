# -*- coding: utf-8 -*-
"""
Stage 2.4a - fileops: transactional, reversible file layer over journal+staging.

Covers atomic create/overwrite, reversible move/rename, the scoped-root guard,
preview, no-temp-leftovers, and multi-op LIFO undo end to end. Pure stdlib -
runs offline as a script or via pytest.

Run:  python -m pytest tests/test_fileops_stage2.py -q
or:   python tests/test_fileops_stage2.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from core import store
from core.journal import Journal
from core.staging import Staging
from core.fileops import FileOps, ScopeError


def _env(safe_roots=None):
    d = Path(tempfile.mkdtemp(prefix="jv_fileops_"))
    conn = store.open_store(d / "jarvis.db")
    fo = FileOps(journal=Journal(conn), staging=Staging(root=d / "staging"),
                 safe_roots=safe_roots if safe_roots is not None else [d])
    return fo, d


def _read(p):
    return Path(p).read_text(encoding="utf-8")


def test_create_new_file_and_undo_removes_it():
    fo, d = _env()
    target = d / "new.txt"
    res = fo.replace_file(target, "hello")
    assert res["ok"] and res["created"] is True and res["undoable"]
    assert _read(target) == "hello"
    assert fo.undo_last()["ok"] is True
    assert not target.exists()  # undo of a create removes the file


def test_overwrite_is_reversible():
    fo, d = _env()
    target = d / "doc.txt"
    target.write_text("original", encoding="utf-8")
    res = fo.replace_file(target, "new-content")
    assert res["created"] is False
    assert _read(target) == "new-content"
    assert fo.undo_last()["ok"] is True
    assert _read(target) == "original"  # old bytes restored


def test_move_and_undo():
    fo, d = _env()
    src = d / "a.txt"; src.write_text("X", encoding="utf-8")
    dst = d / "sub" / "b.txt"
    fo.move(src, dst)
    assert dst.exists() and not src.exists()
    assert fo.undo_last()["ok"] is True
    assert src.exists() and not dst.exists()


def test_rename_and_undo():
    fo, d = _env()
    src = d / "old.txt"; src.write_text("Y", encoding="utf-8")
    fo.rename(src, "new.txt")
    assert (d / "new.txt").exists() and not src.exists()
    assert fo.undo_last()["ok"] is True
    assert src.exists() and not (d / "new.txt").exists()


def test_scope_guard_rejects_outside():
    d = Path(tempfile.mkdtemp(prefix="jv_fileops_"))
    outside = Path(tempfile.mkdtemp(prefix="jv_outside_"))
    conn = store.open_store(d / "jarvis.db")
    fo = FileOps(journal=Journal(conn), staging=Staging(root=d / "staging"),
                 safe_roots=[d])
    assert fo.within_scope(d / "ok.txt") is True
    assert fo.within_scope(outside / "nope.txt") is False
    raised = False
    try:
        fo.replace_file(outside / "nope.txt", "x")
    except ScopeError:
        raised = True
    assert raised


def test_preview_reports_overwrite():
    fo, d = _env()
    p = d / "p.txt"
    assert fo.preview("replace_file", path=p)["will_overwrite"] is False
    p.write_text("x", encoding="utf-8")
    pv = fo.preview("replace_file", path=p)
    assert pv["will_overwrite"] is True and pv["exists"] is True


def test_no_temp_files_left_behind():
    fo, d = _env()
    target = d / "clean.txt"
    fo.replace_file(target, "a")
    fo.replace_file(target, "b")
    leftovers = [p.name for p in d.iterdir() if p.name.startswith(".jv_tmp_")]
    assert leftovers == [], f"atomic replace left temp files: {leftovers}"
    assert _read(target) == "b"


def test_multi_op_lifo_undo():
    fo, d = _env()
    f = d / "m.txt"; f.write_text("v0", encoding="utf-8")
    fo.replace_file(f, "v1")        # op 1: overwrite
    fo.rename(f, "m2.txt")           # op 2: rename
    assert (d / "m2.txt").exists()
    fo.undo_last()                   # undo rename -> m.txt back
    assert f.exists() and not (d / "m2.txt").exists()
    fo.undo_last()                   # undo overwrite -> v0 back
    assert _read(f) == "v0"


def test_feature_flag_is_bool():
    from core.feature_flags import fileops_enabled
    assert isinstance(fileops_enabled(), bool)


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
