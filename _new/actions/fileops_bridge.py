# actions/fileops_bridge.py
"""
Stage 2.4b bridge: a single, lazily-built transactional FileOps wired to the
real jarvis.db journal + staging + file_controller's safe roots.

Returns None whenever the feature flag is OFF or the stack can't initialise, so
every caller transparently falls back to the legacy file path. This keeps the
old behaviour byte-for-byte when fileops is disabled.
"""
from __future__ import annotations

_instance = None
_conn = None
_override = None
_override_active = False


def set_override(fo) -> None:
    """Test seam: force get_fileops() to return `fo` regardless of the flag."""
    global _override, _override_active
    _override = fo
    _override_active = True


def clear_override() -> None:
    global _override_active
    _override_active = False


def reset() -> None:
    """Drop the cached instance (used by tests and after settings changes)."""
    global _instance, _conn
    _instance = None
    _conn = None


def _refresh_safe_roots(fo) -> None:
    """Keep a cached FileOps in sync with file_controller's CURRENT safe roots.

    The instance is cached for the life of the process, but the safe roots can
    change (tests monkeypatch them; runtime config can move personal folders).
    A stale root list made writes silently fall back to legacy while undo still
    consulted the fileops journal -> the Stage-2 undo drift. Refreshing here is
    cheap and keeps the two paths consistent.
    """
    try:
        from pathlib import Path
        from actions.file_controller import _safe_roots
        fo._safe_roots = [Path(r).expanduser().resolve() for r in _safe_roots()]
    except Exception:
        pass


def get_fileops():
    """Return a ready FileOps, or None to signal 'use the legacy path'."""
    if _override_active:
        return _override
    try:
        from core.feature_flags import fileops_enabled
        if not fileops_enabled():
            return None
    except Exception:
        return None

    global _instance, _conn
    if _instance is not None:
        _refresh_safe_roots(_instance)
        return _instance
    try:
        from core import store, writer
        from core.journal import Journal
        from core.staging import Staging
        from core.fileops import FileOps
        from actions.file_controller import _safe_roots
        # Блок 7: соединение берётся у КАССЫ, а не открывается своё. Журнал
        # отмены — самый ценный писатель в проекте (владелец скажет «отмени»,
        # и отменять будет нечем), поэтому он обязан стоять в общей очереди.
        _conn = writer.conn()
        _instance = FileOps(
            journal=Journal(_conn),
            staging=Staging(),
            safe_roots=[str(r) for r in _safe_roots()],
        )
        # Session boundary: a fresh process starts a clean undo/redo timeline so
        # stale entries from a PREVIOUS run can never be navigated into (that
        # caused undo to hit a file from another session).
        try:
            _instance.new_session()
        except Exception:
            pass
        return _instance
    except Exception as e:
        # Р6, шаг 33.2: данные новее кода — не повод тихо уползти на
        # старый путь. Там нет ни журнала, ни отмены: владелец удалит
        # файл, скажет «отмени» — и отменять будет нечем. Такой отказ
        # обязан быть громким. Остальные ошибки — как раньше.
        try:
            from core.store import StoreError as _StoreError
        except Exception:
            _StoreError = ()
        if isinstance(e, _StoreError):
            raise
        print(f"[fileops] init failed, using legacy path: {e}")
        return None
