# core/fileops.py
"""
Stage 2.4a - fileops: the transactional file layer that turns raw writes/moves
into reversible sagas (journal + staging), with a scoped-root guard.

Pipeline for every mutating op (matches the Stage-2 contract):
    gate (Stage 1, upstream)  ->  preview  ->  journal.intent
                              ->  execute (atomic)  ->  journal.complete (push undo)

Guarantees:
  - ATOMIC overwrite: write to a temp file in the same dir, then os.replace()
    (the portable ReplaceFile/rename-swap) so a crash never leaves a
    half-written file.
  - REVERSIBLE: before overwriting, the old bytes are stashed (core.staging);
    a fresh file's inverse is "remove the file we created"; move/rename carry a
    reverse-move. undo_last() replays the compensation.
  - SCOPED: optional safe_roots guard rejects paths outside allowed folders.

Windows note: _atomic_replace uses os.replace, which maps to MoveFileEx and is
atomic on a single volume. IFileOperation (Recycle Bin + Explorer undo) is a
Windows-only enhancement wired in a later sub-step behind the same interface;
the portable path here is what the tests exercise offline.

Free - fast - offline: stdlib only (os, shutil, tempfile, pathlib).
Additive: not wired into file_controller/dispatch yet (that is Stage 2.4b).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path


class FileOpsError(RuntimeError):
    pass


class ScopeError(FileOpsError):
    """Raised when a path falls outside the configured safe roots."""


def _atomic_replace(tmp: Path, target: Path) -> None:
    """Atomically put `tmp` in place of `target` (same volume)."""
    os.replace(str(tmp), str(target))


class FileOps:
    def __init__(self, journal=None, staging=None, safe_roots=None,
                 recycle=None):
        self.journal = journal
        self.staging = staging
        # Recovery custodian for deletions. Injectable so tests can simulate a
        # Recycle Bin on any OS; defaults to the real one lazily.
        self._recycle_impl = recycle
        self._safe_roots = (
            [Path(r).expanduser().resolve() for r in safe_roots]
            if safe_roots else None
        )
        # Serialize journal (SQLite) access: the connection is shared across the
        # runtime's worker threads (each tool call may run on a different one).
        self._lock = threading.RLock()

    def _recycle(self):
        if self._recycle_impl is not None:
            return self._recycle_impl
        try:
            from core import recycle
            return recycle
        except Exception:
            return None

    # -- scope guard ----------------------------------------------------------

    def within_scope(self, path) -> bool:
        if self._safe_roots is None:
            return True  # no roots configured -> caller trusts the path
        try:
            p = Path(path).expanduser().resolve()
        except Exception:
            return False
        for root in self._safe_roots:
            try:
                p.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _require_scope(self, path) -> Path:
        if not self.within_scope(path):
            raise ScopeError(f"path outside safe roots: {path}")
        return Path(path).expanduser()

    # -- preview (check-before-act) ------------------------------------------

    def preview(self, op: str, path=None, dst=None, new_name=None) -> dict:
        info = {"op": op, "reversible": True, "in_scope": True}
        if path is not None:
            p = Path(path).expanduser()
            info["path"] = str(p)
            info["exists"] = p.exists()
            info["will_overwrite"] = p.exists() and p.is_file()
            info["in_scope"] = self.within_scope(p)
        if dst is not None:
            d = Path(dst).expanduser()
            info["dst"] = str(d)
            info["dst_exists"] = d.exists()
            info["in_scope"] = info["in_scope"] and self.within_scope(d)
        if new_name is not None:
            info["new_name"] = new_name
        return info

    # -- saga plumbing --------------------------------------------------------

    def _begin(self, action, intent, inverse, label):
        if not self.journal:
            return None
        with self._lock:
            return self.journal.begin_intent(
                "fileops", action, intent=intent, inverse=inverse, label=label
            )

    def _complete(self, saga_id):
        if self.journal and saga_id is not None:
            with self._lock:
                self.journal.complete(saga_id)

    def _fail(self, saga_id, detail):
        if self.journal and saga_id is not None:
            with self._lock:
                self.journal.mark_failed(saga_id, detail=detail)

    def _emit(self, _name, **payload):
        """Announce a FACT on the event bus (Stage 2.5).

        Imported lazily and failure-proof on purpose: telling the world that a
        file changed must NEVER be able to break the file operation itself.
        """
        try:
            from core.bus import publish_safe
            return publish_safe(_name, **payload)
        except Exception:
            return None

    # -- operations -----------------------------------------------------------

    def replace_file(self, path, content: str, *, encoding: str = "utf-8") -> dict:
        """Create or atomically overwrite a text file, reversibly."""
        target = self._require_scope(path)
        existed = target.exists() and target.is_file()

        # Stash old bytes BEFORE we touch anything (only if overwriting).
        inverse = None
        if existed and self.staging is not None:
            inverse = self.staging.stash(target)
        if not existed:
            inverse = {"kind": "remove-file", "original": str(target)}

        verb = "Overwrote" if existed else "Created"
        label = f"{verb} {target.name}"
        saga_id = self._begin(
            "replace_file", {"path": str(target), "existed": existed}, inverse, label
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=".jv_tmp_", suffix=target.suffix
            )
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
                _atomic_replace(tmp, target)
            finally:
                if tmp.exists():
                    tmp.unlink()  # never leave a temp behind
        except Exception as e:
            self._fail(saga_id, str(e))
            self._emit("file.op_failed", op="replace_file",
                       path=str(target), error=str(e))
            raise FileOpsError(f"replace_file failed: {e}") from e

        self._complete(saga_id)
        self._emit("file.overwritten" if existed else "file.created",
                   path=str(target), saga_id=saga_id)
        return {
            "ok": True, "op": "replace_file", "path": str(target),
            "created": not existed, "saga_id": saga_id,
            "undoable": inverse is not None,
            "message": f"{verb} {target.name} (full path: {target})",
        }

    def move(self, src, dst) -> dict:
        source = self._require_scope(src)
        dest = self._require_scope(dst)
        if not source.exists():
            raise FileOpsError(f"source does not exist: {source}")
        inverse = {"kind": "move", "from": str(dest), "to": str(source)}
        label = f"Moved {source.name}"
        saga_id = self._begin("move", {"from": str(source), "to": str(dest)},
                              inverse, label)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest))
        except Exception as e:
            self._fail(saga_id, str(e))
            self._emit("file.op_failed", op="move", path=str(source), error=str(e))
            raise FileOpsError(f"move failed: {e}") from e
        self._complete(saga_id)
        self._emit("file.moved", src=str(source), dst=str(dest), saga_id=saga_id)
        return {"ok": True, "op": "move", "path": str(dest),
                "saga_id": saga_id, "undoable": True,
                "message": f"Moved to {dest}"}

    def rename(self, path, new_name: str) -> dict:
        source = self._require_scope(path)
        if not source.exists():
            raise FileOpsError(f"source does not exist: {source}")
        dest = source.with_name(new_name)
        if not self.within_scope(dest):
            raise ScopeError(f"rename target outside safe roots: {dest}")
        inverse = {"kind": "move", "from": str(dest), "to": str(source)}
        label = f"Renamed {source.name} -> {new_name}"
        saga_id = self._begin("rename", {"from": str(source), "to": str(dest)},
                              inverse, label)
        try:
            shutil.move(str(source), str(dest))
        except Exception as e:
            self._fail(saga_id, str(e))
            self._emit("file.op_failed", op="rename", path=str(source), error=str(e))
            raise FileOpsError(f"rename failed: {e}") from e
        self._complete(saga_id)
        self._emit("file.renamed", src=str(source), dst=str(dest),
                   new_name=new_name, saga_id=saga_id)
        return {"ok": True, "op": "rename", "path": str(dest),
                "saga_id": saga_id, "undoable": True,
                "message": f"Renamed to {new_name}"}

    def copy(self, src, dst) -> dict:
        """Copy a file, reversibly: the inverse simply removes the new copy."""
        source = self._require_scope(src)
        dest = self._require_scope(dst)
        if not source.exists():
            raise FileOpsError(f"source does not exist: {source}")
        if dest.exists():
            raise FileOpsError(f"destination already exists: {dest}")
        inverse = {"kind": "remove-file", "original": str(dest)}
        label = f"Copied {source.name}"
        saga_id = self._begin("copy", {"from": str(source), "to": str(dest)},
                              inverse, label)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(dest))
        except Exception as e:
            self._fail(saga_id, str(e))
            self._emit("file.op_failed", op="copy", path=str(source), error=str(e))
            raise FileOpsError(f"copy failed: {e}") from e
        self._complete(saga_id)
        self._emit("file.copied", src=str(source), dst=str(dest), saga_id=saga_id)
        return {"ok": True, "op": "copy", "path": str(dest),
                "saga_id": saga_id, "undoable": True,
                "message": f"Copied to {dest}"}

    def create_folder(self, path) -> dict:
        """Create a folder, reversibly (undo removes it while still empty)."""
        target = self._require_scope(path)
        if target.exists():
            raise FileOpsError(f"folder already exists: {target}")
        inverse = {"kind": "remove-dir", "original": str(target)}
        label = f"Created folder {target.name}"
        saga_id = self._begin("create_folder", {"path": str(target)},
                              inverse, label)
        try:
            target.mkdir(parents=True, exist_ok=False)
        except Exception as e:
            self._fail(saga_id, str(e))
            self._emit("file.op_failed", op="create_folder",
                       path=str(target), error=str(e))
            raise FileOpsError(f"create_folder failed: {e}") from e
        self._complete(saga_id)
        self._emit("folder.created", path=str(target), saga_id=saga_id)
        return {"ok": True, "op": "create_folder", "path": str(target),
                "saga_id": saga_id, "undoable": True,
                "message": f"Created folder: {target.name} (full path: {target})"}

    def delete(self, path) -> dict:
        """Delete a file ONLY after stashing its bytes, so undo restores it.

        This is what makes deletion safe enough to consider at all: it is no
        longer a one-way door. If we cannot stash the bytes we refuse, rather
        than perform an irreversible delete. The file then goes to the OS
        Recycle Bin when possible, giving a SECOND recovery route that works
        even if Jarvis itself is broken.
        """
        target = self._require_scope(path)
        if not target.exists() or not target.is_file():
            raise FileOpsError(f"file does not exist: {target}")

        # ONE custodian owns the deleted bytes - never two. Two owners of the
        # same recovery state always drift apart (undo restores from one and
        # leaves a ghost in the other), which is exactly the bug this fixes.
        rec = self._recycle()
        inverse = None
        method = None
        if rec is not None:
            try:
                if rec.available():
                    # Zero-copy: on the same volume this is a metadata move, so
                    # it costs the same for a 10 KB note and a 40 GB video.
                    inverse = rec.send(target)
                    if inverse:
                        method = "recycle-bin"
            except Exception as e:
                print(f"[fileops] Recycle Bin unavailable: {e}")

        if inverse is None:
            # Fallback: WE keep the bytes. This copies, so it is bounded by the
            # staging quota and may honestly refuse very large files.
            if self.staging is None:
                raise FileOpsError("refusing to delete without staging (no undo)")
            if not self.staging.can_stash(target):
                raise FileOpsError(
                    "refusing to delete: file is too large to back up and the "
                    "Recycle Bin is unavailable")
            inverse = self.staging.stash(target)
            if not inverse:
                raise FileOpsError("refusing to delete: could not stash a copy")
            inverse = dict(inverse)
            # Mark provenance so redo knows the forward action was a DELETE.
            inverse["from_delete"] = True
            method = "staged"

        label = f"Deleted {target.name}"
        saga_id = self._begin("delete", {"path": str(target)}, inverse, label)
        try:
            if method == "staged":
                target.unlink()
            elif target.exists():
                raise FileOpsError("Recycle Bin reported success but the file "
                                   "is still there")
        except Exception as e:
            self._fail(saga_id, str(e))
            self._emit("file.op_failed", op="delete", path=str(target), error=str(e))
            raise FileOpsError(f"delete failed: {e}") from e
        self._complete(saga_id)
        self._emit("file.deleted", path=str(target), saga_id=saga_id,
                   recoverable=True, method=method)
        where = (" — файл в Корзине" if method == "recycle-bin" else "")
        return {"ok": True, "op": "delete", "path": str(target),
                "saga_id": saga_id, "undoable": True, "method": method,
                "message": f"Deleted: {target.name} (full path: {target}){where}"}

    # -- undo -----------------------------------------------------------------

    def _raw_move(self, frm, to) -> bool:
        try:
            to_p = Path(to)
            to_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(frm), str(to_p))
            return True
        except Exception:
            return False

    def apply_inverse(self, record) -> bool:
        """Replay a compensation record. Idempotent-friendly, never raises."""
        if not record:
            return False
        kind = record.get("kind")
        if kind == "file-restore":
            if self.staging is not None:
                return self.staging.restore(record)
            # staging not attached -> restore straight from the staged copy
            staged = Path(record.get("staged", ""))
            if not staged.exists():
                return False
            try:
                Path(record["original"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(staged), record["original"])
                return True
            except Exception:
                return False
        if kind == "remove-file":
            try:
                p = Path(record["original"])
                if p.exists():
                    p.unlink()
                return True
            except Exception:
                return False
        if kind == "trash-restore":
            rec = self._recycle()
            if rec is None:
                return False
            try:
                return bool(rec.restore(record))
            except Exception:
                return False
        if kind == "trash-delete":
            # redo of a deletion: send it to the Recycle Bin again
            rec = self._recycle()
            if rec is None:
                return False
            try:
                return bool(rec.send(record.get("original")))
            except Exception:
                return False
        if kind == "move":
            return self._raw_move(record["from"], record["to"])
        if kind == "remove-dir":
            try:
                p = Path(record["original"])
                if p.exists():
                    p.rmdir()  # only removes an EMPTY dir - never destroys data
                return True
            except Exception:
                return False
        if kind == "make-dir":
            try:
                Path(record["original"]).mkdir(parents=True, exist_ok=True)
                return True
            except Exception:
                return False
        return False

    def _build_redo(self, inverse) -> dict | None:
        """Given the compensation we are about to apply, capture how to RE-APPLY
        the original forward action (redo). MUST run BEFORE apply_inverse, while
        the forward result is still on disk.
        """
        kind = (inverse or {}).get("kind")
        if kind == "trash-restore":
            # undo brought it back from the bin; redo puts it back in the bin
            return {"kind": "trash-delete", "original": inverse.get("original")}
        if kind == "trash-delete":
            return {"kind": "trash-restore", "original": inverse.get("original")}
        if kind == "file-restore" and (inverse or {}).get("from_delete"):
            # Forward action was a DELETE; undo put the file back, so redo
            # deletes it again (stashing first would be pointless - we already
            # hold the bytes in this very record).
            return {"kind": "remove-file", "original": inverse.get("original")}
        if kind == "remove-dir":
            return {"kind": "make-dir", "original": inverse.get("original")}
        if kind == "make-dir":
            return {"kind": "remove-dir", "original": inverse.get("original")}
        if kind in ("file-restore", "remove-file"):
            # Current bytes on disk ARE the forward result (new content, or the
            # just-created file). Stash them so redo can re-overwrite / recreate.
            original = inverse.get("original")
            if not original or self.staging is None:
                return None
            return self.staging.stash(original)  # None if the file is missing
        if kind == "move":
            # Forward moved inverse['to'] -> inverse['from']; redo re-does that.
            return {"kind": "move", "from": inverse.get("to"),
                    "to": inverse.get("from")}
        return None

    def _build_undo_for_redo(self, redo) -> dict | None:
        """Given a redo we are about to apply, capture how to UNDO it again, so
        redo is itself undoable (symmetric ping-pong). Runs BEFORE applying redo.
        """
        kind = (redo or {}).get("kind")
        if kind == "trash-delete":
            # Located by ORIGINAL PATH, so the token stays valid for the new
            # Recycle Bin entry that the redo is about to create.
            return {"kind": "trash-restore", "original": redo.get("original")}
        if kind == "trash-restore":
            return {"kind": "trash-delete", "original": redo.get("original")}
        if kind == "remove-dir":
            return {"kind": "make-dir", "original": redo.get("original")}
        if kind == "make-dir":
            return {"kind": "remove-dir", "original": redo.get("original")}
        if kind == "remove-file":
            # redo deletes the file again -> undo must restore it, so stash the
            # current bytes BEFORE the deletion happens.
            original = redo.get("original")
            if not original or self.staging is None:
                return None
            rec = self.staging.stash(original)
            if rec:
                rec = dict(rec)
                rec["from_delete"] = True
            return rec
        if kind == "file-restore":
            original = redo.get("original")
            if not original:
                return None
            p = Path(original).expanduser()
            if p.exists() and p.is_file():
                # redo overwrites existing content -> undo restores current bytes
                if self.staging is None:
                    return None
                return self.staging.stash(p)
            # redo (re)creates a missing file -> undo removes it
            return {"kind": "remove-file", "original": str(p)}
        if kind == "move":
            return {"kind": "move", "from": redo.get("to"), "to": redo.get("from")}
        return None

    # -- content-aware helpers (grounding + timeline) ------------------------
    _PREVIEW_MAX = 160

    def _clip(self, text):
        text = (text or "").replace("\r\n", "\n").strip()
        if len(text) > self._PREVIEW_MAX:
            text = text[: self._PREVIEW_MAX].rstrip() + "\u2026"
        return text

    def _read_preview(self, path):
        """Short UTF-8 text preview of a file's CURRENT bytes, or None."""
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                return None
            data = p.read_bytes()[: self._PREVIEW_MAX * 4]
            return self._clip(data.decode("utf-8", "replace"))
        except Exception:
            return None

    def _record_path(self, record):
        """The primary path a compensation/redo record acts on."""
        if not record:
            return None
        kind = record.get("kind")
        if kind in ("file-restore", "remove-file",
                    "trash-restore", "trash-delete"):
            return record.get("original")
        if kind == "move":
            return record.get("to")
        return None

    def _record_preview(self, record):
        """(preview, note): what content this record PRODUCES when applied.
        preview is text (for file-restore, read from the staged copy); note
        describes non-content outcomes (deletion / move / evicted copy).
        """
        if not record:
            return None, None
        kind = record.get("kind")
        if kind == "file-restore":
            staged = record.get("staged")
            try:
                if staged and Path(staged).exists():
                    data = Path(staged).read_bytes()[: self._PREVIEW_MAX * 4]
                    return self._clip(data.decode("utf-8", "replace")), None
            except Exception:
                pass
            return None, "\u0441\u043e\u0434\u0435\u0440\u0436\u0438\u043c\u043e\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e"
        if kind == "remove-file":
            return None, "\u0444\u0430\u0439\u043b \u0431\u0443\u0434\u0435\u0442 \u0443\u0434\u0430\u043b\u0451\u043d"
        if kind == "trash-restore":
            return None, "\u0444\u0430\u0439\u043b \u0432\u0435\u0440\u043d\u0451\u0442\u0441\u044f \u0438\u0437 \u041a\u043e\u0440\u0437\u0438\u043d\u044b"
        if kind == "trash-delete":
            return None, "\u0444\u0430\u0439\u043b \u0443\u0439\u0434\u0451\u0442 \u0432 \u041a\u043e\u0440\u0437\u0438\u043d\u0443"
        if kind == "move":
            return None, "\u043f\u0435\u0440\u0435\u043c\u0435\u0449\u0435\u043d\u0438\u0435/\u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435"
        return None, None

    def _norm(self, p):
        """Best-effort canonical string for a path (for scope comparisons)."""
        try:
            return str(Path(p).expanduser().resolve())
        except Exception:
            return str(p)

    def _record_paths(self, record):
        """ALL file paths a record touches. For move/rename this is BOTH ends,
        so scoping matches whether the user names the old or the new path."""
        if not record:
            return []
        kind = record.get("kind")
        if kind in ("file-restore", "remove-file",
                    "trash-restore", "trash-delete"):
            return [record.get("original")]
        if kind == "move":
            return [record.get("from"), record.get("to")]
        return []

    def _match_path(self, record, target_norm):
        """True if a compensation/redo record acts on the target file (either end
        of a move/rename counts)."""
        paths = [rp for rp in self._record_paths(record) if rp]
        if not paths:
            return False
        try:
            tgt_name = Path(target_norm).name
        except Exception:
            tgt_name = None
        for rp in paths:
            if self._norm(rp) == target_norm:
                return True
            try:
                if tgt_name and Path(rp).name == tgt_name:
                    return True
            except Exception:
                pass
        return False

    def _count_scoped(self, entries, key, target_norm):
        if target_norm is None:
            return len(entries)
        return sum(1 for e in entries if self._match_path(e.get(key), target_norm))

    def _post_op_state(self, path, deleted=False, scope=None):
        """Ground truth after an undo/redo: current file + steps remaining.
        Step counts are scoped to `scope` (the target file) when given, so the
        user hears how many steps remain FOR THIS FILE, not globally.
        """
        undo_avail = redo_avail = 0
        if self.journal:
            try:
                sid = getattr(self.journal, "session_id", None) if scope is None else None
                uentries = self.journal.open_undo_entries(200, session_id=sid)
                rentries = self.journal.open_redo_entries(200, session_id=sid)
                undo_avail = self._count_scoped(uentries, "inverse", scope)
                redo_avail = self._count_scoped(rentries, "redo", scope)
            except Exception:
                pass
        if deleted:
            cur = {"exists": False, "path": path, "preview": None}
        elif path:
            cur = {"exists": Path(path).exists(), "path": path,
                   "preview": self._read_preview(path)}
        else:
            cur = None
        return cur, undo_avail, redo_avail

    def undo_last(self, path=None) -> dict | None:
        """Undo the most recent action, optionally SCOPED to a single file.

        When `path` is given we pick the most recent open undo entry that acts
        on THAT file, so 'отмени изменение test.txt' never reverts an unrelated
        file (or a leftover entry from another session). Before mutating disk we
        capture a REDO record and push it, so redo can still walk forward.
        """
        if not self.journal:
            return None
        target = self._norm(path) if path else None
        # Named file -> search ALL sessions (cross-restart memory). Unscoped
        # 'undo last' -> only THIS session, so we never touch a prior run.
        sid = getattr(self.journal, "session_id", None) if target is None else None
        with self._lock:
            entries = self.journal.open_undo_entries(200, session_id=sid)
            chosen = None
            for e in entries:  # id DESC -> most recent first
                if target is None or self._match_path(e.get("inverse"), target):
                    chosen = e
                    break
            if chosen is None:
                return None
            popped = self.journal.mark_undone(chosen["id"])
        if not popped:
            return None
        inverse = popped["inverse"] or {}
        redo = self._build_redo(inverse)
        ok = self.apply_inverse(inverse)
        if ok and redo is not None:
            with self._lock:
                self.journal.push_redo(popped["saga_id"], popped["label"], redo)
        path_out = self._record_path(inverse)
        deleted = ok and inverse.get("kind") == "remove-file"
        current, undo_avail, redo_avail = self._post_op_state(
            path_out, deleted=deleted, scope=target)
        self._emit("undo.performed", ok=bool(ok), label=popped["label"],
                   path=path_out, kind=inverse.get("kind"),
                   saga_id=popped["saga_id"], deleted=bool(deleted))
        return {"ok": ok, "label": popped["label"], "saga_id": popped["saga_id"],
                "kind": inverse.get("kind"), "current": current,
                "undo_available": undo_avail, "redo_available": redo_avail}

    def redo_last(self, path=None) -> dict | None:
        """Re-apply the most recently undone action, optionally SCOPED to a file.
        Symmetric with undo_last: the re-applied action is pushed back on undo.
        """
        if not self.journal:
            return None
        target = self._norm(path) if path else None
        sid = getattr(self.journal, "session_id", None) if target is None else None
        with self._lock:
            entries = self.journal.open_redo_entries(200, session_id=sid)
            chosen = None
            for e in entries:  # id DESC -> most recent first
                if target is None or self._match_path(e.get("redo"), target):
                    chosen = e
                    break
            if chosen is None:
                return None
            popped = self.journal.mark_redone(chosen["id"])
        if not popped:
            return None
        redo = popped["redo"] or {}
        undo = self._build_undo_for_redo(redo)
        ok = self.apply_inverse(redo)
        if ok and undo is not None:
            with self._lock:
                self.journal.push_undo(popped["saga_id"], popped["label"], undo)
        path_out = self._record_path(redo)
        deleted = ok and redo.get("kind") == "remove-file"
        current, undo_avail, redo_avail = self._post_op_state(
            path_out, deleted=deleted, scope=target)
        self._emit("redo.performed", ok=bool(ok), label=popped["label"],
                   path=path_out, kind=redo.get("kind"),
                   saga_id=popped["saga_id"])
        return {"ok": ok, "label": popped["label"], "saga_id": popped["saga_id"],
                "kind": redo.get("kind"), "current": current,
                "undo_available": undo_avail, "redo_available": redo_avail}

    def history(self, limit: int = 12, path=None) -> dict:
        """Content-aware version timeline: what each undo/redo step would make
        the file contain, plus the file's real current content. Optionally
        SCOPED to one file so the timeline shows only that file's versions
        (the user asked about ONE file, not every file ever touched).
        """
        if not self.journal:
            return {"back": [], "forward": [], "current": None}
        target = self._norm(path) if path else None
        sid = getattr(self.journal, "session_id", None) if target is None else None
        back = []
        for e in self.journal.open_undo_entries(200, session_id=sid):
            if target and not self._match_path(e.get("inverse"), target):
                continue
            prev, note = self._record_preview(e.get("inverse"))
            back.append({"label": e.get("label"), "preview": prev, "note": note})
            if len(back) >= limit:
                break
        forward = []
        for e in self.journal.open_redo_entries(200, session_id=sid):
            if target and not self._match_path(e.get("redo"), target):
                continue
            prev, note = self._record_preview(e.get("redo"))
            forward.append({"label": e.get("label"), "preview": prev, "note": note})
            if len(forward) >= limit:
                break
        cur_path = None
        if target:
            cur_path = path
        else:
            top_u = self.journal.peek_undo()
            top_r = self.journal.peek_redo()
            if top_u:
                cur_path = self._record_path(top_u.get("inverse"))
            elif top_r:
                cur_path = self._record_path(top_r.get("redo"))
        current = None
        if cur_path:
            current = {"path": cur_path, "exists": Path(cur_path).exists(),
                       "preview": self._read_preview(cur_path)}
        return {"back": back, "forward": forward, "current": current}

    def new_session(self) -> None:
        """Start a NEW session for a fresh process run.

        Entries from previous runs are KEPT on disk so a named file can still be
        navigated across restarts (movie-Jarvis memory). Only unscoped 'undo
        last' is limited to the new session id, so it can never wander into a
        previous session's edits (the report.txt cross-session bug).

        Фаза 1, блок 3: номер сессии ПРОИЗВОДИТСЯ от номера запуска (план Р2):
        '20260818T014530Z-a3f1#1' вместо прежних 32 случайных знаков, которые
        в логах не говорили ничего. Но он остаётся СВОИМ номером, а не тем же
        самым: сессия может начаться дважды за процесс, и тогда «отмени
        последнее» обязано остаться внутри новой сессии (случай с report.txt).
        Подробнее — в шапке core/task_context.new_session_id.
        """
        if self.journal:
            try:
                from core.task_context import new_session_id as _new_sid
                _sid = _new_sid()
                self.journal.start_session(_sid)
                self._emit("session.started", session_id=_sid)
            except Exception:
                pass
