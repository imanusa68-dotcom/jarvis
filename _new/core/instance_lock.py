# core/instance_lock.py
"""
Phase 0, step 4 - one Jarvis at a time.

WHY THIS EXISTS
---------------
Nothing stopped a second Jarvis from starting. Two runtimes share one state
directory, so they write into the same ~/.jarvis/jarvis.db, both open the
microphone, both answer out loud, and both spend the same daily model quota.
Nothing crashes; the day just becomes inexplicable.

WHY NOT A PID FILE THAT IS DELETED ON EXIT
------------------------------------------
The obvious design cannot work in THIS program. Closing the window runs
ui.py:156 -> os._exit(0), which skips every `finally` and every atexit hook.
The file would survive every normal shutdown, and the next launch would refuse
to start with "already running" while nothing is running. A lock that jams is
worse than no lock at all.

WHAT IS USED INSTEAD
--------------------
An operating-system exclusive lock on ~/.jarvis/jarvis.lock, held open for the
lifetime of the process. The OS drops it when the process dies - normal exit,
os._exit, Ctrl+C, crash, power loss. Self-healing by construction: no liveness
probe, no pid-reuse trap, no stale file to clean up.

WHY THE PID LIVES IN A SECOND FILE (measured on Windows, 2026-08-06)
--------------------------------------------------------------------
The first attempt wrote the pid into the lock file itself at offset 0 and put
the locked byte far away at offset 4096, so that the loser could still read the
start of the file. It refused the second instance correctly - and the pid came
back empty every single time, even inside the process that owned the lock.

Cause: Windows byte-range locks are mandatory and are enforced per HANDLE, and
Python's buffered reader asks the OS for a whole 8 KiB block even when the
caller asks for 256 bytes. That block spans offset 4096, so ReadFile fails with
a lock violation, open()/read() raises PermissionError, and read_info() came
back as {}. The owner then saw "[Errno 13] Permission denied" instead of a pid.

So the diagnostics line now lives in a separate file, jarvis.lock.info, which
nobody locks and nobody holds open. The lock file stays a pure token: never
read, never written, only locked. No decision is ever made from the info file -
it exists so that a human can be told which process to close.

RULES FOR LATER PHASES
----------------------
  * Only the main runtime takes this lock. Child processes (vision, browser)
    must never call acquire() - a child is not a second Jarvis.
  * The location comes from core/safe_json.state_dir(), the one door to state.
    JARVIS_STATE_DIR redirects it, which is how tests stay out of the real
    ~/.jarvis.
  * Neither file is ever deleted. Their presence means nothing; only the OS
    lock does. Deleting would race with an instance that just opened them.
  * Nothing technical ever reaches the owner: AlreadyRunning.note is the only
    owner-facing string and it never carries an OS error code.

stdlib only, no new dependencies.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core.safe_json import state_dir

LOCK_FILENAME = "jarvis.lock"
INFO_FILENAME = "jarvis.lock.info"

# Layout, not tunable settings.
#   _INFO_BYTES  - read cap for the diagnostics file: it holds one short json
#                  line written by this module and nothing else.
#   _LOCK_OFFSET - where the locked byte sits inside the lock file. The file
#                  itself stays empty; locking past the end of a zero-length
#                  file is legal on both platforms and is what actually runs
#                  on the first launch on a fresh machine.
_INFO_BYTES = 512
_LOCK_OFFSET = 4096

# The open descriptor IS the lock. It lives in module state because it must
# stay open for the whole life of the process; letting it be garbage-collected
# would hand the lock back to the OS while Jarvis is still running.
_held_fd: int | None = None

# Why the last read of the info file came back empty. Diagnostics only: never
# shown to the owner, but a failing test prints it instead of an empty dict.
_last_read_error: str = ""


class AlreadyRunning(RuntimeError):
    """Another Jarvis holds the lock. Carries what it said about itself."""

    def __init__(self, info: dict | None = None, reason: str = ""):
        self.info = info or {}
        self.reason = reason
        super().__init__(f"another Jarvis holds {LOCK_FILENAME} "
                         f"(pid {self.info.get('pid', '?')})")

    @property
    def note(self) -> str:
        """The one owner-facing phrase, in the owner's language.

        Never contains an OS error code. "[Errno 13] Permission denied" is not
        something the owner can act on, and it is exactly what leaked out on
        the first acceptance run.
        """
        pid = self.info.get("pid")
        started = self.info.get("started_at")
        if pid and started:
            return f"\u043f\u0440\u043e\u0446\u0435\u0441\u0441 {pid}, \u0441\u0442\u0430\u0440\u0442 {started}"
        if pid:
            return f"\u043f\u0440\u043e\u0446\u0435\u0441\u0441 {pid}"
        return "\u043d\u043e\u043c\u0435\u0440 \u043f\u0440\u043e\u0446\u0435\u0441\u0441\u0430 \u0432\u044b\u044f\u0441\u043d\u0438\u0442\u044c \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c"


# -- Paths --------------------------------------------------------------------

def lock_path() -> Path:
    """Resolved at call time: tests redirect state with JARVIS_STATE_DIR."""
    return state_dir() / LOCK_FILENAME


def info_path() -> Path:
    """The human-readable note next to the lock. Never locked, never held."""
    return state_dir() / INFO_FILENAME


def last_read_error() -> str:
    """Why read_info() last returned {}. For tests and logs, not for the owner."""
    return _last_read_error


def read_info() -> dict:
    """What the holder wrote about itself. Never raises, never locks, never
    creates anything."""
    global _last_read_error
    _last_read_error = ""
    try:
        # buffering=0: ask the OS for exactly these bytes. A buffered reader
        # would request 8 KiB behind our back - the very readahead that made
        # the first version unreadable on Windows.
        with open(info_path(), "rb", buffering=0) as fh:
            raw = fh.read(_INFO_BYTES)
    except FileNotFoundError:
        return {}
    except OSError as e:
        _last_read_error = f"{type(e).__name__}: {e}"
        return {}
    try:
        data = json.loads(raw.decode("utf-8", "ignore").strip() or "{}")
    except ValueError as e:
        _last_read_error = f"broken json: {e}"
        return {}
    return data if isinstance(data, dict) else {}


def is_held() -> bool:
    """True when THIS process holds the lock."""
    return _held_fd is not None


# -- The OS lock --------------------------------------------------------------

def _lock_fd(fd: int) -> tuple[bool, str]:
    """Take the exclusive lock without waiting. (taken, reason-if-not).

    Fail-open when the platform primitive is missing: a single instance is a
    convenience, not a security boundary, and refusing to start on an exotic
    Python would be a far worse failure than allowing two.
    """
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:                                   # pragma: no cover
            print("[JARVIS] \U0001f513 instance lock unavailable (no msvcrt) - "
                  "starting without it")
            return True, ""
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True, ""
        except OSError as e:
            return False, str(e)
    try:
        import fcntl
    except ImportError:                                       # pragma: no cover
        print("[JARVIS] \U0001f513 instance lock unavailable (no fcntl) - "
              "starting without it")
        return True, ""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True, ""
    except OSError as e:
        return False, str(e)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        try:
            import msvcrt
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass
        return
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


def _write_info() -> None:
    """Leave a note for a human: who holds the lock, since when.

    Best effort by design. A failure here must never stop Jarvis from starting:
    the note is a courtesy, the lock is the mechanism. Written through a
    temporary file and os.replace so a reader sees either the old note or the
    new one, never half of one.
    """
    path = info_path()
    tmp = Path(str(path) + ".tmp")
    payload = json.dumps({"pid": os.getpid(),
                          "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# -- Public API ---------------------------------------------------------------

def acquire() -> Path:
    """Become the one Jarvis, or raise AlreadyRunning.

    Called exactly once, from main(), before the window is created and before
    anything opens the database or an audio stream.
    """
    global _held_fd
    if _held_fd is not None:
        raise RuntimeError("instance lock is already held by this process")

    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)

    taken, reason = _lock_fd(fd)
    if not taken:
        info = read_info()      # a separate file: readable while the lock is held
        os.close(fd)
        raise AlreadyRunning(info, reason)

    _write_info()
    _held_fd = fd
    return path


def release() -> None:
    """Give the lock back. Safe to call when nothing is held.

    Best effort by nature: the window's X button calls os._exit(0), so this
    often never runs. Correctness does not depend on it - the OS releases the
    lock when the process dies.
    """
    global _held_fd
    fd, _held_fd = _held_fd, None
    if fd is None:
        return
    _unlock_fd(fd)
    try:
        os.close(fd)
    except OSError:
        pass
