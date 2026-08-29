"""
Issue 009 — asking about the open BROWSER PAGE must never touch the browser.

The defect these tests pin down: "какая страница открыта в хроме?" had no
read-only answer, so the model reached for browser_control, which NAVIGATES —
the question itself replaced the user's page with about:blank. The answer now
comes from window captions only.

Run:  python -m pytest tests/test_active_page.py -q
"""

from core.awareness import _inspectors as ins
from core.awareness import _resolver


def _w(title, process, hwnd=1):
    return {"title": title, "process": process, "hwnd": hwnd}


NOTION = _w("Исследование архитектуры проекта | Notion - Google Chrome", "chrome.exe", 11)
GMAIL = _w("(3) Входящие — почта - Google Chrome", "chrome.exe", 12)
DOCS = _w("Смета — Mozilla Firefox", "firefox.exe", 13)
NOTEPAD = _w("test.txt - Notepad", "notepad.exe", 14)
JARVIS = _w("J.A.R.V.I.S — MARK XXXV", "python3.12", 15)


class Windows:
    """Replaces the two OS seams: the window list and the foreground window."""

    def __init__(self, windows, front=None):
        self.windows = list(windows)
        self.front = front
        self.list_calls = 0

    def __enter__(self):
        self._list = ins._list_windows
        self._active = ins.active_window

        def listing():
            self.list_calls += 1
            return [dict(w) for w in self.windows]

        ins._list_windows = listing
        ins.active_window = lambda: (dict(self.front) if self.front else None)
        return self

    def __exit__(self, *exc):
        ins._list_windows = self._list
        ins.active_window = self._active
        return False


# ── the tab name is read out of the caption ──────────────────────────────────

def test_the_browser_name_is_stripped_from_the_caption():
    assert ins.page_title("Notion - Google Chrome") == "Notion"
    assert ins.page_title("Смета — Mozilla Firefox") == "Смета"
    assert ins.page_title("Поиск - Яндекс.Браузер") == "Поиск"
    assert ins.page_title("") is None
    assert ins.page_title(None) is None


def test_an_unread_counter_is_not_part_of_the_page_name():
    assert ins.page_title("(3) Входящие — почта - Google Chrome") == "Входящие — почта"


def test_a_page_name_with_dashes_survives_intact():
    title = ins.page_title("Исследование архитектуры проекта | Notion - Google Chrome")
    assert title == "Исследование архитектуры проекта | Notion"


# ── which window answers ─────────────────────────────────────────────────────

def test_the_foreground_browser_window_is_the_answer():
    with Windows([NOTEPAD, NOTION, GMAIL], front=GMAIL):
        r = ins.active_page()
    assert r["found"] is True
    assert r["active"]["title"] == "Входящие — почта"
    assert r["pages"][0]["title"] == "Входящие — почта"


def test_browser_windows_are_found_even_when_jarvis_is_in_front():
    with Windows([JARVIS, NOTION], front=JARVIS):
        r = ins.active_page()
    assert r["found"] is True
    assert r["pages"][0]["title"] == "Исследование архитектуры проекта | Notion"


def test_a_named_browser_narrows_the_answer():
    with Windows([NOTION, DOCS], front=None):
        only_firefox = ins.active_page("firefox")
        only_chrome = ins.active_page("chrome")
    assert [p["title"] for p in only_firefox["pages"]] == ["Смета"]
    assert [p["title"] for p in only_chrome["pages"]] == [
        "Исследование архитектуры проекта | Notion"
    ]


def test_non_browser_windows_are_never_reported_as_pages():
    with Windows([NOTEPAD, JARVIS], front=NOTEPAD):
        r = ins.active_page()
    assert r["found"] is False
    assert "браузер" in r["reason"].lower()


def test_the_same_page_in_two_windows_is_said_once():
    with Windows([NOTION, dict(NOTION, hwnd=99)], front=None):
        r = ins.active_page()
    assert len(r["pages"]) == 1


def test_a_broken_window_list_is_not_a_crash():
    saved = ins._list_windows
    ins._list_windows = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        r = ins.active_page()
    finally:
        ins._list_windows = saved
    assert isinstance(r, dict)
    assert r["found"] in (True, False)


# ── what gets spoken ─────────────────────────────────────────────────────────

def test_the_spoken_answer_names_the_page_and_the_browser():
    with Windows([NOTION], front=NOTION):
        text = ins.render_page(ins.active_page())
    assert "Открыта страница" in text
    assert "Исследование архитектуры проекта | Notion" in text
    assert "Chrome" in text


def test_other_browser_windows_are_mentioned_after_the_active_one():
    with Windows([NOTION, GMAIL, DOCS], front=NOTION):
        text = ins.render_page(ins.active_page())
    assert text.index("Исследование") < text.index("Входящие")
    assert "Firefox" in text


def test_with_no_browser_open_the_answer_is_the_plain_reason():
    with Windows([NOTEPAD], front=NOTEPAD):
        text = ins.render_page(ins.active_page())
    assert text.startswith("Не вижу открытых окон браузера")


# ── the referent and the read-only guarantee ─────────────────────────────────

def test_page_referents_are_recognised():
    import core.awareness as aw
    for kind in ("active_page", "open_page", "current_tab", "browser_page", "tab"):
        assert kind in _resolver.PAGE_KINDS
        assert aw.is_page_kind(kind) is True
    assert aw.is_page_kind("active_document") is False
    assert aw.is_document_kind("active_page") is False


def test_resolving_a_page_only_reads_windows():
    with Windows([NOTION], front=NOTION) as fake:
        result = _resolver.resolve("active_page")
        text = _resolver.render(result)
    assert fake.list_calls == 1
    assert result["found"] is True
    assert result["type"] == "page"
    assert result["path"] is None
    assert "Notion" in text


def test_the_named_browser_reaches_the_inspector():
    with Windows([NOTION, DOCS], front=None):
        text = _resolver.render(_resolver.resolve("active_page", "firefox"))
    assert "Смета" in text
    assert "Notion" not in text


def test_an_exploding_inspector_never_breaks_the_turn():
    saved = ins.active_page
    ins.active_page = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        result = _resolver.resolve("active_page")
        text = _resolver.render(result)
    finally:
        ins.active_page = saved
    assert result["found"] is False
    assert "boom" in text


def test_the_page_branch_calls_no_browser_tool():
    """The whole point: answering must not import or drive browser automation."""
    import inspect
    source = inspect.getsource(ins.active_page) + inspect.getsource(_resolver._resolve_active_page)
    for forbidden in ("browser_control", "native_browser", "playwright", "go_to", "navigate"):
        assert forbidden not in source


# ── popups, bubbles and dialogs are not pages ────────────────────────────

BUBBLE = {
    "title": "Перевести эту страницу?",
    "process": "chrome.exe",
    "hwnd": 21,
    "owner": 11,
}
PANEL = {
    "title": "Загрузки",
    "process": "chrome.exe",
    "hwnd": 22,
    "toolwindow": True,
}


def test_an_owned_window_is_not_a_real_window():
    assert ins.is_real_window(NOTION) is True
    assert ins.is_real_window(BUBBLE) is False
    assert ins.is_real_window(PANEL) is False
    assert ins.is_real_window(None) is False


def test_the_translate_bubble_is_never_reported_as_the_page():
    """The exact defect: Chrome's translate bubble was answered as a page."""
    with Windows([NOTION, BUBBLE, GMAIL], front=BUBBLE):
        result = ins.active_page("chrome")
    titles = [p["title"] for p in result["pages"]]
    assert "Перевести эту страницу?" not in titles
    assert titles[0] == "Исследование архитектуры проекта | Notion"


def test_a_bubble_in_front_points_at_the_window_it_belongs_to():
    """The bubble is owned by hwnd 11, so hwnd 11 is the page being asked about."""
    with Windows([NOTION, BUBBLE, GMAIL], front=BUBBLE):
        result = ins.active_page("chrome")
    assert result["active"] is not None
    assert result["active"]["hwnd"] == 11


def test_a_floating_panel_is_not_a_page_either():
    with Windows([NOTION, PANEL], front=NOTION):
        result = ins.active_page("chrome")
    assert [p["title"] for p in result["pages"]] == [
        "Исследование архитектуры проекта | Notion"
    ]


def test_with_only_a_bubble_open_the_answer_is_honest():
    with Windows([BUBBLE], front=BUBBLE):
        result = ins.active_page("chrome")
    assert result["found"] is False
    assert "не вижу" in result["reason"].lower()


def test_the_owner_walk_stops_on_a_cycle():
    a = {"title": "a", "hwnd": 1, "owner": 2}
    b = {"title": "b", "hwnd": 2, "owner": 1}
    table = {1: a, 2: b}
    assert ins.pick_real_window(a, lambda h: table.get(h)) in (a, b)


def test_the_owner_walk_returns_the_real_parent():
    dialog = {"title": "Открытие", "process": "notepad.exe", "hwnd": 31, "owner": 14}
    table = {14: NOTEPAD}
    assert ins.pick_real_window(dialog, lambda h: table.get(h))["title"] == "test.txt - Notepad"


def test_a_broken_owner_lookup_keeps_the_window_we_have():
    dialog = {"title": "Открытие", "process": "notepad.exe", "hwnd": 31, "owner": 99}

    def boom(_h):
        raise RuntimeError("boom")

    assert ins.pick_real_window(dialog, boom)["hwnd"] == 31


def test_windows_without_the_flags_are_still_pages():
    """Old callers pass plain dicts; missing flags must not hide real windows."""
    with Windows([NOTION, DOCS], front=NOTION):
        result = ins.active_page("")
    assert len(result["pages"]) == 2
