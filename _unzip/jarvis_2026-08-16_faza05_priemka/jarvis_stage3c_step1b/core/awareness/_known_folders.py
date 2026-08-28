# core/awareness/_known_folders.py
# Single source of truth for the user's real personal folders.
#
# Uses the Windows known-folder API (SHGetKnownFolderPath) so we get the ACTUAL
# location even when Desktop/Documents/Downloads are redirected into OneDrive —
# hardcoding Path.home()/"Desktop" is wrong on such machines. Falls back to
# home/<Name> when the API is unavailable (non-Windows, or the call fails), so a
# caller never crashes.
#
# This is a plain Win32 call via ctypes — NOT COM — so it needs no CoInitialize.

from __future__ import annotations

from pathlib import Path

# name -> (KNOWNFOLDERID GUID string, home-relative fallback subfolder)
_FOLDERS: dict[str, tuple[str, str]] = {
    "desktop":   ("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "Desktop"),
    "downloads": ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
    "documents": ("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", "Documents"),
    "pictures":  ("{33E28130-4E1E-4676-835A-98395C3BC3BB}", "Pictures"),
    "music":     ("{4BD8D571-6D19-48D3-BE97-422220080E43}", "Music"),
    "videos":    ("{18989B1D-99B5-455B-841C-AB7C74E4DDFC}", "Videos"),
}


def _shget(folderid: str) -> str | None:
    """
    Resolve a KNOWNFOLDERID GUID string to a path via SHGetKnownFolderPath.
    Returns None on any failure (non-Windows, API error). Isolated so tests can
    stub it to exercise the fallback path.
    """
    try:
        import ctypes
        from ctypes import wintypes

        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32

        guid = ctypes.create_unicode_buffer(folderid)
        fid = (ctypes.c_byte * 16)()
        if ole32.IIDFromString(guid, ctypes.byref(fid)) != 0:
            return None

        out_ptr = ctypes.c_wchar_p()
        rc = shell32.SHGetKnownFolderPath(
            ctypes.byref(fid), 0, None, ctypes.byref(out_ptr)
        )
        if rc != 0 or not out_ptr.value:
            return None
        try:
            return str(out_ptr.value)
        finally:
            ole32.CoTaskMemFree(out_ptr)
    except Exception:
        return None


def folder(name: str) -> Path | None:
    """
    Real path of a known user folder by short name (desktop/downloads/documents/
    pictures/music/videos). None for an unknown name. Never raises — falls back to
    home/<Name> when the API cannot answer.
    """
    spec = _FOLDERS.get((name or "").strip().lower())
    if spec is None:
        return None
    guid, home_sub = spec
    resolved = _shget(guid)
    if resolved:
        p = Path(resolved)
        if p.exists():
            return p
    return Path.home() / home_sub


def watch_roots() -> list[Path]:
    """The existing personal folders the awareness layer watches (deduplicated)."""
    roots: list[Path] = []
    seen: set[str] = set()
    for name in _FOLDERS:
        p = folder(name)
        if p and p.exists():
            key = str(p).lower()
            if key not in seen:
                seen.add(key)
                roots.append(p)
    return roots
