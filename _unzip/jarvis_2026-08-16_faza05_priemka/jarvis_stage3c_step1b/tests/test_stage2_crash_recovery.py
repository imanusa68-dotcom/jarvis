"""Stage 2 exit criterion: crash recovery of the saga journal.

Goal: prove that killing Jarvis in the MIDDLE of a batch of reversible file
operations never corrupts the undo/redo history. We do NOT touch product code
— this is a durability probe against the real SQLite journal (core/store.py +
core/journal.py).

How the crash is simulated (as brutal as a real power loss):
  * a CHILD python process opens the same DB, runs a batch, then dies via
    os._exit(9) or an external SIGKILL — NO conn.close(), NO atexit, NO flush,
    NO graceful shutdown of any kind.
  * the PARENT then reopens the very same DB file and asserts the invariants.

The load-bearing invariant: journal.complete() flips saga->done AND pushes the
undo entry inside ONE transaction, so a crash can never leave a "done" saga
without its undo entry (or vice-versa).

Runner-style (pytest-free): module-level test_* + _run(). The same file is
re-invoked as a script with `--child <db> <mode>` to BE the crashing process.
"""
import os
import sys
import time
import shutil
import sqlite3
import tempfile
import subprocess

from core import store
from core import journal as journal_mod


# ── the crashing child ────────────────────────────────────────────────────
def _child_batch(db_path, mode):
    """Runs in a CHILD process and dies hard. Never returns normally."""
    conn = store.open_store(path=db_path)
    j = journal_mod.Journal(conn)
    j.start_session("child-session")

    def _saga(i, action="write", kind="file-restore"):
        sid = j.begin_intent(
            "file_controller", action,
            intent={"n": i},
            inverse={"kind": kind, "n": i},
            label=f"op{i}",
        )
        j.complete(sid)
        return sid

    if mode == "commit3_then_kill":
        _saga(0); _saga(1); _saga(2)
        os._exit(9)  # power-loss: 3 committed sagas, no shutdown

    elif mode == "intent_then_kill":
        # die AFTER begin_intent but BEFORE complete
        j.begin_intent("file_controller", "delete",
                       intent={"x": 1}, inverse={"kind": "trash-restore"},
                       label="halfdead")
        os._exit(9)

    elif mode == "partial_batch_then_kill":
        _saga(0); _saga(1)                       # 2 completed
        j.begin_intent("file_controller", "write",
                       intent={"n": 2}, inverse={"kind": "file-restore", "n": 2},
                       label="op2-open")          # 1 left in-flight
        os._exit(9)

    elif mode == "spin_until_killed":
        # loop forever doing full sagas; parent sends SIGKILL at a random point.
        i = 0
        while True:
            _saga(i)
            i += 1
            time.sleep(0.01)

    os._exit(0)


def _run_child(db_path, mode):
    env = dict(os.environ)
    root = os.getcwd()
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--child", db_path, mode],
        env=env,
    )


def _fresh_db():
    d = tempfile.mkdtemp(prefix="jv_crash_")
    return os.path.join(d, "jarvis.db"), d


def _counts(conn):
    done = conn.execute("SELECT count(*) FROM saga WHERE status='done'").fetchone()[0]
    intents = conn.execute("SELECT count(*) FROM saga WHERE status='intent'").fetchone()[0]
    undo_open = conn.execute(
        "SELECT count(*) FROM undo_stack WHERE undone_at IS NULL"
    ).fetchone()[0]
    return done, intents, undo_open


# ── tests ─────────────────────────────────────────────────────────
def test_committed_sagas_survive_a_hard_exit():
    db, d = _fresh_db()
    try:
        p = _run_child(db, "commit3_then_kill")
        p.wait(timeout=30)
        assert p.returncode == 9, f"child should have died via os._exit(9), got {p.returncode}"
        conn = store.open_store(path=db)
        done, intents, undo_open = _counts(conn)
        assert done == 3, f"3 committed sagas must survive, got {done}"
        assert undo_open == 3, f"3 undo entries must survive, got {undo_open}"
        j = journal_mod.Journal(conn)
        top = j.peek_undo()
        assert top and top["label"] == "op2", f"top of undo stack should be op2, got {top}"
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_inflight_intent_is_recoverable_not_corrupt():
    db, d = _fresh_db()
    try:
        p = _run_child(db, "intent_then_kill")
        p.wait(timeout=30)
        conn = store.open_store(path=db)
        j = journal_mod.Journal(conn)
        opens = j.open_intents()
        assert len(opens) == 1 and opens[0]["label"] == "halfdead", opens
        # an un-completed saga must NEVER have reached the undo stack
        assert j.peek_undo() is None, "in-flight intent must not be undoable"
        done, intents, undo_open = _counts(conn)
        assert done == 0 and intents == 1 and undo_open == 0, (done, intents, undo_open)
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_partial_batch_keeps_completed_and_flags_the_open_one():
    db, d = _fresh_db()
    try:
        p = _run_child(db, "partial_batch_then_kill")
        p.wait(timeout=30)
        conn = store.open_store(path=db)
        done, intents, undo_open = _counts(conn)
        assert done == 2, f"the 2 completed sagas must persist, got {done}"
        assert undo_open == 2, f"exactly the 2 completed sagas are undoable, got {undo_open}"
        assert intents == 1, f"the interrupted saga is flagged 'intent', got {intents}"
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_complete_is_atomic_no_done_without_undo_after_sigkill():
    """The core crash-safety invariant, under a real external SIGKILL."""
    db, d = _fresh_db()
    try:
        p = _run_child(db, "spin_until_killed")
        time.sleep(0.35)          # let it commit an unknown number of sagas
        p.kill()                  # SIGKILL — the harshest possible interruption
        p.wait(timeout=30)
        conn = store.open_store(path=db)
        # 1) the DB itself is not corrupt
        ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert ic == "ok", f"integrity_check failed: {ic}"
        # 2) THE invariant: every done saga carries exactly one undo entry
        done, intents, undo_open = _counts(conn)
        assert done == undo_open, (
            f"atomicity broken: {done} done sagas but {undo_open} undo entries"
        )
        assert intents in (0, 1), f"at most one saga can be mid-flight, got {intents}"
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_undo_then_redo_still_work_after_recovery():
    db, d = _fresh_db()
    try:
        p = _run_child(db, "commit3_then_kill")
        p.wait(timeout=30)
        # reopen #1: navigate undo, and record a redo the way FileOps would
        conn = store.open_store(path=db)
        j = journal_mod.Journal(conn)
        j.start_session("recovery-session")
        entry = j.undo_last()
        assert entry and entry["label"] == "op2", entry
        j.push_redo(entry["saga_id"], entry["label"], redo={"kind": "reapply", "n": 2})
        conn.close()
        # reopen #2 (a SECOND crash): the redo must still be there
        conn2 = store.open_store(path=db)
        j2 = journal_mod.Journal(conn2)
        r = j2.redo_last()
        assert r and r["label"] == "op2" and r["redo"]["n"] == 2, r
        # after undoing op2, the next undoable is op1
        top = j2.peek_undo()
        assert top and top["label"] == "op1", top
        conn2.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    last = None
    for fn in fns:
        last = fn
        try:
            fn()
            passed += 1
            print("ok   -", fn.__name__)
        except Exception as e:  # noqa
            failed += 1
            print("FAIL -", fn.__name__, "::", repr(e))
    mod = last.__module__ if last else __name__
    print(f"\n{mod}: passed={passed} failed={failed}")
    return failed


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--child":
        _child_batch(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(_run())
