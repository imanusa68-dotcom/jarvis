# tests/test_stage3a_gate.py
"""
Stage 3A, step 4 - consent tickets wired into the single execution gate.

The headline test here is test_the_model_can_no_longer_approve_itself. Until
now the ONLY thing stopping Jarvis from setting confirmed=true on its own call
was a sentence in the prompt asking it not to. This project's own rule says the
engine enforces safety, not the model - so that sentence was a promise, not a
lock. These tests are the lock.

The flag is OFF by default, so the last group asserts that today's behaviour is
byte-for-byte unchanged until we deliberately switch it on.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import store, gate  # noqa: E402
from core import consent_runtime as rt  # noqa: E402
from core import consent_store as cs  # noqa: E402
from core import feature_flags as ff  # noqa: E402
import core.security as sec  # noqa: E402


DELETE = {"path": r"C:\Users\rdrr\Downloads\report.txt", "action": "delete"}


def setup_consent(on=True):
    """Fresh DB + flag. Returns the connection."""
    d = Path(tempfile.mkdtemp(prefix="jarvis-3a-gate-"))
    conn = store.open_store(d / "jarvis.db")
    rt.set_override(conn, session_id="s-test")
    ff.durable_consent_enabled = lambda: on
    gate._consent_enabled = lambda: on
    return conn


def teardown():
    rt.clear_override()


def gate_call(params, mode="interactive"):
    return gate.dispatch("file_controller", dict(params), mode=mode,
                         screen_control=True)


# -- Precondition -------------------------------------------------------------

def test_delete_is_a_confirm_policy_action():
    # If this ever stops being true the rest of the file is testing nothing.
    assert sec.get_policy("file_controller", DELETE) == "confirm"


# -- The lock -----------------------------------------------------------------

def test_the_model_can_no_longer_approve_itself():
    setup_consent(on=True)
    try:
        r = gate_call({**DELETE, "confirmed": True})
        assert r.verdict == "confirm", "a self-set confirmed=true still executed"
        assert not r.allowed
    finally:
        teardown()


def test_first_call_asks_and_hands_out_a_consent_id():
    conn = setup_consent(on=True)
    try:
        r = gate_call(DELETE)
        assert r.verdict == "confirm"
        assert "consent_id=cst_" in r.message
        # The question is stored, and the model is told to quote it verbatim.
        ticket = cs.pending(conn, "s-test")[0]
        assert ticket["preview"] in r.message
        assert "EXACTLY" in r.message
        assert "report.txt" in ticket["preview"]
    finally:
        teardown()


def test_a_real_consent_id_runs_the_action():
    conn = setup_consent(on=True)
    try:
        gate_call(DELETE)
        ticket = cs.pending(conn, "s-test")[0]["ticket"]
        r = gate_call({**DELETE, "consent_id": ticket})
        assert r.verdict == "run"
        assert cs.get(conn, ticket)["status"] == "consumed"
    finally:
        teardown()


def test_the_same_consent_id_cannot_run_twice():
    conn = setup_consent(on=True)
    try:
        gate_call(DELETE)
        ticket = cs.pending(conn, "s-test")[0]["ticket"]
        assert gate_call({**DELETE, "consent_id": ticket}).verdict == "run"
        second = gate_call({**DELETE, "consent_id": ticket})
        assert second.verdict == "confirm", "a spent consent ran a second time"
        assert "REFUSED" in second.message
    finally:
        teardown()


def test_a_consent_for_one_file_cannot_delete_another():
    conn = setup_consent(on=True)
    try:
        gate_call(DELETE)
        ticket = cs.pending(conn, "s-test")[0]["ticket"]
        r = gate_call({"path": r"C:\Users\rdrr\Downloads\taxes.xlsx",
                       "action": "delete", "consent_id": ticket})
        assert r.verdict == "confirm", "consent was reused for a different file"
        assert "not the operation" in r.message
    finally:
        teardown()


def test_an_invented_consent_id_is_refused():
    setup_consent(on=True)
    try:
        r = gate_call({**DELETE, "consent_id": "cst_1234567890"})
        assert r.verdict == "confirm"
        assert "unknown consent" in r.message
    finally:
        teardown()


def test_re_spelling_the_path_still_matches():
    # The re-call is rarely byte-identical. If this breaks, Jarvis loops.
    conn = setup_consent(on=True)
    try:
        gate_call(DELETE)
        ticket = cs.pending(conn, "s-test")[0]["ticket"]
        r = gate_call({"path": "c:/users/rdrr/downloads/report.txt",
                       "action": "delete", "consent_id": ticket})
        assert r.verdict == "run"
    finally:
        teardown()


def test_asking_twice_does_not_mint_two_tickets():
    conn = setup_consent(on=True)
    try:
        a = gate_call(DELETE)
        b = gate_call(DELETE)
        assert len(cs.pending(conn, "s-test")) == 1
        assert a.message.split("consent_id=")[1][:14] == \
               b.message.split("consent_id=")[1][:14]
    finally:
        teardown()


# -- Unchanged neighbours ------------------------------------------------------

def test_harmless_actions_are_not_slowed_down():
    setup_consent(on=True)
    try:
        r = gate.dispatch("web_search", {"query": "weather"}, mode="interactive")
        assert r.verdict == "run"
    finally:
        teardown()


def test_autonomous_mode_still_refuses_outright():
    # No human is present, so there is nobody to hand a consent id to.
    setup_consent(on=True)
    try:
        r = gate_call({**DELETE, "confirmed": True}, mode="autonomous")
        assert r.verdict == "blocked"
    finally:
        teardown()


def test_a_broken_consent_store_does_not_open_the_gate():
    # Degrading must never mean "run it anyway".
    setup_consent(on=True)
    rt.set_override(None, session_id="s-test")
    try:
        r = gate_call(DELETE)
        assert r.verdict == "confirm", "gate opened when consent storage failed"
    finally:
        teardown()


# -- Flag OFF: today's behaviour is untouched ---------------------------------

def test_with_the_flag_off_nothing_changes():
    conn = setup_consent(on=False)
    try:
        assert gate_call(DELETE).verdict == "confirm"
        # The legacy boolean still works while the flag is off.
        sec.reset_delete_burst()
        assert gate_call({**DELETE, "confirmed": True}).verdict == "run"
        # ...and no tickets are created.
        assert cs.pending(conn, "s-test") == []
    finally:
        sec.reset_delete_burst()
        teardown()
