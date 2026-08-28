# core/awareness/_installed_apps.py
# Read-only snapshot of installed applications from the Windows Uninstall registry
# keys (DisplayName). Used to detect "you just installed X" by diffing snapshots.
# Soft: returns an empty set on any failure or on non-Windows. Imported lazily.

from __future__ import annotations

try:
    import winreg
except ImportError:  # non-Windows
    winreg = None  # type: ignore


_UNINSTALL_ROOTS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _hive(name):
    return winreg.HKEY_LOCAL_MACHINE if name == "HKLM" else winreg.HKEY_CURRENT_USER


def _read_key(hive_name: str, path: str) -> set[str]:
    names: set[str] = set()
    try:
        with winreg.OpenKey(_hive(hive_name), path) as key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        name = _value(sk, "DisplayName")
                        if not name:
                            continue
                        # Skip OS components / updates — not "apps the user installed".
                        if _value(sk, "SystemComponent") == 1:
                            continue
                        if _value(sk, "ParentKeyName") or _value(sk, "ParentDisplayName"):
                            continue
                        if str(name).strip():
                            names.add(str(name).strip())
                except OSError:
                    continue
    except OSError:
        pass
    return names


def _value(sk, field):
    try:
        val, _ = winreg.QueryValueEx(sk, field)
        return val
    except OSError:
        return None


def snapshot() -> set[str]:
    """Set of installed-app DisplayNames. Empty set if unavailable."""
    if winreg is None:
        return set()
    names: set[str] = set()
    for hive_name, path in _UNINSTALL_ROOTS:
        names |= _read_key(hive_name, path)
    return names
