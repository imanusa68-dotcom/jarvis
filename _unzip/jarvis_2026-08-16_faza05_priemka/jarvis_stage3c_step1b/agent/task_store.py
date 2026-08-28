# agent/task_store.py
"""
Задачи в базе: выдать номер, взять в работу, закрыть, вытеснить, восстановить
после перезапуска (фаза 1, блок 8).

ЧЕГО ЗДЕСЬ НЕТ НАРОЧНО
----------------------
Вставки строки своими руками. Она живёт в `agent/contracts.py`, и это не
вкус: сторож `test_only_the_contract_writes_tasks_and_reports` грепает весь
проект и падает, если появится второе место. Комментарий контракта написан
как раз про этот файл: «через несколько блоков появится очередь, которая
начнёт вставлять строки, и никто не вспомнит, что была проверка».

Здесь также нет ни одного своего замка и ни одной своей транзакции: и то и
другое даёт касса записи (блок 7). Два замка вокруг одной базы — это разный
порядок захвата, то есть мёртвая хватка.

ПОЧЕМУ НОМЕР ВЫДАЁТСЯ ВНУТРИ ТОЙ ЖЕ ТРАНЗАКЦИИ
----------------------------------------------
Номер дела — первичный ключ. Посмотреть «какой был последний» и вставить
следующий двумя отдельными действиями — значит однажды получить два
одинаковых номера: два потока увидят одно и то же «последнее». Внутри одной
транзакции кассы этого не может случиться по построению.

Блок 3 нарочно НЕ написал выдачу номеров, хотя формат решил: «выдавать номер
должен тот, кто вставляет строку». Вот он.

ПОЧЕМУ НОМЕР СЧИТАЕТСЯ ЧИСЛОМ, А НЕ СТРОКОЙ
-------------------------------------------
Соблазн — взять максимум по строке одним запросом. Но строки сравниваются по
знакам: '999' больше, чем '1000'. На тысячной задаче за сутки нумерация
пошла бы по кругу и упёрлась в первичный ключ. Задач в сутки единицы (потолок
два одновременно), поэтому берём номера дня и считаем максимум числом —
дешевле, чем ошибка, которую невозможно воспроизвести.

ПРАВИЛО РЕСТАРТА (I15) ДЕРЖИТСЯ НЕ УБОРКОЙ
------------------------------------------
Задача в работе из ПРОШЛОГО запуска обязана стать проваленной и НЕ
возобновляться сама. Уборка при старте приводит базу в честный вид — но
безопасность даёт не она, а то, что в работу берутся только задачи из
состояния «в очереди». Задачу чужого запуска физически некому взять.

Так и надо: конструкция защищает, уборка убирает. Если уборка однажды не
успеет — ничего не сломается.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from core import task_state as ts

# Тип задачи для свободной цели, которую владелец говорит голосом. Живёт в
# config/task_types.yaml; здесь только имя.
FREE_GOAL = "free_goal"

# Потолок одновременно живых задач. Владелец правит в настройках. Два — это
# число из плана; у старой очереди в памяти стояло одно.
DEFAULT_MAX_ALIVE = 2
_MAX_ALIVE_SETTING = "task_max_alive"

# Потолок повторов одной задачи (13.5). Держится колонкой attempts, потому что
# сама задача неизменяема.
MAX_ATTEMPTS = 3

_TAIL = re.compile(r"^T-(\d{8})-(\d+)$")


class TaskStoreError(RuntimeError):
    """Задачу не удалось положить или сдвинуть. Наверх летит как есть."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ready() -> bool:
    """Есть ли в базе таблица задач. Спрашивать можно после открытия."""
    try:
        from core import store, writer
        writer.ensure_open()
        return bool(store.supports("tasks"))
    except Exception:
        return False


def max_alive() -> int:
    """Сколько задач может быть живо одновременно."""
    try:
        from config.loader import get_setting
        got = get_setting(_MAX_ALIVE_SETTING)
        if isinstance(got, int) and not isinstance(got, bool) and got >= 1:
            return got
    except Exception:
        pass
    return DEFAULT_MAX_ALIVE


# -- Форма задачи ---------------------------------------------------------

def form_key_for(goal: str, task_type: str = FREE_GOAL) -> str:
    """Отпечаток «той же формы» для вытеснения (Д18).

    Считается от типа и приведённой цели: регистр и лишние пробелы не должны
    делать одну и ту же просьбу двумя разными. Отпечаток, а не сама цель:
    в колонке form_key не должно лежать речи владельца — её срок жизни другой.

    Разделитель между типом и целью выбран не случайно: тип проверен шаблоном
    кода (только буквы, цифры и подчёркивание), поэтому вертикальная черта в
    него не помещается. Значит склейка «тип + цель» однозначна и две разные
    пары не могут дать один отпечаток.
    """
    text = " ".join(str(goal or "").lower().split())
    raw = (str(task_type) + "|" + text).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def _title_for(goal: str) -> str:
    """Как задачу назовёт голос при перечислении. Потолок 80 знаков — это
    требование контракта, и он есть потому, что заголовок ЗВУЧИТ вслух."""
    text = " ".join(str(goal or "").split()) or "задача без названия"
    return text if len(text) <= 80 else text[:77] + "..."


def _goal_for(goal: str) -> str:
    """Дословные слова владельца, обрезанные до потолка контракта (500).

    Обрезка, а не отказ: не принять задачу из-за длинной фразы значит наказать
    владельца за то, что он подробно объяснил. Но и молча потерять хвост
    нельзя — поэтому обрезка видна многоточием.
    """
    text = " ".join(str(goal or "").split()) or "без цели"
    return text if len(text) <= 500 else text[:497] + "..."


def build_doc(goal: str, *, task_id: str, task_type: str = FREE_GOAL,
              depth: int = 0, parent_id=None, agent_role=None,
              limits=None) -> dict:
    """Собрать документ задачи под схему v1 контракта.

    Собирает КОД, а не модель: если бы документ собирала модель, в задачу
    приехал бы её пересказ чужого текста (Д29, I36).
    """
    doc = {
        "schema_ver": 1,
        "task_id": task_id,
        "depth": int(depth),
        "type": str(task_type),
        "form_key": form_key_for(goal, task_type),
        "title": _title_for(goal),
        "goal": _goal_for(goal),
    }
    if parent_id:
        doc["parent_id"] = parent_id
    if agent_role:
        doc["agent_role"] = agent_role
    if limits:
        doc["limits"] = limits
    return doc


# -- Номер дела -----------------------------------------------------------

def _next_id(conn, day: str) -> str:
    """Следующий номер за эти местные сутки. Транзакцию держит вызывающий."""
    from core.task_context import format_task_id
    rows = conn.execute(
        "SELECT task_id FROM mx_task WHERE task_id LIKE ?",
        (f"T-{day}-%",)).fetchall()
    top = 0
    for row in rows:
        got = _TAIL.match(str(row[0]))
        if got and got.group(1) == day:
            top = max(top, int(got.group(2)))
    return format_task_id(day, top + 1)


# -- Создать --------------------------------------------------------------

def create(goal: str, *, priority: int = 2, task_type: str = FREE_GOAL,
           depth: int = 0, parent_id=None, agent_role=None, limits=None,
           supersede: bool = True) -> str:
    """Положить задачу в базу и вернуть её номер.

    `supersede` — Д18: новая задача той же формы вытесняет старую. Обе живы в
    момент перехода, поэтому уникального индекса по форме в схеме нет нарочно.
    """
    if not _ready():
        raise TaskStoreError("таблицы задач нет: схема старее кода")
    from core import writer
    from core.task_context import run_id, today_stamp
    from agent.contracts import insert_task

    day = today_stamp()
    rid = run_id()

    def job(conn):
        task_id = _next_id(conn, day)
        doc = build_doc(goal, task_id=task_id, task_type=task_type,
                        depth=depth, parent_id=parent_id,
                        agent_role=agent_role, limits=limits)
        if supersede:
            _supersede(conn, doc["form_key"], keep=task_id)
        insert_task(conn, doc, state=ts.QUEUED, priority=int(priority),
                    run_id=rid, now_utc=_now())
        return task_id

    return writer.write(job, label="task_store.create")


def _supersede(conn, form_key: str, *, keep: str) -> int:
    """Вытеснить живые задачи той же формы. Транзакцию держит вызывающий.

    Вытесняются только ЖИВЫЕ и только те, что ещё не начали выполняться:
    задачу в работе вытеснять нельзя — она уже что-то сделала на диске, и
    молча забыть об этом значит потерять след. Такая задача доработает, а
    вытеснение достанется тем, кто ещё стоит в очереди.
    """
    movable = (ts.NEW, ts.QUEUED, ts.WAITING)
    marks = ",".join("?" * len(movable))
    rows = conn.execute(
        f"SELECT task_id, state FROM mx_task WHERE form_key=? "
        f"AND task_id<>? AND state IN ({marks})",
        (form_key, keep, *movable)).fetchall()
    for row in rows:
        _move(conn, str(row[0]), str(row[1]), ts.SUPERSEDED,
              reason="superseded")
    return len(rows)


# -- Сдвинуть состояние ---------------------------------------------------

def _move(conn, task_id: str, src: str, dst: str, *, reason=None) -> None:
    """Один законный переход. Транзакцию держит вызывающий.

    Проверка законности стоит ДО записи и в той же функции: разнести их —
    значит однажды записать состояние, которого не бывает.
    """
    ts.check(src, dst)
    ts.check_reason(reason)
    now = _now()
    finished = now if ts.is_final(dst) else None
    got = conn.execute(
        "UPDATE mx_task SET state=?, updated_utc=?, finished_utc=?, "
        "cancel_reason=COALESCE(?, cancel_reason) WHERE task_id=? AND state=?",
        (dst, now, finished, reason, task_id, src))
    if got.rowcount != 1:
        # Состояние сменилось между чтением и записью — значит решение
        # принимал кто-то другой, и наше уже неверно.
        raise TaskStoreError(
            f"задача {task_id} больше не в состоянии {src}: кто-то успел раньше")


def move(task_id: str, src: str, dst: str, *, reason=None) -> bool:
    """Сдвинуть состояние. False, если кто-то успел раньше."""
    if not _ready():
        return False
    from core import writer
    try:
        writer.write(lambda c: _move(c, str(task_id), src, dst, reason=reason),
                     label="task_store.move")
        return True
    except (TaskStoreError, ts.StateError):
        return False


def finish(task_id: str, dst: str, *, reason=None) -> bool:
    """Закрыть задачу исходом, каким бы ни было текущее состояние.

    Состояние читается и правится в ОДНОЙ транзакции: между чтением и записью
    задачу мог отменить владелец, и тогда перезаписать его решение нельзя.
    """
    if not _ready():
        return False
    from core import writer

    def job(conn):
        row = conn.execute("SELECT state FROM mx_task WHERE task_id=?",
                           (str(task_id),)).fetchone()
        if row is None:
            raise TaskStoreError(f"задачи {task_id} нет")
        src = str(row[0])
        if ts.is_final(src):
            return False          # уже закрыта, и это не ошибка
        _move(conn, str(task_id), src, dst, reason=reason)
        return True

    try:
        return bool(writer.write(job, label="task_store.finish"))
    except (TaskStoreError, ts.StateError):
        return False


# -- Взять в работу -------------------------------------------------------

def claim() -> dict | None:
    """Взять следующую задачу в работу. None — брать нечего.

    Три свойства держатся ОДНОЙ транзакцией:
      * выбор следующей по приоритету и сроку (индекс mx_task_state_idx
        заведён в блоке 2 ровно под этот запрос);
      * потолок одновременно живых — считается ЗДЕСЬ ЖЕ, иначе два потока
        оба увидят «место есть» и оба пойдут;
      * атомарное взятие: правка сторожится состоянием, и второй поток
        получает ноль изменённых строк.

    Задачу чужого запуска взять невозможно: берём только из очереди, а в
    работе чужие остаются до уборки.
    """
    if not _ready():
        return None
    from core import writer
    from core.task_context import run_id
    rid = run_id()
    limit = max_alive()

    def job(conn):
        marks = ",".join("?" * len(ts.ALIVE))
        busy = conn.execute(
            f"SELECT count(*) FROM mx_task WHERE state IN ({marks})",
            ts.ALIVE).fetchone()[0]
        running = conn.execute(
            "SELECT count(*) FROM mx_task WHERE state=?", (ts.RUNNING,)
        ).fetchone()[0]
        if running >= limit:
            return None
        row = conn.execute(
            "SELECT task_id, title, payload_json, attempts FROM mx_task "
            "WHERE state=? ORDER BY priority, due_utc, created_utc LIMIT 1",
            (ts.QUEUED,)).fetchone()
        if row is None:
            return None
        task_id = str(row[0])
        attempts = int(row[3] or 0)
        if attempts >= MAX_ATTEMPTS:
            # Потолок повторов исчерпан. Провал честнее вечного кружения.
            _move(conn, task_id, ts.QUEUED, ts.FAILED, reason="error")
            return None
        _move(conn, task_id, ts.QUEUED, ts.RUNNING)
        conn.execute(
            "UPDATE mx_task SET run_id=?, attempts=attempts+1 WHERE task_id=?",
            (rid, task_id))
        return {"task_id": task_id, "title": str(row[1]),
                "payload_json": str(row[2]), "attempts": attempts + 1,
                "alive": busy}

    try:
        return writer.write(job, label="task_store.claim")
    except (TaskStoreError, ts.StateError):
        return None


# -- После перезапуска ----------------------------------------------------

def recover_after_restart() -> int:
    """Задачи в работе из ПРОШЛОГО запуска -> провалены, причина «рестарт».

    Возобновления НЕТ нарочно (I15, fail-closed). Задача могла успеть сделать
    половину работы на диске; начать её заново значит сделать половину дважды.
    Владелец попросит снова, если ему всё ещё надо.
    """
    if not _ready():
        return 0
    from core import writer
    from core.task_context import run_id
    rid = run_id()

    def job(conn):
        rows = conn.execute(
            "SELECT task_id FROM mx_task WHERE state=? "
            "AND (run_id IS NULL OR run_id<>?)", (ts.RUNNING, rid)).fetchall()
        for row in rows:
            _move(conn, str(row[0]), ts.RUNNING, ts.FAILED, reason="restart")
        return len(rows)

    try:
        return int(writer.write(job, label="task_store.recover") or 0)
    except (TaskStoreError, ts.StateError):
        return 0


# -- Чтение ---------------------------------------------------------------

def get(task_id: str) -> dict | None:
    """Одна задача. Чтение — своё соединение, не через кассу."""
    if not _ready():
        return None
    from core import writer
    try:
        row = writer.reader().execute(
            "SELECT task_id, title, state, priority, attempts, created_utc, "
            "finished_utc, cancel_reason, run_id FROM mx_task WHERE task_id=?",
            (str(task_id),)).fetchone()
    except Exception:
        return None
    return dict(row) if row is not None else None


def alive(limit: int = 20) -> list:
    """Живые задачи — то, что Джарвис перечислит владельцу вслух."""
    if not _ready():
        return []
    from core import writer
    marks = ",".join("?" * len(ts.ALIVE))
    try:
        rows = writer.reader().execute(
            f"SELECT task_id, title, state, priority, created_utc FROM mx_task "
            f"WHERE state IN ({marks}) ORDER BY priority, created_utc "
            f"LIMIT ?", (*ts.ALIVE, int(limit))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def recent(limit: int = 10) -> list:
    """Последние задачи любого состояния — для «чем ты занимался»."""
    if not _ready():
        return []
    from core import writer
    try:
        rows = writer.reader().execute(
            "SELECT task_id, title, state, finished_utc, cancel_reason "
            "FROM mx_task ORDER BY created_utc DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def calls_spent(task_id: str) -> int:
    """Сколько вызовов модели съела задача. Считается ИЗ УЧЁТА, а не из своей
    колонки: две копии одного числа рано или поздно разойдутся (решение
    блока 1, записанное в комментарии миграции 7)."""
    from core import writer
    try:
        row = writer.reader().execute(
            "SELECT count(*) FROM mx_meter_call WHERE task_id=?",
            (str(task_id),)).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
