"""Turn durable confirmations on or off, and see what was approved.

This is a small human-facing switch, not part of the runtime. It exists because
a safety change nobody can inspect is a safety change nobody trusts.

    python consent_mode.py           # show the current mode
    python consent_mode.py on        # durable confirmations (consent tickets)
    python consent_mode.py off       # legacy behaviour (the old boolean flag)
    python consent_mode.py log       # what was asked, approved, refused, expired

Durable confirmations are ON by default, so normally you never need this.

WHERE THE SWITCH LIVES: ~/.jarvis/settings.json - your home folder, NOT the
project folder (phase 0, step 2, 2026-08-06). Flipping it here changes it for
every copy of Jarvis on this machine, including the next one you unzip. Until
that step the setting lived inside the project, so a fresh unzip quietly went
back to the default and nobody noticed - which is why the running mode is still
printed at startup: a safety mode you cannot see is a safety mode you cannot
trust.

Restart Jarvis after flipping it: the tool declarations are sent once at
connection setup and cannot change mid-session.
"""

import sys

from core import feature_flags as flags
from core import store


def _show() -> None:
    on = flags.durable_consent_enabled()
    print()
    print("  Durable confirmations: " + ("ON" if on else "OFF"))
    print()
    if on:
        print("  A dangerous action now needs a consent id that the gate issued.")
        print("  Jarvis cannot approve itself, and your answer survives a restart.")
    else:
        print("  Legacy mode: Jarvis re-calls the action with a 'confirmed' flag")
        print("  that it sets itself. Your answer is lost if the connection drops.")
    print()


def _set(value: bool) -> None:
    flags.set_durable_consent_enabled(value)
    _show()
    print("  Restart Jarvis for this to take effect.")
    print()


def _log() -> None:
    """Answer the question 'what did I actually agree to?' from the record.

    The preview text shown here is the exact sentence that was read out to you,
    stored at the moment it was asked. It is not a later reconstruction, which
    is the whole point: a summary written after the fact can be flattering.
    """
    from core import consent_store as cs
    from core import writer

    # Блок 7: соединение ЧТЕНИЯ. Этот экран только показывает историю
    # подтверждений и не пишет ни строки (проверено: ни одного INSERT,
    # UPDATE или DELETE в файле) — значит и права писать у него быть не
    # должно. Открытие «только на чтение» делает это свойством соединения,
    # а не обещанием в комментарии.
    conn = writer.reader()
    rows = cs.history(conn, limit=25)
    if not rows:
        print("\n  Nothing to show yet - no confirmations have been requested.\n")
        return

    marks = {
        "consumed": "[approved]",
        "pending":  "[waiting] ",
        "declined": "[refused] ",
        "expired":  "[expired] ",
        "revoked":  "[revoked] ",
    }
    print()
    for r in rows:
        mark = marks.get(r["status"], "[" + str(r["status"]) + "]")
        print(f"  {mark} {r['created_at'][:19]}  {r['preview']}")
    print()
    print("  'waiting' means it was asked but never answered - nothing ran.")
    print()


def main() -> int:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if arg in ("", "status", "show"):
        _show()
    elif arg in ("on", "enable", "true", "1"):
        _set(True)
    elif arg in ("off", "disable", "false", "0"):
        _set(False)
    elif arg in ("log", "history", "audit"):
        _log()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())