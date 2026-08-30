# -*- coding: utf-8 -*-
"""
Одна касса на весь проект: Джарвис помнит, что делал (шаг 30, фаза 0.7).

Зачем этот файл. До шага 30 в проекте жили два журнала, которые не знали друг
о друге: память диалога (core/dialogue_state) и таблица action_journal в базе
(core/journal). Вопрос владельца «что ты делал» читает БАЗУ, а оффлайновый
исполнитель не писал вообще никуда. Джарвис открывал блокнот и тут же об этом
забывал: без сети ответ всегда был «журнал пуст».

Что здесь проверяется:
  1. одна запись ложится сразу в оба журнала — и в память, и в базу;
  2. запись переживает перезапуск: база это диск, а не оперативка;
  3. предохранитель: под прогоном тестов настоящая база владельца не трогается,
     а названная папка состояния предохранитель снимает;
  4. касса НИКОГДА не ломает дело: ни сломанная база, ни сломанная касса
     не имеют права утопить открытие блокнота;
  5. записывается только то, что действительно случилось: отказ двери
     в журнал не попадает, а упавший инструмент попадает как неудача;
  6. полный круг ушами владельца: «открой блокнот» → «что ты делал»
     → и он про блокнот рассказывает;
  7. онлайновый путь main.py ходит в ту же кассу — двух касс больше нет;
  8. ядро ввозит кассу лениво и само остаётся молчаливым;
  9. три места согласны, что «последних дел» ровно восемь.

Ни один тест не ходит в сеть и не трогает настоящую базу владельца: дверь
и инструменты подставные, база — временная папка, которая тут же удаляется.

Run standalone: python tests/test_action_log_step30.py
"""
import os
import shutil
import sys
import tempfile
import traceback
import types
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.action_log as al  # noqa: E402
import core.dialogue_state as ds  # noqa: E402
import core.gate as gate  # noqa: E402
import core.journal as jr  # noqa: E402
import core.offline_core as oc  # noqa: E402

CASH_SOURCE = (ROOT / "core" / "action_log.py").read_text(encoding="utf-8")
CORE_SOURCE = (ROOT / "core" / "offline_core.py").read_text(encoding="utf-8")
MAIN_SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")


# ─────────────────────────── стенды ───────────────────────────

class _StateDir:
    """
    Временный дом состояния: своя папка, своя база, чистая память диалога.

    JARVIS_STATE_DIR — та самая переменная, которой касса снимает
    предохранитель. Настоящий ~/.jarvis владельца здесь не при чём.
    """

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="jarvis_step30_")
        self._saved = os.environ.get("JARVIS_STATE_DIR")
        os.environ["JARVIS_STATE_DIR"] = self.path
        al.reset()
        ds._state["action_journal"] = []
        return self

    def __exit__(self, *exc):
        al.reset()
        if self._saved is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = self._saved
        shutil.rmtree(self.path, ignore_errors=True)
        ds._state["action_journal"] = []
        return False


class _PretendPytest:
    """Притворяется прогоном тестов без названной папки: касса обязана молчать."""

    def __enter__(self):
        self._saved = os.environ.get("JARVIS_STATE_DIR")
        os.environ.pop("JARVIS_STATE_DIR", None)
        self._added = "pytest" not in sys.modules
        if self._added:
            sys.modules["pytest"] = types.ModuleType("pytest")
        al.reset()
        return self

    def __exit__(self, *exc):
        if self._added:
            sys.modules.pop("pytest", None)
        if self._saved is not None:
            os.environ["JARVIS_STATE_DIR"] = self._saved
        al.reset()
        return False


@dataclass
class _Verdict:
    """Ответ подставной двери — той же формы, что настоящий GateResult."""

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
    """Подставная дверь: помнит всё, что через неё прошло."""

    def __init__(self, verdict="run"):
        self.verdict = verdict
        self.calls = []

    def __call__(self, tool, params=None, *, mode="interactive", screen_control=False):
        self.calls.append({"tool": tool, "params": dict(params or {})})
        return _Verdict(verdict=self.verdict, tool=tool,
                        action=(params or {}).get("action"))


class _Spy:
    """Подставной инструмент: диск не трогает, при желании ломается."""

    def __init__(self, answer="Launching: блокнот -> notepad.exe (Windows)", boom=None):
        self.answer = answer
        self.boom = boom
        self.calls = []

    def __call__(self, parameters=None, **kw):
        self.calls.append(dict(parameters or {}))
        if self.boom:
            raise RuntimeError(self.boom)
        return self.answer


class _CashDeskSpy:
    """Подставная касса: видно, что именно ядро собиралось записать."""

    def __init__(self, boom=None):
        self.boom = boom
        self.calls = []

    def __call__(self, tool=None, action=None, summary="", ok=True):
        self.calls.append({"tool": tool, "action": action,
                           "summary": str(summary), "ok": bool(ok)})
        if self.boom:
            raise RuntimeError(self.boom)
        return True


class _Rig:
    """Подменяет дверь, ввоз инструментов и (по желанию) кассу."""

    def __init__(self, verdict="run", boom=None, cash_boom=None, real_cash=False):
        self.verdict = verdict
        self.boom = boom
        self.cash_boom = cash_boom
        self.real_cash = real_cash

    def __enter__(self):
        self.door = _Door(self.verdict)
        self.spy = _Spy(boom=self.boom)
        self.cash = _CashDeskSpy(boom=self.cash_boom)
        self._real_dispatch = gate.dispatch
        self._real_import = oc._import_tool
        self._real_note = al.note
        gate.dispatch = self.door
        oc._import_tool = lambda tool: self.spy
        if not self.real_cash:
            al.note = self.cash
        return self

    def __exit__(self, *exc):
        gate.dispatch = self._real_dispatch
        oc._import_tool = self._real_import
        al.note = self._real_note
        return False


def _ram_journal():
    return list(ds.get().get("action_journal") or [])


# ─────────────────────────── проверки ───────────────────────────

def test_a_one_record_lands_in_both_journals():
    """Одна запись — и память, и база видят одно и то же."""
    with _StateDir():
        landed = al.note("open_app", summary="Launching: блокнот -> notepad.exe")
        assert landed is True, "касса сказала, что не записала"

        ram = _ram_journal()
        assert len(ram) == 1, "в памяти не одна запись: %r" % (ram,)
        assert "блокнот" in ram[-1]["summary"], ram[-1]
        assert ram[-1]["ok"] is True, ram[-1]

        base = al.recent()
        assert len(base) == 1, "в базе не одна запись: %r" % (base,)
        assert "блокнот" in base[-1]["summary"], base[-1]
        assert base[-1]["ok"] is True, base[-1]

        assert ram[-1]["summary"] == base[-1]["summary"], \
            "память и база записали по-разному: %r vs %r" % (ram[-1], base[-1])


def test_b_the_record_survives_a_restart():
    """База это диск. Забыли соединение — запись всё равно на месте."""
    with _StateDir():
        al.note("open_app", summary="Launching: блокнот -> notepad.exe")
        al.reset()                      # как будто процесс перезапустили
        ds._state["action_journal"] = []  # память рестарт не переживает

        base = al.recent()
        assert len(base) == 1, "после рестарта база забыла: %r" % (base,)
        assert "блокнот" in base[-1]["summary"], base[-1]
        assert _ram_journal() == [], "память не должна была выжить"


def test_c_an_empty_tool_is_not_a_record():
    """Без имени инструмента записи нет: пустых строк в журнале не будет."""
    with _StateDir():
        assert al.note("", summary="что-то") is False
        assert al.note(None, summary="что-то") is False
        assert _ram_journal() == []
        assert al.recent() == []


def test_d_the_fuse_keeps_the_test_run_out_of_the_real_base():
    """Под прогоном тестов касса молчит: чужая база и чужая память целы."""
    with _PretendPytest():
        assert al._enabled() is False, "предохранитель не сработал"
        before = _ram_journal()
        assert al.note("open_app", summary="это не должно быть записано") is False
        assert _ram_journal() == before, "касса влезла в память под pytest"
        assert al.recent() == [], "касса полезла в базу под pytest"


def test_e_a_named_state_dir_lifts_the_fuse():
    """Тест, который назвал свою папку, получает полноценную кассу."""
    added = "pytest" not in sys.modules
    if added:
        sys.modules["pytest"] = types.ModuleType("pytest")
    try:
        with _StateDir():
            assert al._enabled() is True, "названная папка не сняла предохранитель"
            assert al.note("open_app", summary="записано осознанно") is True
            assert len(al.recent()) == 1
    finally:
        if added:
            sys.modules.pop("pytest", None)
        al.reset()


def test_f_a_broken_base_never_breaks_the_deed():
    """Сломанная база — не повод терять дело: память всё равно помнит."""
    with _StateDir():
        real = al._journal

        def _boom():
            raise RuntimeError("база не открылась")

        al._journal = _boom
        try:
            landed = al.note("open_app", summary="Launching: блокнот")
        finally:
            al._journal = real

        assert landed is True, "при сломанной базе память тоже не записала"
        ram = _ram_journal()
        assert len(ram) == 1 and "блокнот" in ram[-1]["summary"], ram


def test_g_a_finished_deed_is_written_down():
    """Инструмент отработал — ядро понесло записку в кассу."""
    with _Rig() as rig:
        reply = oc._run_tool("open_app", {"app_name": "блокнот"}, 0)

        assert reply.ok is not False, reply
        assert len(rig.cash.calls) == 1, "касса позвана %d раз" % len(rig.cash.calls)
        written = rig.cash.calls[0]
        assert written["tool"] == "open_app", written
        assert written["ok"] is True, written
        assert "notepad" in written["summary"], written


def test_h_a_refused_deed_is_not_written_down():
    """Дверь не пустила — записывать нечего: дела не было."""
    with _Rig(verdict="blocked") as rig:
        reply = oc._run_tool("open_app", {"app_name": "блокнот"}, 0)

        assert reply.ok is False, reply
        assert rig.spy.calls == [], "инструмент запустился после отказа двери"
        assert rig.cash.calls == [], "отказ попал в журнал: %r" % (rig.cash.calls,)


def test_i_a_broken_tool_is_written_down_as_failed():
    """Инструмент упал — это тоже история, но с крестиком."""
    with _Rig(boom="дверь заклинило") as rig:
        reply = oc._run_tool("open_app", {"app_name": "блокнот"}, 0)

        assert reply.ok is False, reply
        assert len(rig.cash.calls) == 1, rig.cash.calls
        written = rig.cash.calls[0]
        assert written["ok"] is False, written
        assert "RuntimeError" in written["summary"], written


def test_j_a_broken_cashdesk_never_breaks_the_answer():
    """Касса сломалась — владелец всё равно получает ответ по делу."""
    with _Rig(cash_boom="касса сгорела") as rig:
        reply = oc._run_tool("open_app", {"app_name": "блокнот"}, 0)

        assert "notepad" in reply.text, reply.text
        assert reply.ok is not False, reply
        assert len(rig.spy.calls) == 1, "дело не сделано из-за кассы"


def test_k_what_did_you_do_finally_sees_the_deed():
    """Полный круг ушами владельца: открыли блокнот — и он про это помнит."""
    with _StateDir():
        with _Rig(real_cash=True) as rig:
            done = oc.handle("открой блокнот")
            assert done is not None, "ядро не взяло «открой блокнот»"
            assert len(rig.spy.calls) == 1, rig.spy.calls

            asked = oc.handle("что ты делал")
            assert asked is not None, "ядро не взяло «что ты делал»"
            text = asked.text
            assert "Последнее, что я делал" in text, text
            assert "open_app" in text, text
            assert "notepad" in text, text
            assert "Журнал пуст" not in text, text


def test_l_the_online_path_uses_the_same_cashdesk():
    """Одна касса на весь проект: онлайновый путь ходит туда же."""
    assert "from core.action_log import note as _note_action" in MAIN_SOURCE, \
        "main.py не ввозит кассу"
    assert "_note_action(" in MAIN_SOURCE, "main.py кассу не зовёт"
    assert "record_action as _ds_record" not in MAIN_SOURCE, \
        "старый путь не удалён, а оставлен рядом"
    assert "_ds_record(" not in MAIN_SOURCE, "старый вызов всё ещё в main.py"
    assert 'res_str.startswith(("SECURITY", "Tool \'"))' in MAIN_SOURCE, \
        "правило «что считать удачей» потерялось"


def test_m_the_core_imports_the_cashdesk_lazily():
    """Ядро не тащит базу при старте: ввоз кассы только внутри функции."""
    assert "from core.action_log import note" in CORE_SOURCE, "ядро кассу не ввозит"
    for line in CORE_SOURCE.splitlines():
        if line.startswith("import core.action_log") or line.startswith("from core.action_log"):
            raise AssertionError("ввоз кассы вылез на верхний уровень: %r" % line)


def test_n_the_cashdesk_stays_silent_and_offline():
    """Касса ничего не печатает и никуда не звонит."""
    for word in ("print(", "requests", "urllib", "socket", "websocket",
                 "genai", "generativeai", "generate_content",
                 "aux_call", "cheap_call"):
        assert word not in CASH_SOURCE, "в кассе завелось запрещённое: %r" % word


def test_o_three_places_agree_on_eight():
    """Сколько дел помнить — три места не имеют права спорить."""
    assert al.RECENT_MAX == 8, al.RECENT_MAX
    assert ds._JOURNAL_MAX == 8, ds._JOURNAL_MAX
    assert jr.JOURNAL_MAX == 8, jr.JOURNAL_MAX


# ─────────────────────────── запуск без pytest ───────────────────────────

def _run_all():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    green = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            print("FAIL  %s: %r" % (name, exc))
            traceback.print_exc()
        else:
            green += 1
            print("green %s" % name)
    print("RESULT: %d/%d" % (green, len(tests)))
    return 0 if green == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run_all())
