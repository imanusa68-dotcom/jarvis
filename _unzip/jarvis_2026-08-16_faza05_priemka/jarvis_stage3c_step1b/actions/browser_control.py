import asyncio
import threading
import concurrent.futures
import platform
import shutil
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

def _get_default_browser_id() -> str:
    """Returns raw default browser identifier string for current OS."""
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            key     = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
            )
            prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
            winreg.CloseKey(key)
            return prog_id

        elif system == "Darwin":
            result = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.lower()

        elif system == "Linux":
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.lower()

    except Exception:
        pass

    return ""

_BROWSER_BINARIES = {
    "Windows": {
        "opera":   ["opera.exe"],
        "brave":   ["brave.exe"],
        "vivaldi": ["vivaldi.exe"],
        "chrome":  ["chrome.exe"],
        "firefox": ["firefox.exe"],
        "yandex":  ["browser.exe"],  # Yandex Browser
        "edge":    ["msedge.exe"],
    },
    "Darwin": {
        "opera":   ["opera"],
        "brave":   ["brave browser", "brave"],
        "vivaldi": ["vivaldi"],
        "chrome":  ["google chrome", "google-chrome"],
        "firefox": ["firefox"],
        "yandex":  ["yandex"],
        "edge":    ["microsoft edge"],
    },
    "Linux": {
        "opera":   ["opera", "opera-stable"],
        "brave":   ["brave-browser", "brave"],
        "vivaldi": ["vivaldi-stable", "vivaldi"],
        "chrome":  ["google-chrome", "google-chrome-stable"],  # НЕ chromium!
        "firefox": ["firefox"],
        "yandex":  ["yandex-browser", "yandex-browser-stable"],
        "edge":    ["microsoft-edge", "microsoft-edge-stable"],
    },
}

# Алиасы для распознавания браузеров из голосовых команд
_BROWSER_ALIASES = {
    # Chrome
    "chrome": "chrome",
    "хром": "chrome",
    "гугл хром": "chrome",
    "google chrome": "chrome",
    # Yandex
    "yandex": "yandex",
    "яндекс": "yandex",
    "яндекс браузер": "yandex",
    "yandex browser": "yandex",
    # Firefox
    "firefox": "firefox",
    "фаерфокс": "firefox",
    # Edge
    "edge": "edge",
    "эдж": "edge",
    "microsoft edge": "edge",
    # Opera
    "opera": "opera",
    "опера": "opera",
    # Brave
    "brave": "brave",
    "брейв": "brave",
}


def _normalize_browser_name(name: str) -> str:
    """Преобразует название браузера в стандартное имя."""
    if not name:
        return ""
    name_lower = name.lower().strip()
    return _BROWSER_ALIASES.get(name_lower, name_lower)


def _get_opera_executable() -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        import winreg
        candidate_keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\launcher.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
        ]
        for key_path in candidate_keys:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key  = winreg.OpenKey(hive, key_path)
                    val  = winreg.QueryValue(key, None)
                    winreg.CloseKey(key)
                    exe  = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] 🔍 Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _find_browser_executable(prog_id: str) -> tuple:
    system  = platform.system()
    os_bins = _BROWSER_BINARIES.get(system, {})

    if any(x in prog_id for x in ["firefox", "mozilla"]):
        return "firefox", None, None

    if "safari" in prog_id:
        return "webkit", None, None

    if "edge" in prog_id:
        return "chromium", None, "msedge"

    if "opera" in prog_id:
        exe = _get_opera_executable()
        if exe:
            return "chromium", exe, None
        for binary in os_bins.get("opera", []):
            path = shutil.which(binary)
            if path:
                return "chromium", path, None

    browser_patterns = {
        "brave":   ["brave"],
        "vivaldi": ["vivaldi"],
        "chrome":  ["chrome"],
    }
    for browser_name, patterns in browser_patterns.items():
        if not any(p in prog_id for p in patterns):
            continue
        binaries = os_bins.get(browser_name, [])
        for binary in binaries:
            path = shutil.which(binary)
            if path:
                print(f"[Browser] 🔍 Found {browser_name} at: {path}")
                return "chromium", path, None

    if "chrome" in prog_id or not prog_id:
        return "chromium", None, "chrome"

    return "chromium", None, None


def _find_yandex_browser() -> str | None:
    """Ищет Yandex Browser на Windows."""
    if platform.system() != "Windows":
        path = shutil.which("yandex-browser") or shutil.which("yandex-browser-stable")
        return path
    
    # Все возможные пути для Yandex Browser на Windows
    possible_paths = [
        # Стандартный путь установки для текущего пользователя
        Path.home() / "AppData" / "Local" / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
        # Альтернативные пути
        Path("C:/Users") / Path.home().name / "AppData" / "Local" / "Yandex" / "YandexBrowser" / "Application" / "browser.exe",
        # Системная установка
        Path("C:/Program Files/Yandex/YandexBrowser/Application/browser.exe"),
        Path("C:/Program Files (x86)/Yandex/YandexBrowser/Application/browser.exe"),
    ]
    
    for ypath in possible_paths:
        if ypath.exists():
            print(f"[Browser] Found Yandex Browser at: {ypath}")
            return str(ypath)
    
    # Пробуем найти через реестр Windows
    try:
        import winreg
        registry_paths = [
            r"SOFTWARE\Yandex\YandexBrowser",
            r"SOFTWARE\Clients\StartMenuInternet\YandexBrowser\shell\open\command",
        ]
        for reg_path in registry_paths:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key = winreg.OpenKey(hive, reg_path)
                    val = winreg.QueryValue(key, None)
                    winreg.CloseKey(key)
                    exe = val.strip().strip('"').split('"')[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Found Yandex via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    
    return None


def _find_chrome_browser() -> str | None:
    """Ищет Google Chrome (НЕ Chromium!) на Windows."""
    if platform.system() != "Windows":
        path = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        return path
    
    # Пути для Google Chrome на Windows
    possible_paths = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    
    for cpath in possible_paths:
        if cpath.exists():
            print(f"[Browser] Found Google Chrome at: {cpath}")
            return str(cpath)
    
    # Пробуем найти через реестр
    try:
        import winreg
        registry_paths = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            r"SOFTWARE\Clients\StartMenuInternet\Google Chrome\shell\open\command",
        ]
        for reg_path in registry_paths:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key = winreg.OpenKey(hive, reg_path)
                    val = winreg.QueryValue(key, None)
                    winreg.CloseKey(key)
                    exe = val.strip().strip('"').split('"')[0].strip()
                    # Убедимся что это Chrome, а не Chromium
                    if exe and Path(exe).exists() and "chromium" not in exe.lower():
                        print(f"[Browser] Found Chrome via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    
    return None


def _get_browser_executable(browser_name: str) -> tuple:
    """
    Находит исполняемый файл для конкретного браузера.
    Возвращает (engine_name, exe_path, channel)
    
    ВАЖНО: НЕ фолбэчится на Chromium! Если браузер не найден - возвращает ошибку.
    """
    system = platform.system()
    os_bins = _BROWSER_BINARIES.get(system, {})
    browser_name = _normalize_browser_name(browser_name)
    
    print(f"[Browser] Looking for browser: {browser_name}")
    
    # Firefox
    if browser_name == "firefox":
        return "firefox", None, None
    
    # Yandex Browser - ОБЯЗАТЕЛЬНО найти именно Yandex!
    if browser_name == "yandex":
        exe_path = _find_yandex_browser()
        if exe_path:
            return "chromium", exe_path, None
        # Yandex не найден - возвращаем специальную ошибку
        print("[Browser] ERROR: Yandex Browser not found on this computer!")
        return "chromium", None, "YANDEX_NOT_FOUND"
    
    # Chrome - ОБЯЗАТЕЛЬНО найти именно Chrome, НЕ Chromium!
    if browser_name == "chrome":
        exe_path = _find_chrome_browser()
        if exe_path:
            return "chromium", exe_path, None
        # Пробуем channel как последний вариант
        return "chromium", None, "chrome"
    
    # Edge
    if browser_name == "edge":
        return "chromium", None, "msedge"
    
    # Opera
    if browser_name == "opera":
        exe = _get_opera_executable()
        if exe:
            return "chromium", exe, None
        for binary in os_bins.get("opera", []):
            path = shutil.which(binary)
            if path:
                return "chromium", path, None
    
    # Brave
    if browser_name == "brave":
        for binary in os_bins.get("brave", []):
            path = shutil.which(binary)
            if path:
                return "chromium", path, None
    
    # По умолчанию - системный браузер
    return None, None, None


class _BrowserThread:

    def __init__(self):
        self._loop          = None
        self._thread        = None
        self._ready         = threading.Event()
        self._playwright    = None
        # Словарь браузеров: {browser_name: (browser, context, page)}
        self._browsers      = {}
        self._current_browser = None  # Текущий активный браузер
        self._engine_name   = "chromium"
        self._exe_path      = None
        self._channel       = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BrowserThread"
        )
        self._thread.start()
        self._ready.wait(timeout=15)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self):
        self._playwright = await async_playwright().start()

    def run(self, coro, timeout: int = 30):
        if not self._loop:
            raise RuntimeError("BrowserThread not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Запуск браузера ────────────────────────────────────────────────────

    async def _launch_specific_browser(self, browser_name: str = None):
        """
        Запускает КОНКРЕТНЫЙ браузер по имени.
        Если браузер уже запущен через Playwright - использует ��го.
        ��АЖНО: НЕ фолбэчится на другой браузер!
        """
        browser_name = _normalize_browser_name(browser_name) if browser_name else "default"
        
        print(f"[Browser] === Launch request for: {browser_name} ===")
        print(f"[Browser] Currently tracked browsers: {list(self._browsers.keys())}")
        
        # Если этот браузер уже запущен через Playwright - используем его
        if browser_name in self._browsers:
            browser_data = self._browsers[browser_name]
            try:
                # Для persistent context проверяем через context.pages
                context = browser_data["context"]
                pages = context.pages
                is_alive = len(pages) > 0 or True  # context существует
                # Дополнительная проверка - попробуем получить URL
                if browser_data["page"] and not browser_data["page"].is_closed():
                    is_alive = True
                else:
                    is_alive = False
            except Exception as e:
                print(f"[Browser] Check failed: {e}")
                is_alive = False
            
            if is_alive:
                print(f"[Browser] REUSING existing {browser_name} browser (already tracked)")
                self._current_browser = browser_name
                return browser_data
            else:
                # Браузер был закрыт, удаляем из словаря
                print(f"[Browser] Previous {browser_name} was closed, will launch new")
                del self._browsers[browser_name]
        
        # Определяем параметры запуска
        if browser_name == "default" or not browser_name:
            prog_id = _get_default_browser_id()
            engine_name, exe_path, channel = _find_browser_executable(prog_id)
        else:
            # Запрашиваем КОНКРЕТНЫЙ браузер
            engine_name, exe_path, channel = _get_browser_executable(browser_name)
            
            # Проверяем на ошибку "браузер не найден"
            if channel and "NOT_FOUND" in channel:
                error_msg = f"{browser_name.upper()} browser is not installed on this computer!"
                print(f"[Browser] ERROR: {error_msg}")
                raise RuntimeError(error_msg)
        
        engine = getattr(self._playwright, engine_name)
        
        launch_kwargs = {"headless": False}
        if engine_name == "chromium":
            # Запускаем с remote-debugging чтобы потом можно было переиспользовать
            launch_kwargs["args"] = ["--start-maximized"]
        if exe_path:
            launch_kwargs["executable_path"] = exe_path
            print(f"[Browser] Using executable: {exe_path}")
        elif channel:
            launch_kwargs["channel"] = channel
            print(f"[Browser] Using channel: {channel}")
        
        # Используем persistent context для сохранения сессии
        import tempfile
        import os
        
        # Создаём директорию для профиля браузера
        profile_dir = os.path.join(tempfile.gettempdir(), f"jarvis_browser_{browser_name}")
        os.makedirs(profile_dir, exist_ok=True)
        print(f"[Browser] Using profile directory: {profile_dir}")
        
        try:
            # Используем launch_persistent_context вместо launch + new_context
            # Это позволяет сохранять cookies, историю и т.д.
            context_kwargs = {
                "headless": False,
                "viewport": None,
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
            if engine_name == "chromium":
                context_kwargs["args"] = ["--start-maximized"]
            if exe_path:
                context_kwargs["executable_path"] = exe_path
            elif channel:
                context_kwargs["channel"] = channel
            
            context = await engine.launch_persistent_context(profile_dir, **context_kwargs)
            browser = context  # В persistent context, context является и browser
            
            print(f"[Browser] SUCCESS: Launched {browser_name} with persistent profile")
        except Exception as e:
            # НЕ фолбэчимся на Chromium! Сообщаем об ошибке.
            error_msg = f"Failed to launch {browser_name}: {e}"
            print(f"[Browser] ERROR: {error_msg}")
            raise RuntimeError(error_msg)
        
        # Получаем существующую страницу или создаём новую
        pages = context.pages
        if pages:
            page = pages[0]
            print(f"[Browser] Using existing page from persistent context")
        else:
            page = await context.new_page()
            print(f"[Browser] Created new page in persistent context")
        
        # Сохраняем браузер в словарь для переиспользования
        self._browsers[browser_name] = {
            "browser": browser,
            "context": context,
            "page": page,
            "engine_name": engine_name,
            "exe_path": exe_path,
            "channel": channel,
        }
        self._current_browser = browser_name
        
        print(f"[Browser] {browser_name} is now tracked. Future requests will reuse this window.")
        return self._browsers[browser_name]

    async def _get_page(self, browser_name: str = None, new_tab: bool = False, reuse_if_same_site: str = None):
        """
        Получает страницу в указанном браузере.
        ВАЖНО: Всегда использует существующее окно браузера, создаёт новую ВКЛАДКУ если нужно.
        
        browser_name: "chrome", "yandex", "firefox" и т.д.
        new_tab: если True - открывает новую вкладку
        reuse_if_same_site: если указан домен, ищем существующую вкладку с этим сайтом
        """
        browser_name = _normalize_browser_name(browser_name) if browser_name else "default"
        
        # Запускаем/получаем браузер
        browser_data = await self._launch_specific_browser(browser_name)
        
        # Получаем все открытые вкладки
        try:
            all_pages = browser_data["context"].pages
            print(f"[Browser] {browser_name} has {len(all_pages)} tab(s) open")
        except Exception:
            all_pages = []
        
        # Если есть reuse_if_same_site - ищем вкладку с этим сайтом
        if reuse_if_same_site and all_pages:
            for page in all_pages:
                try:
                    if reuse_if_same_site.lower() in page.url.lower():
                        print(f"[Browser] Reusing existing tab with {reuse_if_same_site}: {page.url}")
                        browser_data["page"] = page
                        await page.bring_to_front()
                        return page
                except Exception:
                    continue
        
        # Если нужна новая вкладка явно
        if new_tab:
            print(f"[Browser] Creating new tab (new_tab=True)")
            page = await browser_data["context"].new_page()
            browser_data["page"] = page
            return page
        
        # Проверяем текущую страницу
        current_page = browser_data["page"]
        
        if current_page.is_closed():
            # Страница закрыта - создаём новую
            print(f"[Browser] Current page was closed, creating new tab")
            page = await browser_data["context"].new_page()
            browser_data["page"] = page
            return page
        
        # Проверяем URL текущей страницы
        try:
            current_url = current_page.url
        except Exception:
            current_url = ""
        
        # Если текущая страница пустая - используем её
        if current_url in ["about:blank", "chrome://newtab/", "", "about:srcdoc"]:
            print(f"[Browser] Using current empty tab")
            return current_page
        
        # Страница уже имеет конте��т - используем её же (не создаём новую вкладку для каждого действия)
        print(f"[Browser] Using current tab with URL: {current_url[:50]}...")
        return current_page

    # ── Действия браузера ─────────────────────────────────────────────────────

    async def _go_to(self, url: str, browser_name: str = None, new_tab: bool = False) -> str:
        """Переход по URL в указанном браузере. Использует существующую вкладку если возможно."""
        if not url.startswith("http"):
            url = "https://" + url
        
        # Извлекаем домен для поиска существующей вкладки
        from urllib.parse import urlparse
        domain = urlparse(url).netloc or url.split('/')[0]
        
        page = await self._get_page(browser_name=browser_name, new_tab=new_tab, reuse_if_same_site=domain)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            browser_info = f" [{browser_name}]" if browser_name else ""
            return f"Opened{browser_info}: {page.url}"
        except PlaywrightTimeout:
            return f"Timeout loading: {url}"
        except Exception as e:
            return f"Navigation error: {e}"

    async def _search(self, query: str, engine: str = "google", browser_name: str = None, new_tab: bool = False) -> str:
        """Поиск в указанном браузере."""
        engines = {
            "google":     f"https://www.google.com/search?q={query.replace(' ', '+')}",
            "bing":       f"https://www.bing.com/search?q={query.replace(' ', '+')}",
            "duckduckgo": f"https://duckduckgo.com/?q={query.replace(' ', '+')}",
            "yandex":     f"https://yandex.ru/search/?text={query.replace(' ', '+')}",
            "youtube":    f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}",
        }
        url = engines.get(engine.lower(), engines["google"])
        return await self._go_to(url, browser_name=browser_name, new_tab=new_tab)

    async def _youtube_search(self, query: str, browser_name: str = None) -> str:
        """Специальный поиск на YouTube - вводит текст в поисковую строку и нажимает Enter."""
        # Пытаемся найти существующую вкладку YouTube
        page = await self._get_page(browser_name=browser_name, reuse_if_same_site="youtube.com")
        current_url = page.url
        
        # Если не на YouTube - открываем YouTube в текущей вкладке
        if "youtube.com" not in current_url:
            print(f"[Browser] Navigating to YouTube...")
            await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)
        
        try:
            # YouTube search box selector (из скриншота пользователя)
            search_selectors = [
                'input[name="search_query"]',
                'input#search',
                'input.ytSearchboxComponentInput',
                'input[placeholder="Search"]',
                'input[placeholder="Поиск"]',
            ]
            
            search_input = None
            for selector in search_selectors:
                try:
                    search_input = page.locator(selector).first
                    if await search_input.is_visible(timeout=2000):
                        print(f"[Browser] Found YouTube search: {selector}")
                        break
                except Exception:
                    continue
            
            if search_input:
                await search_input.click()
                await page.wait_for_timeout(300)
                await search_input.fill(query)
                await page.wait_for_timeout(300)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2000)
                return f"Searched YouTube for: {query}"
            else:
                # Fallback - использовать URL поиска
                search_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                return f"Searched YouTube (via URL) for: {query}"
                
        except Exception as e:
            print(f"[Browser] YouTube search error: {e}")
            # Fallback
            search_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            return f"Searched YouTube (fallback) for: {query}"

    async def _click(self, selector=None, text=None, browser_name: str = None) -> str:
        """Клик по элементу."""
        page = await self._get_page(browser_name=browser_name)
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8000)
                return f"Clicked: '{text}'"
            elif selector:
                await page.click(selector, timeout=8000)
                return f"Clicked: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found or not clickable."
        except Exception as e:
            return f"Click error: {e}"

    async def _type(self, selector=None, text: str = "", clear_first: bool = True, browser_name: str = None) -> str:
        """Ввод текста."""
        page = await self._get_page(browser_name=browser_name)
        try:
            element = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await element.clear()
            await element.type(text, delay=50)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def _scroll(self, direction: str = "down", amount: int = 500, browser_name: str = None) -> str:
        """Прокрутка страницы."""
        page = await self._get_page(browser_name=browser_name)
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def _press(self, key: str, browser_name: str = None) -> str:
        """Нажатие клавиши."""
        page = await self._get_page(browser_name=browser_name)
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def _get_text(self, browser_name: str = None) -> str:
        """Получение текста страницы."""
        page = await self._get_page(browser_name=browser_name)
        try:
            text = await page.inner_text("body")
            return text[:4000] if len(text) > 4000 else text
        except Exception as e:
            return f"Could not get page text: {e}"

    async def _fill_form(self, fields: dict, browser_name: str = None) -> str:
        """Заполнение формы."""
        page = await self._get_page(browser_name=browser_name)
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"OK: {selector}")
            except Exception as e:
                results.append(f"FAIL: {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def _smart_click(self, description: str, browser_name: str = None) -> str:
        """Умный клик по описанию элемента."""
        page = await self._get_page(browser_name=browser_name)
        desc_lower = description.lower()

        role_hints = {
            "button":    ["button", "buton", "btn", "кнопка"],
            "link":      ["link", "ссылка", "bağlantı"],
            "searchbox": ["search", "поиск", "arama"],
            "textbox":   ["input", "field", "поле", "alan"],
        }
        for role, keywords in role_hints.items():
            if any(k in desc_lower for k in keywords):
                try:
                    await page.get_by_role(role).first.click(timeout=5000)
                    return f"Clicked ({role}): '{description}'"
                except Exception:
                    pass

        try:
            await page.get_by_text(description, exact=False).first.click(timeout=5000)
            return f"Clicked (text): '{description}'"
        except Exception:
            pass

        try:
            await page.get_by_placeholder(description, exact=False).first.click(timeout=5000)
            return f"Clicked (placeholder): '{description}'"
        except Exception:
            pass

        return f"Could not find: '{description}'"

    async def _smart_type(self, description: str, text: str, browser_name: str = None) -> str:
        """Умный ввод текста по описанию поля."""
        page = await self._get_page(browser_name=browser_name)
        desc_lower = description.lower()
        
        # Специальная обработка для YouTube
        if "youtube" in page.url and any(kw in desc_lower for kw in ["search", "поиск", "найти"]):
            return await self._youtube_search(text, browser_name=browser_name)
        
        # Список селекторов для поиска
        selectors_to_try = []
        
        # Если это поиск - добавляем специфичные селекторы
        if any(kw in desc_lower for kw in ["search", "поиск", "найти", "искать"]):
            selectors_to_try.extend([
                'input[name="search_query"]',  # YouTube
                'input[name="q"]',              # Google
                'input[name="search"]',
                'input[type="search"]',
                'input[placeholder*="Search" i]',
                'input[placeholder*="Поиск" i]',
                'input[aria-label*="Search" i]',
                'input[aria-label*="Поиск" i]',
            ])
        
        # Пробуем специфичные селекторы
        for selector in selectors_to_try:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await el.fill(text)
                    print(f"[Browser] Typed via selector: {selector}")
                    return f"Typed into search field: '{text}'"
            except Exception:
                continue
        
        # Стандартные методы Playwright
        for method, locator in [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("searchbox")),
            ("role",        page.get_by_role("textbox")),
        ]:
            try:
                el = locator.first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await el.fill(text)
                    print(f"[Browser] Typed via {method}")
                    return f"Typed into ({method}): '{description}'"
            except Exception:
                continue

        return f"Could not find input: '{description}'"

    async def _close_browser(self, browser_name: str = None) -> str:
        """Закрытие браузера."""
        if browser_name:
            browser_name = _normalize_browser_name(browser_name)
            if browser_name in self._browsers:
                data = self._browsers[browser_name]
                try:
                    await data["browser"].close()
                except Exception:
                    pass
                del self._browsers[browser_name]
                return f"Closed {browser_name} browser."
        
        # Закрываем все браузеры
        for name, data in list(self._browsers.items()):
            try:
                await data["browser"].close()
            except Exception:
                pass
        self._browsers.clear()
        
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        
        return "All browsers closed."


# ── Singleton browser thread ─────────────────────────────────────────────────

_bt         = _BrowserThread()
_bt_started = False
_bt_lock    = threading.Lock()


def _ensure_started():
    global _bt_started
    with _bt_lock:
        if not _bt_started:
            _bt.start()
            _bt_started = True


# ── Public API ───────────────────────────────────────────────────────────────

def browser_control(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Управление браузером с поддержкой выбора конкретного браузера.

    parameters:
        action       : go_to | search | youtube_search | click | type | scroll | 
                       fill_form | smart_click | smart_type | get_text | press | close
        browser      : chrome | yandex | firefox | edge | opera | brave
                       (если не указан - используется браузер по умолчанию)
        new_tab      : True - открыть новую вкладку (по умолчанию False)
        url          : URL для go_to
        query        : поисковый запрос
        engine       : google | bing | duckduckgo | yandex | youtube (по умолчанию: google)
        selector     : CSS селектор для click/type
        text         : текст для клика или ввода
        description  : описание элемента для smart_click/smart_type
        direction    : up | down для scroll
        amount       : количество пикселей скролла (по умолчанию: 500)
        key          : имя клавиши для press (Enter, Escape, Tab и т.д.)
        fields       : {selector: value} словарь для fill_form
        clear_first  : очистить поле перед вводом (по умолчанию: True)
    
    Примеры:
        {"action": "go_to", "url": "youtube.com", "browser": "yandex"}
        {"action": "youtube_search", "query": "MrBeast", "browser": "yandex"}
        {"action": "click", "text": "Videos", "browser": "yandex"}
    """
    action = (parameters or {}).get("action", "").lower().strip()
    browser_name = parameters.get("browser", "").strip()
    new_tab = bool(parameters.get("new_tab", False))
    
    print(f"\n[browser_control] ========================================")
    same_tab = bool(parameters.get("same_tab", False))
    
    print(f"[browser_control] Action: {action}, Browser: {browser_name or 'default'}, same_tab: {same_tab}")
    
    if not action:
        return "Please specify an action, sir."

    # Для go_to, search, youtube_search - пробуем сначала native browser
    # Это позволит использовать уже открытый браузер вместо создания нового окна
    
    if action in ("go_to", "youtube_search", "search") and browser_name:
        try:
            from actions.native_browser import (
                is_browser_running, navigate_to_url, search_youtube,
                set_active_browser, PYAUTOGUI_AVAILABLE
            )
            
            browser_running = is_browser_running(browser_name)
            
            if PYAUTOGUI_AVAILABLE and browser_running:
                print(f"[browser_control] {browser_name} is running, using native control (same_tab={same_tab})")
                
                native_result = None
                
                if action == "go_to":
                    url = parameters.get("url", "")
                    success = navigate_to_url(browser_name, url, same_tab=same_tab)
                    if success:
                        set_active_browser(browser_name)
                        tab_msg = "same tab" if same_tab else "new tab"
                        native_result = f"Opened {url} in {browser_name} ({tab_msg})"
                
                elif action == "youtube_search":
                    query = parameters.get("query", "")
                    success = search_youtube(browser_name, query, same_tab=same_tab)
                    if success:
                        set_active_browser(browser_name)
                        tab_msg = "same tab" if same_tab else "new tab"
                        native_result = f"Searched YouTube for '{query}' in {browser_name} ({tab_msg})"
                
                elif action == "search":
                    engine = parameters.get("engine", "google").lower()
                    query = parameters.get("query", "")
                    
                    if engine == "youtube":
                        success = search_youtube(browser_name, query, same_tab=same_tab)
                        if success:
                            set_active_browser(browser_name)
                            tab_msg = "same tab" if same_tab else "new tab"
                            native_result = f"Searched YouTube for '{query}' in {browser_name} ({tab_msg})"
                    else:
                        import urllib.parse
                        search_urls = {
                            "google": f"https://www.google.com/search?q={urllib.parse.quote(query)}",
                            "yandex": f"https://yandex.ru/search/?text={urllib.parse.quote(query)}",
                            "bing": f"https://www.bing.com/search?q={urllib.parse.quote(query)}",
                            "duckduckgo": f"https://duckduckgo.com/?q={urllib.parse.quote(query)}",
                        }
                        search_url = search_urls.get(engine, search_urls["google"])
                        success = navigate_to_url(browser_name, search_url, same_tab=same_tab)
                        if success:
                            set_active_browser(browser_name)
                            tab_msg = "same tab" if same_tab else "new tab"
                            native_result = f"Searched {engine} for '{query}' in {browser_name} ({tab_msg})"
                
                if native_result:
                    print(f"[browser_control] NATIVE SUCCESS: {native_result}")
                    return native_result
                    
        except ImportError as e:
            print(f"[browser_control] Native browser import error: {e}")
        except Exception as e:
            print(f"[browser_control] Native browser exception: {e}")
            import traceback
            traceback.print_exc()

    # Fallback к Playwright только если native не сработал
    print(f"[browser_control] *** USING PLAYWRIGHT FALLBACK ***")
    _ensure_started()
    result = "Unknown action."

    try:
        if action == "go_to":
            result = _bt.run(_bt._go_to(
                parameters.get("url", ""),
                browser_name=browser_name,
                new_tab=new_tab
            ))

        elif action == "search":
            engine = parameters.get("engine", "google").lower()
            if engine == "youtube":
                result = _bt.run(_bt._youtube_search(
                    parameters.get("query", ""),
                    browser_name=browser_name
                ))
            else:
                result = _bt.run(_bt._search(
                    parameters.get("query", ""),
                    engine,
                    browser_name=browser_name,
                    new_tab=new_tab
                ))

        elif action == "youtube_search":
            result = _bt.run(_bt._youtube_search(
                parameters.get("query", ""),
                browser_name=browser_name
            ))

        elif action == "click":
            result = _bt.run(_bt._click(
                selector=parameters.get("selector"),
                text=parameters.get("text"),
                browser_name=browser_name
            ))

        elif action == "type":
            result = _bt.run(_bt._type(
                selector=parameters.get("selector"),
                text=parameters.get("text", ""),
                clear_first=parameters.get("clear_first", True),
                browser_name=browser_name
            ))

        elif action == "scroll":
            result = _bt.run(_bt._scroll(
                direction=parameters.get("direction", "down"),
                amount=parameters.get("amount", 500),
                browser_name=browser_name
            ))

        elif action == "fill_form":
            result = _bt.run(_bt._fill_form(
                parameters.get("fields", {}),
                browser_name=browser_name
            ))

        elif action == "smart_click":
            result = _bt.run(_bt._smart_click(
                parameters.get("description", ""),
                browser_name=browser_name
            ))

        elif action == "smart_type":
            result = _bt.run(_bt._smart_type(
                parameters.get("description", ""),
                parameters.get("text", ""),
                browser_name=browser_name
            ))

        elif action == "get_text":
            result = _bt.run(_bt._get_text(browser_name=browser_name))

        elif action == "press":
            result = _bt.run(_bt._press(
                parameters.get("key", "Enter"),
                browser_name=browser_name
            ))

        elif action == "close":
            result = _bt.run(_bt._close_browser(browser_name=browser_name))

        else:
            result = f"Unknown action: {action}"

    except concurrent.futures.TimeoutError:
        result = "Browser action timed out."
    except Exception as e:
        result = f"Browser error: {e}"

    browser_info = f"[{browser_name}] " if browser_name else ""
    print(f"[Browser] {browser_info}{result[:80]}")
    if player:
        player.write_log(f"[browser] {browser_info}{result[:60]}")

    return result
