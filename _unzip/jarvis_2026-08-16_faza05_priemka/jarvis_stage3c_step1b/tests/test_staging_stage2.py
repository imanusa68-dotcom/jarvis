# -*- coding: utf-8 -*-
"""
Stage 2.3 - staging: N-level backup area with quota, wired to the saga undo stack.

Covers stash/restore, N-level history (versions not overwritten), quota eviction
(honest undo depth), the legacy ~/.jarvis/backups bridge, and an end-to-end
journal integration (stash -> begin_intent -> complete -> undo_last -> restore).
Pure stdlib - runs offline as a script or via pytest.

Run:  python -m pytest tests/test_staging_stage2.py -q
or:   python tests/test_staging_stage2.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import hashlib
import tempfile
from pathlib import Path

from core import store
from core.journal import Journal
from core.staging import Staging


def _dir():
    return Path(tempfile.mkdtemp(prefix="jv_staging_"))


def _staging():
    d = _dir()
    return Staging(root=d / "staging"), d


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_stash_and_restore_roundtrip():
    st, d = _staging()
    f = _write(d / "a.txt", "v1")
    rec = st.stash(f)
    assert rec and rec["kind"] == "file-restore"
    f.write_text("v2-mutated", encoding="utf-8")  # user/agent overwrote it
    assert st.restore(rec) is True
    assert f.read_text(encoding="utf-8") == "v1"  # rolled back


def test_stash_missing_or_dir_returns_none():
    st, d = _staging()
    assert st.stash(d / "nope.txt") is None
    sub = d / "sub"; sub.mkdir()
    assert st.stash(sub) is None


def test_n_level_versions_not_overwritten():
    st, d = _staging()
    f = _write(d / "note.txt", "one")
    r1 = st.stash(f)
    f.write_text("two", encoding="utf-8")
    r2 = st.stash(f)
    assert r1["staged"] != r2["staged"]  # distinct staged copies
    # restoring the OLDER version works too (multi-level history)
    assert st.restore(r2) and f.read_text(encoding="utf-8") == "two"
    assert st.restore(r1) and f.read_text(encoding="utf-8") == "one"


def test_quota_evicts_oldest_and_undo_is_honest():
    d = _dir()
    st = Staging(root=d / "staging", quota_bytes=2500)  # ~2 files of 1000B
    payload = "x" * 1000
    recs = []
    for i in range(4):
        f = _write(d / f"f{i}.txt", payload)
        recs.append(st.stash(f))
    assert st.total_bytes() <= 2500, "quota must be respected"
    # oldest evicted -> their undo is honestly unavailable
    assert st.available(recs[0]) is False
    assert st.restore(recs[0]) is False
    # newest survives
    assert st.available(recs[-1]) is True


def test_legacy_backup_bridge():
    st, d = _staging()
    target = d / "docs" / "report.txt"
    _write(target, "current")
    # Fabricate a Stage-1 legacy backup at the exact path the old code used.
    from core import staging as S
    orig_app = S._app_dir
    try:
        S._app_dir = lambda: d / ".jarvis"  # redirect legacy dir into tmp
        legacy = S.legacy_backup_path(target)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("old-version", encoding="utf-8")
        assert S.has_legacy_backup(target) is True
        assert S.restore_legacy(target) is True
        assert target.read_text(encoding="utf-8") == "old-version"
    finally:
        S._app_dir = orig_app


def test_end_to_end_with_journal():
    d = _dir()
    st = Staging(root=d / "staging")
    conn = store.open_store(d / "jarvis.db")
    j = Journal(conn)

    f = _write(d / "e2e.txt", "before")
    rec = st.stash(f)                                   # 1. stage current bytes
    sid = j.begin_intent("file_controller", "write",    # 2. record intent
                         intent={"path": str(f)}, inverse=rec,
                         label="Overwrote e2e.txt")
    f.write_text("after", encoding="utf-8")             # 3. do the mutation
    j.complete(sid)                                     # 4. push to undo stack

    popped = j.undo_last()                              # 5. voice: undo last
    assert popped["inverse"] == rec
    assert st.restore(popped["inverse"]) is True        # 6. replay compensation
    assert f.read_text(encoding="utf-8") == "before"    # rolled back end-to-end
    conn.close()


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
