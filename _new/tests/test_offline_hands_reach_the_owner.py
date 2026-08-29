# -*- coding: utf-8 -*-
"""Шаг 2 фазы 0.7 — руки доходят до владельца.

Живой случай, с которого начался шаг: сессия не поднялась, владелец набрал
«открой загрузки» и получил «SYS: Not connected — text command queued.». Очереди
не было никогда: фраза просто умирала. Обещание, которое никто не собирался
выполнять, хуже честного отказа: владелец ждёт.

Здесь же сторожится вторая тихая потеря: напоминание, срок которого наступил
без сессии. speak() в этот момент молча выбрасывает фразу, а в файле
напоминание уже помечено сработавшим — второго шанса нет.

И третье: метка пояса. У пояса Windows имени обычно нет, и старый код печатал
смещение дважды: «(UTC+03:00, UTC+03:00)».

Главный файл здесь НЕ ввозится: import main тянет pyaudio, tkinter и SDK, а этот
тест обязан работать везде. Методы вырезаются из исходника по AST и
исполняются над двойником — то есть проверяется настоящее поведение живого
кода, а не его пересказ в тесте.

Runner-style (pytest-free): module-level test_* + _run().
"""
import ast
import sys
import types
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
CORE_SRC = (ROOT / "core/offline_core.py").read_text(encoding="utf-8")

LIE = "text command queued"
WANTED = ("_on_text_command", "_answer_offline",
          "_reminder_checker_loop", "_deliver_reminder", "_say_local")


class _QuietThreading:
    """
    Тихий поток для стенда, которому голос неинтересен.

    Шаг 29 научил _answer_offline и _deliver_reminder звать свой голос. Здесь
    проверяется маршрут фразы, а не звук, поэтому поток заводится и не
    стартует: голосу посвящён отдельный файл test_voice_step29.py.
    """

    class Thread:
        def __init__(self, target=None, name=None, daemon=None,
                     args=(), kwargs=None):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            return None


# ───── стенд ─────

class _FakeAsyncio:
    """Подмена asyncio: запоминает, что ушло в живую сессию."""

    def __init__(self):
        self.calls = []

    def run_coroutine_threadsafe(self, coro, loop):
        self.calls.append((coro, loop))
        return None


class _Ui:
    def __init__(self):
        self.log = []

    def write_log(self, text):
        self.log.append(text)


class _Sm:
    def __init__(self, writable):
        self._writable = writable

    def is_writable(self):
        return self._writable


class _Rig:
    """Двойник JarvisLive: только то, чего касается эта врезка."""

    def __init__(self, online):
        self.ui = _Ui()
        self._loop = object() if online else None
        self._sm = _Sm(online)
        self.sent = []
        self.spoken = []

    def _safe_send_text(self, text):
        self.sent.append(text)
        return "coro:" + text

    def speak(self, text):
        self.spoken.append(text)


def _rig(online=False):
    """Собрать двойника с живыми методами из main.py."""
    tree = ast.parse(MAIN_SRC)
    cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "JarvisLive":
            cls = node
            break
    assert cls is not None, "в main.py нет класса JarvisLive"
    picked = [n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    missing = set(WANTED) - {n.name for n in picked}
    assert not missing, "в main.py нет методов: %s" % ", ".join(sorted(missing))
    module = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(module)
    loop_spy = _FakeAsyncio()
    space = {"asyncio": loop_spy, "threading": _QuietThreading,
             "print": lambda *a, **k: None}
    exec(compile(module, "main.py<extract>", "exec"), space)  # noqa: S102
    rig = _Rig(online)
    for name in WANTED:
        setattr(rig, name, types.MethodType(space[name], rig))
    rig.loop_spy = loop_spy
    return rig


class _BrokenCore:
    """Ядро, которое не поднялось вообще."""

    @staticmethod
    def handle(text):
        raise RuntimeError("ядро сломано")

    @staticmethod
    def offline_notice(text=""):
        raise RuntimeError("и фраза тоже")


# ───── старый путь удалён, а не выключен ─────

def test_the_queued_lie_is_gone_from_the_source():
    """Фразы про очередь не должно остаться нигде в главном файле."""
    assert LIE not in MAIN_SRC, "в main.py всё ещё живёт обещание про очередь"


def test_the_core_is_imported_lazily():
    """Ядро ввозится внутри функции: старт голоса не должен тяжелеть."""
    tree = ast.parse(MAIN_SRC)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            text = ast.dump(node)
            assert "offline_core" not in text, \
                "core.offline_core ввозится на верхнем уровне main.py"


def test_the_offline_branch_never_touches_the_session():
    """Оффлайн-ветка не имеет права стучаться в модель."""
    tree = ast.parse(MAIN_SRC)
    node = None
    for candidate in ast.walk(tree):
        if isinstance(candidate, ast.FunctionDef) and candidate.name == "_answer_offline":
            node = candidate
    assert node is not None, "метода _answer_offline в main.py нет"
    # Докстринг выбрасывается: слово «session» в объяснении — это текст для
    # человека, а не обращение к сессии. Игла обязана судить код.
    steps = [s for s in node.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                     and isinstance(s.value.value, str))]
    body = "\n".join(ast.dump(s) for s in steps)
    for needle in ("_safe_send_text", "run_coroutine_threadsafe", "_sm", "_loop"):
        assert needle not in body, "оффлайн-ветка трогает сессию: %s" % needle


# ───── один рот ─────

def test_a_live_session_still_owns_the_mouth():
    """Пока сессия жива, фраза идёт в модель и больше никуда."""
    rig = _rig(online=True)
    rig._on_text_command("сколько времени")
    assert rig.sent == ["сколько времени"], "фраза не ушла в живую сессию: %r" % rig.sent
    assert len(rig.loop_spy.calls) == 1, "в сессию ушло не ровно одно сообщение"
    assert rig.ui.log == [], "при живой сессии ядро влезло в разговор: %r" % rig.ui.log


# ───── без сети руки работают ─────

def test_without_a_session_the_core_answers():
    """«Сколько времени» без сети обязано получить настоящий ответ."""
    rig = _rig(online=False)
    rig._on_text_command("сколько сейчас времени")
    assert rig.sent == [], "без сессии что-то ушло в модель: %r" % rig.sent
    assert len(rig.ui.log) == 1, "ответ владельцу не один: %r" % rig.ui.log
    line = rig.ui.log[0]
    assert line.startswith("Jarvis: "), "ответ без имени говорящего: %r" % line
    assert "Сейчас" in line, "это не ответ про время: %r" % line


def test_an_unknown_phrase_gets_an_honest_refusal():
    """Разговорная фраза без сети — честный отказ со списком умений."""
    rig = _rig(online=False)
    rig._on_text_command("расскажи анекдот про кота")
    assert len(rig.ui.log) == 1, "ответов не один: %r" % rig.ui.log
    line = rig.ui.log[0]
    assert LIE not in line, "владелец снова видит обещание про очередь"
    assert "напоминания" in line, "в отказе нет списка умений: %r" % line


def test_an_english_phrase_is_refused_in_english():
    """Отказ на чужом языке — тоже непонятный отказ."""
    rig = _rig(online=False)
    rig._on_text_command("tell me a joke about cats please")
    line = rig.ui.log[0]
    assert "reminders" in line, "английская фраза получила не тот язык: %r" % line


def test_a_broken_core_still_says_something():
    """Даже если ядро развалилось, молчания быть не должно."""
    saved = sys.modules.get("core.offline_core")
    fake = types.ModuleType("core.offline_core")
    fake.handle = _BrokenCore.handle
    fake.offline_notice = _BrokenCore.offline_notice
    sys.modules["core.offline_core"] = fake
    try:
        rig = _rig(online=False)
        rig._on_text_command("открой загрузки")
    finally:
        if saved is None:
            sys.modules.pop("core.offline_core", None)
        else:
            sys.modules["core.offline_core"] = saved
    assert rig.ui.log, "ядро упало и владелец не узнал об этом ничего"
    assert "RuntimeError" in rig.ui.log[0], "отказ не называет причину: %r" % rig.ui.log


def test_rubbish_input_does_not_crash_the_bar():
    """Мусор во вводе не должен ронять поток ввода."""
    for junk in ("", "   ", "?" * 500, "\n\t", "а" * 100):
        rig = _rig(online=False)
        rig._on_text_command(junk)
        assert len(rig.ui.log) == 1, "на вход %r ответов %d" % (junk[:12], len(rig.ui.log))


# ───── напоминание не теряется ─────

def test_a_reminder_is_spoken_while_online():
    """С сетью напоминание говорится вслух и не дублируется в лог."""
    rig = _rig(online=True)
    rig._deliver_reminder("Напоминание: выпить воды")
    assert rig.spoken == ["Напоминание: выпить воды"], rig.spoken
    assert rig.ui.log == [], "напоминание сказано и написано дважды: %r" % rig.ui.log


def test_a_reminder_offline_reaches_the_log():
    """Без сети напоминание обязано остаться видимым хотя бы текстом."""
    rig = _rig(online=False)
    rig._deliver_reminder("Напоминание: позвонить в банк")
    assert rig.spoken == [], "без сессии пытались говорить вслух: %r" % rig.spoken
    assert rig.ui.log == ["Jarvis: Напоминание: позвонить в банк"], \
        "напоминание потерялось: %r" % rig.ui.log


def test_the_checker_loop_goes_through_the_one_delivery_point():
    """Цикл напоминаний больше не зовёт speak напрямую."""
    tree = ast.parse(MAIN_SRC)
    body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_reminder_checker_loop":
            body = ast.dump(node)
    assert body, "цикла напоминаний в main.py нет"
    assert "_deliver_reminder" in body, "цикл не ходит через общую точку выдачи"
    assert "'speak'" not in body, "цикл всё ещё говорит мимо общей точки"


# ───── метка пояса одна ─────

def test_a_nameless_zone_is_printed_once():
    """Пояс без имени (такой отдаёт Windows) — одна метка, не две."""
    from datetime import timedelta, timezone
    from core.time_utils import describe_timezone
    got = describe_timezone(timezone(timedelta(hours=3)))
    assert got == "UTC+03:00", "метка пояса собрана неверно: %r" % got
    assert got.count("UTC") == 1, "смещение напечатано дважды: %r" % got


def test_a_named_zone_keeps_both_facts():
    """У именованного пояса два разных факта — оба остаются.

    На Windows без пакета tzdata именованный пояс может быть недоступен —
    тогда проверять нечего и тест честно молчит, а не краснеет.
    """
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("Europe/Moscow")
    except Exception:
        return
    from core.time_utils import describe_timezone
    got = describe_timezone(zone)
    assert got.startswith("Europe/Moscow"), "имя пояса потеряно: %r" % got
    assert "UTC+03:00" in got, "смещение потеряно: %r" % got


def test_the_prompt_line_never_doubles_the_zone():
    """Строка времени в системном промпте тоже лечится, не только ядро."""
    from core.time_utils import format_time_context
    text = format_time_context()
    head = text.split("\n")[1]
    inside = head[head.rfind("(") + 1:head.rfind(")")]
    parts = [p.strip() for p in inside.split(",")]
    assert len(parts) == len(set(parts)), "метка пояса повторяется: %r" % head


def test_the_core_stamp_uses_the_common_describer():
    """Ядро не собирает метку своими руками — иначе починка разойдётся."""
    assert "describe_timezone" in CORE_SRC, "ядро не зовёт общий описатель пояса"
    assert "get_timezone_label" not in CORE_SRC, "в ядре осталась вторая сборка метки"


def test_the_time_answer_shows_one_zone_label():
    """Живой ответ про время: в скобках не должно быть повтора."""
    from core.offline_core import handle
    reply = handle("сколько времени")
    assert reply is not None, "вопрос про время больше не узнаётся"
    inside = reply.text[reply.text.rfind("(") + 1:reply.text.rfind(")")]
    parts = [p.strip() for p in inside.split(",")]
    assert len(parts) == len(set(parts)), "в ответе про время метка дважды: %r" % reply.text


# ───── честный отказ живёт в ядре ─────

def test_the_notice_never_promises_anything():
    """Фраза отказа не должна обещать очередь, позже или потом."""
    from core.offline_core import offline_notice
    for phrase, forbidden in (("расскажи анекдот", ("очеред", "позже", "потом", "как только")),
                             ("tell me a joke", ("queue", "later", "as soon as"))):
        text = offline_notice(phrase).lower()
        for word in forbidden:
            assert word not in text, "отказ что-то обещает (%s): %r" % (word, text)


def test_the_notice_survives_rubbish():
    """На любой вход фраза отказа обязана собраться."""
    from core.offline_core import offline_notice
    for junk in ("", "   ", None, 42, "?" * 300):
        text = offline_notice(junk) if isinstance(junk, str) else offline_notice("")
        assert text and len(text) > 20, "пустой отказ на входе %r" % (junk,)


def test_the_core_still_needs_no_network():
    """Врезка не должна была привести в ядро сеть или модель."""
    for needle in ("aux_call", "genai.", "requests", "urllib", "api_key", "import main"):
        assert needle not in CORE_SRC, "в ядре появилась дверь наружу: %s" % needle


if __name__ == "__main__":
    names = sorted(n for n in globals() if n.startswith("test_"))
    passed = 0
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print("  FAIL  %s -- %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  ERROR %s -- %s: %s" % (name, type(exc).__name__, exc))
        else:
            passed += 1
            print("  PASS  %s" % name)
    total = passed + failed
    print("RESULT: %d/%d %s" % (passed, total, "ALL PASS" if failed == 0 else "SOME FAILED"))
    sys.exit(0 if failed == 0 else 1)
