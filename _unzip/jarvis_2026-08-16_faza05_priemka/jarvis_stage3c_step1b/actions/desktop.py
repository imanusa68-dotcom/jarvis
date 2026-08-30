# actions/desktop.py
# Read-only desktop info: list contents, stats, current wallpaper.
# Mutating actions (wallpaper change, organize, clean, generated code)
# are blocked at the desktop_control() entry point.

import sys
from pathlib import Path


def _get_desktop() -> Path:
    return Path.home() / "Desktop"


def get_current_wallpaper() -> str:
    """Returns the current wallpaper path."""
    try:
        if sys.platform == "win32":
            import winreg
            key  = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\Desktop")
            val, _ = winreg.QueryValueEx(key, "Wallpaper")
            return f"Current wallpaper: {val}"
        else:
            return "Wallpaper path retrieval not supported on this OS."
    except Exception as e:
        return f"Could not get wallpaper: {e}"


def list_desktop() -> str:
    """Lists everything on the desktop."""
    desktop = _get_desktop()
    items   = []

    for item in sorted(desktop.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            count = len(list(item.iterdir()))
            items.append(f"📁 {item.name}/ ({count} items)")
        else:
            size = item.stat().st_size
            size_str = f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/1024/1024:.1f} MB"
            items.append(f"📄 {item.name} ({size_str})")

    if not items:
        return "Desktop is empty."
    return f"Desktop ({len(items)} items):\n" + "\n".join(items)


def get_desktop_stats() -> str:
    """Returns stats about the desktop."""
    desktop     = _get_desktop()
    files       = [i for i in desktop.iterdir() if i.is_file()]
    folders     = [i for i in desktop.iterdir() if i.is_dir()]
    total_size  = sum(f.stat().st_size for f in files)
    size_str    = f"{total_size/1024:.1f} KB" if total_size < 1024*1024 else f"{total_size/1024/1024:.1f} MB"

    return (
        f"Desktop stats:\n"
        f"  Files   : {len(files)}\n"
        f"  Folders : {len(folders)}\n"
        f"  Total size: {size_str}"
    )


def desktop_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    SECURED: Desktop control is limited to read-only operations.

    ALLOWED actions (read-only):
        list              : List desktop contents
        stats             : Get desktop statistics
        current_wallpaper : Get current wallpaper path

    BLOCKED actions (can modify files or execute code):
        wallpaper, wallpaper_url, organize, clean, task
    """
    action = (parameters or {}).get("action", "").lower().strip()
    task = (parameters or {}).get("task", "").strip()

    # SECURITY: Block dangerous actions
    BLOCKED_ACTIONS = {"wallpaper", "wallpaper_url", "organize", "clean", "task"}
    SAFE_ACTIONS = {"list", "stats", "current_wallpaper"}

    if action in BLOCKED_ACTIONS or task:
        print(f"[Desktop] BLOCKED: Action '{action or task}' disabled for security")
        return (
            f"SECURITY: Desktop action '{action or 'task'}' is blocked for safety. "
            "File organization, cleanup, wallpaper changes, and code execution are disabled. "
            "Only read-only actions (list, stats, current_wallpaper) are allowed."
        )

    result = "Unknown action."

    try:
        if action == "current_wallpaper":
            result = get_current_wallpaper()

        elif action == "list":
            result = list_desktop()

        elif action == "stats":
            result = get_desktop_stats()

        else:
            result = f"SECURITY: Action '{action}' is not allowed. Use list, stats, or current_wallpaper."

    except Exception as e:
        result = f"Desktop control error: {e}"

    print(f"[Desktop] (safe) {result[:100]}")
    if player:
        player.write_log(f"[desktop] (safe) {result[:60]}")

    return result
