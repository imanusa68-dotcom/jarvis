# actions/open_app.py
# MARK XXV — Cross-Platform App Launcher

import os
import time
import subprocess
import platform
import shutil


def _launch_shortcut(path: str) -> None:
    """Launch a Start Menu .lnk (like clicking it). Seam so tests don't launch."""
    os.startfile(path)  # noqa: S606


def _open_installed_by_name(app_name: str, player=None) -> str:
    """
    Open ANY installed app by (possibly Russian / imprecise) name via the Start
    Menu index — no allowlist, no screen vision (issue 014). Below the match
    threshold, suggest close names instead of guessing.
    """
    try:
        from core.awareness import _app_index
        match = _app_index.best_match(app_name)
        if match:
            _launch_shortcut(match["path"])
            if player:
                try:
                    player.write_log(f"[open_app] {match['name']}")
                except Exception:
                    pass
            return f"Открываю {match['name']}."
        near = _app_index.suggestions(app_name, 3)
        if near:
            return f"Не нашёл приложение «{app_name}». Возможно: {', '.join(near)}?"
        return f"Не нашёл приложение «{app_name}» среди установленных."
    except Exception as e:
        return f"Не удалось открыть «{app_name}»: {e}"

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    # Chrome - именно Google Chrome, НЕ Chromium
    "chrome":             {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "хром":               {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "гугл хром":          {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    # Yandex Browser - отдельно от Chrome
    "yandex":             {"Windows": "yandex",                 "Darwin": "Yandex",              "Linux": "yandex-browser"},
    "yandex browser":     {"Windows": "yandex",                 "Darwin": "Yandex",              "Linux": "yandex-browser"},
    "яндекс":             {"Windows": "yandex",                 "Darwin": "Yandex",              "Linux": "yandex-browser"},
    "яндекс браузер":     {"Windows": "yandex",                 "Darwin": "Yandex",              "Linux": "yandex-browser"},
    "firefox":            {"Windows": "firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "spotify"},
    "vscode":             {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "telegram"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gedit"},
    "calculator":         {"Windows": "calc.exe",               "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "bash"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "gimp"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "vlc":                {"Windows": "vlc",                    "Darwin": "VLC",                 "Linux": "vlc"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Preferences",  "Linux": "gnome-control-center"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "bash"},
    "edge":               {"Windows": "msedge",                 "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion"},
    "blender":            {"Windows": "blender",                "Darwin": "Blender",             "Linux": "blender"},
    "capcut":             {"Windows": "CapCut",                 "Darwin": "CapCut",              "Linux": "capcut"},
    "postman":            {"Windows": "Postman",                "Darwin": "Postman",             "Linux": "postman"},
    "figma":              {"Windows": "Figma",                  "Darwin": "Figma",               "Linux": "figma"},
}


# Русские имена → канонические ключи _APP_ALIASES. Раньше «блокнот» проходил
# allowlist (_SAFE_APPS), но не находил маппинга — и уходил в слепой GUI-поиск.
_RU_TO_KEY = {
    "блокнот": "notepad",
    "калькулятор": "calculator",
    "проводник": "explorer",
    "настройки": "settings",
    "хром": "chrome",
    "гугл хром": "google chrome",
    "яндекс": "yandex",
    "яндекс браузер": "yandex browser",
    "ворд": "word",
    "эксель": "excel",
    "консоль": "terminal",
    "терминал": "terminal",
    "диспетчер задач": "task manager",
    "vs code": "vscode",
}


def _normalize(raw: str) -> str:
    system = platform.system()
    key    = raw.lower().strip()
    key    = _RU_TO_KEY.get(key, key)
    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(system, raw)
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(system, raw)
    return raw


def _is_running(app_name: str) -> bool:
    if not _PSUTIL:
        return True
    app_lower = app_name.lower().replace(" ", "").replace(".exe", "")
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info["name"].lower().replace(" ", "").replace(".exe", "")
                if app_lower in proc_name or proc_name in app_lower:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def _launch_windows(app_name: str) -> bool:
    """
    Прямой запуск через ShellExecute (PATH, App Paths, URI вроде ms-settings:).

    Слепой GUI-поиск через Пуск (Win → вставить имя → Enter) УДАЛЁН: верхней
    подсказкой Windows Search мог оказаться веб-поиск — так «блокнот»
    открывал Microsoft Edge с поисковой выдачей. Всё, чего нет в PATH/App
    Paths (Store-приложения, нестандартные имена), открывает fallback в
    open_app() — индекс меню «Пуск» (fuzzy + RU→EN транслитерация).
    """
    try:
        os.startfile(app_name)
        return True
    except OSError:
        return False


def _launch_macos(app_name: str) -> bool:
    """
    Запускает приложение на macOS.
    Сначала пробует subprocess, потом Spotlight с clipboard.
    """
    try:
        result = subprocess.run(["open", "-a", app_name], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(["open", "-a", f"{app_name}.app"], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    # Fallback: Spotlight с clipboard (работает с любой раскладкой)
    try:
        import pyautogui
        import pyperclip
        
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        
        # Используем clipboard вместо write
        pyperclip.copy(app_name)
        pyautogui.hotkey("command", "v")
        time.sleep(0.8)
        
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] macOS Spotlight failed: {e}")
        return False



def _launch_linux(app_name: str) -> bool:
    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-"))
    )
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(["xdg-open", app_name], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    try:
        desktop_name = app_name.lower().replace(" ", "-")
        subprocess.run(["gtk-launch", desktop_name], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}


# Список БЕЗОПАСНЫХ приложений, которые можно открывать
_SAFE_APPS = {
    # Браузеры
    "chrome", "google chrome", "хром", "гугл хром",
    "yandex", "yandex browser", "яндекс", "яндекс браузер",
    "firefox", "edge", "brave", "opera", "vivaldi",
    # Системные утилиты
    "calculator", "calc", "калькулятор",
    "notepad", "блокнот",
    "explorer", "file explorer", "проводник",
    "settings", "настройки",
    # Медиа
    "spotify", "vlc",
    # Коммуникации
    "discord", "telegram", "whatsapp", "slack", "zoom",
    # Разработка
    "vscode", "visual studio code", "code",
    # Другие безопасные
    "steam", "obsidian", "notion", "figma",
}


def _is_safe_app(app_name: str) -> bool:
    """Проверяет, является ли приложение безопасным для открытия."""
    app_lower = app_name.lower().strip()
    # Проверяем точное совпадение
    if app_lower in _SAFE_APPS:
        return True
    # Проверяем частичное совпадение
    for safe_app in _SAFE_APPS:
        if safe_app in app_lower or app_lower in safe_app:
            return True
    return False


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Открывает приложения на компьютере.
    
    БЕЗОПАСНОСТЬ: Разрешены только проверенные приложения из белого списка
    (браузеры, системные утилиты, медиа-плееры и т.д.)
    """
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "Please specify which application to open."

    # Not in the fast allowlist → open ANY installed app by name via the Start
    # Menu index (fuzzy + RU→EN transliteration), no screen vision (issue 014).
    if not _is_safe_app(app_name):
        return _open_installed_by_name(app_name, player)

    system = platform.system()
    launcher = _OS_LAUNCHERS.get(system)

    if launcher is None:
        return f"Unsupported OS: {system}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: {app_name} -> {normalized} ({system})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        success = launcher(normalized)

        if success:
            return f"Opened {app_name}. ready."

        if normalized != app_name:
            success = launcher(app_name)
            if success:
                return f"Opened {app_name}. ready."

        # Прямой запуск не нашёл исполняемого — открываем через индекс меню
        # «Пуск» (тот же путь, что для приложений вне allowlist, issue 014).
        if system == "Windows":
            return _open_installed_by_name(app_name, player)

        return (
            f"Launched {app_name} — no confirmation window appeared. "
            f"It may still be loading or might not be installed."
        )

    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"
