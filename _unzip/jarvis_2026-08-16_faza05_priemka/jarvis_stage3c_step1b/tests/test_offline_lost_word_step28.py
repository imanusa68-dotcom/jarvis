# -*- coding: utf-8 -*-
"""
Шаг 28. Два дефекта живой приёмки 13.08.2026 (ночь).

Д-7  СЛОВО ПРОПАЛО. Владелец сказал «через 1 минут напомни выключить
      чайник» в тот миг, когда сессия уже умирала, но ещё считалась живой.
      Фраза ушла в сессию, та ответила «send_text skipped — session closing»,
      и никто не посмотрел на этот ответ. В окне — тишина.
      _safe_send_text всегда возвращал True/False; его просто не читали.
Д-2b «открой блокнот пж» → в лаунчер уехало имя «блокнот пж». На шаге 27
      добавили «плиз/плз/пжлст/пжл», а самое короткое «пж» забыли.

Главное правило этого файла: методы main.py исполняются в пустой комнате —
в ней есть только asyncio и print. Если правка потребует верхнего импорта или
глобального имени — тест упадёт здесь же, а не у владельца вечером.
Сети и диска здесь нет: дверь и руки подставные, сессия — двойник.

Run standalone: python tests/test_offline_lost_word_step28.py
"""
import ast
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.gate as gate  # noqa: E402
import core.offline_core as oc  # noqa: E402
import actions.reminder as _reminder  # noqa: E402

# УРОК 13.08.2026. Этот файл ставил владельцу НАСТОЯЩЕЕ напоминание.
# Тест с фразой про чайник идёт через настоящую дверь и живые руки, а руки
# честно записывали напоминание в memory/reminders.json папки проекта. После
# обычного прогона тестов Джарвис через минуту напоминал про чайник, который
# владелец не ставил. Теперь руки пишут во временную папку. Подмена работает,
# потому что _load_reminders/_save_reminders читают это имя из модуля в момент
# вызова, а не запоминают при импорте.
_TMP_MEMORY = Path(tempfile.mkdtemp(prefix="jarvis_test28_"))
_reminder.REMINDERS_PATH = _TMP_MEMORY / "reminders.json"

MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")

WANTED = ("_on_text_command", "_answer_offline", "_say_local")


class _QuietThreading:
    """
    Тихий поток для стенда, которому голос неинтересен.

    Шаг 29 научил _answer_offline звать свой голос. Здесь проверяется судьба
    фразы, а не звук, поэтому поток заводится и не стартует.
    """

    class Thread:
        def __init__(self, target=None, name=None, daemon=None,
                     args=(), kwargs=None):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            return None


# ───── двойник живой сессии ─────

class _Future:
    """То, что возвращает run_coroutine_threadsafe в бою."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.waited = None

    def result(self, timeout=None):
        self.waited = timeout
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeAsyncio:
    """Подмена asyncio: запоминает отправку и решает её судьбу."""

    def __init__(self, outcome=True, future=True):
        self.outcome = outcome
        self.give_future = future
        self.calls = []
        self.futures = []

    def run_coroutine_threadsafe(self, coro, loop):
        self.calls.append((coro, loop))
        if not self.give_future:
            return None
        fut = _Future(self.outcome)
        self.futures.append(fut)
        return fut


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


class _Live:
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


def _live(online=True, outcome=True, future=True):
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
    spy = _FakeAsyncio(outcome=outcome, future=future)
    space = {"asyncio": spy, "threading": _QuietThreading,
             "print": lambda *a, **k: None}
    exec(compile(module, "main.py<extract>", "exec"), space)  # noqa: S102
    rig = _Live(online)
    for name in WANTED:
        setattr(rig, name, types.MethodType(space[name], rig))
    rig.spy = spy
    return rig


def _answers(rig):
    return [line for line in rig.ui.log if line.startswith("Jarvis: ")]


# ───── двойник двери и рук для ядра ─────

@dataclass
class _Verdict:
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
    def __init__(self):
        self.calls = []

    def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
        self.calls.append((tool, dict(params or {})))
        return _Verdict(tool=tool, action=(params or {}).get("action"))


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, parameters=None, **kw):
        self.calls.append(dict(parameters or {}))
        return "сделано"


class _CoreRig:
    def __enter__(self):
        self.door = _Door()
        self.spy = _Spy()
        self._old_door = gate.dispatch
        self._old_import = oc._import_tool
        gate.dispatch = self.door
        oc._import_tool = lambda tool: self.spy
        return self

    def __exit__(self, *exc):
        gate.dispatch = self._old_door
        oc._import_tool = self._old_import
        return False


def _call(phrase):
    """Возвращает (инструмент, параметры, ответ) после фразы."""
    with _CoreRig() as rig:
        reply = oc.handle(phrase)
        tool, params = rig.door.calls[-1] if rig.door.calls else (None, {})
    return tool, params, reply


# ═════ Д-7: слово не теряется ═════

def test_a_live_mouth_keeps_the_phrase():
    """Сессия жива и взяла фразу: рот один, ядро молчит."""
    rig = _live(online=True, outcome=True)
    rig._on_text_command("сколько времени")
    assert rig.sent == ["сколько времени"]
    assert rig.ui.log == [], "при живой сессии ядро заговорило вторым голосом"


def test_b_refused_phrase_reaches_the_hands():
    """Д-7: сессия отказалась (False) — отвечает ядро, а не тишина."""
    rig = _live(online=True, outcome=False)
    rig._on_text_command("сколько времени")
    assert rig.sent == ["сколько времени"], "в сессию даже не попробовали"
    got = _answers(rig)
    assert got, "фраза пропала в тишине: окно пустое"
    assert "Сейчас" in got[0], got


def test_c_reminder_survives_a_dying_session():
    """Фраза владельца дословно: в тот миг она исчезла без следа."""
    rig = _live(online=True, outcome=False)
    rig._on_text_command("через 1 минут напомни выключить чайник")
    got = _answers(rig)
    assert got, "просьба про чайник снова пропала"


def test_d_broken_mouth_is_not_a_silent_hole():
    """Отправка взорвалась исключением — руки всё равно отвечают."""
    rig = _live(online=True, outcome=TimeoutError("рот не ответил"))
    rig._on_text_command("сколько времени")
    got = _answers(rig)
    assert got, "при взрыве отправки окно осталось пустым"
    assert "Сейчас" in got[0], got


def test_e_wait_for_the_mouth_is_bounded():
    """Ожидание ответа обязано быть с часами: иначе поле ввода виснет."""
    rig = _live(online=True, outcome=True)
    rig._on_text_command("сколько времени")
    assert rig.spy.futures, "ответ сессии вообще не спрашивали"
    waited = rig.spy.futures[0].waited
    assert waited is not None, "ждём ответа бесконечно — окно может замерзнуть"
    assert 0 < float(waited) <= 10, "слишком долгое ожидание: %r" % (waited,)


def test_f_old_rigs_without_future_still_work():
    """Старые риги и сторож возвращают None — код обязан это пережить."""
    rig = _live(online=True, outcome=True, future=False)
    rig._on_text_command("сколько времени")
    assert rig.sent == ["сколько времени"]
    assert rig.ui.log == [], "при неизвестной судьбе фразы ядро ответило вторым голосом"


def test_g_no_session_at_all_is_unchanged():
    """Сессии нет вовсе: старое поведение шага 25 не сдвинулось."""
    rig = _live(online=False)
    rig._on_text_command("сколько времени")
    assert rig.sent == [], "без сессии что-то ушло в сеть"
    assert _answers(rig), "без сессии окно осталось пустым"


def test_h_exactly_one_answer_per_phrase():
    """На одну просьбу — ровно один ответ, даже на разрыве связ��."""
    rig = _live(online=True, outcome=False)
    rig._on_text_command("сколько времени")
    assert len(_answers(rig)) == 1, _answers(rig)


def test_i_refused_conversation_gets_the_honest_no():
    """Разговорная фраза на разрыве: честный отказ, а не выдумка."""
    rig = _live(online=True, outcome=False)
    rig._on_text_command("расскажи анекдот про кота")
    got = _answers(rig)
    assert got and "напоминания" in got[0], got


def test_j_the_queued_lie_is_still_gone():
    """Старая ложь «text command queued» удалена, а не возвращена назад."""
    assert "text command queued" not in MAIN_SRC


# ═════ Д-2b: семья «пж» ═════

def test_k_short_please_never_reaches_the_launcher():
    """Все живые сокращения «пожалуйста» срезаются с имени приложения."""
    bad = []
    for word in ("пж", "пжп", "пжл", "пжлст", "пжалуста", "пжалста",
                 "плиз", "плииз", "плз", "плс", "please", "plz", "pls",
                 "пожалуйста"):
        tool, params, _ = _call("открой блокнот " + word)
        if tool != "open_app" or params.get("app_name") != "блокнот":
            bad.append((word, tool, params.get("app_name")))
    assert not bad, "вежливый хвост уехал в лаунчер: %r" % (bad,)


def test_l_a_real_word_is_not_eaten():
    """Не всё короткое — вежливость: чужие слова остаются в имени."""
    tool, params, _ = _call("открой блокнот плюс")
    assert tool == "open_app"
    assert params.get("app_name") == "блокнот плюс", params


def test_m_thanks_is_still_not_politeness_tail():
    """«Спасибо» чаще текст заметки, чем вежливый хвост — шаг 27."""
    tool, params, _ = _call("открой блокнот спасибо")
    assert params.get("app_name") == "блокнот спасибо", params


def test_n_step27_wins_did_not_move():
    """Победы шага 27 на месте: целая записка и «бро какое время»."""
    tool, params, _ = _call("через 1 минут напомни выключить чайник")
    assert tool == "reminder" and params.get("message") == "выключить чайник", params
    _, _, reply = _call("бро какое время?")
    assert reply is not None and "Сейчас" in reply.text, reply


def test_o_anti_corpus_did_not_move():
    """Чужое осталось чужим: восемь фраз, где ядро обязано молчать."""
    stolen = []
    for phrase in ("открой мне глаза на правду",
                   "переведи фразу на английский",
                   "расскажи про историю рима",
                   "сделай скриншот экрана",
                   "какая погода в москве",
                   "какое время года",
                   "включи музыку погромче",
                   "что такое квота"):
        _, _, reply = _call(phrase)
        if reply is not None:
            stolen.append((phrase, reply.tool or reply.text[:40]))
    assert not stolen, "ядро украло чужие фразы: %r" % (stolen,)


def test_p_polite_short_word_alone_is_not_a_command():
    """Одно вежливое слово — не команда и не запуск пустого имени."""
    for phrase in ("пж", "плиз", "открой пж"):
        _, params, reply = _call(phrase)
        if reply is not None and reply.tool == "open_app":
            assert params.get("app_name", "").strip(), "пустое имя ушло в лаунчер: %r" % (phrase,)


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    ok = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            print("RED   %s\n      %s" % (name, e))
        except Exception as e:  # noqa: BLE001
            print("ERROR %s\n      %s: %s" % (name, type(e).__name__, e))
        else:
            ok += 1
            print("green %s" % name)
    print("RESULT: %d/%d" % (ok, len(tests)))
