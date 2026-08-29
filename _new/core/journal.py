# core/journal.py
"""
Stage 2.2 - journal: persistent saga journal + undo stack, built on core.store.

What this gives Jarvis:
  - A DURABLE action journal (survives restart, not just reconnect) that is a
    strict SUPERSET of the RAM ring buffer in core/dialogue_state.py. Same
    "tool/action: summary" shape and same prompt rendering, so it can later
    replace the RAM source with zero behaviour change (RAM stays a hot slice).
  - A saga spine for reversible file operations: begin_intent -> complete, with
    an IDEMPOTENT compensation (inverse) so "undo" is safe to replay.
  - A LIFO undo stack powering voice "otmeni posledneye" for move/rename.
  - Crash recovery: open_intents() lists sagas killed between intent & complete,
    so startup can decide docat/otkat.

Free - fast - offline: stdlib only (sqlite3 via store, json, contextlib).
Additive: nothing here is wired into the live pipeline yet.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone


# Keep the hot-slice size identical to core/dialogue_state._JOURNAL_MAX so the
# prompt block looks the same whichever source feeds it.
JOURNAL_MAX = 8


class JournalError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(obj):
    return None if obj is None else json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _load(text):
    return None if text is None else json.loads(text)


@contextmanager
def _tx(conn):
    """Транзакция журнала. С блока 7 — через одну кассу записи.

    Почему правка здесь ОДНА, а мест записи четырнадцать: все они уже ходили
    через эту обёртку. Это ровно та выгода, которую даёт одна общая точка
    вместо четырнадцати своих `BEGIN`.

    Касса сама решает, нужна ли очередь: соединение её собственное — встаёт в
    очередь; принесли чужое (инструмент, тест) — работает на нём. И если мы
    уже внутри чужой транзакции, второй `BEGIN` не делается — именно так
    журнал пишется из-под записи талона согласия.
    """
    from core import writer
    with writer.transaction(conn):
        yield


class Journal:
    """Thin persistence layer over a jarvis.db connection (from store.open_store)."""

    def __init__(self, conn):
        self.conn = conn
        # Set by FileOps.new_session() at process start. Entries created during
        # this run carry it; unscoped 'undo last' is limited to this session.
        self.session_id = None

    def start_session(self, session_id):
        """Begin a new run. Does NOT retire old entries - a named file can still
        be navigated across sessions; only unscoped undo is session-limited."""
        self.session_id = session_id

    # -- Action journal (durable superset of the RAM ring buffer) -------------

    def record_action(self, tool, action, summary, ok=True, correlation_id=None):
        """Append one human-readable action line. Mirrors dialogue_state.record_action.

        Блок 7: единственная запись журнала, у которой транзакции не было
        вовсе — она шла автокоммитом. Теперь идёт через кассу, как остальные
        тринадцать. Одна строка без транзакции — не беда сама по себе, но
        писатель вне общей очереди становится тем самым «одним из десятков
        модулей, который забыл», о котором говорит решение про одну кассу.
        """
        if not tool:
            return None
        label = f"{tool}/{action}" if action else str(tool)
        short = str(summary).strip().replace("\n", " ")[:160]
        full = f"{label}: {short}"
        with _tx(self.conn):
            cur = self.conn.execute(
                "INSERT INTO action_journal (ts, tool, action, summary, ok, "
                "correlation_id) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), str(tool), action, full, 1 if ok else 0,
                 correlation_id),
            )
            return cur.lastrowid

    def recent_actions(self, limit=JOURNAL_MAX):
        rows = self.conn.execute(
            "SELECT summary, ok FROM action_journal ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [{"summary": r["summary"], "ok": bool(r["ok"])} for r in reversed(rows)]

    def format_for_prompt(self, limit=JOURNAL_MAX):
        """Render exactly like the RAM journal block in dialogue_state.format_for_prompt."""
        entries = self.recent_actions(limit)
        if not entries:
            return ""
        lines = ["Recent actions you performed (most recent last):"]
        for e in entries:
            mark = "\u2713" if e["ok"] else "\u2717"
            lines.append(f"  {mark} {e['summary']}")
        return "\n".join(lines)

    # -- Saga lifecycle -------------------------------------------------------

    def begin_intent(self, tool, action=None, intent=None, inverse=None,
                     label=None, correlation_id=None):
        """Record the INTENT to perform a reversible op, BEFORE doing it.

        `inverse` is the idempotent compensation used to undo it later.
        Returns the new saga id.
        """
        if not tool:
            raise JournalError("begin_intent requires a tool")
        now = _now()
        with _tx(self.conn):
            cur = self.conn.execute(
                "INSERT INTO saga (correlation_id, tool, action, intent, inverse, "
                "status, label, created_at) VALUES (?, ?, ?, ?, ?, 'intent', ?, ?)",
                (correlation_id, str(tool), action, _dump(intent), _dump(inverse),
                 label, now),
            )
            saga_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO execution_log (ts, saga_id, phase, detail) "
                "VALUES (?, ?, 'intent', NULL)",
                (now, saga_id),
            )
        return saga_id

    def complete(self, saga_id, push_undo=True):
        """Mark a saga done. Idempotent: a second call is a no-op.

        If the saga carries an inverse and push_undo is set, it lands on the
        undo stack so voice \"undo last\" can revert it.
        """
        with _tx(self.conn):
            row = self.conn.execute(
                "SELECT status, inverse, label FROM saga WHERE id=?", (saga_id,)
            ).fetchone()
            if row is None:
                raise JournalError(f"saga {saga_id} not found")
            if row["status"] != "intent":
                return  # already completed / compensated -> idempotent
            now = _now()
            self.conn.execute(
                "UPDATE saga SET status='done', completed_at=? WHERE id=?",
                (now, saga_id),
            )
            self.conn.execute(
                "INSERT INTO execution_log (ts, saga_id, phase, detail) "
                "VALUES (?, ?, 'complete', NULL)",
                (now, saga_id),
            )
            # A brand-new forward action invalidates any pending redo: you
            # cannot redo after diverging history (standard undo/redo rules).
            self.conn.execute(
                "UPDATE redo_stack SET redone_at=? WHERE redone_at IS NULL",
                (now,),
            )
            if push_undo and row["inverse"] is not None:
                self.conn.execute(
                    "INSERT INTO undo_stack (saga_id, label, inverse, created_at, session_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (saga_id, row["label"] or "", row["inverse"], now, self.session_id),
                )

    def mark_failed(self, saga_id, detail=None):
        """Mark an in-flight saga failed. Idempotent for non-intent states."""
        with _tx(self.conn):
            now = _now()
            self.conn.execute(
                "UPDATE saga SET status='failed', completed_at=? "
                "WHERE id=? AND status='intent'",
                (now, saga_id),
            )
            self.conn.execute(
                "INSERT INTO execution_log (ts, saga_id, phase, detail) "
                "VALUES (?, ?, 'failed', ?)",
                (now, saga_id, detail),
            )

    def open_intents(self):
        """Sagas stuck in 'intent' (killed before complete) - crash recovery scan."""
        rows = self.conn.execute(
            "SELECT id, tool, action, intent, inverse, label, created_at "
            "FROM saga WHERE status='intent' ORDER BY id"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["intent"] = _load(d["intent"])
            d["inverse"] = _load(d["inverse"])
            out.append(d)
        return out

    def get_saga(self, saga_id):
        row = self.conn.execute("SELECT * FROM saga WHERE id=?", (saga_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["intent"] = _load(d["intent"])
        d["inverse"] = _load(d["inverse"])
        return d

    # -- Undo stack -----------------------------------------------------------

    def peek_undo(self):
        """The next undoable entry (top of stack), or None. Does not modify state."""
        row = self.conn.execute(
            "SELECT id, saga_id, label, inverse FROM undo_stack "
            "WHERE undone_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "saga_id": row["saga_id"],
                "label": row["label"], "inverse": _load(row["inverse"])}

    def undo_last(self):
        """Pop the top undo entry: mark it undone + its saga compensated.

        Returns {saga_id, label, inverse} for the caller to actually replay the
        compensation, or None if the stack is empty. Idempotent per entry: an
        already-undone entry is never returned twice.
        """
        with _tx(self.conn):
            row = self.conn.execute(
                "SELECT id, saga_id, label, inverse FROM undo_stack "
                "WHERE undone_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = _now()
            self.conn.execute(
                "UPDATE undo_stack SET undone_at=? WHERE id=?", (now, row["id"])
            )
            self.conn.execute(
                "UPDATE saga SET status='compensated' WHERE id=?", (row["saga_id"],)
            )
            self.conn.execute(
                "INSERT INTO execution_log (ts, saga_id, phase, detail) "
                "VALUES (?, ?, 'compensate', NULL)",
                (now, row["saga_id"]),
            )
            return {"saga_id": row["saga_id"], "label": row["label"],
                    "inverse": _load(row["inverse"])}

    def mark_undone(self, entry_id):
        """Pop a SPECIFIC undo entry by id (for per-file scoped undo). Mirrors
        undo_last but lets the caller choose which entry to revert."""
        with _tx(self.conn):
            row = self.conn.execute(
                "SELECT id, saga_id, label, inverse FROM undo_stack "
                "WHERE id=? AND undone_at IS NULL", (int(entry_id),)
            ).fetchone()
            if row is None:
                return None
            now = _now()
            self.conn.execute(
                "UPDATE undo_stack SET undone_at=? WHERE id=?", (now, row["id"])
            )
            self.conn.execute(
                "UPDATE saga SET status='compensated' WHERE id=?", (row["saga_id"],)
            )
            self.conn.execute(
                "INSERT INTO execution_log (ts, saga_id, phase, detail) "
                "VALUES (?, ?, 'compensate', NULL)", (now, row["saga_id"]),
            )
            return {"saga_id": row["saga_id"], "label": row["label"],
                    "inverse": _load(row["inverse"])}

    def mark_redone(self, entry_id):
        """Pop a SPECIFIC redo entry by id (for per-file scoped redo)."""
        with _tx(self.conn):
            row = self.conn.execute(
                "SELECT id, saga_id, label, redo FROM redo_stack "
                "WHERE id=? AND redone_at IS NULL", (int(entry_id),)
            ).fetchone()
            if row is None:
                return None
            now = _now()
            self.conn.execute(
                "UPDATE redo_stack SET redone_at=? WHERE id=?", (now, row["id"])
            )
            return {"saga_id": row["saga_id"], "label": row["label"],
                    "redo": _load(row["redo"])}

    def close_open_entries(self):
        """Session boundary: retire every still-open undo/redo entry so a NEW
        process starts with a clean timeline. Prevents accidental navigation
        into a previous session's edits (which made undo hit the wrong file).
        Does NOT touch files on disk - it only closes the navigation stacks.
        """
        now = _now()
        with _tx(self.conn):
            self.conn.execute(
                "UPDATE undo_stack SET undone_at=? WHERE undone_at IS NULL", (now,)
            )
            self.conn.execute(
                "UPDATE redo_stack SET redone_at=? WHERE redone_at IS NULL", (now,)
            )

    def open_undo_entries(self, limit=JOURNAL_MAX, session_id=None):
        """Undoable entries, most recent first (for a 'what can I undo' view).

        Includes the decoded `inverse` payload so callers can preview the
        content each undo would produce (content-aware timeline). When
        `session_id` is given, only entries from THAT session are returned
        (used for unscoped 'undo last'); when None, entries from ALL sessions
        are returned (used for per-file navigation across restarts).
        """
        if session_id is None:
            rows = self.conn.execute(
                "SELECT id, saga_id, label, inverse, created_at FROM undo_stack "
                "WHERE undone_at IS NULL ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, saga_id, label, inverse, created_at FROM undo_stack "
                "WHERE undone_at IS NULL AND session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["inverse"] = _load(r["inverse"])
            out.append(d)
        return out

    # -- Redo stack -----------------------------------------------------------
    # Symmetric counterpart of the undo stack. When undo_last replays a
    # compensation, the caller captures how to RE-APPLY the original action and
    # calls push_redo. redo_last consumes it and (via push_undo) puts the action
    # back on the undo stack, so undo<->redo can ping-pong any number of levels.

    def push_redo(self, saga_id, label, redo):
        """Record how to re-apply an action that was just undone."""
        now = _now()
        with _tx(self.conn):
            self.conn.execute(
                "INSERT INTO redo_stack (saga_id, label, redo, created_at, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (saga_id, label or "", _dump(redo), now, self.session_id),
            )

    def push_undo(self, saga_id, label, inverse):
        """Put an entry back on the undo stack (used when a redo re-applies an
        action, so that re-applied action is itself undoable again)."""
        now = _now()
        with _tx(self.conn):
            self.conn.execute(
                "INSERT INTO undo_stack (saga_id, label, inverse, created_at, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (saga_id, label or "", _dump(inverse), now, self.session_id),
            )

    def peek_redo(self):
        """The next redoable entry (top of redo stack), or None."""
        row = self.conn.execute(
            "SELECT id, saga_id, label, redo FROM redo_stack "
            "WHERE redone_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "saga_id": row["saga_id"],
                "label": row["label"], "redo": _load(row["redo"])}

    def redo_last(self):
        """Pop the top redo entry: mark it consumed, return it for replay."""
        with _tx(self.conn):
            row = self.conn.execute(
                "SELECT id, saga_id, label, redo FROM redo_stack "
                "WHERE redone_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = _now()
            self.conn.execute(
                "UPDATE redo_stack SET redone_at=? WHERE id=?", (now, row["id"])
            )
            return {"saga_id": row["saga_id"], "label": row["label"],
                    "redo": _load(row["redo"])}

    def open_redo_entries(self, limit=JOURNAL_MAX, session_id=None):
        """Redoable entries, most recent first (for a 'what can I redo' view).

        Includes the decoded `redo` payload so callers can preview the content
        each redo would produce. When `session_id` is given, only that session's
        entries are returned; when None, all sessions (cross-restart redo).
        """
        if session_id is None:
            rows = self.conn.execute(
                "SELECT id, saga_id, label, redo, created_at FROM redo_stack "
                "WHERE redone_at IS NULL ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, saga_id, label, redo, created_at FROM redo_stack "
                "WHERE redone_at IS NULL AND session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["redo"] = _load(r["redo"])
            out.append(d)
        return out
