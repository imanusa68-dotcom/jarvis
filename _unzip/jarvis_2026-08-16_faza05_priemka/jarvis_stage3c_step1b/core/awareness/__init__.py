# core/awareness/ — ambient system-awareness layer (read-only).
#
# Public facade. main.py and the system_context tool import ONLY these names;
# the world model and watchers are private (leading-underscore modules) so their
# internals can change without touching callers.
#
# Issue 001 scope: active window + open apps, exposed via snapshot()/render() and
# the system_context tool. File events (002), prompt block (003) and the referent
# resolver (004) build on this same facade later.

from __future__ import annotations

import os
import threading

from core.awareness import _world_model

# Ingestion + read (also the seam tests drive with fake data)
ingest_windows = _world_model.ingest_windows
ingest_file_event = _world_model.ingest_file_event
ingest_installed_snapshot = _world_model.ingest_installed_snapshot
snapshot = _world_model.snapshot
events_since = _world_model.events_since
render = _world_model.render
render_changes = _world_model.render_changes
format_for_prompt = _world_model.format_for_prompt
reset = _world_model.reset


def resolve(kind: str, hint: str = "", text: str = "") -> dict:
    """Resolve a referent (see _resolver.resolve). Lazy import keeps startup light."""
    from core.awareness import _resolver
    return _resolver.resolve(kind, hint, text)


def is_document_kind(kind: str) -> bool:
    """
    True for referents asking about the document open right now. The caller
    (main.py) needs this BEFORE resolving: these are the only kinds that may
    touch COM or the disk, so they must run off the asyncio event loop.
    """
    from core.awareness import _resolver
    return (kind or "").strip().lower() in _resolver.DOCUMENT_KINDS


def is_page_kind(kind: str) -> bool:
    """
    True for referents asking about the web page open right now. Like document
    kinds these enumerate OS windows, so main.py must resolve them off the
    asyncio event loop.
    """
    from core.awareness import _resolver
    return (kind or "").strip().lower() in _resolver.PAGE_KINDS


def is_real_window(win):
    """True when a window is a real window, not a popup, bubble or dialog."""
    from . import _inspectors as _ins

    return _ins.is_real_window(win)


def active_page(browser_hint: str = "") -> dict:
    """Raw page result for the foreground browser window. Read-only."""
    from core.awareness import _inspectors
    return _inspectors.active_page(browser_hint)


def render_resolved(result: dict) -> str:
    """
    Turn an already-resolved referent into the sentence the tool speaks back.
    Split out from render_reference so a caller that had to resolve on a worker
    thread does not have to resolve a second time.
    """
    from core.awareness import _resolver
    return _resolver.render(result)


def active_document(deadline_s=None) -> dict:
    """Raw DocResult for the foreground document (issue 009). Read-only."""
    from core.awareness import _inspectors
    return _inspectors.active_document(deadline_s=deadline_s)


# ── Perception Core (the single description of what is in front) ────────────

def describe(target: str = "foreground", name_hint: str = "", deadline_s=None) -> dict:
    """One Subject describing the foreground / a named window / all windows."""
    from core.awareness import _perception
    return _perception.describe(target, name_hint, deadline_s)


def render_subject(subject: dict, focus: str = "") -> str:
    """The Russian sentence for a Subject."""
    from core.awareness import _perception
    return _perception.render_subject(subject, focus)


def describe_kind(kind: str, hint: str = "", text: str = "") -> dict:
    from core.awareness import _resolver
    return _resolver.describe_kind(kind, hint, text)


def interpret(text: str) -> dict:
    """Which window the user's own words are about (see _perception.interpret)."""
    from core.awareness import _perception
    return _perception.interpret(text)


def is_subject_kind(kind: str) -> bool:
    """True for category-free questions ("что сейчас активно", "а щас?")."""
    from core.awareness import _resolver
    return (kind or "").strip().lower() in _resolver.SUBJECT_KINDS


def dedupe_answer(text: str) -> str:
    """Do not repeat an identical answer twice in a row — say what changed."""
    from core.awareness import _perception
    return _perception.dedupe_answer(text)


def perception_trace(target: str = "foreground", name_hint: str = "") -> str:
    """Full evidence dump for one question — for diagnosing a real machine."""
    from core.awareness import _perception
    return _perception.trace(_perception.describe(target, name_hint))


def render_context() -> str:
    """Open apps and recent app events, WITHOUT the active-window line."""
    return _world_model.render(with_active=False)


def render_reference(kind: str, hint: str = "") -> str:
    """Resolve a referent and return the Russian text the tool speaks back."""
    from core.awareness import _resolver
    return _resolver.render(_resolver.resolve(kind, hint))

# ── Lifecycle ────────────────────────────────────────────────────────────────
# One daemon thread, started once by the orchestrator (like the reminder loop).
# Feature-flagged and soft-dependency guarded: if disabled or the OS libraries
# are missing, the layer stays off and Jarvis behaves exactly as before.

_ENABLE_ENV = "JARVIS_AWARENESS"
_lifecycle_lock = threading.Lock()
_stop_event: threading.Event | None = None
_file_observers: list = []
_running = False


def is_enabled() -> bool:
    """Feature flag. On by default; JARVIS_AWARENESS=0/false/no/off turns it off."""
    val = os.environ.get(_ENABLE_ENV)
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "no", "off")


def is_running() -> bool:
    return _running


def start() -> None:
    """
    Start the background watcher thread. No-op (never raises) when the feature is
    disabled or the OS watcher dependencies are unavailable — so a missing
    dependency can never take Jarvis down. Idempotent.
    """
    global _stop_event, _running
    with _lifecycle_lock:
        if _running:
            return
        if not is_enabled():
            print("[Awareness] disabled via feature flag — layer off")
            return
        _stop_event = threading.Event()
        started: list[str] = []

        # Windows/apps (needs pywin32 + psutil). Guarded independently so a
        # missing GUI dependency does not disable file watching, and vice-versa.
        try:
            from core.awareness import _watchers
            threading.Thread(
                target=_watchers.run_loop,
                args=(_stop_event, ingest_windows, ingest_installed_snapshot),
                daemon=True,
                name="awareness-windows",
            ).start()
            started.append("windows")
        except Exception as e:
            print(f"[Awareness] window watcher off: {e}")

        # File events (needs watchdog).
        try:
            from core.awareness import _file_watcher
            obs = _file_watcher.start(ingest_file_event)
            if obs is not None:
                _file_observers.append(obs)
                started.append("files")
        except Exception as e:
            print(f"[Awareness] file watcher off: {e}")

        _running = bool(started)
        print(f"[Awareness] on — {', '.join(started) if started else 'nothing available'}")


def stop() -> None:
    """Signal all watchers to stop. Idempotent; safe even if never started."""
    global _running
    with _lifecycle_lock:
        if _stop_event is not None:
            _stop_event.set()
        while _file_observers:
            try:
                _file_observers.pop().stop()
            except Exception:
                pass
        _running = False
