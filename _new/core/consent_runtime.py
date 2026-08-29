# core/consent_runtime.py
"""
Stage 3A, step 4 - handing the gate a database connection and a session id.

core/gate.py is deliberately a pure decision function: given a tool call, it
returns a verdict. Consent needs storage, and storage is exactly the kind of
dependency that turns a testable function into a knot. So the wiring lives
here, behind two tiny accessors that the gate can call and tests can replace.

Why reuse the fileops connection instead of opening our own:
  - One writer per database file is the rule this project settled on in Stage 2
    (WAL, single-writer). Two connections from one process would work, but the
    consent flip and the journal intent MUST commit in the SAME transaction,
    and a transaction cannot span two connections. Sharing is not an
    optimisation here - it is what makes atomicity possible at all.
  - The session id already exists on the journal (FileOps.new_session), and
    consent needs the same notion of "this run", so inventing a second one
    would guarantee they eventually disagree.

Everything degrades to None rather than raising: if consent storage is
unavailable, the gate falls back to the old behaviour instead of bringing the
assistant down. A safety feature that crashes the app is not a safety feature.
"""
from __future__ import annotations

import threading
import uuid

_LOCK = threading.RLock()
_fallback_conn = None
_fallback_session = None
_override_conn = None
_override_session = None
_override_active = False


def set_override(conn, session_id="test-session") -> None:
    """Test seam: force a specific connection/session."""
    global _override_conn, _override_session, _override_active
    _override_conn = conn
    _override_session = session_id
    _override_active = True


def clear_override() -> None:
    global _override_active
    _override_active = False


def reset() -> None:
    global _fallback_conn, _fallback_session
    _fallback_conn = None
    _fallback_session = None


def get_conn():
    """The jarvis.db connection consent tickets live on, or None."""
    if _override_active:
        return _override_conn
    # Preferred: the connection fileops already owns.
    try:
        from actions import fileops_bridge
        fo = fileops_bridge.get_fileops()
        journal = getattr(fo, "journal", None) if fo is not None else None
        conn = getattr(journal, "conn", None)
        if conn is not None:
            return conn
    except Exception as exc:
        # Р6, шаг 33.2: одноразовость подтверждений — свойство
        # безопасности, а не удобство. Если хранилище новее кода,
        # талоны перестают быть одноразовыми — молчать нельзя.
        try:
            from core.store import StoreError as _StoreError
        except Exception:
            _StoreError = ()
        if isinstance(exc, _StoreError):
            raise
    # Fallback: fileops is switched off, but confirmations still must be
    # durable - that is a security property, not a file-feature.
    #
    # Блок 7: соединение берётся у КАССЫ и БЕЗ здешнего замка. Раньше тут
    # стояло открытие базы прямо под замком этого файла — а открытие умеет
    # запустить миграции и снять копию всего дома. Замок, под которым идёт
    # такая работа, останавливает всех, кто спрашивает про талоны; а с
    # приходом общей кассы это дало бы ещё и разный порядок захвата, то есть
    # мёртвую хватку. Касса открывает базу вне своего замка.
    global _fallback_conn
    if _fallback_conn is not None:
        return _fallback_conn
    try:
        from core import writer
        _fallback_conn = writer.conn()
    except Exception as exc:
        # Р6, шаг 33.2: тот же закон и для запасного пути.
        try:
            from core.store import StoreError as _StoreError
        except Exception:
            _StoreError = ()
        if isinstance(exc, _StoreError):
            raise
        return None
    return _fallback_conn


def get_session_id():
    """The id of the current run. Stable for the life of the process."""
    if _override_active:
        return _override_session
    try:
        from actions import fileops_bridge
        fo = fileops_bridge.get_fileops()
        journal = getattr(fo, "journal", None) if fo is not None else None
        sid = getattr(journal, "session_id", None)
        if sid:
            return sid
    except Exception:
        pass
    global _fallback_session
    with _LOCK:
        if _fallback_session is None:
            _fallback_session = uuid.uuid4().hex
        return _fallback_session
