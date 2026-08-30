# core/recycle.py
"""
Stage 2.7 - the Windows Recycle Bin as a FIRST-CLASS undo custodian.

Why this module exists (the bug it fixes):
  Stage 2.6 deleted a file by making TWO independent recovery copies - a staged
  byte-copy owned by Jarvis, AND the OS Recycle Bin copy. Undo consumed only the
  staged one, so the file came back while a ghost stayed in the Recycle Bin.
  Two owners of the same recovery state always drift. This module lets the
  Recycle Bin be the SINGLE owner, so there is nothing left to drift.

Why it also fixes a much bigger problem (large files):
  Staging recovers a file by COPYING its bytes: deleting a 4 GB video would copy
  4 GB, take minutes and blow the 500 MB staging quota. Moving a file to the
  Recycle Bin on the same volume is a metadata operation - instant and free, no
  matter how large the file is. So the Recycle Bin is not just tidier, it is the
  only route that scales.

Design notes:
  - Recovery tokens are located by ORIGINAL PATH, not by a fragile internal id.
    That keeps undo working across restarts and after a redo re-deletes a file.
  - COM is initialised per call (Jarvis runs tools on worker threads), mirroring
    core/awareness/_explorer.py.
  - Everything degrades softly: on non-Windows, or without pywin32, availability
    is False and callers fall back to staged copies.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Shell.NameSpace id for the Recycle Bin.
_SSF_BITBUCKET = 10
# "Original location" column in the Recycle Bin view.
_COL_ORIGINAL_LOCATION = 1

# Canonical verb first; localized names are a fallback for non-English Windows.
_RESTORE_VERBS = (
    "UNDELETE",
    "&Restore", "Restore",
    "&\u0412\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c",
    "\u0412\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(p) -> str:
    try:
        return str(Path(p).expanduser().resolve()).lower()
    except Exception:
        return str(p).lower()


def available() -> bool:
    """True only if we can BOTH trash and restore. Trashing without a restore
    route would recreate the 'one-way door' we are trying to remove."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import send2trash  # noqa: F401
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


def _bin_items():
    """Yield (item, namespace) for everything currently in the Recycle Bin."""
    import pythoncom
    import win32com.client
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    shell = win32com.client.Dispatch("Shell.Application")
    ns = shell.NameSpace(_SSF_BITBUCKET)
    if ns is None:
        return
    items = ns.Items()
    for i in range(items.Count):
        yield items.Item(i), ns


def _find(original) -> object | None:
    """Most recent Recycle Bin entry whose ORIGINAL path matches, or None."""
    want = _norm(original)
    want_name = Path(original).name.lower()
    best = None
    best_when = None
    for item, ns in _bin_items():
        try:
            if str(item.Name).lower() != want_name:
                continue
            folder = ns.GetDetailsOf(item, _COL_ORIGINAL_LOCATION)
            if not folder:
                continue
            if _norm(Path(folder) / item.Name) != want:
                continue
            when = getattr(item, "ModifyDate", None)
            if best is None or (when is not None and best_when is not None
                                and when > best_when):
                best, best_when = item, when
        except Exception:
            continue
    return best


def send(path) -> dict | None:
    """Move a file to the Recycle Bin. Returns an undo token, or None.

    The token is deliberately just the original path + a timestamp: it stays
    valid across restarts and is re-resolvable after a redo deletes the file
    again, because we look the entry up by where it USED to live.
    """
    if not available():
        return None
    target = Path(path).expanduser()
    try:
        from send2trash import send2trash
        send2trash(str(target))
    except Exception as e:
        print(f"[recycle] could not send to Recycle Bin: {e}")
        return None
    if target.exists():
        return None  # nothing actually happened - do not claim a recovery route
    return {"kind": "trash-restore", "original": str(target),
            "deleted_at": _now()}


def restore(token) -> bool:
    """Restore a file from the Recycle Bin back to its original location.

    Restoring REMOVES the entry from the Recycle Bin, which is exactly why the
    Recycle Bin can be the single owner: there is no leftover ghost copy.
    """
    if not token or token.get("kind") != "trash-restore":
        return False
    original = token.get("original")
    if not original:
        return False
    if Path(original).exists():
        return True  # already back (idempotent undo)
    if not available():
        return False
    try:
        item = _find(original)
        if item is None:
            return False  # user emptied the bin - do not lie about undo
        for verb in _RESTORE_VERBS:
            try:
                item.InvokeVerb(verb)
            except Exception:
                continue
            if Path(original).exists():
                return True
        return Path(original).exists()
    except Exception as e:
        print(f"[recycle] restore failed: {e}")
        return False


def holds(token) -> bool:
    """True if the Recycle Bin still holds this entry (undo is honest)."""
    if not token or token.get("kind") != "trash-restore":
        return False
    if not available():
        return False
    try:
        return _find(token.get("original")) is not None
    except Exception:
        return False
