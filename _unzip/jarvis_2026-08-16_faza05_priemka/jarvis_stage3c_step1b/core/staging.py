# core/staging.py
"""
Stage 2.3 - staging: N-level backup area with a size quota, feeding the saga
undo stack in core.journal.

Why it exists:
  Stage 1 shipped a ONE-level, per-path backup in actions/file_controller.py
  (~/.jarvis/backups, sha1 slot, overwritten each time). Stage 2 upgrades this
  to a MULTI-level staging area so "undo" can walk back several steps, bounded
  by an honest disk quota (Stage 2 decision: ~500 MB).

How it plugs in:
  - stash(path)   -> copies the CURRENT bytes of a file to a fresh staged copy
                     and returns a `record` dict. That record is exactly the
                     saga `inverse` (compensation) journal stores; undo_last
                     hands it back and we call restore(record).
  - restore(rec)  -> copies the staged bytes back over the original path.
  - quota         -> after each stash, evict OLDEST staged copies until the
                     area is under quota. Undo depth is therefore honest: if a
                     copy was evicted, restore() returns False rather than lying.
  - legacy bridge -> read the Stage-1 ~/.jarvis/backups slots during migration
                     (2.4 wires this in); we WRITE only into the new staging area.

Free - fast - offline: stdlib only (shutil, hashlib, pathlib, time).
Additive: not wired into file_controller/dispatch yet (that is Stage 2.4).
"""
from __future__ import annotations

import hashlib
import itertools
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


APP_DIR_NAME = ".jarvis"
STAGING_DIRNAME = "staging"
LEGACY_BACKUP_DIRNAME = "backups"
# Stage 2 decision: N-level undo with a ~500 MB staging quota.
DEFAULT_QUOTA_BYTES = 500 * 1024 * 1024

_seq = itertools.count()  # monotonic tiebreaker within a process


def _app_dir() -> Path:
    """Дом. Тот же, что у базы, состояния, памяти и журнала двери.

    Шаг 35.1. До этого здесь стоял жёсткий Path.home(): хранилище
    отмен было единственной дверью в дом БЕЗ замка, и прогон тестов
    складывал копии в настоящий ~/.jarvis/staging владельца (доказано
    временем правки папки 15.08 18:28 при выставленном JARVIS_STATE_DIR).

    Путь считается ПРИ ВЫЗОВЕ, а не при импорте, иначе переменная,
    выставленная тестом после импорта, не действует (грабли шага 31).
    Ввоз локальный — чтобы не заводить кольцо импортов.
    """
    from core.safe_json import state_dir
    return state_dir()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(target: Path) -> str:
    return hashlib.sha1(
        str(target).encode("utf-8", "ignore")
    ).hexdigest()[:12]


# -- Legacy Stage-1 backup bridge (read-only during migration) ----------------
# Mirrors actions/file_controller._backup_key EXACTLY so the new code can still
# honour backups written by Stage 1. We never WRITE here anymore.

def legacy_backup_path(target) -> Path:
    t = Path(target).expanduser()
    try:
        resolved = t.resolve()
    except Exception:
        resolved = t
    h = hashlib.sha1(str(resolved).encode("utf-8", "ignore")).hexdigest()[:16]
    return _app_dir() / LEGACY_BACKUP_DIRNAME / f"{h}{t.suffix}"


def has_legacy_backup(target) -> bool:
    return legacy_backup_path(target).exists()


def restore_legacy(target) -> bool:
    """Restore a file from its Stage-1 backup slot, if present."""
    src = legacy_backup_path(target)
    if not src.exists():
        return False
    dst = Path(target).expanduser()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return True
    except Exception:
        return False


class Staging:
    """N-level staged-copy area with a size quota (oldest-first eviction)."""

    def __init__(self, root=None, quota_bytes: int = DEFAULT_QUOTA_BYTES):
        self.root = Path(root) if root else _app_dir() / STAGING_DIRNAME
        self.quota_bytes = int(quota_bytes)

    # -- core operations ------------------------------------------------------

    def stash(self, path):
        """Copy the current bytes of `path` into a fresh staged file.

        Returns a `record` dict (the saga inverse), or None if there is nothing
        to stash (missing path or not a regular file). Each call makes a NEW
        staged copy - previous versions are NOT overwritten (that is what makes
        undo N-level).
        """
        target = Path(path).expanduser()
        if not target.exists() or not target.is_file():
            return None
        if not self.can_stash(target):
            # A file bigger than the whole quota would evict EVERY other staged
            # copy to make room for itself - destroying other undo history for
            # one giant file. Refuse honestly instead; callers then either use a
            # zero-copy route (Recycle Bin) or report the op as not undoable.
            print(f"[staging] too large to stage ({self._size(target)} bytes > "
                  f"quota {self.quota_bytes}): {target}")
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        # Name embeds wall-clock ns (19 fixed digits -> lexical == chronological)
        # + a monotonic seq, so oldest-first eviction is a plain name sort.
        staged_name = f"{time.time_ns():019d}-{next(_seq):06d}-{_key(target)}{target.suffix}"
        staged = self.root / staged_name
        shutil.copy2(str(target), str(staged))
        record = {
            "kind": "file-restore",
            "original": str(target),
            "staged": str(staged),
            "size": staged.stat().st_size,
            "created_at": _now(),
        }
        self._enforce_quota()
        return record

    def _size(self, path) -> int:
        try:
            return Path(path).stat().st_size
        except Exception:
            return 0

    def can_stash(self, path) -> bool:
        """False if copying this file would blow the entire quota by itself."""
        return self._size(path) <= self.quota_bytes

    def restore(self, record) -> bool:
        """Copy a staged file back over its original path. False if unavailable."""
        if not record or record.get("kind") != "file-restore":
            return False
        staged = Path(record["staged"])
        original = Path(record["original"])
        if not staged.exists():
            return False  # evicted by quota - do not lie about undo
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(staged), str(original))
            return True
        except Exception:
            return False

    def available(self, record) -> bool:
        """True if this record's staged copy still exists (not evicted)."""
        return bool(record) and Path(record.get("staged", "")).exists()

    # -- quota bookkeeping ----------------------------------------------------

    def _staged_files(self):
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_file())

    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self._staged_files())

    def _enforce_quota(self) -> None:
        """Evict oldest staged copies until under quota. Always keep the newest."""
        files = self._staged_files()  # oldest first (name sort)
        total = sum(p.stat().st_size for p in files)
        i = 0
        while total > self.quota_bytes and len(files) - i > 1:
            victim = files[i]
            try:
                sz = victim.stat().st_size
                victim.unlink()
                total -= sz
            except Exception:
                pass
            i += 1
