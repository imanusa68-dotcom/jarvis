# -*- coding: utf-8 -*-
"""
Живая речь без сети: ядро понимает владельца, а не заученную строчку.

Зачем этот файл. Ядро обещает в отказе семь умений, но проверялось оно ровно
теми формулировками, которыми было написано. Владелец сказал «сколько время» —
и получил отказ, где первым пунктом значится «время и дата». Это не промах
разбора, это нарушенное обещание, и оно дороже любой новой возможности.

Что здесь проверяется:
  1. у каждого пункта меню есть пример, и пример действительно срабатывает —
     меню больше не может обещать то, чего ядро не умеет;
  2. живой корпус: те же умения обычными человеческими словами;
  3. анти-корпус: обычная человеческая речь остаётся модели, и расширение
     разбора ничего у неё не украло;
  4. заглавные буквы имени доезжают до инструмента нетронутыми;
  5. язык ответа решает исходная фраза, а не причёсанная копия;
  6. голое «отмени» переспрашивает, а не угадывает;
  7. порядок веток пережил расширение: узкое всё ещё раньше широкого;
  8. в исходнике ядра не появилось запрещённых слов.

Ни один тест не ходит в сеть и не трогает диск: дверь и инструменты подставные.

Run standalone: python tests/test_offline_speech_step26.py
"""
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.gate as gate  # noqa: E402
import core.offline_core as oc  # noqa: E402

SOURCE = (ROOT / "core" / "offline_core.py").read_text(encoding="utf-8")


@dataclass
class _Verdict:
    """Ответ подставной двери — той же формы, что и настоящий GateResult."""

    verdict: str = "run"
    tool: str = ""
    action: object = None
    risk: str = "low"
    policy: str = "auto"
    mode: str = "interactive"
    message: str = "текст, написанный для модели"
    reason: str = "проверка"

    @property
    def allowed(self):
        return self.verdict == "run"


class _Door:
    """Подставная дверь: записывает всё, что через неё прошло."""

    def __init__(self):
        self.calls = []

    def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
        self.calls.append({"tool": tool, "params": dict(params or {})})
        return _Verdict(verdict="run", tool=tool, action=(params or {}).get("action"))


class _Spy:
    """Подставной инструмент: считает вызовы и никогда не трогает диск."""

    def __init__(self):
        self.calls = []

    def __call__(self, parameters=None, **kw):
        self.calls.append(dict(parameters or {}))
        return "сделано"


class _FakeJournal:
    """Подставной журнал: ветка «что ты делал» не должна открывать базу."""

    def __init__(self, store=None):
        pass

    def recent_actions(self, limit=None):
        return [{"ok": True, "summary": "проверочная запись"}]


class _Rig:
    """Подменяет дверь и ввоз инструментов на время одной проверки."""

    def __enter__(self):
        self.door = _Door()
        self.spy = _Spy()
        self._real_dispatch = gate.dispatch
        self._real_import = oc._import_tool
        gate.dispatch = self.door
        oc._import_tool = lambda tool: self.spy
        # Журнал — единственная ветка, которая ходит в базу, а не в дверь.
        # Подменяем и её: тест обязан быть быстрым и не трогать настоящий диск.
        self._real_journal = None
        try:
            import core.journal as journal_mod
            import core.store as store_mod
            self._real_journal = (journal_mod, journal_mod.Journal,
                                  store_mod, store_mod.open_store)
            journal_mod.Journal = _FakeJournal
            store_mod.open_store = lambda *a, **kw: None
        except Exception:
            self._real_journal = None
        return self

    def __exit__(self, *exc):
        gate.dispatch = self._real_dispatch
        oc._import_tool = self._real_import
        if self._real_journal:
            journal_mod, journal_cls, store_mod, opener = self._real_journal
            journal_mod.Journal = journal_cls
            store_mod.open_store = opener
        return False

    def last(self):
        return self.door.calls[-1] if self.door.calls else None


# ── 1. меню под присмотром ──────────────────────────────────────────────────

def test_every_menu_item_has_an_example_that_really_works():
    """Пункт меню без работающего примера — это обещание, которого нет."""
    assert oc._SKILLS, "таблица умений пуста"
    for name, example in oc._SKILLS:
        with _Rig():
            reply = oc.handle(example)
        assert reply is not None, (
            "пункт меню «%s» обещан, но пример «%s» ядро не опознало" % (name, example))


def test_the_menu_is_built_from_the_same_table_it_promises():
    """Меню и умения не могут разойтись: строка собирается из таблицы."""
    expected = " · ".join(name for name, _ in oc._SKILLS)
    assert oc._MENU[0] == expected, "меню разошлось с таблицей умений"
    assert "напоминания" in oc._MENU[0], "слово, которое ищут сторож и тест, исчезло"


def test_the_refusal_still_names_the_skills_and_promises_nothing():
    """Отказ обязан перечислить умения и не обещать «потом сделаю»."""
    line = oc.offline_notice("расскажи что нибудь интересное про космос")
    assert "напоминания" in line, "в отказе пропало слово «напоминания»"
    for lie in ("очеред", "позже", "потом", "как только", "queue", "later"):
        assert lie not in line.lower(), "отказ пообещал будущее: «%s»" % lie


# ── 2. живой корпус ─────────────────────────────────────────────────────────

_LIVING = (
    # фраза                                    инструмент         действие
    ("сколько время", "", ""),
    ("сколько времени", "", ""),
    ("сколько там времени", "", ""),
    ("время?", "", ""),
    ("который час", "", ""),
    ("джарвис, сколько время", "", ""),
    ("какое сегодня число", "", ""),
    ("напомни через 20 минут выпить воды", "reminder", "set"),
    ("через 10 минут напомни выключить чайник", "reminder", "set"),
    ("напомнить завтра в 9:00 позвонить в банк", "reminder", "set"),
    ("поставь напоминание через час про плиту", "reminder", "set"),
    ("покажи мои напоминания", "reminder", "list"),
    ("какие у меня напоминания", "reminder", "list"),
    ("отмени напоминание про воду", "reminder", "cancel"),
    ("а отмени напоминание про воду", "reminder", "cancel"),
    ("слушай, открой блокнот", "open_app", None),
    ("а открой загрузки", "open_path", None),
    ("открой папку документы", "open_path", None),
    ("запиши заметку купить хлеб", "file_controller", "create_file"),
    ("заметку сделай: позвонить маме", "file_controller", "create_file"),
    ("запиши в заметки идею про робота", "file_controller", "create_file"),
    ("найди файл отчет", "file_controller", "find"),
    ("найди отчет", "file_controller", "find"),
    ("найди мне смету", "file_controller", "find"),
    ("где лежит смета", "file_controller", "find"),
    ("отмени последнее действие", "file_controller", "undo"),
    ("верни как было", "file_controller", "undo"),
    ("повтори последнее действие", "file_controller", "redo"),
)


def test_the_living_corpus_is_understood_and_goes_where_promised():
    """Обычные слова владельца доходят до нужного инструмента."""
    for phrase, tool, action in _LIVING:
        with _Rig() as rig:
            reply = oc.handle(phrase)
            call = rig.last()
        assert reply is not None, "фраза «%s» не опознана" % phrase
        if not tool:
            assert call is None, "фраза «%s» зря постучалась в дверь" % phrase
            continue
        assert call is not None, "фраза «%s» не дошла до двери" % phrase
        assert call["tool"] == tool, (
            "фраза «%s» ушла в «%s», а ждали «%s»" % (phrase, call["tool"], tool))
        if action is not None:
            got = call["params"].get("action")
            assert got == action, (
                "фраза «%s»: действие «%s», а ждали «%s»" % (phrase, got, action))


def test_a_reminder_keeps_its_text_when_the_time_comes_first():
    """«через 10 минут напомни выключить чайник» — в записке только дело."""
    with _Rig() as rig:
        oc.handle("через 10 минут напомни выключить чайник")
        call = rig.last()
    message = call["params"].get("message", "")
    assert "чайник" in message, "из напоминания пропало дело: «%s»" % message
    assert "напомни" not in message, "в записку затесался сам глагол: «%s»" % message
    assert "10" not in message, "в записку затесалось время: «%s»" % message


# ── 3. анти-корпус ──────────────────────────────────────────────────────────

_NOT_FOR_US = (
    # то, что было в защите раньше
    "открой мне глаза на правду",
    "переведи фразу на английский",
    "расскажи про историю рима",
    "что такое рекурсия простыми словами",
    "сделай скриншот экрана",
    "какая погода в москве",
    "напиши письмо другу",
    "включи музыку погромче",
    "посчитай два плюс два",
    # новое: расширенный разбор обязан пройти мимо этого
    "��ачем ты открыл блокнот",
    "а во сколько ты вчера открыл этот файл",
    "почему напоминание не сработало",
    "расскажи как работают напоминания",
    "я вчера отменил встречу с врачом",
    "что такое квота",
    "ты умеешь открывать программы",
    "найди мне место в этой жизни",
    "объясни как работает поиск файлов",
    "",
    "   ",
    "а" * 500,
)


def test_the_anti_corpus_is_left_to_the_model():
    """Разговор — не команда. Ядро молчит и не мешает модели."""
    for phrase in _NOT_FOR_US:
        with _Rig() as rig:
            reply = oc.handle(phrase)
            call = rig.last()
        assert reply is None, "ядро перехватило разговорную фразу «%s»" % phrase
        assert call is None, "ядро полезло в дверь из-за фразы «%s»" % phrase


# ── 4. имя доезжает целым ───────────────────────────────────────────────────

def test_a_capital_letter_survives_the_trip_to_the_tool():
    """Причёсывание фразы не смеет испортить имя файла или программы."""
    with _Rig() as rig:
        oc.handle("слушай, открой Блокнот")
        call = rig.last()
    assert call["params"].get("app_name") == "Блокнот", (
        "имя пришло искажённым: %r" % call["params"])

    with _Rig() as rig:
        oc.handle("открой Отчёт 2026.docx")
        call = rig.last()
    got = call["params"].get("app_name") or call["params"].get("path")
    assert got == "Отчёт 2026.docx", "имя пришло искажённым: %r" % got


def test_a_note_keeps_the_words_of_the_owner():
    """Заметка записывается словами владельца, а не причёсанной копией."""
    with _Rig() as rig:
        oc.handle("Джарвис, запиши заметку: Купить Хлеб")
        call = rig.last()
    assert call["params"].get("content") == "Купить Хлеб", (
        "текст заметки испорчен: %r" % call["params"].get("content"))


# ── 5. язык ─────────────────────────────────────────────────────────────────

def test_the_language_is_decided_by_the_original_phrase():
    """Срезали «джарвис» — но ответ обязан остаться русским."""
    reply = oc.handle("джарвис, status")
    assert reply is not None, "«джарвис, status» не опознан"
    assert any("а" <= ch <= "я" for ch in reply.text.lower()), (
        "ответ ушёл на чужом языке: %s" % reply.text)


def test_an_english_phrase_is_still_answered_in_english():
    reply = oc.handle("what time is it now")
    assert reply is not None, "английская фраза перестала опознаваться"
    assert not any("а" <= ch <= "я" for ch in reply.text.lower()), (
        "английской фразе ответили по-русски: %s" % reply.text)


# ── 6. голое «отмени» ───────────────────────────────────────────────────────

def test_a_bare_cancel_asks_which_one_instead_of_guessing():
    """«отмени» без продолжения — переспрос, а не выстрел наугад."""
    for phrase in ("отмени", "отмена", "Отмени."):
        with _Rig() as rig:
            reply = oc.handle(phrase)
            call = rig.last()
        assert reply is not None, "голое «%s» осталось без ответа" % phrase
        assert call is None, "голое «%s» что-то сделало: %r" % (phrase, call)
        assert "?" in reply.text, "на «%s» ответили не вопросом: %s" % (phrase, reply.text)


# ── 7. порядок веток ────────────────────────────────────────────────────────

def test_narrow_branches_still_win_over_wide_ones():
    """«отмени напоминание» обязано пройти раньше «отмени действие»."""
    pairs = (
        ("отмени напоминание про воду", "reminder", "cancel"),
        ("отмени последнее действие", "file_controller", "undo"),
        ("покажи мои напоминания", "reminder", "list"),
        ("напомни через 5 минут про суп", "reminder", "set"),
    )
    for phrase, tool, action in pairs:
        with _Rig() as rig:
            oc.handle(phrase)
            call = rig.last()
        assert call is not None, "«%s» не дошла до двери" % phrase
        assert (call["tool"], call["params"].get("action")) == (tool, action), (
            "«%s» ушла в %s/%s" % (phrase, call["tool"], call["params"].get("action")))


# ── 8. исходник остался чистым ──────────────────────────────────────────────

def test_the_new_code_added_no_forbidden_word_to_the_source():
    """Расширение разбора не смеет протащить в ядро дверь наружу или печать."""
    for word in ("Path.home()", "Desktop", "Downloads", "Documents", "print(",
                 "requests", "urllib", "socket", "genai", "generativeai",
                 "generate_content", "aux_call", "cheap_call"):
        assert word not in SOURCE, "в ядре появилось запрещённое: «%s»" % word


def test_junk_still_never_raises():
    """Мусор на входе не роняет ядро и не будит инструменты."""
    for junk in (None, 12, "", "   ", "?!;", "x" * 5000, ["открой"], {"a": 1}):
        with _Rig():
            oc.handle(junk)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print("  PASS  " + fn.__name__)
        except AssertionError as e:
            print("  FAIL  " + fn.__name__ + ": " + str(e))
        except Exception as e:
            print("  ERROR " + fn.__name__ + ": " + type(e).__name__ + ": " + str(e))
    total = len(tests)
    print("RESULT: %d/%d %s" % (passed, total, "ALL PASS" if passed == total else "RED"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(_run())
