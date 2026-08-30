# tests/test_stage3a_consent_store.py
"""
Stage 3A, step 3 - the consent ticket lifecycle.

Every test here is a bug we would otherwise ship. The old mechanism (a boolean
the model sets on its own call) fails every single one of them, which is the
point: this file is the difference between a confirmation and a formality.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import store  # noqa: E402
from core import consent_store as cs  # noqa: E402
from core.consent import ConsentError  # noqa: E402


def fresh_db():
    d = Path(tempfile.mkdtemp())
    return store.open_store(d / "jarvis.db")


DEL = {"tool": "file_controller", "action": "delete"}
P = {"path": r"C:\Users\rdrr\Downloads\report.txt"}


def mint(conn, params=None, **kw):
    kw.setdefault("preview", "Delete report.txt from Downloads?")
    kw.setdefault("session_id", "s1")
    return cs.mint(conn, tool=DEL["tool"], action=DEL["action"],
                   parameters=params if params is not None else P, **kw)


def spend(conn, ticket, params=None, session_id="s1", **kw):
    return cs.consume(conn, ticket=ticket, tool=DEL["tool"], action=DEL["action"],
                      parameters=params if params is not None else P,
                      session_id=session_id, **kw)


# -- The happy path -----------------------------------------------------------

def test_mint_then_consume():
    c = fresh_db()
    t = mint(c)
    assert t["status"] == "pending"
    out = spend(c, t["ticket"])
    assert out["status"] == "consumed"
    assert out["consumed_at"]


def test_spelling_differences_still_match():
    # The re-call almost never looks byte-identical to the mint. If this fails,
    # Jarvis asks the same question forever and the user stops listening.
    c = fresh_db()
    t = mint(c, {"path": r"C:\Users\rdrr\Downloads\report.txt"})
    out = spend(c, t["ticket"], {"path": "c:/users/rdrr/downloads/report.txt",
                                 "confirmed": True})
    assert out["status"] == "consumed"


# -- Single use ---------------------------------------------------------------

def test_a_ticket_cannot_be_spent_twice():
    c = fresh_db()
    t = mint(c)
    spend(c, t["ticket"])
    try:
        spend(c, t["ticket"])
        assert False, "a replayed yes was accepted"
    except ConsentError as e:
        assert "consumed" in str(e)


def test_race_only_one_winner():
    # Two calls arriving together must not both execute. The UPDATE guard is
    # what makes this true; a read-then-write check would let both through.
    c = fresh_db()
    t = mint(c)
    wins = 0
    for _ in range(5):
        try:
            spend(c, t["ticket"])
            wins += 1
        except ConsentError:
            pass
    assert wins == 1


# -- Binding (the TOCTOU property) --------------------------------------------

def test_a_yes_for_one_file_cannot_delete_another():
    c = fresh_db()
    t = mint(c, {"path": "C:/tmp/a.txt"})
    try:
        spend(c, t["ticket"], {"path": "C:/tmp/b.txt"})
        assert False, "consent was reused for a different file"
    except ConsentError as e:
        assert "not the operation" in str(e)
        assert "a.txt" in str(e) and "b.txt" in str(e)  # names the field


def test_a_yes_for_a_file_cannot_delete_its_folder():
    c = fresh_db()
    t = mint(c, {"path": r"C:\Users\rdrr\Downloads\a.txt"})
    try:
        spend(c, t["ticket"], {"path": r"C:\Users\rdrr\Downloads"})
        assert False, "consent escalated from a file to its folder"
    except ConsentError:
        pass


def test_a_yes_for_three_files_cannot_delete_four():
    c = fresh_db()
    t = mint(c, {"paths": ["C:/a", "C:/b", "C:/c"]})
    try:
        spend(c, t["ticket"], {"paths": ["C:/a", "C:/b", "C:/c", "C:/d"]})
        assert False, "the set of files grew after approval"
    except ConsentError:
        pass


def test_mismatch_leaves_the_ticket_usable():
    # A near-miss is usually the model rebuilding the call, not the user
    # changing their mind. Burning the ticket would force a pointless re-ask.
    c = fresh_db()
    t = mint(c, {"path": "C:/tmp/a.txt"})
    try:
        spend(c, t["ticket"], {"path": "C:/tmp/b.txt"})
    except ConsentError:
        pass
    assert cs.get(c, t["ticket"])["status"] == "pending"
    assert spend(c, t["ticket"], {"path": "C:/tmp/a.txt"})["status"] == "consumed"


# -- Fail closed --------------------------------------------------------------

def test_no_ticket_is_refused():
    c = fresh_db()
    for bad in (None, "", "cst_deadbeef99"):
        try:
            spend(c, bad)
            assert False, f"executed with consent id {bad!r}"
        except ConsentError:
            pass


def test_expired_ticket_is_refused_and_recorded():
    c = fresh_db()
    t = mint(c)
    later = datetime.now(timezone.utc) + timedelta(seconds=cs.TTL_IRREVERSIBLE_S + 1)
    try:
        spend(c, t["ticket"], now=later)
        assert False, "an expired consent was accepted"
    except ConsentError as e:
        assert "expired" in str(e)
    assert cs.get(c, t["ticket"])["status"] == "expired"


def test_reversible_actions_get_a_longer_deadline():
    c = fresh_db()
    quick = mint(c, {"path": "C:/a"}, reversible=False)
    slow = mint(c, {"path": "C:/b"}, reversible=True)
    span = lambda t: (datetime.fromisoformat(t["expires_at"])
                      - datetime.fromisoformat(t["created_at"])).total_seconds()
    assert span(quick) == cs.TTL_IRREVERSIBLE_S
    assert span(slow) == cs.TTL_REVERSIBLE_S


def test_clock_jumping_backwards_fails_closed():
    # Laptop sleep and NTP corrections are normal on a real machine.
    c = fresh_db()
    t = mint(c)
    earlier = datetime.now(timezone.utc) - timedelta(hours=2)
    try:
        spend(c, t["ticket"], now=earlier)
        assert False, "accepted a consent while the clock was wrong"
    except ConsentError as e:
        assert "clock" in str(e)


def test_a_declined_answer_cannot_be_cashed_later():
    c = fresh_db()
    t = mint(c)
    cs.decline(c, t["ticket"])
    assert cs.get(c, t["ticket"])["status"] == "declined"
    try:
        spend(c, t["ticket"])
        assert False, "a no was executed as a yes"
    except ConsentError:
        pass


def test_mint_requires_a_question_the_user_can_hear():
    c = fresh_db()
    try:
        mint(c, preview="   ")
        assert False, "minted a consent with no question"
    except ConsentError:
        pass


# -- Restart behaviour (the stage-3 criterion) --------------------------------

def test_the_question_survives_a_restart():
    d = Path(tempfile.mkdtemp())
    c1 = store.open_store(d / "jarvis.db")
    t = mint(c1)
    c1.close()  # websocket dropped, process died

    c2 = store.open_store(d / "jarvis.db")
    open_qs = cs.pending(c2)
    assert len(open_qs) == 1
    # Re-read WORD FOR WORD, not paraphrased.
    assert open_qs[0]["preview"] == "Delete report.txt from Downloads?"
    assert open_qs[0]["ticket"] == t["ticket"]


def test_the_answer_does_not_survive_a_restart():
    # The opposite bug: a zombie yes applied to a world that has moved on.
    c = fresh_db()
    t = mint(c, session_id="session-A")
    try:
        spend(c, t["ticket"], session_id="session-B")
        assert False, "an old session's yes was reused after a restart"
    except ConsentError as e:
        assert "earlier session" in str(e)


def test_shutdown_revokes_open_questions():
    c = fresh_db()
    mint(c, {"path": "C:/a"})
    mint(c, {"path": "C:/b"})
    assert cs.revoke_session(c, "s1") == 2
    assert cs.pending(c, "s1") == []


# -- Housekeeping -------------------------------------------------------------

def test_re_asking_reuses_one_ticket():
    c = fresh_db()
    a = mint(c)
    b = mint(c)
    assert a["ticket"] == b["ticket"]
    assert len(cs.pending(c, "s1")) == 1


def test_pending_tickets_cannot_pile_up_forever():
    c = fresh_db()
    for i in range(cs.MAX_PENDING_PER_SESSION + 4):
        mint(c, {"path": f"C:/f{i}.txt"})
    assert len(cs.pending(c, "s1")) <= cs.MAX_PENDING_PER_SESSION


def test_sweep_retires_deadlines():
    c = fresh_db()
    mint(c)
    later = datetime.now(timezone.utc) + timedelta(seconds=cs.TTL_IRREVERSIBLE_S + 1)
    assert cs.sweep_expired(c, now=later) == 1
    assert cs.sweep_expired(c, now=later) == 0  # idempotent


def test_history_answers_what_did_i_approve():
    c = fresh_db()
    t = mint(c)
    spend(c, t["ticket"])
    h = cs.history(c)
    assert h[0]["status"] == "consumed"
    assert h[0]["preview"] == "Delete report.txt from Downloads?"


# -- Atomicity with the journal ------------------------------------------------

def test_consent_and_intent_commit_together():
    c = fresh_db()
    from core.journal import Journal
    j = Journal(c)
    t = mint(c)

    def authorize(conn, row):
        return j.begin_intent("file_controller", "delete", intent={"path": "x"})

    out = spend(c, t["ticket"], on_authorized=authorize)
    assert out["consumed_saga_id"] is not None
    assert j.get_saga(out["consumed_saga_id"])["status"] == "intent"


def test_a_failure_while_authorizing_does_not_eat_the_consent():
    # If the ticket were spent in one transaction and the work recorded in
    # another, a crash between them would burn the user's yes on nothing.
    c = fresh_db()
    t = mint(c)

    def boom(conn, row):
        raise RuntimeError("crash right after the flip")

    try:
        c.execute("BEGIN IMMEDIATE")
        spend(c, t["ticket"], on_authorized=boom)
        c.execute("COMMIT")
    except RuntimeError:
        c.execute("ROLLBACK")

    assert cs.get(c, t["ticket"])["status"] == "pending", \
        "the yes was consumed even though nothing ran"
