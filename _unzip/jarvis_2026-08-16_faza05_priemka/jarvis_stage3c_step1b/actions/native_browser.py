"""
Native Browser Control - управление браузером через Windows API.
Позволяет подключаться к УЖЕ ОТКРЫТОМУ браузеру вместо создания нового окна.
"""

import subprocess
import time
import platform
from pathlib import Path

# Проверяем доступность pyautogui и pygetwindow
try:
    import pyautogui
    import pyperclip
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    print("[NativeBrowser] WARNING: pyautogui/pyperclip not installed")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[NativeBrowser] WARNING: psutil not installed")


# Имена процессов браузеров
BROWSER_PROCESSES = {
    "yandex": ["browser.exe", "yandex.exe", "YandexBrowser"],
    "chrome": ["chrome.exe", "Google Chrome"],
    "firefox": ["firefox.exe", "Firefox"],
    "edge": ["msedge.exe", "Microsoft Edge"],
    "opera": ["opera.exe", "Opera"],
}

# Заголовки окон браузеров (частичное совпадение)
BROWSER_WINDOW_TITLES = {
    "yandex": ["Yandex", "Яндекс"],
    "chrome": ["Google Chrome", "Chrome"],
    "firefox": ["Mozilla Firefox", "Firefox"],
    "edge": ["Microsoft Edge", "Edge"],
    "opera": ["Opera"],
}

# Пути к исполняемым файлам браузеров
BROWSER_PATHS = {
    "yandex": [
        Path.home() / "AppData/Local/Yandex/YandexBrowser/Application/browser.exe",
        Path("C:/Program Files/Yandex/YandexBrowser/Application/browser.exe"),
        Path("C:/Program Files (x86)/Yandex/YandexBrowser/Application/browser.exe"),
    ],
    "chrome": [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
    ],
    "firefox": [
        Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
        Path("C:/Program Files (x86)/Mozilla Firefox/firefox.exe"),
    ],
    "edge": [
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ],
}


def _normalize_browser_name(name: str) -> str:
    """Нормализует имя браузера."""
    if not name:
        return "default"
    name = name.lower().strip()
    mapping = {
        "яндекс": "yandex", "yandex": "yandex", "yandex browser": "yandex",
        "хром": "chrome", "chrome": "chrome", "google chrome": "chrome", "гугл хром": "chrome",
        "firefox": "firefox", "фаерфокс": "firefox", "mozilla": "firefox",
        "edge": "edge", "эдж": "edge", "microsoft edge": "edge",
        "opera": "opera", "опера": "opera",
    }
    return mapping.get(name, name)


def is_browser_running(browser_name: str) -> bool:
    """Проверяет, запущен ли браузер."""
    if not PSUTIL_AVAILABLE:
        return False
    
    browser_name = _normalize_browser_name(browser_name)
    process_names = BROWSER_PROCESSES.get(browser_name, [])
    
    for proc in psutil.process_iter(['name']):
        try:
            proc_name = proc.info['name']
            if proc_name and any(pn.lower() in proc_name.lower() for pn in process_names):
                print(f"[NativeBrowser] Found running {browser_name}: {proc_name}")
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    return False


def get_browser_window(browser_name: str):
    """Находит окно браузера по имени. Возвращает hwnd или None."""
    if platform.system() != "Windows":
        return None
    
    browser_name = _normalize_browser_name(browser_name)
    titles_to_find = BROWSER_WINDOW_TITLES.get(browser_name, [browser_name])
    
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # Callback для EnumWindows
        found_hwnd = [None]
        
        def enum_callback(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    
                    for search_title in titles_to_find:
                        if search_title.lower() in title.lower():
                            print(f"[NativeBrowser] Found window: '{title}'")
                            found_hwnd[0] = hwnd
                            return False  # Stop enumeration
            return True  # Continue enumeration
        
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        
        return found_hwnd[0]
        
    except Exception as e:
        print(f"[NativeBrowser] Error finding window: {e}")
        return None


def activate_window(hwnd) -> bool:
    """Активирует (выводит на передний план) окно по hwnd."""
    if not hwnd:
        return False
    
    try:
        import ctypes
        user32 = ctypes.windll.user32
        
        # Восстанавливаем окно если оно свёрнуто
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        
        # Выводим на передний план
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        
        return True
    except Exception as e:
        print(f"[NativeBrowser] Error activating window: {e}")
        return False


def get_browser_executable(browser_name: str) -> str | None:
    """Возвращает путь к исполняемому файлу браузера."""
    browser_name = _normalize_browser_name(browser_name)
    paths = BROWSER_PATHS.get(browser_name, [])
    
    for path in paths:
        if path.exists():
            return str(path)
    
    return None


def navigate_in_same_tab(browser_name: str, url: str) -> bool:
    """
    Переходит по URL в ТЕКУЩЕЙ вкладке (не открывает новую).
    Используется когда пользователь говорит "в той же вкладке".
    """
    print(f"[NativeBrowser] navigate_in_same_tab called: {browser_name}, url={url}")
    
    if not PYAUTOGUI_AVAILABLE:
        print("[NativeBrowser] pyautogui not available")
        return False
    
    browser_name = _normalize_browser_name(browser_name)
    
    try:
        # Находим окно браузера
        hwnd = get_browser_window(browser_name)
        if not hwnd:
            print(f"[NativeBrowser] {browser_name} window not found")
            return False
        
        # Активируем окно
        if not activate_window(hwnd):
            print(f"[NativeBrowser] Failed to activate window")
            return False
        
        time.sleep(0.3)
        
        # Убеждаемся что URL полный
        if not url.startswith("http"):
            url = "https://" + url
        
        # Ctrl+L чтобы выделить адресную строку
        print(f"[NativeBrowser] Pressing Ctrl+L and entering URL in SAME tab...")
        pyautogui.hotkey('ctrl', 'l')
        time.sleep(0.3)
        
        # Вводим URL через clipboard
        pyperclip.copy(url)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.3)
        
        # Нажимаем Enter
        pyautogui.press('enter')
        print(f"[NativeBrowser] URL entered in same tab, waiting for page load...")
        time.sleep(1.0)
        
        print(f"[NativeBrowser] SUCCESS: Navigated to {url} in same tab")
        return True
        
    except Exception as e:
        print(f"[NativeBrowser] ERROR in navigate_in_same_tab: {e}")
        import traceback
        traceback.print_exc()
        return False


def open_new_tab_in_existing_browser(browser_name: str, url: str = None) -> bool:
    """
    Открывает новую вкладку в уже запущенном браузере.
    Возвращает True если успешно, False если браузер не найден.
    """
    print(f"[NativeBrowser] open_new_tab_in_existing_browser called: {browser_name}, url={url}")
    
    if not PYAUTOGUI_AVAILABLE:
        print("[NativeBrowser] pyautogui not available")
        return False
    
    browser_name = _normalize_browser_name(browser_name)
    
    try:
        # Находим окно браузера
        hwnd = get_browser_window(browser_name)
        if not hwnd:
            print(f"[NativeBrowser] {browser_name} window not found")
            return False
        
        print(f"[NativeBrowser] Found hwnd: {hwnd}")
        
        # Активируем окно
        if not activate_window(hwnd):
            print(f"[NativeBrowser] Failed to activate window")
            return False
        
        print(f"[NativeBrowser] Window activated, waiting...")
        time.sleep(0.5)
        
        # Нажимаем Ctrl+T для новой вкладки
        print(f"[NativeBrowser] Pressing Ctrl+T...")
        pyautogui.hotkey('ctrl', 't')
        time.sleep(0.7)
        
        # Если есть URL - вводим его
        if url:
            # Убеждаемся что URL полный
            if not url.startswith("http"):
                url = "https://" + url
            
            # Ctrl+L чтобы выделить адресную строку
            print(f"[NativeBrowser] Pressing Ctrl+L and entering URL...")
            pyautogui.hotkey('ctrl', 'l')
            time.sleep(0.3)
            
            # Вводим URL через clipboard (быстрее и надёжнее)
            pyperclip.copy(url)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.3)
            
            # Нажимаем Enter
            pyautogui.press('enter')
            print(f"[NativeBrowser] URL entered, waiting for page load...")
            time.sleep(1.0)
        
        print(f"[NativeBrowser] SUCCESS: Opened new tab in {browser_name}" + (f" with URL: {url}" if url else ""))
        return True
        
    except Exception as e:
        print(f"[NativeBrowser] ERROR in open_new_tab_in_existing_browser: {e}")
        import traceback
        traceback.print_exc()
        return False


def start_browser(browser_name: str, url: str = None) -> bool:
    """Запускает браузер. Если уже открыт - создаёт новую вкладку."""
    browser_name = _normalize_browser_name(browser_name)
    
    # Сначала проверяем, открыт ли уже браузер
    if is_browser_running(browser_name):
        print(f"[NativeBrowser] {browser_name} is already running, opening new tab")
        return open_new_tab_in_existing_browser(browser_name, url)
    
    # Браузер не запущен - запускаем его
    exe_path = get_browser_executable(browser_name)
    if not exe_path:
        print(f"[NativeBrowser] ERROR: {browser_name} executable not found")
        return False
    
    try:
        args = [exe_path]
        if url:
            if not url.startswith("http"):
                url = "https://" + url
            args.append(url)
        
        subprocess.Popen(args)
        print(f"[NativeBrowser] Started {browser_name}: {exe_path}")
        time.sleep(2)  # Ждём пока браузер запустится
        return True
    except Exception as e:
        print(f"[NativeBrowser] ERROR starting browser: {e}")
        return False


def type_in_browser(text: str, press_enter: bool = True) -> bool:
    """Вводит текст в активное поле браузера."""
    if not PYAUTOGUI_AVAILABLE:
        return False
    
    try:
        # Используем clipboard для надёжного ввода
        pyperclip.copy(text)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.2)
        
        if press_enter:
            pyautogui.press('enter')
            time.sleep(0.3)
        
        return True
    except Exception as e:
        print(f"[NativeBrowser] Error typing: {e}")
        return False


def click_element_by_image(image_path: str) -> bool:
    """Кликает на элемент по изображению (для сложных случаев)."""
    if not PYAUTOGUI_AVAILABLE:
        return False
    
    try:
        location = pyautogui.locateOnScreen(image_path, confidence=0.8)
        if location:
            pyautogui.click(pyautogui.center(location))
            return True
    except Exception as e:
        print(f"[NativeBrowser] Error clicking by image: {e}")
    
    return False


def navigate_to_url(browser_name: str, url: str, same_tab: bool = False) -> bool:
    """
    Переходит по URL в указанном браузере.
    same_tab: если True - использует текущую вкладку, иначе открывает новую
    """
    browser_name = _normalize_browser_name(browser_name)
    
    print(f"[NativeBrowser] navigate_to_url called: browser={browser_name}, url={url}, same_tab={same_tab}")
    
    # Проверяем, открыт ли браузер
    if is_browser_running(browser_name):
        if same_tab:
            # Используем текущую вкладку
            result = navigate_in_same_tab(browser_name, url)
            print(f"[NativeBrowser] navigate_to_url result (same tab): {result}")
        else:
            # Открываем новую вкладку
            result = open_new_tab_in_existing_browser(browser_name, url)
            print(f"[NativeBrowser] navigate_to_url result (new tab): {result}")
        return result
    else:
        # Браузер не открыт - возвращаем False чтобы Playwright запустил
        print(f"[NativeBrowser] Browser not running, returning False for Playwright fallback")
        return False


def search_youtube(browser_name: str, query: str, same_tab: bool = False) -> bool:
    """
    Ищет на YouTube в указанном браузере.
    same_tab: если True - использует текущую вкладку
    """
    print(f"[NativeBrowser] search_youtube called: browser={browser_name}, query={query}, same_tab={same_tab}")
    
    # Формируем URL поиска YouTube
    import urllib.parse
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    
    result = navigate_to_url(browser_name, search_url, same_tab=same_tab)
    print(f"[NativeBrowser] search_youtube result: {result}")
    return result


def focus_browser(browser_name: str) -> bool:
    """Выводит браузер на передний план."""
    browser_name = _normalize_browser_name(browser_name)
    hwnd = get_browser_window(browser_name)
    return activate_window(hwnd) if hwnd else False


# Глобальное состояние для отслеживания активного браузера
_active_browser = None

def get_active_browser() -> str | None:
    """Возвращает имя активного браузера."""
    global _active_browser
    return _active_browser

def set_active_browser(browser_name: str):
    """Устанавливает активный браузер."""
    global _active_browser
    _active_browser = _normalize_browser_name(browser_name)
