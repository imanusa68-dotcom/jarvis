# core/awareness/_watchers.py
# The OS-facing collector. Enumerates VISIBLE top-level windows every ~2s and
# resolves their owning process, then pushes the raw list into the world model.
#
# Deriving "open apps" from windowed processes (rather than diffing every PID)
# is both cheaper and naturally free of background services — a service has no
# visible top-level window, so it never appears. App launch/install events that
# DO need process/registry diffing are issue 008.
#
# Everything here is best-effort and wrapped: a failure in one window, or a
# missing dependency, must never crash the loop or the app. Imported lazily by
# the facade's start(), so the package never hard-requires pywin32.

from __future__ import annotations

import win32gui
import win32process
import psutil

POLL_SECONDS = 2.0

# Shell windows that are visible + sometimes titled but are not "apps".
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Button", "DV2ControlHost"}


def _collect_windows() -> list[dict]:
    fg = None
    try:
        fg = win32gui.GetForegroundWindow()
    except Exception:
        fg = None

    windows: list[dict] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not title.strip():
                return
            try:
                if win32gui.GetClassName(hwnd) in _SHELL_CLASSES:
                    return
            except Exception:
                pass
            try:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = 0
            proc = ""
            if pid:
                try:
                    proc = psutil.Process(pid).name()
                except Exception:  # NoSuchProcess/AccessDenied/etc — best effort
                    proc = ""
            windows.append({
                "hwnd": hwnd,
                "pid": pid,
                "title": title,
                "process": proc,
                "foreground": hwnd == fg,
                "visible": True,
            })
        except Exception:
            # One bad window must not abort enumeration.
            return

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception as e:
        print(f"[Awareness] window enumeration failed: {e}")
    return windows


INSTALLED_POLL_SECONDS = 45.0  # registry snapshot is heavier → check rarely


def run_loop(stop_event, sink, installed_sink=None) -> None:
    """
    Poll visible windows every POLL_SECONDS and push them to `sink`. Additionally,
    every INSTALLED_POLL_SECONDS take an installed-apps registry snapshot and push
    it to `installed_sink` (issue 008) — throttled because it is heavier.
    """
    print("[Awareness] watcher loop started")
    import time as _t
    last_installed = 0.0
    while not stop_event.is_set():
        try:
            sink(_collect_windows())
        except Exception as e:
            print(f"[Awareness] poll error (continuing): {e}")

        if installed_sink is not None and (_t.monotonic() - last_installed) >= INSTALLED_POLL_SECONDS:
            last_installed = _t.monotonic()
            try:
                from core.awareness import _installed_apps
                installed_sink(_installed_apps.snapshot())
            except Exception as e:
                print(f"[Awareness] installed snapshot skipped: {e}")

        stop_event.wait(POLL_SECONDS)
    print("[Awareness] watcher loop stopped")
