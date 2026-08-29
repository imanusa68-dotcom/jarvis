# core/session_manager.py
# MARK XXXV — Centralized Session State Manager
#
# Provides:
#   SessionState      — enum of live session lifecycle states
#   SessionManager    — single owner of session state, thread-safe queries
#   ReconnectGuard    — exponential backoff + circuit breaker for reconnects
#   is_recoverable_error() — classify exceptions as recoverable vs fatal
#   is_fatal_error()       — opposite of recoverable
#   classify_error()       — short human-readable error description
#
# Design rules:
#   - Only SessionManager.set_state() may change the session state.
#   - Any code that wants to send to the session MUST call is_writable() first.
#   - ReconnectGuard is the single source of truth for backoff timing.

from __future__ import annotations

import asyncio
import json
import math
import time
from enum import Enum, auto
from typing import List


# ─────────────────────────────────────────────────────────────────────────────
# SessionState
# ─────────────────────────────────────────────────────────────────────────────

class SessionState(Enum):
    DISCONNECTED = auto()   # no session, not trying
    CONNECTING   = auto()   # handshake in progress
    CONNECTED    = auto()   # session live, I/O allowed
    CLOSING      = auto()   # cleanup in progress
    RECONNECTING = auto()   # waiting for next connect attempt
    STOPPED      = auto()   # terminal — user/config error, do not reconnect


# ─────────────────────────────────────────────────────────────────────────────
# SessionManager — single owner of session state
# ─────────────────────────────────────────────────────────────────────────────

class SessionManager:
    """
    Central state registry for the live session.

    Thread-safe: all public methods may be called from any thread.
    All writes go through set_state() which logs the transition.
    """

    def __init__(self) -> None:
        self._state:        SessionState  = SessionState.DISCONNECTED
        self._connected_at: float | None  = None

    # ── State mutation ────────────────────────────────────────────────────────

    def set_state(self, state: SessionState) -> None:
        old = self._state
        self._state = state
        if old != state:
            print(f"[Session] {old.name} → {state.name}")

    def mark_connected(self) -> None:
        """Called immediately after successful session open."""
        self._connected_at = time.monotonic()
        self.set_state(SessionState.CONNECTED)

    # ── State queries ─────────────────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self._state

    def is_alive(self) -> bool:
        """True iff session is in CONNECTED state."""
        return self._state == SessionState.CONNECTED

    def is_writable(self) -> bool:
        """
        True iff it is safe to write to the session channel.
        Only CONNECTED allows writing.
        """
        return self._state == SessionState.CONNECTED

    def connection_uptime(self) -> float:
        """Seconds since the session became CONNECTED (0 if never connected)."""
        if self._connected_at is None:
            return 0.0
        return time.monotonic() - self._connected_at


# ─────────────────────────────────────────────────────────────────────────────
# Error classification
# ─────────────────────────────────────────────────────────────────────────────

# Text patterns that indicate a transient/network/close error → recoverable
_RECOVERABLE_PATTERNS: List[str] = [
    "1011", "1001", "1000",
    "closed", "closing", "close",
    "cancelled", "cancel",
    "aborted",
    "startstep", "endstep",
    "channel",
    "streaming context",
    "connection reset",
    "connection closed",
    "connection aborted",
    "write: transport is closing",
    "eof",
    "broken pipe",
    "session closed",
    "failed to close",
    "thread was cancelled",
    "receiving",
    "websocket",
]

# HTTP status codes that are transient
_RECOVERABLE_HTTP_CODES = {429, 500, 502, 503, 504, 1000, 1001, 1011}

# Text patterns that indicate a fatal/config error → do NOT reconnect
_FATAL_PATTERNS: List[str] = [
    "invalid api key",
    "api key not valid",
    "api_key_invalid",
    "authentication failed",
    "permission denied",
    "forbidden",
]

# HTTP status codes that are fatal
_FATAL_HTTP_CODES = {401, 403}


def _error_text(e: Exception) -> str:
    return str(e).lower()


def _get_genai_status_code(e: Exception) -> int | None:
    """Extract numeric status code from google-genai APIError, if any."""
    try:
        from google.genai import errors as _ge
        if isinstance(e, _ge.APIError):
            for attr in ("code", "status_code", "status", "http_code"):
                val = getattr(e, attr, None)
                if isinstance(val, int):
                    return val
    except ImportError:
        pass
    return None


def is_recoverable_error(e: Exception) -> bool:
    """
    Return True if this error is expected/transient and we should reconnect.

    Recoverable examples:
      - WebSocket 1011 (server internal error close)
      - ConnectionClosedError
      - CancelledError (task was cancelled)
      - APIError 429/503 (quota / service unavailable)
      - "Thread was cancelled when writing StartStep status to channel"
      - "Failed to close the streaming context"
    """
    # asyncio cancellation is always recoverable
    if isinstance(e, asyncio.CancelledError):
        return True

    # ExceptionGroup — recoverable if ALL inner exceptions are recoverable
    if isinstance(e, BaseException) and hasattr(e, "exceptions"):
        return all(is_recoverable_error(ex) for ex in e.exceptions)

    t = _error_text(e)
    if any(p in t for p in _RECOVERABLE_PATTERNS):
        return True

    code = _get_genai_status_code(e)
    if code is not None and code in _RECOVERABLE_HTTP_CODES:
        return True

    # websockets library exceptions
    try:
        import websockets.exceptions as _wse
        if isinstance(e, (_wse.ConnectionClosedError, _wse.ConnectionClosedOK,
                           _wse.ConnectionClosed)):
            return True
    except ImportError:
        pass

    # OS-level network errors
    if isinstance(e, (ConnectionResetError, ConnectionAbortedError,
                      ConnectionRefusedError, EOFError, BrokenPipeError,
                      TimeoutError)):
        return True

    return False


def is_fatal_error(e: Exception) -> bool:
    """
    Return True if the error means further reconnects will not help.
    Examples: invalid API key, permission denied.
    """
    # Never treat cancellation as fatal
    if isinstance(e, asyncio.CancelledError):
        return False

    t = _error_text(e)
    if any(p in t for p in _FATAL_PATTERNS):
        return True

    code = _get_genai_status_code(e)
    if code is not None and code in _FATAL_HTTP_CODES:
        return True

    if isinstance(e, (FileNotFoundError,)):
        return True

    try:
        if isinstance(e, json.JSONDecodeError):
            return True
    except Exception:
        pass

    return False


def classify_error(e: Exception) -> str:
    """
    Return a short, human-readable description of the error.
    Used for logging — does NOT suppress the error.
    """
    # ExceptionGroup
    if hasattr(e, "exceptions"):
        inner = [classify_error(ex) for ex in e.exceptions[:4]]
        return f"ExceptionGroup[{', '.join(inner)}]"

    if isinstance(e, asyncio.CancelledError):
        return "task cancelled"

    t = str(e)
    tl = t.lower()

    if "1011" in t:
        return "websocket 1011 (server closed session)"
    if "1001" in t:
        return "websocket 1001 (going away)"
    if "thread was cancelled" in tl and "channel" in tl:
        return "channel cancelled (tool status skipped)"
    if "failed to close" in tl and "streaming" in tl:
        return "streaming context close failed (recoverable)"
    if "429" in t:
        return "quota exceeded (429) — will retry after backoff"
    if "503" in t:
        return "service unavailable (503)"
    if "closed" in tl and "websocket" in tl:
        return "websocket closed"
    if "connection reset" in tl:
        return "connection reset by server"
    if "invalid api key" in tl or "api_key_invalid" in tl:
        return "FATAL: invalid API key"

    return t[:150]


# ─────────────────────────────────────────────────────────────────────────────
# ReconnectGuard — exponential backoff + circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

class ReconnectGuard:
    """
    Controls reconnect pacing:
      - Exponential backoff starting at BASE_DELAY seconds
      - Circuit breaker: if MAX_FAILURES consecutive failures → trigger extended pause
      - Extended pause = EXTENDED_PAUSE seconds before resetting
      - Consecutive failure counter resets if session stays CONNECTED >= STABLE_SECONDS

    Usage in run loop:
        guard = ReconnectGuard()
        while True:
            try:
                await _run_session(...)
                guard.record_success()    ← call if session ran stably
            except ...:
                ...
            if guard.is_circuit_open():
                await asyncio.sleep(guard.extended_backoff())
                guard.reset_circuit()
                continue
            guard.record_failure()
            await asyncio.sleep(guard.next_delay())
    """

    # Tuning constants
    BASE_DELAY      = 3.0      # initial reconnect delay (seconds)
    MAX_DELAY       = 60.0     # cap reconnect delay (seconds)
    EXTENDED_PAUSE  = 180.0    # pause when circuit trips (seconds)
    MAX_FAILURES    = 7        # consecutive failures before circuit break
    STABLE_SECONDS  = 30.0     # connected for this long → reset failure count

    def __init__(self) -> None:
        self._consecutive    = 0       # consecutive failure counter
        self._circuit_open   = False
        self._last_connected: float | None = None

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_failure(self) -> None:
        """Call after a session exits with an error."""
        self._consecutive += 1
        if self._consecutive >= self.MAX_FAILURES:
            self._circuit_open = True
            print(
                f"[ReconnectGuard] ⚡ Circuit open after {self._consecutive} "
                f"consecutive failures."
            )

    def record_success(self, uptime_seconds: float = 0.0) -> None:
        """
        Call after session exits cleanly or after a long stable run.
        Resets the consecutive failure counter.
        """
        if self._consecutive > 0 or self._circuit_open:
            print(
                f"[ReconnectGuard] ✅ Resetting after stable session "
                f"({uptime_seconds:.1f}s uptime)."
            )
        self._consecutive  = 0
        self._circuit_open = False

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def is_circuit_open(self) -> bool:
        return self._circuit_open

    def reset_circuit(self) -> None:
        self._circuit_open = False
        self._consecutive  = 0
        print("[ReconnectGuard] 🔄 Circuit reset — attempting reconnect.")

    # ── Backoff ───────────────────────────────────────────────────────────────

    def next_delay(self) -> float:
        """
        Return the next reconnect delay using exponential backoff.
        Does NOT call record_failure() — caller does that.
        """
        if self._consecutive <= 1:
            return self.BASE_DELAY
        delay = self.BASE_DELAY * (2.0 ** (self._consecutive - 1))
        return min(delay, self.MAX_DELAY)

    def extended_backoff(self) -> float:
        return self.EXTENDED_PAUSE

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive
