# core/bus.py
"""
Stage 2.5 - the event bus: Jarvis' internal "notice board".

WHY THIS EXISTS
---------------
Before 2.5 every action wired itself directly to everyone who cared (fileops
called the journal by hand). That is O(N*M) wiring: each NEW capability had to
re-run every wire. The bus flips it around: an action PUBLISHES a fact
("file was overwritten") and whoever cares SUBSCRIBES. Adding a capability no
longer means touching the listeners, and adding a listener (voice, UI toast,
awareness) no longer means touching the actions.

THE THREE HARD RULES (from the MARK XXXVI contract)
---------------------------------------------------
1. FACTS ONLY, NEVER COMMANDS. Every event is something that ALREADY happened,
   named in the past tense. You cannot ask the bus to *do* anything - all
   actions still go through the Stage-1 gate/dispatch, which is what enforces
   confirmation and risk policy. This is a security boundary: if commands could
   ride the bus, a subscriber would be an un-gated execution path.
2. FROZEN CATALOG. Every event name and its exact field set live in EVENTS
   below - one module, one source of truth. Publishing an unknown event or an
   unknown/missing field raises in strict mode, so two parts of the system can
   never drift into different ideas of the same fact. Evolution is ADDITIVE:
   add new events or new OPTIONAL fields; never repurpose or drop one.
3. NEVER BREAK THE PUBLISHER. A subscriber that raises, hangs on a bad format,
   or is simply buggy must not corrupt a file operation. Listeners are isolated
   per-subscriber; publish() swallows subscriber errors (recording them) and
   always returns normally.

BOUNDED BY DESIGN: a flight-recorder ring keeps only the last N events, and
high-volume events can be coalesced, so an awareness storm (thousands of file
changes) can never balloon memory or stall the voice path.

Free - fast - offline: stdlib only. Thread-safe (the runtime calls tools from
worker threads).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# --------------------------------------------------------------------------
# The FROZEN event catalog.
#   name -> (required_fields, optional_fields, human description)
# Names are past-tense FACTS. Adding entries is additive and safe; changing or
# removing one is a breaking change and must be treated as such.
# --------------------------------------------------------------------------
EVENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "session.started": (
        ("session_id",), (),
        "A fresh Jarvis run began; unscoped undo is limited to this session.",
    ),
    "file.created": (
        ("path",), ("saga_id",),
        "A new file was created on disk.",
    ),
    "file.overwritten": (
        ("path",), ("saga_id",),
        "An existing file's contents were atomically replaced (reversible).",
    ),
    "file.moved": (
        ("src", "dst"), ("saga_id",),
        "A file was moved to another location.",
    ),
    "file.renamed": (
        ("src", "dst"), ("new_name", "saga_id"),
        "A file was renamed in place.",
    ),
    "file.copied": (
        ("src", "dst"), ("saga_id",),
        "A file was copied; the copy is removable by undo.",
    ),
    "file.deleted": (
        ("path",), ("saga_id", "recoverable", "method"),
        "A file was deleted after its bytes were stashed, so undo restores it.",
    ),
    "folder.created": (
        ("path",), ("saga_id",),
        "A folder was created; undo removes it while it is still empty.",
    ),
    "file.op_failed": (
        ("op", "error"), ("path",),
        "A file operation failed and was rolled back / never applied.",
    ),
    "undo.performed": (
        ("ok",), ("label", "path", "kind", "saga_id", "deleted"),
        "One step was undone; 'deleted' marks undoing an original creation.",
    ),
    "redo.performed": (
        ("ok",), ("label", "path", "kind", "saga_id"),
        "One previously undone step was re-applied.",
    ),
}


@dataclass(frozen=True)
class Event:
    """An immutable fact. Frozen so a subscriber can never mutate what the next
    subscriber sees."""
    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    seq: int = 0
    correlation_id: str | None = None

    def __getitem__(self, key):
        return self.payload[key]

    def get(self, key, default=None):
        return self.payload.get(key, default)


class EventContractError(ValueError):
    """Raised when an event violates the frozen catalog (strict mode only)."""


class Bus:
    """A tiny synchronous publish/subscribe bus with per-listener isolation."""

    def __init__(self, *, strict: bool = True, max_recent: int = 500):
        self._subs: list[tuple[str, Callable[[Event], None], str]] = []
        self._lock = threading.RLock()
        self._seq = 0
        self._recent: deque[Event] = deque(maxlen=max_recent)
        self._errors: deque[tuple[str, str]] = deque(maxlen=50)
        self.strict = strict

    # -- subscribing ------------------------------------------------------

    def subscribe(self, pattern: str, handler: Callable[[Event], None],
                  *, name: str | None = None) -> Callable[[], None]:
        """Listen to one event name, a `"prefix.*"` pattern, or `"*"` for all.

        Returns an unsubscribe callable.
        """
        if not callable(handler):
            raise TypeError("handler must be callable")
        entry = (pattern, handler, name or getattr(handler, "__name__", "listener"))
        with self._lock:
            self._subs.append(entry)

        def _unsubscribe() -> None:
            with self._lock:
                if entry in self._subs:
                    self._subs.remove(entry)

        return _unsubscribe

    @staticmethod
    def _matches(pattern: str, name: str) -> bool:
        if pattern == "*" or pattern == name:
            return True
        if pattern.endswith(".*"):
            return name.startswith(pattern[:-1])
        return False

    # -- the contract -----------------------------------------------------

    def _validate(self, name: str, payload: Mapping[str, Any]) -> None:
        spec = EVENTS.get(name)
        if spec is None:
            raise EventContractError(
                f"unknown event '{name}' - add it to core.bus.EVENTS first "
                f"(the catalog is the single source of truth)"
            )
        required, optional, _ = spec
        allowed = set(required) | set(optional)
        missing = [f for f in required if f not in payload]
        if missing:
            raise EventContractError(f"event '{name}' missing field(s): {missing}")
        extra = [k for k in payload if k not in allowed]
        if extra:
            raise EventContractError(
                f"event '{name}' has unknown field(s): {extra}; allowed={sorted(allowed)}"
            )

    # -- publishing -------------------------------------------------------

    def publish(self, name: str, *, correlation_id: str | None = None,
                **payload: Any) -> Event | None:
        """Announce a FACT. Never raises because of a subscriber.

        Contract violations raise in strict mode (they are OUR bug, caught in
        tests). Subscriber failures never propagate: a broken listener must not
        be able to fail a file operation.
        """
        try:
            self._validate(name, payload)
        except EventContractError:
            if self.strict:
                raise
            return None

        with self._lock:
            self._seq += 1
            event = Event(name=name, payload=dict(payload), seq=self._seq,
                          correlation_id=correlation_id)
            self._recent.append(event)
            targets = [(h, n) for p, h, n in self._subs if self._matches(p, name)]

        for handler, sub_name in targets:
            try:
                handler(event)
            except Exception as e:  # per-listener isolation
                with self._lock:
                    self._errors.append((sub_name, f"{type(e).__name__}: {e}"))
        return event

    # -- introspection (flight recorder) ----------------------------------

    def recent(self, limit: int = 50, name: str | None = None) -> list[Event]:
        with self._lock:
            items = list(self._recent)
        if name:
            items = [e for e in items if self._matches(name, e.name)]
        return items[-limit:]

    def errors(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._errors)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


# --------------------------------------------------------------------------
# Process-wide singleton
# --------------------------------------------------------------------------
_bus: Bus | None = None
_bus_lock = threading.RLock()


def get_bus() -> Bus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = Bus()
        return _bus


def reset_bus() -> None:
    """Test seam: drop all subscribers and history."""
    global _bus
    with _bus_lock:
        _bus = None


def publish(name: str, **payload: Any) -> Event | None:
    """Module-level convenience so callers need one import."""
    return get_bus().publish(name, **payload)


def publish_safe(name: str, **payload: Any) -> Event | None:
    """Publish that NEVER raises, not even on a contract violation.

    Used on hot paths inside real file operations: telling the world about a
    write must never be able to fail the write itself.
    """
    try:
        return get_bus().publish(name, **payload)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Console subscriber - the debug window into the bus
# --------------------------------------------------------------------------
def attach_console_subscriber(bus: Bus | None = None, *, prefix: str = "[bus]",
                              printer: Callable[[str], None] = print):
    """Print every fact as it happens. Handy while developing; cheap enough to
    leave available behind a flag."""
    bus = bus or get_bus()

    def _on_event(event: Event) -> None:
        bits = " ".join(f"{k}={v!r}" for k, v in event.payload.items())
        printer(f"{prefix} {event.name} {bits}".rstrip())

    return bus.subscribe("*", _on_event, name="console")


# --------------------------------------------------------------------------
# EVENTS.md generator - docs can never drift from the catalog
# --------------------------------------------------------------------------
def generate_events_md() -> str:
    lines = [
        "# Event catalog (generated)",
        "",
        "Generated from `core/bus.py` (`EVENTS`). Do not edit by hand -- run:",
        "",
        "```bash",
        "python -m core.bus > docs/EVENTS.md",
        "```",
        "",
        "Rules: events are **facts in the past tense**, never commands "
        "(all actions go through the gate). The catalog is **frozen**: "
        "add new events or optional fields, never repurpose existing ones.",
        "",
        f"**{len(EVENTS)} events**",
        "",
        "| Event | Required | Optional | Meaning |",
        "| --- | --- | --- | --- |",
    ]
    for name in sorted(EVENTS):
        required, optional, doc = EVENTS[name]
        req = ", ".join(f"`{f}`" for f in required) or "--"
        opt = ", ".join(f"`{f}`" for f in optional) or "--"
        lines.append(f"| `{name}` | {req} | {opt} | {doc} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_events_md())
