"""Stage 2.9: delete confirmation "ask once, then burst".

Policy (user choice): the FIRST delete is confirmed; a rapid series of
follow-up deletes then runs without re-prompting, within a short rolling
window. The burst is interactive-only — an autonomous task can never ride it.
Delete is reversible (Recycle Bin + undo), so its confirm reason must not
claim it is "hard to undo".

Runner-style (pytest-free): module-level test_* + _run().
"""
from core import security as sec


def _delete(confirmed=False):
    p = {"action": "delete", "name": "x.txt", "path": "desktop"}
    if confirmed:
        p["confirmed"] = True
    return p


def test_first_delete_needs_confirmation():
    sec.reset_delete_burst()
    need, reason = sec.needs_confirmation("file_controller", _delete())
    assert need is True, "a cold first delete must ask"
    assert reason, "should carry a reason"


def test_confirmed_delete_opens_the_burst():
    sec.reset_delete_burst()
    assert sec._burst_active() is False
    need, _ = sec.needs_confirmation("file_controller", _delete(confirmed=True))
    assert need is False, "confirmed delete runs"
    assert sec._burst_active() is True, "a confirmed delete opens the burst window"


def test_followup_delete_rides_the_burst_without_asking():
    sec.reset_delete_burst()
    sec.needs_confirmation("file_controller", _delete(confirmed=True))  # open
    need, _ = sec.needs_confirmation("file_controller", _delete())  # no confirmed
    assert need is False, "a follow-up delete during the burst must NOT re-ask"


def test_burst_is_delete_only_write_still_confirms():
    sec.reset_delete_burst()
    sec.needs_confirmation("file_controller", _delete(confirmed=True))  # burst open
    need, reason = sec.needs_confirmation(
        "file_controller", {"action": "write", "name": "a.txt", "content": "x"}
    )
    assert need is True, "write must still confirm even during a delete burst"
    assert "hard to undo" in reason, "write keeps the hard-to-undo wording"


def test_autonomous_never_rides_the_burst():
    sec.reset_delete_burst()
    sec.needs_confirmation("file_controller", _delete(confirmed=True))  # open (interactive)
    need, _ = sec.needs_confirmation("file_controller", _delete(), mode="autonomous")
    assert need is True, "an autonomous delete must confirm even if a burst is open"


def test_reset_closes_the_burst():
    sec.reset_delete_burst()
    sec.needs_confirmation("file_controller", _delete(confirmed=True))
    sec.reset_delete_burst()
    need, _ = sec.needs_confirmation("file_controller", _delete())
    assert need is True, "after reset the next delete asks again"


def test_window_expires_by_time():
    sec.reset_delete_burst()
    sec.open_delete_burst(now=0.0)
    assert sec._burst_active(now=sec._DELETE_BURST_WINDOW_S - 1) is True
    assert sec._burst_active(now=sec._DELETE_BURST_WINDOW_S + 1) is False


def test_delete_reason_says_reversible_not_hard_to_undo():
    sec.reset_delete_burst()
    _, reason = sec.needs_confirmation("file_controller", _delete())
    assert "REVERSIBLE" in reason or "Recycle Bin" in reason
    assert "hard to undo" not in reason, "delete is reversible now; drop the false wording"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print("ok   -", fn.__name__)
        except Exception as e:  # noqa
            failed += 1
            print("FAIL -", fn.__name__, "::", repr(e))
    print(f"\n{fn.__module__}: passed={passed} failed={failed}")
    return failed


if __name__ == "__main__":
    raise SystemExit(_run())
