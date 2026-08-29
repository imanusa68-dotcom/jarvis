# -*- coding: utf-8 -*-
"""
Stage 2.6 - auto+undo enablement for the LAST mutating operations.

Until now copy / create_folder / delete went down a legacy path: they changed
the disk but left no saga, so "отмени" could not reach them. These tests lock
the Stage 2 exit rule:

    every operation that mutates the disk produces a reversible saga,
    and deletion is refused unless it can be undone.

Run:  PYTHONPATH=.:/data/shims python tests/test_stage26_ops.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from core import store
from core.journal import Journal
from core.staging import Staging
from core.fileops import FileOps, FileOpsError
from core.bus import EVENTS, get_bus, reset_bus


def _raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    raise AssertionError(f"expected {exc.__name__} to be raised")


def _fo(wire: Path, staging=True) -> FileOps:
    conn = store.open_store(str(wire / "jarvis.db"))
    return FileOps(
        journal=Journal(conn),
        staging=Staging(root=wire / "staging") if staging else None,
        safe_roots=[str(wire)],
    )


def _fresh(prefix="jv_26_"):
    reset_bus()
    return Path(tempfile.mkdtemp(prefix=prefix))


def _names(bus):
    return [e.name for e in bus.recent(50)]


# -- copy -------------------------------------------------------------------

def test_copy_is_undoable():
    w = _fresh()
    fo = _fo(w)
    src = w / "a.txt"
    src.write_text("hello", encoding="utf-8")
    dst = w / "b.txt"

    fo.copy(src, dst)
    assert dst.exists() and dst.read_text(encoding="utf-8") == "hello"

    res = fo.undo_last()
    assert res and res["ok"], res
    assert not dst.exists(), "undo of a copy must remove the copy"
    assert src.exists(), "undo of a copy must NEVER touch the original"


def test_copy_redo_recreates_the_copy():
    w = _fresh()
    fo = _fo(w)
    src = w / "a.txt"
    src.write_text("hello", encoding="utf-8")
    dst = w / "b.txt"

    fo.copy(src, dst)
    fo.undo_last()
    assert not dst.exists()

    res = fo.redo_last()
    assert res and res["ok"], res
    assert dst.exists() and dst.read_text(encoding="utf-8") == "hello"


def test_copy_refuses_to_clobber_an_existing_file():
    w = _fresh()
    fo = _fo(w)
    src = w / "a.txt"
    src.write_text("new", encoding="utf-8")
    dst = w / "b.txt"
    dst.write_text("precious", encoding="utf-8")

    _raises(lambda: fo.copy(src, dst), FileOpsError)
    assert dst.read_text(encoding="utf-8") == "precious"


def test_copy_publishes_a_fact():
    w = _fresh()
    fo = _fo(w)
    (w / "a.txt").write_text("x", encoding="utf-8")
    fo.copy(w / "a.txt", w / "b.txt")
    assert "file.copied" in _names(get_bus())


# -- create_folder ----------------------------------------------------------

def test_create_folder_is_undoable():
    w = _fresh()
    fo = _fo(w)
    d = w / "Reports"

    fo.create_folder(d)
    assert d.is_dir()

    res = fo.undo_last()
    assert res and res["ok"], res
    assert not d.exists(), "undo of create_folder must remove the folder"


def test_undo_never_deletes_a_folder_that_has_content():
    """rmdir-only: if the user put files in it, undo must not destroy them."""
    w = _fresh()
    fo = _fo(w)
    d = w / "Reports"
    fo.create_folder(d)
    (d / "keep.txt").write_text("important", encoding="utf-8")

    fo.undo_last()
    assert d.exists(), "a non-empty folder must survive undo"
    assert (d / "keep.txt").read_text(encoding="utf-8") == "important"


def test_create_folder_redo_recreates_it():
    w = _fresh()
    fo = _fo(w)
    d = w / "Reports"
    fo.create_folder(d)
    fo.undo_last()
    assert not d.exists()

    res = fo.redo_last()
    assert res and res["ok"], res
    assert d.is_dir()


def test_create_folder_publishes_a_fact():
    w = _fresh()
    fo = _fo(w)
    fo.create_folder(w / "Reports")
    assert "folder.created" in _names(get_bus())


# -- delete -----------------------------------------------------------------

def test_delete_is_undoable_and_restores_exact_bytes():
    w = _fresh()
    fo = _fo(w)
    f = w / "notes.txt"
    f.write_text("важный текст", encoding="utf-8")

    fo.delete(f)
    assert not f.exists()

    res = fo.undo_last()
    assert res and res["ok"], res
    assert f.exists(), "undo of a delete must bring the file back"
    assert f.read_text(encoding="utf-8") == "важный текст"


def test_delete_is_refused_when_there_is_NO_recovery_route():
    """No Recycle Bin AND no staging -> no way to undo -> refuse the delete.

    (With a working Recycle Bin, a delete without staging is fine, because the
    bin owns recovery - so 'no staging' alone must NOT refuse.)
    """
    w = _fresh()
    conn = store.open_store(str(w / "jarvis.db"))
    fo = FileOps(journal=Journal(conn), staging=None,
                 safe_roots=[str(w)], recycle=FakeBin(w / "rb", works=False))
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")

    _raises(lambda: fo.delete(f), FileOpsError)
    assert f.exists(), "a delete we cannot undo must not happen at all"


def test_delete_without_staging_still_works_via_the_recycle_bin():
    """The bin alone is enough: no staging needed when recovery is owned by it."""
    w = _fresh()
    conn = store.open_store(str(w / "jarvis.db"))
    fb = FakeBin(w / "rb", works=True)
    fo = FileOps(journal=Journal(conn), staging=None,
                 safe_roots=[str(w)], recycle=fb)
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")

    res = fo.delete(f)
    assert res["method"] == "recycle-bin", res
    assert not f.exists()
    assert fo.undo_last()["ok"]
    assert f.read_text(encoding="utf-8") == "data"
    assert fb.contents() == [], "no ghost after undo"


def test_delete_redo_deletes_again_and_is_still_undoable():
    w = _fresh()
    fo = _fo(w)
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")

    fo.delete(f)
    fo.undo_last()
    assert f.exists()

    res = fo.redo_last()
    assert res and res["ok"], res
    assert not f.exists(), "redo must re-apply the delete"

    res = fo.undo_last()
    assert res and res["ok"], res
    assert f.exists() and f.read_text(encoding="utf-8") == "data", \
        "redo of a delete must itself stay undoable (ping-pong)"


def test_delete_outside_scope_is_refused():
    w = _fresh()
    fo = _fo(w)
    outside = Path(tempfile.mkdtemp(prefix="jv_26_out_")) / "x.txt"
    outside.write_text("not yours", encoding="utf-8")

    _raises(lambda: fo.delete(outside))
    assert outside.exists()


def test_delete_publishes_a_fact():
    w = _fresh()
    fo = _fo(w)
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")
    fo.delete(f)
    assert "file.deleted" in _names(get_bus())


# -- the Stage 2 invariant --------------------------------------------------

def test_every_mutating_op_is_reversible():
    """The whole point of Stage 2: no mutating op without a compensation."""
    w = _fresh()
    fo = _fo(w)
    src = w / "a.txt"
    src.write_text("one", encoding="utf-8")

    fo.replace_file(src, "two")
    fo.copy(src, w / "b.txt")
    fo.create_folder(w / "dir")
    fo.rename(src, "renamed.txt")
    fo.delete(w / "b.txt")

    # five operations -> five undos, all successful, ending at the start state
    for i in range(5):
        res = fo.undo_last()
        assert res and res["ok"], f"undo #{i + 1} failed: {res}"

    assert src.exists(), "we should be back at the original file"
    assert src.read_text(encoding="utf-8") == "one"
    assert not (w / "dir").exists()
    assert not (w / "renamed.txt").exists()


def test_new_events_are_facts_in_the_frozen_catalog():
    for name in ("file.copied", "file.deleted", "folder.created"):
        assert name in EVENTS, f"{name} must be in the frozen catalog"
        verb = name.split(".", 1)[1]
        assert verb.endswith(("ed", "en")), \
            f"{name} must be past tense (a fact that already happened)"


def test_history_lists_the_new_operations():
    w = _fresh()
    fo = _fo(w)
    f = w / "a.txt"
    f.write_text("x", encoding="utf-8")
    fo.copy(f, w / "b.txt")
    fo.create_folder(w / "dir")

    steps = fo.history()
    blob = str(steps)
    assert "Copied" in blob, blob
    assert "Created folder" in blob, blob


# -- 2.6.1: deletion needs BOTH safety nets ---------------------------------

def test_delete_is_no_longer_blocked_but_needs_confirmation():
    """Deletion became reachable ONLY because it is now reversible - and it
    still must ask first, because the real risk is a misheard command."""
    import core.security as sec
    assert sec.get_policy("file_controller", {"action": "delete"}) == "confirm"

    need, reason = sec.needs_confirmation("file_controller", {"action": "delete"})
    assert need is True, "delete must never run without confirmation"
    assert reason

    d = sec.check_tool_call("file_controller", {"action": "delete", "name": "x.txt"})
    assert d.allowed is True, "delete is no longer hard-blocked"
    assert d.policy == "confirm"


def test_delete_skips_confirmation_once_confirmed():
    import core.security as sec
    need, _ = sec.needs_confirmation(
        "file_controller", {"action": "delete", "confirmed": True})
    assert need is False


class FakeBin:
    """A Recycle Bin we can actually inspect: it MOVES files (never copies),
    exactly like the real one on a single volume."""

    def __init__(self, root, works=True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.works = works
        self.entries = {}  # original path -> path inside the bin

    def available(self):
        return self.works

    def send(self, path):
        if not self.works:
            return None
        src = Path(path)
        dst = self.root / f"{len(self.entries)}_{src.name}"
        src.replace(dst)  # a MOVE: cost does not depend on file size
        self.entries[str(src)] = dst
        return {"kind": "trash-restore", "original": str(src)}

    def restore(self, token):
        dst = self.entries.get(token.get("original"))
        if dst is None or not dst.exists():
            return False
        Path(token["original"]).parent.mkdir(parents=True, exist_ok=True)
        dst.replace(Path(token["original"]))
        # leaving the bin is what makes a ghost copy impossible
        self.entries.pop(token["original"], None)
        return True

    def holds(self, token):
        dst = self.entries.get(token.get("original"))
        return bool(dst and dst.exists())

    def contents(self):
        return [q for q in self.root.iterdir() if q.exists()]


def _fo_bin(wire, works=True, quota=None):
    """FileOps wired to an inspectable Recycle Bin."""
    conn = store.open_store(str(wire / "jarvis.db"))
    st = Staging(root=wire / "staging",
                 **({"quota_bytes": quota} if quota else {}))
    fb = FakeBin(wire / "recyclebin", works=works)
    fo = FileOps(journal=Journal(conn), staging=st,
                 safe_roots=[str(wire)], recycle=fb)
    return fo, fb, st


def test_undo_of_a_delete_leaves_NOTHING_in_the_recycle_bin():
    """THE regression test for the reported bug.

    Restoring a deleted file used to bring it back while a ghost copy stayed in
    the Recycle Bin - the file existed in two places at once. Recovery state
    must have exactly ONE owner.
    """
    w = _fresh()
    fo, fb, st = _fo_bin(w)
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")

    res = fo.delete(f)
    assert res["method"] == "recycle-bin", res
    assert not f.exists()
    assert len(fb.contents()) == 1, "file must be IN the bin while deleted"

    assert fo.undo_last()["ok"]
    assert f.read_text(encoding="utf-8") == "data", "file must come back"
    assert fb.contents() == [], "no ghost copy may remain in the Recycle Bin"


def test_delete_via_recycle_bin_copies_no_bytes():
    """Scaling rule: deleting must not duplicate the file's bytes.

    A staged copy of a 40 GB video would be unusable. The Recycle Bin route is
    a metadata move, so the staging area must stay completely empty.
    """
    w = _fresh()
    fo, fb, st = _fo_bin(w)
    f = w / "big.bin"
    f.write_text("x" * 5000, encoding="utf-8")

    fo.delete(f)
    assert st.total_bytes() == 0, "Recycle Bin route must copy nothing"
    assert fo.undo_last()["ok"]
    assert f.exists()


def test_huge_file_is_deletable_regardless_of_the_staging_quota():
    """A file far larger than the quota is still deletable AND undoable,
    because the zero-copy route does not care about size."""
    w = _fresh()
    fo, fb, st = _fo_bin(w, quota=100)  # tiny quota on purpose
    f = w / "huge.bin"
    f.write_text("y" * 20000, encoding="utf-8")

    res = fo.delete(f)
    assert res["method"] == "recycle-bin", res
    assert fo.undo_last()["ok"]
    assert f.read_text(encoding="utf-8").startswith("y")
    assert fb.contents() == []


def test_huge_file_delete_is_refused_when_only_copying_is_left():
    """No Recycle Bin + file bigger than the quota = we would have to either
    delete irreversibly or evict everyone else's undo history. Refuse instead.
    """
    w = _fresh()
    fo, fb, st = _fo_bin(w, works=False, quota=100)
    f = w / "huge.bin"
    f.write_text("z" * 20000, encoding="utf-8")

    _raises(lambda: fo.delete(f), FileOpsError)
    assert f.exists(), "a delete we cannot undo must not happen at all"


def test_one_giant_stash_never_evicts_everyone_elses_undo():
    """Quota safety: a single oversized file must not wipe the staging area."""
    w = _fresh()
    st = Staging(root=w / "staging", quota_bytes=3000)
    small = w / "small.txt"
    small.write_text("a" * 500, encoding="utf-8")
    keep = st.stash(small)
    assert keep is not None

    giant = w / "giant.bin"
    giant.write_text("b" * 9000, encoding="utf-8")
    assert st.stash(giant) is None, "oversized file must be refused"
    assert st.available(keep) is True, "existing undo history must survive"


def test_delete_falls_back_to_staging_when_there_is_no_recycle_bin():
    """Network drives and some volumes have no Recycle Bin: we keep the bytes
    ourselves so deletion is still reversible."""
    w = _fresh()
    fo, fb, st = _fo_bin(w, works=False)
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")

    res = fo.delete(f)
    assert res["method"] == "staged", res
    assert not f.exists()
    assert st.total_bytes() > 0, "fallback must actually hold the bytes"
    assert fo.undo_last()["ok"]
    assert f.read_text(encoding="utf-8") == "data"


def test_delete_undo_redo_round_trip_through_the_bin():
    """Redo must re-delete, and a second undo must bring it back again -
    without ever leaving two copies around."""
    w = _fresh()
    fo, fb, st = _fo_bin(w)
    f = w / "notes.txt"
    f.write_text("data", encoding="utf-8")

    fo.delete(f)
    assert fo.undo_last()["ok"] and f.exists()
    assert fo.redo_last()["ok"], "redo must delete it again"
    assert not f.exists()
    assert len(fb.contents()) == 1
    assert fo.undo_last()["ok"]
    assert f.read_text(encoding="utf-8") == "data"
    assert fb.contents() == [], "still no ghost after a redo cycle"


def test_no_operation_leaves_two_owners_of_the_same_recovery_state():
    """Architectural invariant, checked over EVERY mutating operation.

    For each op: after undo, the file system must hold exactly one copy of the
    affected file - nothing stranded in the bin, nothing stranded in staging.
    """
    w = _fresh()
    fo, fb, st = _fo_bin(w)

    a = w / "a.txt"
    fo.replace_file(a, "one")
    fo.replace_file(a, "two")
    assert fo.undo_last()["ok"] and a.read_text(encoding="utf-8") == "one"
    assert fb.contents() == [], "overwrite must not touch the Recycle Bin"

    b = w / "b.txt"
    fo.replace_file(b, "x")
    fo.move(b, w / "sub" / "b.txt")
    assert fo.undo_last()["ok"]
    assert b.exists() and not (w / "sub" / "b.txt").exists(), "no duplicate"

    c = w / "c.txt"
    fo.replace_file(c, "x")
    fo.copy(c, w / "c2.txt")
    assert fo.undo_last()["ok"]
    assert c.exists() and not (w / "c2.txt").exists(), "copy must be undone"

    fo.delete(c)
    assert fo.undo_last()["ok"]
    assert c.exists() and fb.contents() == [], "delete must leave no ghost"


def test_scoped_undo_of_a_deleted_file_restores_it_not_the_create():
    """THE bug from the Windows run: create then delete, then undo BY NAME.

    The scoped undo must pick the most recent saga that touches this file - the
    DELETE - and restore it. Previously the delete saga (trash-restore) was not
    recognised as touching any path, so undo skipped it and reverted the CREATE
    instead, removing the file the user was trying to get back.
    """
    w = _fresh()
    fo, fb, st = _fo_bin(w)
    f = w / "notes.txt"
    fo.replace_file(f, "hello")          # create (saga A)
    res = fo.delete(f)                    # delete (saga B) -> recycle bin
    assert res["method"] == "recycle-bin" and not f.exists()

    out = fo.undo_last(path=str(f))       # "verni notes.txt"
    assert out and out["ok"], out
    assert out["kind"] == "trash-restore", f"undo picked wrong saga: {out}"
    assert f.read_text(encoding="utf-8") == "hello", "file must come back"
    assert fb.contents() == [], "and nothing left in the bin"


def test_scoped_undo_matches_a_trashed_file_by_name():
    """Unit-level guard: _match_path must see a trash-restore record's path."""
    w = _fresh()
    fo, fb, st = _fo_bin(w)
    rec = {"kind": "trash-restore", "original": str(w / "x.txt")}
    assert fo._match_path(rec, fo._norm(w / "x.txt")) is True
    assert fo._record_path(rec) == str(w / "x.txt")
    assert fo._record_paths(rec) == [str(w / "x.txt")]


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"test_stage26_ops: {passed} passed, {failed} failed")


if __name__ == "__main__":
    _run()
