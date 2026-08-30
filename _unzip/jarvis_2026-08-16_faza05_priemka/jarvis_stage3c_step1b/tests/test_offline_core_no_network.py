# -*- coding: utf-8 -*-
"""
Оффлайн-ядро: ни сети, ни модели, ни второй двери (фаза 0.7, шаг 1).

Проверяется ровно то, что можно проверить без интернета и без ключа:
  1. ядро ввозится при заминированной сети и не тянет за собой ни одного
     модуля действий — иначе оно утащило бы сетевые библиотеки;
  2. каждое действие идёт через core.gate.dispatch и только через него;
  3. отказ двери означает, что инструмент не был ввезён вообще;
  4. заметка создаёт новый файл и никогда не перезаписывает чужой;
  5. время, статус и квота не трогают инструменты вовсе;
  6. обычная человеческая фраза остаётся модели — ядро молчит;
  7. напоминание без часа спрашивает час, а не выдумывает его;
  8. handle не падает ни на каком мусоре и не проглатывает поломку молча.

Run standalone: python tests/test_offline_core_no_network.py
"""
import importlib
import socket
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

    def __init__(self, verdict="run"):
        self.verdict = verdict
        self.calls = []

    def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
        self.calls.append({"tool": tool, "params": dict(params or {}),
                           "mode": mode, "screen_control": screen_control})
        return _Verdict(verdict=self.verdict, tool=tool,
                        action=(params or {}).get("action"))


class _Spy:
    """Подставной инструмент: считает вызовы и никогда не трогает диск."""

    def __init__(self, answer="сделано", boom=False):
        self.answer = answer
        self.boom = boom
        self.calls = []

    def __call__(self, parameters=None, **kw):
        self.calls.append(dict(parameters or {}))
        if self.boom:
            raise RuntimeError("диск отвалился")
        return self.answer


class _Rig:
    """Подменяет дверь и ленивый ввоз инструментов, затем всё возвращает."""

    def __init__(self, verdict="run", answer="сделано", boom=False):
        self.door = _Door(verdict)
        self.tool = _Spy(answer, boom)

    def __enter__(self):
        self._saved_dispatch = gate.dispatch
        self._saved_import = oc._import_tool
        gate.dispatch = self.door
        oc._import_tool = lambda tool: self.tool
        return self

    def __exit__(self, *exc):
        gate.dispatch = self._saved_dispatch
        oc._import_tool = self._saved_import
        return False


# ═════════════════════════════════════════════════════════════════════
# 1. НИ СЕТИ, НИ ЛИШНИХ ВВОЗОВ
# ═════════════════════════════════════════════════════════════════════

def test_the_core_is_imported_with_the_network_mined():
    mines = ("socket", "create_connection", "getaddrinfo")
    saved = {name: getattr(socket, name) for name in mines}
    before = {n for n in sys.modules if n.startswith("actions")}
    original = sys.modules.get("core.offline_core")

    def boom(*a, **k):
        raise AssertionError("оффлайн-ядро полезло в сеть")

    try:
        for name in mines:
            setattr(socket, name, boom)
        sys.modules.pop("core.offline_core", None)
        fresh = importlib.import_module("core.offline_core")
        assert callable(fresh.handle)
        after = {n for n in sys.modules if n.startswith("actions")}
        assert after == before, f"ввоз ядра притащил модули действий: {after - before}"
    finally:
        for name, value in saved.items():
            setattr(socket, name, value)
        if original is not None:
            sys.modules["core.offline_core"] = original


def test_no_action_module_is_imported_at_the_top():
    offenders = []
    for number, line in enumerate(SOURCE.splitlines(), start=1):
        if line[:1].strip() == "" and line.strip():
            continue                      # ввоз с отступом — это ленивый ввоз
        text = line.strip()
        for needle in ("import actions", "from actions", "import main", "from main"):
            if text.startswith(needle):
                offenders.append(f"{number}: {text}")
    assert not offenders, f"наверху появился тяжёлый ввоз: {offenders}"


def test_the_core_has_no_door_to_a_model_or_to_the_network():
    for forbidden in ("aux_call", "cheap_call", "genai", "generativeai",
                      "generate_content", "requests", "urllib", "socket",
                      "websocket", "print("):
        assert forbidden not in SOURCE, f"в оффлайн-ядре появилась дверь наружу: {forbidden}"


# ═════════════════════════════════════════════════════════════════════
# 2. ОДНА ДВЕРЬ
# ═════════════════════════════════════════════════════════════════════

def test_every_action_goes_through_the_one_door():
    cases = [
        ("напомни через 20 минут выпить воды", "reminder", "set"),
        ("покажи мои напоминания", "reminder", "list"),
        ("отмени напоминание про воду", "reminder", "cancel"),
        ("открой блокнот", "open_app", None),
        ("найди файл отчет", "file_controller", "find"),
        ("запиши заметку: купить хлеб", "file_controller", "create_file"),
        ("отмени последнее действие", "file_controller", "undo"),
        ("повтори действие", "file_controller", "redo"),
    ]
    for phrase, tool, action in cases:
        with _Rig() as rig:
            reply = oc.handle(phrase)
            assert reply is not None, f"«{phrase}»: ядро промолчало"
            assert len(rig.door.calls) == 1, f"«{phrase}»: дверь не одна"
            call = rig.door.calls[0]
            assert call["tool"] == tool, f"«{phrase}»: ушло в {call['tool']}"
            assert call["mode"] == "interactive"
            assert call["screen_control"] is False
            if action is not None:
                assert call["params"].get("action") == action, f"«{phrase}»: {call['params']}"
            assert len(rig.tool.calls) == 1, f"«{phrase}»: инструмент не отработал"
            assert reply.text


def test_a_refused_verdict_never_imports_the_tool():
    for verdict in ("confirm", "blocked", "screen_off"):
        with _Rig(verdict=verdict) as rig:
            reply = oc.handle("запиши заметку: секрет")
            assert reply is not None and reply.ok is False
            assert reply.verdict == verdict
            assert rig.tool.calls == [], f"{verdict}: инструмент всё-таки отработал"
            assert "написанный для модели" not in reply.text, "владельцу показали текст для модели"
            assert reply.text.strip()


def test_time_status_and_quota_never_touch_a_tool():
    for phrase in ("сколько времени", "статус", "сколько осталось квоты",
                   "what time is it"):
        with _Rig() as rig:
            reply = oc.handle(phrase)
            assert reply is not None, f"«{phrase}»: ядро промолчало"
            assert rig.door.calls == [], f"«{phrase}»: зачем-то дёрнули дверь"
            assert rig.tool.calls == []
            assert reply.text.strip()


# ═════════════════════════════════════════════════════════════════════
# 3. ЧЕСТНОСТЬ И ГРАНИЦЫ
# ═════════════════════════════════════════════════════════════════════

def test_a_note_creates_a_new_file_and_never_overwrites():
    with _Rig() as rig:
        oc.handle("запиши заметку: купить хлеб и молоко")
        params = rig.door.calls[0]["params"]
        assert params["action"] == "create_file", "заметка пошла перезаписью"
        assert params["action"] != "write"
        assert "купить хлеб и молоко" in params["content"]
        assert params["name"].endswith(".txt")


def test_a_reminder_without_a_time_asks_instead_of_inventing():
    with _Rig() as rig:
        reply = oc.handle("напомни купить хлеб")
        assert reply is not None
        assert rig.door.calls == [], "час выдуман и напоминание уже поставлено"
        assert "время" in reply.text.lower()


def test_the_reminder_time_is_computed_locally():
    from core.time_utils import get_now
    with _Rig() as rig:
        oc.handle("напомни через 20 минут выпить воды")
        params = rig.door.calls[0]["params"]
        expected = get_now()
        assert params["date"]
        assert len(params["time"]) == 5 and ":" in params["time"]
        assert "выпить воды" in params["message"]
        hours, minutes = (int(x) for x in params["time"].split(":"))
        shift = (hours * 60 + minutes) - (expected.hour * 60 + expected.minute)
        assert shift % (24 * 60) in (19, 20, 21), f"сдвиг вышел {shift} минут"


def test_an_ordinary_sentence_is_left_to_the_model():
    for phrase in (
        "открой мне глаза на правду",
        "переведи фразу на английский",
        "расскажи про историю рима",
        "что такое рекурсия простыми словами",
        "сделай скриншот экрана",
        "какая погода в москве",
        "напиши письмо другу",
        "включи музыку погромче",
        "посчитай два плюс два",
        "",
        "   ",
        "а" * 500,
    ):
        with _Rig() as rig:
            assert oc.handle(phrase) is None, f"перехвачена чужая фраза: «{phrase[:40]}»"
            assert rig.door.calls == []


def test_the_answer_speaks_the_language_of_the_command():
    def cyrillic(text):
        return any("а" <= ch.lower() <= "я" for ch in text)

    with _Rig():
        assert cyrillic(oc.handle("сколько времени").text)
        assert not cyrillic(oc.handle("what time is it now").text)


def test_handle_never_raises_and_never_swallows_a_failure():
    for junk in (None, 12, "", "   ", "?!;", "\n\n", "🙂", "x" * 5000):
        result = oc.handle(junk)
        assert result is None or isinstance(result, oc.Reply)

    with _Rig(boom=True) as rig:
        reply = oc.handle("открой блокнот")
        assert reply is not None and reply.ok is False
        assert "диск отвалился" in reply.text, "поломка инструмента проглочена молча"
        assert len(rig.door.calls) == 1


def test_opening_is_split_between_an_app_and_a_path():
    with _Rig() as rig:
        oc.handle("открой блокнот")
        assert rig.door.calls[0]["tool"] == "open_app"
        assert rig.door.calls[0]["params"]["app_name"] == "блокнот"

    with _Rig() as rig:
        oc.handle("открой C:\\temp\\otchet.txt")
        assert rig.door.calls[0]["tool"] == "open_path"
        assert "otchet.txt" in rig.door.calls[0]["params"]["path"]


def test_folder_words_use_the_one_resolver_of_the_project():
    # Своей таблицы папок у ядра нет: настоящий путь (в том числе перенесённый
    # в OneDrive) знает только core.awareness._known_folders.
    assert "_known_folders" in SOURCE, "ядро перестало пользоваться общим резолвером"
    for invented in ("Path.home()", "Desktop", "Downloads", "Documents"):
        assert invented not in SOURCE, f"ядро само выдумывает путь папки: {invented}"
    with _Rig() as rig:
        reply = oc.handle("открой загрузки")
        assert reply is not None, "«открой загрузки» осталось без ответа"
        assert reply.ok, reply.text
        assert rig.door.calls[0]["tool"] == "open_path"
        path = rig.door.calls[0]["params"]["path"]
        assert Path(path).is_absolute(), f"путь папки не настоящий: {path}"
        assert path.lower().endswith("downloads"), path


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
