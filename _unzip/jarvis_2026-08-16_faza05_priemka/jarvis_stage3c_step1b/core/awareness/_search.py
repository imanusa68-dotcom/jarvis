# core/awareness/_search.py
# Fast file search with graceful layering:
#   1. Everything (es.exe) when available — instant, whole disk.
#   2. Bounded fallback otherwise — a TIME- and COUNT-capped scan of the user's
#      personal folders only. Never walks the whole C:, so "find a file" can
#      never hang for minutes again (the bug that motivated this).
#
# Everything is a SOFT dependency: if es.exe isn't installed the search still
# works (bounded), just limited to known folders. Results: {path, mtime, size}.

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

_FALLBACK_TIMEOUT_S = 4.0
# Directories never worth descending into for a user file search.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", "$recycle.bin", "appdata",
              ".vs", ".idea", "venv", ".venv", "site-packages"}


def _es_available() -> bool:
    """True if Everything's CLI (es.exe) is on PATH."""
    return shutil.which("es") is not None or shutil.which("es.exe") is not None


def _run_es(query: str, limit: int) -> str:
    """Run es.exe and return its stdout (one full path per line). Seam for tests."""
    exe = shutil.which("es") or shutil.which("es.exe") or "es.exe"
    proc = subprocess.run(
        [exe, "-n", str(limit), query],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    return proc.stdout or ""


def _build_es_query(name: str, ext: str) -> str:
    parts = []
    if ext:
        parts.append("ext:" + ext.strip().lstrip("."))
    if name:
        parts.append(name.strip())
    return " ".join(parts) if parts else "*"


def _stat_result(p: Path) -> dict | None:
    try:
        st = p.stat()
        return {"path": str(p), "mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        return None


def _everything_search(name: str, ext: str, limit: int) -> list[dict]:
    out = _run_es(_build_es_query(name, ext), limit)
    results = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        r = _stat_result(Path(line))
        results.append(r or {"path": line, "mtime": 0, "size": 0})
        if len(results) >= limit:
            break
    return results


def _matches(entry_name: str, name: str, ext: str) -> bool:
    n = entry_name.lower()
    if ext and not n.endswith("." + ext.strip().lstrip(".").lower()):
        return False
    if name and name.strip().lower() not in n:
        return False
    return True


def _bounded_fallback(name: str, ext: str, roots, limit: int, deadline: float) -> list[dict]:
    """
    Depth-first scan of `roots`, capped by `limit` and a hard time `deadline`.
    Skips known noise/system dirs and never leaves the given roots — so it can
    never turn into a whole-disk walk.
    """
    results: list[dict] = []
    stack = [Path(r) for r in roots]
    while stack:
        if time.monotonic() > deadline:
            break
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if time.monotonic() > deadline:
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in _SKIP_DIRS:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and _matches(entry.name, name, ext):
                    r = _stat_result(Path(entry.path))
                    if r:
                        results.append(r)
                        if len(results) >= limit:
                            return results
            except OSError:
                continue
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results


def search(name: str = "", ext: str = "", scope=None,
           limit: int = 50, timeout_s: float = _FALLBACK_TIMEOUT_S) -> list[dict]:
    """
    Find files by name substring and/or extension. Uses Everything if available,
    otherwise a bounded scan of the user's personal folders. Returns a list of
    {path, mtime, size}, newest-ish first, never more than `limit`.
    """
    if _es_available():
        return _everything_search(name, ext, limit)
    if scope:
        roots = [Path(scope)] if isinstance(scope, (str, Path)) else list(scope)
    else:
        from core.awareness import _known_folders
        roots = _known_folders.watch_roots()
    deadline = time.monotonic() + max(0.0, timeout_s)
    return _bounded_fallback(name, ext, roots, limit, deadline)


def _roots_from(scope):
    if scope:
        return [Path(scope)] if isinstance(scope, (str, Path)) else list(scope)
    from core.awareness import _known_folders
    return _known_folders.watch_roots()


def _bounded_all_files(roots, deadline: float, cap: int = 5000) -> list[dict]:
    """Collect up to `cap` files under `roots` within the time `deadline`."""
    results: list[dict] = []
    stack = [Path(r) for r in roots]
    while stack:
        if time.monotonic() > deadline:
            break
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if time.monotonic() > deadline:
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in _SKIP_DIRS:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    r = _stat_result(Path(entry.path))
                    if r:
                        results.append(r)
                        if len(results) >= cap:
                            return results
            except OSError:
                continue
    return results


def largest(scope=None, count: int = 10, timeout_s: float = _FALLBACK_TIMEOUT_S) -> list[dict]:
    """Largest files (by size). Everything's size sort when available, else a
    bounded scan of the roots (never the whole disk)."""
    if _es_available():
        try:
            exe = shutil.which("es") or shutil.which("es.exe") or "es.exe"
            out = subprocess.run(
                [exe, "-n", str(count), "-sort", "size-descending", "size:>0"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10,
            ).stdout or ""
            res = [_stat_result(Path(l.strip())) for l in out.splitlines() if l.strip()]
            res = [r for r in res if r]
            if res:
                return res[:count]
        except Exception:
            pass
    deadline = time.monotonic() + max(0.0, timeout_s)
    files = _bounded_all_files(_roots_from(scope), deadline)
    files.sort(key=lambda r: r["size"], reverse=True)
    return files[:count]


# Content search is deliberately CONSERVATIVE so it scales when a folder also
# holds multi-gigabyte files: only small, text-like files are ever opened, and
# the whole walk stays time- and count-bounded. A huge file is skipped, never
# read into memory.
_CONTENT_MAX_BYTES = 512 * 1024
_CONTENT_TEXT_EXTS = {".txt", ".md", ".log", ".csv", ".json", ".xml", ".yml",
                      ".yaml", ".ini", ".cfg", ".py", ".js", ".ts", ".html",
                      ".css", ".rtf", ".tex"}


def search_content(term: str, scope=None, limit: int = 20,
                   timeout_s: float = _FALLBACK_TIMEOUT_S) -> list[dict]:
    """Find files whose TEXT CONTENT contains `term` (case-insensitive).

    Users often refer to a file by what is INSIDE it ('файл привет' = the file
    that CONTAINS привет), not by its name. Bounded + huge-file-safe: only
    small text files are opened, the count is capped, and the walk is
    time-limited, so this never hangs and never reads a giant file.
    """
    term = (term or "").strip()
    if not term:
        return []
    needle = term.lower()
    deadline = time.monotonic() + max(0.0, timeout_s)
    hits: list[dict] = []
    for r in _bounded_all_files(_roots_from(scope), deadline, cap=5000):
        if time.monotonic() > deadline or len(hits) >= limit:
            break
        p = Path(r["path"])
        if p.suffix.lower() not in _CONTENT_TEXT_EXTS:
            continue
        if r.get("size", 0) > _CONTENT_MAX_BYTES:   # huge file -> never read it
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in text.lower():
            hits.append(r)
    return hits


def backend_name() -> str:
    """Which search backend is active — for honest user messaging."""
    return "everything" if _es_available() else "bounded"
