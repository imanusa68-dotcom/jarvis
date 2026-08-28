# tests/test_perception_addressing.py
# Дефект 23 — как пользователь называет окно.
#
# Живой случай: «а в проводнике что» → ответ про Chrome. Слово «проводник»
# потерялось ещё до слоя восприятия. Здесь закреплены три свойства:
#   1. слой сам читает фразу и находит цель, даже если модель ошиблась;
#   2. окно можно назвать ролью («проводник», «браузер»), а не только именем;
#   3. вопрос без цели («а щас?») по-прежнему про передний план.
#
# Всё подаётся фикстурами: тесты не читают настоящий экран.

from core.awareness import _perception as pc
from core.awareness import _inspectors as ins
from core.awareness import _resolver


def _w(title, process="unknown.exe", hwnd=1000, **extra):
    window = {"title": title, "process": process, "hwnd": hwnd,
              "owner": 0, "toolwindow": False}
    window.update(extra)
    return window


class Sources:
    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        pc.reset()
        defaults = {
            "active_window": lambda: None,
            "list_windows": lambda: [],
            "active_document": lambda deadline_s=None: ins.new_result(),
            "recent_candidates": lambda name, process="": [],
            "path_exists": lambda path: False,
            "is_own_window": lambda title, process="", hwnd=0: False,
            "snapshot": lambda: {},
        }
        defaults.update(self.overrides)
        pc.set_sources(**defaults)
        return pc

    def __exit__(self, *exc):
        pc.reset()
        return False


# Каптин проводника БЕЗ слова «проводник» — именно так это выглядит
# на английской Windows. Совпадение по строке здесь не сработает.
EXPLORER = _w("cloudflared-old", process="explorer.exe", hwnd=21)
CHROME = _w("Исследование архитектуры | Notion - Google Chrome",
                process="chrome.exe", hwnd=22)
HAPP = _w("Happ 3.3.6 (591)", process="Happ.exe", hwnd=23)
TERMINAL = _w(r"C:\Windows\System32\cmd.exe - python -m pytest -q",
              process="cmd.exe", hwnd=24)

DESK = [EXPLORER, CHROME, HAPP, TERMINAL]


# ── 1. Слова пользователя читаются в самом слое ───────────────────────────

def test_the_layer_reads_the_phrase_itself_when_the_model_lost_the_word():
    """Живой дефект 23: kind=active, имени нет, а спросили про проводник."""
    with Sources(active_window=lambda: CHROME, list_windows=lambda: DESK):
        result = _resolver.describe_kind("active", "", "а в проводнике что")
        spoken = _resolver.render(result)
    assert "cloudflared-old" in spoken, spoken
    assert "Notion" not in spoken, spoken


def test_a_question_without_a_target_still_means_the_foreground():
    with Sources(active_window=lambda: CHROME, list_windows=lambda: DESK):
        result = _resolver.describe_kind("active", "", "а щас?")
        spoken = _resolver.render(result)
    assert "Notion" in spoken, spoken


def test_an_explicit_name_always_beats_the_phrase():
    with Sources(active_window=lambda: CHROME, list_windows=lambda: DESK):
        result = _resolver.describe_kind("named_window", "happ", "а в проводнике что")
        spoken = _resolver.render(result)
    assert "Happ" in spoken, spoken


def test_the_phrase_can_also_ask_for_every_window():
    with Sources(active_window=lambda: CHROME, list_windows=lambda: DESK):
        result = _resolver.describe_kind("active", "", "а какие окна у меня вообще")
        spoken = _resolver.render(result)
    assert "cloudflared-old" in spoken and "Happ" in spoken, spoken


# ── 2. Роль вместо имени ──────────────────────────────────────────────

def test_explorer_is_found_by_role_even_with_an_english_caption():
    """В заголовке нет ни «проводник», ни «explorer» — только имя папки."""
    with Sources(list_windows=lambda: DESK):
        subject = pc.describe("window", "проводнике")
    assert subject["found"], subject["reason"]
    assert subject["window"]["title"] == "cloudflared-old"


def test_the_browser_answers_to_the_word_browser():
    with Sources(list_windows=lambda: DESK):
        subject = pc.describe("window", "браузере")
    assert subject["window"]["title"].endswith("Google Chrome")


def test_the_terminal_answers_to_the_word_terminal():
    with Sources(list_windows=lambda: DESK):
        subject = pc.describe("window", "терминале")
    assert subject["found"], subject["reason"]
    assert subject["surface"] == "terminal", subject["surface"]
    assert subject["window"]["process"] == "cmd"


def test_a_real_name_wins_over_a_role_word():
    """Окно, которое так и зовётся, важнее окна с такой ролью."""
    named = _w("Проводник Pro", process="provodnik.exe", hwnd=31)
    with Sources(list_windows=lambda: DESK + [named]):
        subject = pc.describe("window", "проводник")
    assert subject["window"]["title"] == "Проводник Pro"


def test_a_role_nobody_is_playing_is_admitted_not_guessed():
    """Если терминала нет — нельзя тихо ответить про переднее окно."""
    with Sources(active_window=lambda: CHROME,
                 list_windows=lambda: [EXPLORER, CHROME]):
        subject = pc.describe("window", "терминале")
    assert not subject["found"]
    assert "терминале" in subject["reason"], subject["reason"]


def test_content_words_address_a_window_too():
    archive = _w("Turnstile-Solver-main.zip", process="7zFM.exe", hwnd=41)
    with Sources(list_windows=lambda: DESK + [archive]):
        subject = pc.describe("window", "архив")
    assert subject["window"]["title"] == "Turnstile-Solver-main.zip"


# ── 3. Разбор фразы отдельно ───────────────────────────────────────

def test_interpretation_table():
    cases = [
        ("а в проводнике что", "window", "проводнике"),
        ("а в браузере?", "window", "браузере"),
        ("что в терминале", "window", "терминале"),
        ("а щас?", "foreground", ""),
        ("что сейчас активно", "foreground", ""),
        ("", "foreground", ""),
        ("какие окна у меня", "all", ""),
        ("нет, а happ", "window", "happ"),
    ]
    with Sources(list_windows=lambda: DESK):
        for text, target, hint in cases:
            got = pc.interpret(text)
            assert got["target"] == target, (text, got)
            assert got["hint"] == hint, (text, got)


def test_question_words_are_never_taken_for_a_window_name():
    trap = _w("Что делать.docx - Word", process="WINWORD.EXE", hwnd=51)
    with Sources(active_window=lambda: CHROME, list_windows=lambda: DESK + [trap]):
        got = pc.interpret("что сейчас открыто")
    assert got["target"] == "foreground", got


def test_reading_a_phrase_never_touches_the_screen_twice_per_word():
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return DESK

    with Sources(list_windows=counted):
        pc.interpret("а в проводнике что")
    assert calls["n"] == 0, "роль узнаётся без обхода окон"


def test_a_broken_phrase_never_raises():
    for junk in [None, "", "?????", "\x00\x01", "a" * 5000, "———"]:
        with Sources(list_windows=lambda: DESK):
            got = pc.interpret(junk)
        assert got["target"] in ("foreground", "window", "all")
