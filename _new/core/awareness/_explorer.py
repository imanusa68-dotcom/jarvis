# core/awareness/_explorer.py
# Per-command lookup of OPEN Explorer (file browser) windows via the Shell COM
# object — used to resolve "the open folder" (issue 017). COM, called only on
# demand (never in the background). Soft: empty/0 on any failure or non-Windows.

from __future__ import annotations

import urllib.parse


def foreground_hwnd() -> int:
    """HWND of the current foreground window, or 0. Seam for tests."""
    try:
        import win32gui
        return int(win32gui.GetForegroundWindow())
    except Exception:
        return 0


def open_folders() -> list[dict]:
    """
    [{hwnd, path}] for each open Explorer FOLDER window (not browsers/control
    panel). COM (Shell.Application). Seam for tests; returns [] on any failure.
    """
    try:
        import pythoncom
        import win32com.client
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        shell = win32com.client.Dispatch("Shell.Application")
        out: list[dict] = []
        for w in shell.Windows():
            try:
                url = w.LocationURL
                if not url or not url.lower().startswith("file:"):
                    continue  # skip Internet Explorer / non-filesystem windows
                path = urllib.parse.unquote(url[8:]).replace("/", "\\")
                if path:
                    out.append({"hwnd": int(w.HWND), "path": path})
            except Exception:
                continue
        return out
    except Exception:
        return []
