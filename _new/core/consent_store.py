# core/consent_store.py
"""
Stage 3A, step 3 - the lifecycle of a consent ticket.

core/consent.py answers "are these two calls the same operation?".
This module answers "did the user agree to THIS operation, right now, once?".

The four properties that make a consent real, and where each one lives:

  bound       mint() stores the fingerprint of the exact call; consume()
              recomputes it and refuses on any difference. The user cannot be
              shown one thing and served another (TOCTOU).
  single-use  consume() is an UPDATE guarded by `status='pending'`. SQLite
              gives us the rowcount, so exactly one caller can win a race. A
              replayed "yes" hits zero rows and is rejected.
  expiring    expires_at is UTC WALL CLOCK, not monotonic(), because the whole
              point of 3A is surviving a process restart - and monotonic()
              resets to zero there.
  durable     it is a row. A dropped websocket mid-confirmation no longer
              throws the answer away.

Fail-closed is the rule everywhere: every unexpected state (missing ticket,
unknown ticket, expired, already used, clock gone backwards, fingerprint
mismatch) results in NO execution. The cost of a wrong "no" is one extra
question. The cost of a wrong "yes" is a deleted folder.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from core import consent as _fp
from core.consent import ConsentError


# -- TTL policy ---------------------------------------------------------------
# Two clocks, because the two risks are not the same:
#   irreversible  short. The world moves while the user thinks; a two-minute
#                 old "yes" about a folder is already a guess about the past.
#   reversible    longer. Undo is the real safety net here, so the deadline is
#                 a courtesy, not a wall.
# Deliberately NOT user-configurable yet: a knob nobody understands is how a
# safety default quietly becomes 24 hours.
TTL_IRREVERSIBLE_S = 120
TTL_REVERSIBLE_S = 600

# A scope replaces the old in-RAM delete-burst window. That window refreshed on
# every delete, so one "yes" could ride an unbroken chain for hours. A scope is
# bounded on THREE axes at once - folder, count and time - and any one of them
# running out ends it.
SCOPE_MAX_USES = 20
SCOPE_TTL_S = 180

# Guard against a model that re-asks in a loop and floods the table.
MAX_PENDING_PER_SESSION = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_ticket_id() -> str:
    # Short enough to survive a round trip through the model, random enough
    # that it cannot be guessed or auto-completed into a valid one.
    return "cst_" + secrets.token_hex(5)


# -- Mint ---------------------------------------------------------------------

def mint(
    conn,
    *,
    tool: str,
    action: str | None,
    parameters: dict | None,
    preview: str,
    risk: str = "high",
    reversible: bool = False,
    session_id: str | None = None,
    origin: str = "interactive",
    now: datetime | None = None,
) -> dict:
    """Open a question. Returns the ticket row.

    `preview` is the sentence the user is about to hear. It is stored, not
    regenerated, so that after a restart the question can be re-read WORD FOR
    WORD. A model paraphrasing '340 files' as 'a few files' on the second
    telling would be asking about a different operation than the one bound.

    Re-minting the same operation in the same session REUSES the open ticket
    and pushes its deadline out, rather than creating a second one. Two live
    tickets for one operation is an ambiguity, and ambiguity is how the wrong
    one gets consumed.
    """
    if not tool:
        raise ConsentError("mint requires a tool")
    if not str(preview or "").strip():
        # A consent with no human-readable question is not a consent.
        raise ConsentError("mint requires a preview the user can actually hear")

    at = now or _now()
    ttl = TTL_REVERSIBLE_S if reversible else TTL_IRREVERSIBLE_S
    fingerprint = _fp.payload_fingerprint(tool, action, parameters)
    payload = _fp.canonical_payload(tool, action, parameters)

    sweep_expired(conn, now=at)

    row = conn.execute(
        "SELECT * FROM consent_ticket WHERE fingerprint=? AND status='pending' "
        "AND (session_id IS ? OR session_id=?)",
        (fingerprint, session_id, session_id),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE consent_ticket SET expires_at=?, preview=? WHERE id=?",
            (_iso(at + timedelta(seconds=ttl)), preview, row["id"]),
        )
        return get(conn, row["ticket"])

    open_count = conn.execute(
        "SELECT COUNT(*) FROM consent_ticket WHERE status='pending' "
        "AND (session_id IS ? OR session_id=?)",
        (session_id, session_id),
    ).fetchone()[0]
    if open_count >= MAX_PENDING_PER_SESSION:
        # Drop the OLDEST question rather than refuse the newest: the thing the
        # user is talking about right now is the one that matters.
        conn.execute(
            "UPDATE consent_ticket SET status='expired', decided_at=? WHERE id = ("
            "  SELECT id FROM consent_ticket WHERE status='pending' "
            "  AND (session_id IS ? OR session_id=?) ORDER BY created_at LIMIT 1)",
            (_iso(at), session_id, session_id),
        )

    ticket = new_ticket_id()
    conn.execute(
        "INSERT INTO consent_ticket (ticket, session_id, tool, action, fingerprint, "
        "payload, preview, risk, reversible, status, origin, created_at, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?,?,?)",
        (ticket, session_id, str(tool), action, fingerprint, payload, str(preview),
         str(risk), 1 if reversible else 0, str(origin),
         _iso(at), _iso(at + timedelta(seconds=ttl))),
    )
    return get(conn, ticket)


# -- Consume ------------------------------------------------------------------

def consume(
    conn,
    *,
    ticket: str | None,
    tool: str,
    action: str | None,
    parameters: dict | None,
    session_id: str | None = None,
    now: datetime | None = None,
    on_authorized=None,
) -> dict:
    """Spend a consent, or raise. There is no third outcome.

    `on_authorized(conn, row)` runs INSIDE the same transaction as the status
    flip and may return a saga id. That is not a nicety: if the ticket were
    marked used in one transaction and the action journalled in another, a
    crash between them leaves a consent that was spent on nothing, and the user
    would have to answer again for work that may already be half done. One
    transaction means the consent and the intent are born together or not at
    all.
    """
    at = now or _now()
    if not ticket:
        raise ConsentError("no consent id was supplied")

    row = conn.execute(
        "SELECT * FROM consent_ticket WHERE ticket=?", (str(ticket),)
    ).fetchone()
    if row is None:
        # Includes the model inventing an id that looks plausible.
        raise ConsentError(f"unknown consent id {ticket!r}")
    if row["status"] != "pending":
        raise ConsentError(
            f"consent {ticket!r} is already {row['status']} and cannot be reused"
        )
    if _parse(row["expires_at"]) <= at:
        conn.execute(
            "UPDATE consent_ticket SET status='expired', decided_at=? WHERE id=? "
            "AND status='pending'",
            (_iso(at), row["id"]),
        )
        raise ConsentError(f"consent {ticket!r} expired - ask again")
    if _parse(row["created_at"]) > at + timedelta(seconds=5):
        # Laptop sleep or an NTP correction can move the clock backwards. A
        # ticket minted "in the future" means we cannot reason about deadlines,
        # so we refuse instead of guessing generously.
        raise ConsentError(
            f"consent {ticket!r} was created in the future - clock changed, ask again"
        )
    if session_id is not None and row["session_id"] is not None \
            and row["session_id"] != session_id:
        # Survives a restart as a QUESTION, never as an ANSWER: a new run must
        # re-present it, not silently cash it in.
        raise ConsentError(
            f"consent {ticket!r} belongs to an earlier session - confirm again"
        )

    fingerprint = _fp.payload_fingerprint(tool, action, parameters)
    if fingerprint != row["fingerprint"]:
        diffs = _fp.explain_mismatch(
            row["tool"], row["action"], _payload_params(row["payload"]),
            tool, action, parameters,
        )
        # The ticket stays PENDING on purpose. A mismatch usually means the
        # model rebuilt the call slightly differently, not that the user
        # changed their mind - burning the ticket would force a pointless
        # re-ask. What it must never do is execute.
        raise ConsentError(
            "this is not the operation that was approved: "
            + ("; ".join(diffs) if diffs else "payload differs")
        )

    # БЛОК 7: ОДНА ТРАНЗАКЦИЯ НА ТРИ ПРАВКИ. Раньше их было три отдельных.
    #
    # Обещание в шапке этой функции («on_authorized runs INSIDE the same
    # transaction as the status flip») было НЕ ВЫПОЛНЕНО: слово BEGIN в этом
    # файле не встречалось ни разу, и атомарность держалась только на том, что
    # соединение одно. Найдено разбором 19.08.2026.
    #
    # Цена невыполнения названа в той же шапке: сбой между отметкой талона и
    # записью в журнал оставляет талон, потраченный НИ НА ЧТО, и владелец
    # отвечает на вопрос второй раз за работу, которая может быть уже сделана
    # наполовину. Теперь либо талон потрачен И работа записана, либо не
    # произошло ни того, ни другого.
    from core import writer
    with writer.transaction(conn):
        cur = conn.execute(
            "UPDATE consent_ticket SET status='consumed', consumed_at=?, "
            "decided_at=? WHERE id=? AND status='pending'",
            (_iso(at), _iso(at), row["id"]),
        )
        if cur.rowcount != 1:
            # Someone else won the race in the microseconds since we read the row.
            raise ConsentError(f"consent {ticket!r} was used by another call")

        if on_authorized is not None:
            saga_id = on_authorized(conn, dict(row))
            if saga_id is not None:
                conn.execute(
                    "UPDATE consent_ticket SET consumed_saga_id=? WHERE id=?",
                    (int(saga_id), row["id"]),
                )
    return get(conn, row["ticket"])


def _payload_params(payload_text: str) -> dict:
    import json
    try:
        return dict(json.loads(payload_text).get("params") or {})
    except Exception:
        return {}


# -- Other transitions --------------------------------------------------------

def decline(conn, ticket: str, now: datetime | None = None) -> None:
    """The user said no. Recorded, not deleted: 'I already told you no' has to
    be answerable, and a declined question must never be re-cashed."""
    conn.execute(
        "UPDATE consent_ticket SET status='declined', decided_at=? "
        "WHERE ticket=? AND status='pending'",
        (_iso(now or _now()), str(ticket)),
    )


def revoke_session(conn, session_id: str | None, now: datetime | None = None) -> int:
    """Cancel every open question of a session (called on shutdown/reset).

    Scoped grants especially must not outlive the conversation that opened
    them - that was the old burst window's worst property.
    """
    cur = conn.execute(
        "UPDATE consent_ticket SET status='revoked', decided_at=? "
        "WHERE status='pending' AND (session_id IS ? OR session_id=?)",
        (_iso(now or _now()), session_id, session_id),
    )
    return cur.rowcount


def sweep_expired(conn, now: datetime | None = None) -> int:
    """Retire deadlines that have passed. Cheap, indexed, and idempotent."""
    at = _iso(now or _now())
    cur = conn.execute(
        "UPDATE consent_ticket SET status='expired', decided_at=? "
        "WHERE status='pending' AND expires_at<=?",
        (at, at),
    )
    return cur.rowcount


# -- Reads --------------------------------------------------------------------

def get(conn, ticket: str) -> dict:
    row = conn.execute(
        "SELECT * FROM consent_ticket WHERE ticket=?", (str(ticket),)
    ).fetchone()
    if row is None:
        raise ConsentError(f"unknown consent id {ticket!r}")
    return dict(row)


def pending(conn, session_id: str | None = None, now: datetime | None = None) -> list:
    """Open questions, oldest first. This is what a restarted Jarvis re-reads
    out loud instead of pretending the conversation never happened."""
    sweep_expired(conn, now=now)
    if session_id is None:
        rows = conn.execute(
            "SELECT * FROM consent_ticket WHERE status='pending' ORDER BY created_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM consent_ticket WHERE status='pending' "
            "AND (session_id IS ? OR session_id=?) ORDER BY created_at",
            (session_id, session_id),
        ).fetchall()
    return [dict(r) for r in rows]


def history(conn, limit: int = 20) -> list:
    """'What did I approve, and when?' - previously unanswerable."""
    rows = conn.execute(
        "SELECT ticket, tool, action, preview, status, created_at, decided_at, "
        "consumed_saga_id FROM consent_ticket ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]
