import os
import re
import json
import subprocess
import winreg
from pathlib import Path


def _find_steam_path() -> Path | None:
    registry_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam"),
    ]
    for hive, key_path in registry_keys:
        try:
            key = winreg.OpenKey(hive, key_path)
            val, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            p = Path(val)
            if p.exists() and (p / "steam.exe").exists():
                return p
        except Exception:
            continue
    for p in [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Steam",
        Path(os.environ.get("ProgramFiles", "")) / "Steam",
        Path("C:/Steam"), Path("D:/Steam"), Path("E:/Steam"), Path("F:/Steam"),
    ]:
        if p.exists() and (p / "steam.exe").exists():
            return p
    return None


def _get_steam_libraries(steam_path: Path) -> list[Path]:
    libraries = [steam_path / "steamapps"]
    vdf_path  = steam_path / "steamapps" / "libraryfolders.vdf"
    if not vdf_path.exists():
        return libraries
    try:
        content = vdf_path.read_text(encoding="utf-8", errors="ignore")
        for raw_path in re.findall(r'"path"\s+"([^"]+)"', content):
            lib = Path(raw_path.replace("\\\\", "/")) / "steamapps"
            if lib.exists() and lib not in libraries:
                libraries.append(lib)
    except Exception:
        pass
    return libraries


def _get_steam_games(steam_path: Path) -> list[dict]:
    games = []
    for lib in _get_steam_libraries(steam_path):
        for acf in lib.glob("appmanifest_*.acf"):
            try:
                content = acf.read_text(encoding="utf-8", errors="ignore")
                app_id  = re.search(r'"appid"\s+"(\d+)"',     content)
                name    = re.search(r'"name"\s+"([^"]+)"',     content)
                state   = re.search(r'"StateFlags"\s+"(\d+)"', content)
                size    = re.search(r'"SizeOnDisk"\s+"(\d+)"', content)
                if app_id and name:
                    games.append({
                        "id":    app_id.group(1),
                        "name":  name.group(1),
                        "state": int(state.group(1)) if state else 0,
                        "size":  int(size.group(1))  if size  else 0,
                        "lib":   str(lib),
                    })
            except Exception:
                continue
    return games


def _get_download_status(steam_path: Path) -> str:
    games   = _get_steam_games(steam_path)
    active  = [g for g in games if g["state"] == 1026]
    pending = [g for g in games if g["state"] in (6, 516)]
    lines   = []
    if active:
        lines.append(f"Downloading: {', '.join(g['name'] for g in active)}.")
    if pending:
        names  = ", ".join(g["name"] for g in pending[:5])
        suffix = f" and {len(pending) - 5} more" if len(pending) > 5 else ""
        lines.append(f"Pending updates: {names}{suffix}.")
    return " ".join(lines) if lines else "No active downloads or pending updates."


def _get_epic_games() -> list[dict]:
    manifests_path = (Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
                      / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests")
    if not manifests_path.exists():
        return []
    games = []
    for item_file in manifests_path.glob("*.item"):
        try:
            data = json.loads(item_file.read_text(encoding="utf-8"))
            name = data.get("DisplayName") or data.get("AppName", "")
            if name:
                games.append({"id": data.get("AppName", ""), "name": name})
        except Exception:
            continue
    return games


def _get_schedule_status() -> str:
    result = subprocess.run(["schtasks", "/Query", "/TN", "JARVIS_GameUpdater", "/FO", "LIST"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return "No scheduled game update found."
    for line in result.stdout.strip().split("\n"):
        if any(k in line for k in ("Next Run", "Sonraki", "Prochaine", "Próxima", "Nächste")):
            return f"Game update scheduled. {line.strip()}"
    return "Game update is scheduled."


def game_updater(parameters: dict, player=None, speak=None) -> str:
    """
    SECURED: Game updater actions are limited for safety.

    ALLOWED actions (read-only):
        list            : List installed games
        download_status : Check download status
        schedule_status : Check scheduled update status

    BLOCKED actions (can modify system):
        update, install, schedule, cancel_schedule, shutdown
    """
    p = parameters or {}
    action = p.get("action", "update").lower().strip()
    platform = p.get("platform", "both").lower().strip()

    # SECURITY: Block dangerous actions
    BLOCKED_ACTIONS = {"update", "install", "schedule", "cancel_schedule"}
    SAFE_ACTIONS = {"list", "download_status", "schedule_status"}

    if action in BLOCKED_ACTIONS:
        print(f"[GameUpdater] BLOCKED: Action '{action}' disabled for security")
        return (
            f"SECURITY: Game updater action '{action}' is blocked for safety. "
            "Game installation, updates, and scheduling are disabled. "
            "Only read-only actions (list, download_status, schedule_status) are allowed."
        )

    results = []

    if action == "schedule_status":
        return _get_schedule_status()

    if action == "list":
        if platform in ("steam", "both"):
            steam_path = _find_steam_path()
            if steam_path:
                games = _get_steam_games(steam_path)
                if games:
                    names  = ", ".join(g["name"] for g in games[:8])
                    suffix = f" and {len(games) - 8} more" if len(games) > 8 else ""
                    results.append(f"Steam ({len(games)} games): {names}{suffix}.")
                else:
                    results.append("Steam: No games found.")
            else:
                results.append("Steam: Not installed.")
        if platform in ("epic", "both"):
            games = _get_epic_games()
            if games:
                names  = ", ".join(g["name"] for g in games[:8])
                suffix = f" and {len(games) - 8} more" if len(games) > 8 else ""
                results.append(f"Epic ({len(games)} games): {names}{suffix}.")
            else:
                results.append("Epic: No games found.")
        return " | ".join(results) or "No platforms found."

    if action == "download_status":
        if platform in ("steam", "both"):
            steam_path = _find_steam_path()
            results.append(_get_download_status(steam_path) if steam_path else "Steam: Not installed.")
        if platform in ("epic", "both"):
            results.append("Epic download status not available directly.")
        return " ".join(results)

    # SECURITY: install/update actions are blocked above
    return f"SECURITY: Action '{action}' is not allowed."
