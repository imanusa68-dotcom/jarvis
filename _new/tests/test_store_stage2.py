# -*- coding: utf-8 -*-
"""
Stage 2.1 - store: SQLite foundation (jarvis.db / history.db).

Exercises the migration framework (PRAGMA user_version), WAL, config_kv,
backup/restore, downgrade protection and migration atomicity. Pure stdlib -
no google, no send2trash - so it runs offline as a plain script or via pytest.

Run:  python -m pytest tests/test_store_stage2.py -q
or:   python tests/test_store_stage2.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sqlite3
import tempfile
from pathlib import Path

from core import store

LATEST = max(m[0] for m in store.JARVIS_MIGRATIONS)  # newest schema version


def _tmp(name="jarvis.db"):
    d = tempfile.mkdtemp(prefix="jv_store_")
    return Path(d) / name


def test_fresh_db_migrates_to_latest():
    conn = store.open_store(_tmp())
    assert store._user_version(conn) == LATEST
    assert store._table_exists(conn, "config_kv")
    assert store._table_exists(conn, "applied_migrations")
    # migration history records the full chain in order
    hist = store.migration_history(conn)
    assert [h["version"] for h in hist] == list(range(1, LATEST + 1))
    conn.close()


def test_journal_tables_present_at_latest():
    conn = store.open_store(_tmp())
    for t in ("action_journal", "saga", "undo_stack", "execution_log"):
        assert store._table_exists(conn, t), f"missing table {t}"
    conn.close()


def test_reopen_is_idempotent():
    p = _tmp()
    store.open_store(p).close()
    conn = store.open_store(p)
    assert store._user_version(conn) == LATEST
    assert len(store.migration_history(conn)) == LATEST  # not re-applied
    conn.close()


def test_config_kv_roundtrip():
    conn = store.open_store(_tmp())
    assert store.config_get(conn, "missing", "def") == "def"
    store.config_set(conn, "voice", "gemini-live")
    assert store.config_get(conn, "voice") == "gemini-live"
    store.config_set(conn, "voice", "half-cascade")  # overwrite
    assert store.config_get(conn, "voice") == "half-cascade"
    conn.close()


def test_wal_enabled():
    conn = store.open_store(_tmp())
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    conn.close()


def test_backup_and_restore():
    src = _tmp()
    conn = store.open_store(src)
    store.config_set(conn, "k", "v-original")
    dest = src.parent / "backup.db"
    store.backup(conn, dest)
    conn.close()
    # Open the BACKUP as a brand-new store - the value survived intact.
    restored = store.connect(dest)
    assert store._user_version(restored) == LATEST
    assert store.config_get(restored, "k") == "v-original"
    restored.close()


def test_downgrade_protection():
    conn = store.open_store(_tmp())
    store._set_user_version(conn, 999)  # pretend a newer Jarvis wrote this file
    raised = False
    try:
        store.migrate(conn, store.JARVIS_MIGRATIONS)
    except store.StoreError:
        raised = True
    assert raised, "opening a DB newer than the code must be refused"
    conn.close()


def test_migration_atomicity_rolls_back():
    conn = store.open_store(_tmp())  # at LATEST
    bad = [(LATEST + 1, "broken", ["CREATE TABLE t_ok (x)", "THIS IS NOT VALID SQL"])]
    crashed = False
    try:
        store.migrate(conn, bad)
    except sqlite3.Error:
        crashed = True
    assert crashed
    assert store._user_version(conn) == LATEST, "version must not advance on failure"
    assert not store._table_exists(conn, "t_ok"), "partial DDL must roll back"
    conn.close()


def test_history_db_is_independent():
    conn = store.open_history(_tmp("history.db"))
    assert store._user_version(conn) == 1
    assert store._table_exists(conn, "observations")
    # history.db has NO applied_migrations table (decoupled from jarvis.db)
    assert not store._table_exists(conn, "applied_migrations")
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
