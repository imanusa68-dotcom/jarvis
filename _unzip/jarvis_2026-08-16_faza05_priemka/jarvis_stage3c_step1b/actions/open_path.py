# actions/open_path.py
# Open a file or folder in its DEFAULT application. This is the executor that was
# missing: the resolver (issue 004) can find "the file I just downloaded", but
# until now nothing could open it (open_app is app-name-only; cmd_control blocks
# start/explorer; file_controller has no open verb).
#
# Safety lives in this module, not just the prompt:
#   - the target must sit inside the user's personal folders (same known-folder
#     roots the watcher/resolver use, so OneDrive redirection can't cause a
#     "found it but refused to open it" mismatch);
#   - executable/script extensions are refused;
#   - the actual launch goes through _launch() so tests can stub it.

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Named apps that reliably accept a file argument on the command line. Opening a
# file in a SPECIFIC app this way is deterministic — no screen automation. The
# critical case (notepad) is a System32 exe and always works.
_APP_LAUNCHERS = {
    "notepad": "notepad.exe", "блокнот": "notepad.exe", "notepad.exe": "notepad.exe",
    "wordpad": "write.exe", "write": "write.exe", "вордпад": "write.exe",
    "paint": "mspaint.exe", "mspaint": "mspaint.exe", "паинт": "mspaint.exe",
    "vs code": "code", "vscode": "code", "code": "code", "visual studio code": "code",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "chrome": "chrome", "google chrome": "chrome", "хром": "chrome",
    "edge": "msedge", "firefox": "firefox",
    "explorer": "explorer.exe", "проводник": "explorer.exe",
}


def _resolve_launcher(app: str) -> str:
    """Map an app name to a launcher command. Falls back to open_app's aliases."""
    key = (app or "").strip().lower()
    if key in _APP_LAUNCHERS:
        return _APP_LAUNCHERS[key]
    try:
        from actions.open_app import _normalize
        return _normalize(app)
    except Exception:
        return app


# Executable / script / installer types we refuse to shell-open — opening these
# would run code. Documents and media are fine.
_BLOCKED_EXTS = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".scr", ".msi", ".msp", ".hta", ".cpl", ".reg", ".jar", ".lnk",
    ".pif", ".gadget", ".application", ".msc", ".inf",
}


def _safe_roots() -> list[Path]:
    """The personal folders where opening is permitted (OneDrive-aware)."""
    from core.awareness import _known_folders
    return _known_folders.watch_roots()


def _within_safe_roots(target: Path) -> bool:
    """
    True if `target` resolves inside one of the safe roots. Resolves first so that
    '..' traversal and symlinks cannot escape (same approach as file_controller).
    """
    try:
        probe = target
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        resolved = probe.resolve()
    except Exception:
        return False
    for root in _safe_roots():
        try:
            root_resolved = root.resolve()
        except Exception:
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


def _check(path: str) -> tuple[bool, str]:
    """Decide whether `path` may be opened. Returns (ok, refusal_reason)."""
    p = Path(path).expanduser()
    if p.suffix.lower() in _BLOCKED_EXTS:
        return False, (
            f"SECURITY: Открытие исполняемых файлов ({p.suffix}) отключено для "
            "безопасности."
        )
    if not _within_safe_roots(p):
        allowed = ", ".join(r.name for r in _safe_roots()) or "личных папках"
        return False, (
            f"SECURITY: Открывать можно только файлы в ваших личных папках ({allowed})."
        )
    if not p.exists():
        return False, f"Файл не найден: {p}"
    return True, ""


def _launch(path: str, app: str | None = None) -> None:
    """
    Open `path`. With no `app`, use the default handler (os.startfile). With an
    `app`, launch that application directly with the file — deterministic, no
    screen automation. Seam so tests don't open anything.
    """
    if not app:
        os.startfile(path)  # noqa: S606 — Windows shell-open of a boundary-checked path
        return
    launcher = _resolve_launcher(app)
    exe = shutil.which(launcher) or launcher
    subprocess.Popen([exe, path], close_fds=True)


def open_path(parameters=None, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    path = (params.get("path") or "").strip()
    app = (params.get("app") or "").strip()
    if not path:
        return "Не указан путь для открытия."
    ok, reason = _check(path)
    if not ok:
        print(f"[OpenPath] BLOCKED: {reason}")
        return reason
    try:
        _launch(path, app or None)
    except Exception as e:
        name = Path(path).name
        if app:
            # Honest failure — do NOT silently fall back to the default app,
            # that is exactly what confused the user (opened VS Code again).
            return f"Не удалось открыть «{name}» в приложении «{app}»: {e}"
        return f"Не удалось открыть «{name}»: {e}"
    if player:
        try:
            player.write_log(f"[open_path] {path}" + (f" ({app})" if app else ""))
        except Exception:
            pass
    if app:
        return f"Открываю: {path} в приложении «{app}»"
    return f"Открываю: {path}"
