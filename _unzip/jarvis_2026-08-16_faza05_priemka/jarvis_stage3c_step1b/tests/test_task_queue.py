# tests/test_task_queue.py
"""
Сторожа очереди задач (фаза 1, блок 8, шаги 17-18).

Главное свойство блока — задача переживает перезапуск. Его нельзя проверить
внутри одного процесса, поэтому здесь есть тесты с двумя настоящими процессами.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import tokenize
from pathlib import Path

import pytest

from agent import task_queue as tq
from agent import task_store as tstore
from core import store
from core import task_state as ts

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db():
    conn = store.open_store()
    yield conn
    conn.close()


class _Executor:
    """Подставной исполнитель. Настоящий зовёт модель, а нам нужна очередь."""

    def __init__(self, answer="готово", boom=None, pause=0.0):
        self.answer = answer
        self.boom = boom
        self.pause = pause
        self.seen = []

    def execute(self, goal=None, speak=None, cancel_flag=None):
        self.seen.append(goal)
        if self.pause:
            time.sleep(self.pause)
        if self.boom is not None:
            raise self.boom
        if speak:
            speak(self.answer)
        return self.answer


def _install(monkeypatch, execu):
    q = tq.get_queue()
    monkeypatch.setattr(q, "_get_executor", lambda: execu)
    return q


def _settle(task_id, tries=60):
    """Дождаться, пока задача придёт в конечное состояние."""
    for _ in range(tries):
        row = tstore.get(task_id)
        if row and ts.is_final(str(row["state"])):
            return row
        time.sleep(0.05)
    return tstore.get(task_id)


# -- Наружный вид, за который держится замороженный main.py ---------------

def test_the_surface_main_py_depends_on_is_unchanged():
    """`main.py` заморожен и имеет сторож на слепок. Он зовёт ровно это:

        from agent.task_queue import get_queue, TaskPriority
        get_queue().submit(goal=..., priority=..., speak=...)

    Значит имена и форма обязаны сохраниться, что бы ни менялось внутри.
    """
    import inspect
    assert hasattr(tq, "get_queue") and hasattr(tq, "TaskPriority")
    for name in ("LOW", "NORMAL", "HIGH"):
        assert hasattr(tq.TaskPriority, name), f"пропал приоритет {name}"
    # Меньше — раньше: направление совпадает с колонкой priority в базе.
    assert tq.TaskPriority.HIGH.value < tq.TaskPriority.NORMAL.value
    assert tq.TaskPriority.NORMAL.value < tq.TaskPriority.LOW.value
    sig = inspect.signature(tq.TaskQueue.submit)
    for arg in ("goal", "priority", "speak"):
        assert arg in sig.parameters, f"submit потерял параметр {arg}"


def test_the_number_the_owner_hears_is_the_one_in_the_database(db, monkeypatch):
    """Владелец слышит «Task started (ID: ...)». Раньше это был обрывок uuid,
    который нельзя ни произнести, ни найти. Теперь — номер дела из базы."""
    execu = _Executor()
    q = _install(monkeypatch, execu)
    task_id = q.submit(goal="разбери загрузки", priority=tq.TaskPriority.NORMAL)
    assert task_id.startswith("T-"), f"номер не из базы: {task_id}"
    assert db.execute("SELECT count(*) FROM mx_task WHERE task_id=?",
                      (task_id,)).fetchone()[0] == 1
    _settle(task_id)


# -- Строка появляется ДО работы -----------------------------------------

def test_the_row_exists_before_the_work_starts(db, monkeypatch):
    """Иначе существовал бы промежуток, в котором работа уже идёт, а следа о
    ней нет — ровно то, что блок 8 и лечит."""
    started = threading.Event()
    seen_rows = []

    class _Watching(_Executor):
        def execute(self, goal=None, speak=None, cancel_flag=None):
            seen_rows.append(db.execute(
                "SELECT state FROM mx_task").fetchall())
            started.set()
            return "готово"

    q = _install(monkeypatch, _Watching())
    task_id = q.submit(goal="цель", priority=tq.TaskPriority.NORMAL)
    assert started.wait(timeout=10), "работник не запустился"
    assert seen_rows and seen_rows[0], "к началу работы строки в базе не было"
    assert str(seen_rows[0][0][0]) == ts.RUNNING
    _settle(task_id)


# -- Исходы --------------------------------------------------------------

def test_a_finished_task_is_marked_done(db, monkeypatch):
    q = _install(monkeypatch, _Executor())
    task_id = q.submit(goal="цель", priority=tq.TaskPriority.NORMAL)
    row = _settle(task_id)
    assert row["state"] == ts.DONE and row["cancel_reason"] is None
    assert row["finished_utc"]


def test_a_crashed_task_is_marked_failed_not_forgotten(db, monkeypatch):
    """Работник упал — задача обязана остаться в базе с честным исходом.
    В памяти она бы просто исчезла вместе с объектом."""
    q = _install(monkeypatch, _Executor(boom=RuntimeError("инструмент умер")))
    task_id = q.submit(goal="цель", priority=tq.TaskPriority.NORMAL)
    row = _settle(task_id)
    assert row["state"] == ts.FAILED and row["cancel_reason"] == "error"


def test_a_task_refused_by_the_gate_never_lands_as_done(db, monkeypatch):
    """ГЛАВНЫЙ СТОРОЖ ШАГА 18, и он о лжи, а не о сбое.

    Замер 19.08.2026: дверь запрещала действие, исполнитель писал «✅ done»,
    а Джарвис отвечал «Сообщение отправлено, сэр». Пока задачи жили в памяти,
    эта ложь была сказана и забыта. С блока 8 она стала бы СТРОКОЙ В БАЗЕ
    «выполнено», которую в фазе 3 прочитает приёмка и заверит.

    Причина отказа отдельная от 'error' нарочно: сбой и запрет — разные вещи,
    и различать их придётся в тот день, когда владелец спросит «почему не
    сделал».
    """
    from agent.executor import ToolRefused
    q = _install(monkeypatch, _Executor(boom=ToolRefused("SECURITY: blocked")))
    task_id = q.submit(goal="сделай запрещённое", priority=tq.TaskPriority.NORMAL)
    row = _settle(task_id)
    assert row["state"] != ts.DONE, "запрещённое действие записано как выполненное"
    assert row["state"] == ts.FAILED
    assert row["cancel_reason"] == "gate", (
        f"запрет свалили в общую причину {row['cancel_reason']!r}")


def test_the_gate_refusal_is_not_retried_and_not_replanned():
    """Отказ двери не повторяют и не разбирают моделью.

    Перепланирование после запрета означает «поищи другой способ сделать то,
    что тебе запретили» — то есть автоматический поиск обхода вокруг границы
    безопасности. Такого свойства в системе быть не должно даже на две попытки.

    Проверяется по ДЕРЕВУ КОДА, а не по позициям строк в файле. Первая версия
    искала первое вхождение `except Exception` во всём файле и нашла чужое, из
    совсем другой функции, — то есть сравнивала не то.
    """
    import ast
    import agent.executor as ex

    assert issubclass(ex.ToolRefused, Exception)
    tree = ast.parse((ROOT / "agent" / "executor.py").read_text(encoding="utf-8"))

    def calls_tool(node) -> bool:
        return any(isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name)
                   and n.func.id == "_call_tool"
                   for n in ast.walk(node))

    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not calls_tool(node):
            continue
        names = []
        for handler in node.handlers:
            if handler.type is None:
                names.append("*")
            elif isinstance(handler.type, ast.Name):
                names.append(handler.type.id)
            else:
                names.append(ast.dump(handler.type))
        assert "ToolRefused" in names, (
            f"шаг с вызовом инструмента не ловит отказ двери: {names}")
        broad = [i for i, n in enumerate(names) if n in ("Exception", "*")]
        narrow = names.index("ToolRefused")
        assert all(narrow < i for i in broad), (
            f"общая ветвь ошибок стоит РАНЬШЕ отказа двери: {names} — "
            f"она перехватит отказ и отправит его в разбор и переплан")
        checked += 1
    assert checked, "не нашёл ни одного места, где зовётся инструмент"


# -- Отмена --------------------------------------------------------------

def test_stop_waits_for_a_worker_that_is_taking_the_next_task(db, monkeypatch):
    """НАЙДЕНО ПАДЕНИЕМ ПРОГОНА 20.08.2026 — `access violation`, то есть
    смерть всего процесса, а не красный тест.

        поток-работник: берёт следующую задачу -> касса -> ПИШЕТ
        другой поток:   в это же мгновение ЗАКРЫВАЕТ то же соединение

    Обращение к закрытому соединению — авария на уровне C, её не поймает
    никакой `try/except`. Живьём это выглядит как «Джарвис молча исчез».

    ПЕРВАЯ ПРАВКА БЫЛА НЕВЕРНОЙ: я ждал по списку живых потоков, а работник
    вычёркивает себя из него ДО того, как позовёт следующую задачу. `stop()`
    видел пустой список и уходил. Авария повторилась. Поэтому ждём по
    СЧЁТЧИКУ работников внутри.

    Сторож проверяет ровно это: пока работник внутри, `stop()` не возвращается.
    """
    inside = threading.Event()
    release = threading.Event()

    class _Holding(_Executor):
        def execute(self, goal=None, speak=None, cancel_flag=None):
            inside.set()
            release.wait(timeout=10)
            return "готово"

    q = _install(monkeypatch, _Holding())
    q.submit(goal="держит соединение", priority=tq.TaskPriority.NORMAL)
    assert inside.wait(timeout=10), "работник не начал работу"

    assert q._inflight >= 1, "счётчик работников не увидел живого работника"

    done = threading.Event()

    def stopper():
        q.stop(wait_s=10.0)
        done.set()

    th = threading.Thread(target=stopper)
    th.start()
    # Пока работник внутри, остановка обязана ЖДАТЬ, а не возвращаться.
    assert not done.wait(timeout=0.6), (
        "stop() вернулся, пока работник ещё внутри — соединение закрыли бы "
        "у него под руками")
    release.set()
    assert done.wait(timeout=15), "stop() не дождался работника и завис"
    th.join(timeout=5)
    assert q._inflight == 0, f"счётчик остался {q._inflight}"


def test_a_stopped_queue_takes_no_new_work(db, monkeypatch):
    """НАЙДЕНО ПЛАВАЮЩИМ ПАДЕНИЕМ ПРОГОНА 20.08.2026.

    Работник в конце своего дела берёт СЛЕДУЮЩУЮ задачу. Без флага остановки
    он делал это и после того, как всё вокруг закрылось: живьём — поток,
    берущийся за новое дело на выходе из программы; в прогоне — работник,
    переживший конец теста и залезший в дом, которого уже нет.

    Плавающий тест хуже отсутствующего: ему перестают верить, а потом
    перестают верить и соседним.
    """
    execu = _Executor()
    q = _install(monkeypatch, execu)
    first = q.submit(goal="первая", priority=tq.TaskPriority.NORMAL)
    _settle(first)

    q.stop()
    from agent import task_store as tsx
    tsx.create("после остановки", supersede=False)
    q._pump()                     # не должен взять ничего
    time.sleep(0.3)
    running = [t for t in tsx.alive() if t["state"] == ts.RUNNING]
    assert not running, f"остановленная очередь взяла работу: {running}"


def test_the_owner_can_cancel_a_waiting_task(db, monkeypatch):
    q = _install(monkeypatch, _Executor(pause=0.4))
    first = q.submit(goal="первая", priority=tq.TaskPriority.NORMAL)
    second = q.submit(goal="вторая", priority=tq.TaskPriority.NORMAL)
    assert q.cancel(second) is True
    row = tstore.get(second)
    assert row["state"] == ts.CANCELLED and row["cancel_reason"] == "owner_stop"
    _settle(first)


def test_cancelling_a_finished_task_is_refused_not_pretended(db, monkeypatch):
    q = _install(monkeypatch, _Executor())
    task_id = q.submit(goal="цель", priority=tq.TaskPriority.NORMAL)
    _settle(task_id)
    assert q.cancel(task_id) is False, "отмена закрытой задачи сделала вид, что вышла"


# -- Потолок вызовов модели на задачу ------------------------------------

def test_the_per_task_call_cap_turns_a_task_into_partial(db, monkeypatch):
    """Потолок «не больше восьми вызовов на задачу» до блока 8 не применялся
    ни к чему: номер дела не доезжал до учёта. Теперь доезжает."""
    q = _install(monkeypatch, _Executor())
    monkeypatch.setattr(q, "_limits_for", lambda tid: 2)

    class _Greedy(_Executor):
        def execute(self, goal=None, speak=None, cancel_flag=None):
            from core import writer
            from core.task_context import current
            tid = current().task_id
            for i in range(3):        # три вызова при потолке два
                writer.write(lambda c, n=i: c.execute(
                    "INSERT INTO mx_meter_call (call_id, quota_day, role, "
                    "model_name, bucket, ok, started_utc, task_id) VALUES "
                    "(?,'2026-08-19','aux_light','м','task',1,"
                    "'2026-08-19T00:00:00Z',?)", (f"c-{tid}-{n}", tid)))
            return "готово"

    monkeypatch.setattr(q, "_get_executor", lambda: _Greedy())
    task_id = q.submit(goal="жадная цель", priority=tq.TaskPriority.NORMAL)
    row = _settle(task_id)
    assert row["state"] == ts.PARTIAL, (
        f"потолок не сработал, задача закрыта как {row['state']}")
    assert row["cancel_reason"] == "budget"


def test_the_blackbox_record_of_a_task_is_closed_with_the_same_outcome(db):
    """НАЙДЕНО ЖИВОЙ ПРОБОЙ ВЛАДЕЛЬЦА 20.08.2026 — дефект, созданный блоком 8.

    До блока 8 записи чёрного ящика были на ЗАПУСК, и закрывать их посреди
    работы было некому: их подбирала уборка при следующем старте. С приходом
    номера дела запись стала на ЗАДАЧУ, и у неё появился естественный момент
    закрытия — конец задачи. Он не использовался.

    Что было видно в настоящей базе после ОДНОЙ успешной задачи:

        задача  -> DONE
        запись  -> исход failed, вызовов 0, открыта
        учёт    -> 2 вызова

    Три числа об одном деле, и два из трёх врут. Хуже: уборка при следующем
    старте закрыла бы запись как «прервано», то есть чёрный ящик НАВСЕГДА
    запомнил бы, что успешная задача сорвалась. Ровно то, ради чего он
    существует, оказалось бы ложью.
    """
    from core import blackbox, provider as pp, writer

    class _FakeProvider:
        name = "fake"

        def build_payload(self, prompt, image_parts=None):
            return prompt

        def generate(self, model, payload, api_key):
            if "steps" in str(payload).lower():
                return ('{"steps":[{"step":1,"tool":"weather_report",'
                        '"description":"p","parameters":{"city":"Moscow"},'
                        '"critical":false}]}')
            return "Готово, сэр."

    saved = pp.set_provider(_FakeProvider())
    try:
        q = tq.get_queue()
        task_id = q.submit(goal="узнай погоду", priority=tq.TaskPriority.NORMAL)
        row = _settle(task_id, tries=150)
        assert row["state"] == ts.DONE, f"задача закрылась как {row['state']}"

        head = writer.reader().execute(
            "SELECT outcome, calls_n, closed_utc FROM mx_bb_head WHERE rec_id=?",
            ("B-" + task_id,)).fetchone()
        assert head is not None, "записи о задаче нет вовсе"
        assert head["closed_utc"] is not None, (
            "запись осталась открытой — уборка закроет её как «прервано» и "
            "чёрный ящик навсегда соврёт про успешную задачу")
        assert head["outcome"] == "done", (
            f"исход записи {head['outcome']!r} не совпал с исходом задачи DONE")
        spent = tstore.calls_spent(task_id)
        assert spent > 0, "учёт не увидел ни одного вызова — тест проверял бы пустоту"
        assert head["calls_n"] == spent, (
            f"в шапке записи {head['calls_n']} вызовов, а в учёте {spent} — "
            f"два числа об одном разошлись")
    finally:
        pp.set_provider(saved)


def test_the_task_number_reaches_the_meter_row_in_the_database(db, monkeypatch):
    """НАЙДЕНО ПРИ ПОДГОТОВКЕ ЖИВОЙ ПРОБЫ 20.08.2026, и мой прежний сторож это
    ПРОПУСКАЛ.

    Прежний тест проверял пропуск В КОНТЕКСТЕ рабочего потока — и был зелёным.
    Но дверь к модели зовут планировщик, разбор ошибок и сборка ответа, и ни
    один из них пропуск НЕ передаёт. Значит в строку расхода номер дела не
    попадал: колонка оставалась пустой, а потолок «не больше восьми вызовов на
    задачу» не мог сработать НИКОГДА.

    Это грабли №4 в моём исполнении: тест проверял полпути вместо результата.
    Теперь проверяется ТО, ЧТО ЛЕГЛО В БАЗУ.

    Поставщик подменён, а не дверь: учёт, касса и чёрный ящик работают
    по-настоящему — иначе тест снова проверял бы не то.
    """
    from core import provider as pp
    from core import writer

    class _FakeProvider:
        name = "fake"

        def build_payload(self, prompt, image_parts=None):
            return prompt

        def generate(self, model, payload, api_key):
            if "steps" in str(payload).lower():
                return ('{"steps":[{"step":1,"tool":"weather_report",'
                        '"description":"p","parameters":{"city":"Moscow"},'
                        '"critical":false}]}')
            return "Готово, сэр."

    saved = pp.set_provider(_FakeProvider())
    try:
        q = tq.get_queue()
        task_id = q.submit(goal="узнай погоду", priority=tq.TaskPriority.NORMAL)
        row = _settle(task_id, tries=150)
        assert row and ts.is_final(str(row["state"])), f"задача не закрылась: {row}"

        landed = writer.reader().execute(
            "SELECT task_id FROM mx_meter_call WHERE task_id=?",
            (task_id,)).fetchall()
        assert landed, (
            "ни одна строка расхода не получила номер дела — потолок вызовов "
            "на задачу применить нечем")
        assert tstore.calls_spent(task_id) == len(landed)
    finally:
        pp.set_provider(saved)


def test_the_task_number_reaches_the_meter(db, monkeypatch):
    """Пропуск ставится в рабочем потоке, потому что замер блока 3 показал:
    контекст в новый поток НЕ переносится."""
    seen = {}

    class _Looking(_Executor):
        def execute(self, goal=None, speak=None, cancel_flag=None):
            from core.task_context import current
            seen["task_id"] = current().task_id
            seen["bucket"] = current().bucket
            return "готово"

    q = _install(monkeypatch, _Looking())
    task_id = q.submit(goal="цель", priority=tq.TaskPriority.NORMAL)
    _settle(task_id)
    assert seen.get("task_id") == task_id, (
        f"внутри работника номер дела {seen.get('task_id')!r}, а не {task_id!r}")
    assert seen.get("bucket") == "task"


def test_the_call_cap_number_lives_in_the_config_not_in_the_code(db):
    """Число правит владелец в yaml, не залезая в код."""
    from agent.contracts import known_types
    spec = known_types().get(tstore.FREE_GOAL) or {}
    assert (spec.get("limits") or {}).get("max_llm_calls") == 8
    assert tq.get_queue()._limits_for("T-20260819-001") == 8


# -- Старая очередь удалена, а не оставлена рядом -------------------------

def test_the_in_memory_queue_is_gone_not_kept_beside():
    """Правило проекта: старый путь УДАЛЁН, не выключен.

    До блока 8 здесь жила очередь в памяти со своим номером (`uuid4()[:8]`),
    своими состояниями строчными буквами и живыми функциями внутри задачи.
    Второй очереди рядом появиться не должно.
    """
    src = (ROOT / "agent" / "task_queue.py").read_text(encoding="utf-8")
    with io.open(ROOT / "agent" / "task_queue.py", "rb") as fh:
        code = " ".join(t.string for t in tokenize.tokenize(fh.readline)
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "uuid" not in code, "вернулся старый номер задачи"
    assert not hasattr(tq, "TaskStatus"), "вернулся второй словарь состояний"
    assert not hasattr(tq, "Task"), "вернулась старая модель задачи"
    # Состояния берутся из одного места на весь проект.
    assert "task_state" in src


def test_the_queue_writes_tasks_only_through_the_store():
    """У очереди нет ни своей транзакции, ни своего открытия базы: и то и
    другое даёт касса (блок 7), а вставку — контракт (блок 4)."""
    with io.open(ROOT / "agent" / "task_queue.py", "rb") as fh:
        code = " ".join(t.string for t in tokenize.tokenize(fh.readline)
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "BEGIN" not in code
    assert "open_store" not in code
    assert "INSERT" not in code.upper(), "очередь пишет в таблицу сама"


def test_no_eternal_thread_wakes_up_for_nothing():
    """У старой очереди был вечный поток, который просыпался раз в секунду и
    почти всегда ничего не находил — это расход батареи на пустом ноутбуке.
    Работник теперь рождается под задачу и умирает с ней."""
    q = tq.get_queue()
    assert not hasattr(q, "_worker_thread"), "вернулся вечный поток"
    live = [t.name for t in threading.enumerate()
            if t.name.startswith("AgentTaskQueue")]
    assert not live, f"вечный поток запущен: {live}"


# -- Перезапуск: ДВА НАСТОЯЩИХ ПРОЦЕССА ----------------------------------

WORKER = r'''
import os, sys, time
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
from agent import task_queue as tq, task_store as tstore
from core import task_state as ts


class _Slow:
    def execute(self, goal=None, speak=None, cancel_flag=None):
        time.sleep(30)      # процесс умрёт раньше, чем это кончится
        return "не дойдём"


{body}
'''


def _run(body, home, kill_after=None):
    code = WORKER.format(root=str(ROOT), body=body)
    env = dict(os.environ, JARVIS_STATE_DIR=str(home),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if kill_after is None:
        proc = subprocess.run([sys.executable, "-c", code], env=env,
                              capture_output=True, text=True,
                              encoding="utf-8", cwd=str(ROOT), timeout=180)
        assert proc.returncode == 0, proc.stderr[-1200:]
        return proc.stdout
    proc = subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.PIPE, text=True, cwd=str(ROOT),
                            encoding="utf-8")
    # Ждём ИМЕННО нашу метку, а не первую строку вывода: очередь печатает
    # «принята ...» раньше, и первая версия читала её и падала.
    marker = ""
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith("STARTED"):
            marker = line
            break
    time.sleep(kill_after)
    proc.kill()
    proc.wait(timeout=30)
    return marker


def test_a_task_killed_mid_flight_is_failed_and_never_resumes(db, tmp_path):
    """САМОЕ ВАЖНОЕ СВОЙСТВО БЛОКА, и внутри одного процесса его не проверить.

    Старая очередь жила в памяти: процесс упал — задачи не было ни строки, ни
    следа, и владельцу нельзя было сказать «то, что ты просил, сорвалось».

    Возобновления нет нарочно (I15, fail-closed): задача могла успеть сделать
    половину работы на диске, и начать заново значит сделать половину дважды.
    """
    home = tmp_path / "дом"
    home.mkdir()

    line = _run("""
q = tq.get_queue()
q._get_executor = lambda: _Slow()
tid = q.submit(goal="долгая работа")
print("STARTED " + tid, flush=True)
time.sleep(30)
""", home, kill_after=2.0)
    assert line.startswith("STARTED"), line
    task_id = line.split()[1]

    out = _run(f"""
row = tstore.get({task_id!r})
print("BEFORE " + str(row["state"]))
fixed = tstore.recover_after_restart()
row = tstore.get({task_id!r})
print("AFTER " + str(fixed) + " " + str(row["state"]) + " " +
      str(row["cancel_reason"]))
q = tq.get_queue()
q._get_executor = lambda: _Slow()
print("CLAIM " + str(tstore.claim()))
""", home)

    before = [l for l in out.splitlines() if l.startswith("BEFORE")][0]
    after = [l for l in out.splitlines() if l.startswith("AFTER")][0]
    claim = [l for l in out.splitlines() if l.startswith("CLAIM")][0]

    assert before.split()[1] == ts.RUNNING, (
        f"задача не осталась помеченной работающей: {before}")
    _, fixed, state, reason = after.split()
    assert fixed == "1" and state == ts.FAILED and reason == "restart", after
    assert claim.strip() == "CLAIM None", (
        f"задача возобновилась сама, а это запрещено: {claim}")
