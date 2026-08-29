# agent/task_queue.py
"""
Очередь задач, которая переживает перезапуск (фаза 1, блок 8, шаг 17).

ЧТО ЗДЕСЬ ПРОИЗОШЛО И ПОЧЕМУ ЭТО ЗАМЕНА, А НЕ ДОБАВЛЕНИЕ
--------------------------------------------------------
До блока 8 в этом файле жила очередь В ПАМЯТИ: список объектов, номер вида
`uuid4()[:8]`, свои состояния строчными буквами (`pending`, `running`) и живые
функции внутри задачи. Она была ЖИВОЙ: инструмент `agent_task` объявлен
голосовой модели, дверь безопасности его пропускает, и владелец мог сказать
«разбери загрузки» (проверено замером 19.08.2026).

И у неё было одно свойство, которое нельзя было оставить: **процесс упал —
задачи не было**. Ни строки, ни следа, ни возможности сказать владельцу «то,
что ты просил вчера, сорвалось». Правило проекта «старый путь УДАЛЁН, не
выключен» поэтому и означает замену: второй очереди рядом не появилось.

Старая модель задачи ушла целиком: в её полях лежали ВЫЗЫВАЕМЫЕ ФУНКЦИИ
(`speak`, `on_complete`), а такое в базу не положить в принципе. Осталось
только то, что можно записать: номер, цель, состояние, попытки.

НАРУЖНЫЙ ВИД НЕ ИЗМЕНИЛСЯ НИ НА ЗНАК, И ЭТО ГЛАВНОЕ ОГРАНИЧЕНИЕ
---------------------------------------------------------------
`main.py` заморожен: за всю фазу 1 его тронули один раз, и у него есть
сторож на слепок. Он зовёт ровно это:

    from agent.task_queue import get_queue, TaskPriority
    get_queue().submit(goal=..., priority=..., speak=...)  ->  строка номера

Значит имена `get_queue`, `TaskPriority`, `submit` и форма ответа обязаны
остаться. Внутри изменилось всё, снаружи — ничего. Владелец услышит только
одно отличие: номер станет `T-20260819-001` вместо `a3f9c1d2`, то есть его
можно будет назвать вслух и найти в базе.

ЧТО ТЕПЕРЬ ДЕЛАЕТ ПОТОК-РАБОТНИК
--------------------------------
1. Берёт задачу через `task_store.claim()` — атомарно, с потолком, из очереди.
2. Ставит ПРОПУСК с номером дела (`with bind(...)`). Это не косметика: пропуск
   даёт учёту расхода понять, какому делу принадлежит вызов модели, а без
   этого потолок «не больше восьми вызовов на задачу» применить нечем.
   Замер блока 3: контекст в поток НЕ переносится, поэтому ставим здесь сами.
3. Перед каждым шагом смотрит, не съеден ли потолок вызовов.
4. Закрывает задачу настоящим исходом.

ПОЧЕМУ ВОССТАНОВЛЕНИЕ ЗОВЁТСЯ ЛЕНИВО, А НЕ ПРИ СТАРТЕ
-----------------------------------------------------
Правило I15 («задача в работе из прошлого запуска становится проваленной и
сама не возобновляется») хотелось бы выполнить в первую секунду запуска. Но
`main.py` заморожен, и позвать оттуда нечего. Поэтому уборка идёт при первом
обращении к очереди.

Это не ослабление правила, и вот почему: безопасность даёт НЕ уборка, а то,
что в работу берутся только задачи из состояния «в очереди». Задачу чужого
запуска физически некому взять — она просто лежит помеченной как работающая,
пока уборка не приведёт базу в честный вид. Конструкция защищает, уборка
убирает.
"""
from __future__ import annotations

import threading
import time
from enum import Enum

from agent import task_store as tstore
from core import task_state as ts


class TaskPriority(Enum):
    """Меньше — раньше. Числа совпадают с колонкой priority в базе.

    Имя и значения сохранены дословно: `main.py` строит по ним свою карту
    («low»/«normal»/«high»), а он заморожен.
    """
    LOW = 3
    NORMAL = 2
    HIGH = 1


def _say(line: str) -> None:
    """Печать, которая НИКОГДА не роняет вызов.

    Образец не новый: `_out` в core/aux_model.py, `_say` в
    core/state_snapshot.py и core/model_guard.py. На консоли cp1251 обычный
    print со значком бросает UnicodeEncodeError, и если это случится внутри
    обработчика ошибки, вместо честного отказа наверх уйдёт падение.
    """
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            print(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    except Exception:
        pass


class TaskQueue:
    """Очередь поверх таблицы задач. Своего состояния почти не держит.

    В памяти живут только две вещи, которые в базу не положить: флаги отмены
    (их проверяет работающий поток между шагами) и голос, которым надо
    отвечать. Всё остальное — в базе, и поэтому переживает перезапуск.
    """

    def __init__(self, max_concurrent: int | None = None):
        # Потолок живёт в task_store и берётся из настроек владельца. Здесь
        # параметр оставлен только для тестов; второго числа про одно и то же
        # в проекте быть не должно.
        self._max_concurrent = max_concurrent
        self._lock = threading.Lock()
        self._cancel: dict = {}       # номер дела -> флаг отмены
        self._voices: dict = {}       # номер дела -> чем говорить
        self._threads: dict = {}      # номер дела -> поток
        self._recovered = False
        self._stopped = False
        # Сколько работников СЕЙЧАС внутри, считая тех, кто уже закрыл свою
        # задачу и берёт следующую. Ждать по списку потоков оказалось
        # недостаточно — см. объяснение в stop().
        self._inflight = 0
        self._idle = threading.Condition(self._lock)
        self._executor = None

    # -- Наружный вид (его зовёт main.py) ---------------------------------

    def submit(self, goal: str, priority=TaskPriority.NORMAL,
               speak=None, on_complete=None) -> str:
        """Принять задачу. Возвращает номер дела — его владелец услышит.

        Строка в базе появляется ДО того, как запустится поток. Иначе
        существовал бы промежуток, в котором работа уже идёт, а следа о ней
        нет — ровно то, что блок 8 и лечит.
        """
        self._recover_once()
        weight = priority.value if isinstance(priority, TaskPriority) else int(
            priority or TaskPriority.NORMAL.value)
        try:
            task_id = tstore.create(goal, priority=weight)
        except Exception as exc:                       # noqa: BLE001
            # Не приняли — говорим вслух. Молчаливая потеря просьбы владельца
            # хуже честного отказа (I19).
            _say(f"[Очередь] задачу не принял: {exc}")
            raise
        with self._lock:
            self._cancel[task_id] = threading.Event()
            if speak is not None:
                self._voices[task_id] = speak
        _say(f"[Очередь] принята {task_id}: {str(goal)[:60]}")
        self._pump()
        return task_id

    def cancel(self, task_id: str) -> bool:
        """Отменить задачу. Работающий поток увидит флаг между шагами."""
        with self._lock:
            flag = self._cancel.get(str(task_id))
        if flag is not None:
            flag.set()
        row = tstore.get(task_id)
        if row is None:
            return False
        if ts.is_final(str(row["state"])):
            return False
        moved = tstore.finish(task_id, ts.CANCELLED, reason="owner_stop")
        if moved:
            _say(f"[Очередь] отменена {task_id}")
        return moved

    def get_status(self, task_id: str) -> dict | None:
        """Состояние одной задачи. Читается из базы, а не из памяти."""
        row = tstore.get(task_id)
        if row is None:
            return None
        return {"task_id": row["task_id"], "goal": row["title"],
                "status": row["state"], "attempts": row["attempts"],
                "reason": row["cancel_reason"],
                "calls": tstore.calls_spent(task_id)}

    def get_all_statuses(self) -> list:
        """Живые задачи — то, что Джарвис перечислит владельцу."""
        return [{"task_id": t["task_id"], "goal": t["title"],
                 "status": t["state"]} for t in tstore.alive()]

    def pending_count(self) -> int:
        return sum(1 for t in tstore.alive() if t["state"] == ts.QUEUED)

    def start(self) -> None:
        """Оставлено ради прежнего вида. Отдельного вечного потока больше нет:
        работник рождается под задачу и умирает с ней. Вечный поток, который
        просыпается раз в секунду и почти всегда ничего не находит, — это
        расход батареи на пустом ноутбуке."""
        self._recover_once()

    def stop(self, *, wait_s: float = 10.0) -> None:
        """Остановить очередь: новых задач не брать, живым — отмену, и ДОЖДАТЬСЯ.

        ЭТО НЕ УБОРКА РАДИ КРАСОТЫ. Найдено падением прогона 20.08.2026 —
        `access violation`, то есть смерть всего процесса, а не красный тест.
        Стек показал ровно то, чего быть не должно:

            поток-работник: берёт следующую задачу -> касса -> ПИШЕТ
            другой поток:   в это же мгновение ЗАКРЫВАЕТ то же соединение

        Обращение к закрытому соединению — авария на уровне C. Никакой
        `try/except` её не поймает: процесс просто исчезает.

        ПЕРВАЯ ПРАВКА БЫЛА НЕВЕРНОЙ, и это важно записать. Я ждал по списку
        живых потоков — а работник вычёркивает себя из этого списка в своём
        `finally` ДО того, как позовёт следующую задачу. Значит `stop()` видел
        пустой список, считал, что всё тихо, и отдавал соединение на закрытие,
        пока работник уже входил в новую запись. Авария повторилась.

        Поэтому ждём не по списку потоков, а по СЧЁТЧИКУ «работников внутри»:
        он растёт до начала работы и падает только тогда, когда работник
        действительно вышел, включая попытку взять следующую задачу.

        Ждём с потолком: зависший работник не имеет права держать выход из
        программы. Лучше уйти без него, чем не уйти совсем.
        """
        with self._lock:
            self._stopped = True
            flags = list(self._cancel.values())
        for flag in flags:
            flag.set()
        deadline = time.monotonic() + max(0.0, wait_s)
        with self._idle:
            while self._inflight > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    _say(f"[Очередь] работники не вышли за {wait_s:g} с "
                         f"(осталось {self._inflight}) — ухожу без них")
                    break
                self._idle.wait(timeout=min(left, 0.2))

    # -- Внутреннее -------------------------------------------------------

    def _recover_once(self) -> None:
        """Привести базу в честный вид после перезапуска. Один раз за процесс."""
        with self._lock:
            if self._recovered:
                return
            self._recovered = True
        try:
            fixed = tstore.recover_after_restart()
        except Exception as exc:                       # noqa: BLE001
            _say(f"[Очередь] уборка после перезапуска не вышла: {exc}")
            return
        if fixed:
            _say(f"[Очередь] задач было в работе на момент падения: {fixed} — "
                 f"пометил провалом, сами возобновляться не будут")

    def _get_executor(self):
        if self._executor is None:
            from agent.executor import AgentExecutor
            self._executor = AgentExecutor()
        return self._executor

    def _pump(self) -> None:
        """Взять всё, что можно, и запустить работников.

        Потолок и атомарность взятия — забота базы. Здесь только цикл: пока
        база даёт задачи, запускаем поток на каждую.
        """
        while True:
            with self._lock:
                if self._stopped:
                    return
            if self._max_concurrent is not None:
                running = sum(1 for t in tstore.alive()
                              if t["state"] == ts.RUNNING)
                if running >= self._max_concurrent:
                    return
            got = tstore.claim()
            if not got:
                return
            task_id = got["task_id"]
            thread = threading.Thread(
                target=self._run, args=(task_id, got.get("title") or ""),
                daemon=True, name=f"Task-{task_id}")
            with self._lock:
                # Счётчик растёт ДО старта потока: между «взяли задачу» и
                # «поток побежал» тоже нельзя закрывать соединение.
                self._inflight += 1
                self._threads[task_id] = thread
                self._cancel.setdefault(task_id, threading.Event())
            try:
                thread.start()
            except Exception:
                with self._lock:
                    self._inflight -= 1
                    self._threads.pop(task_id, None)
                    self._idle.notify_all()
                raise

    def _limits_for(self, task_id: str) -> int:
        """Потолок вызовов модели на эту задачу. Число живёт в yaml, не здесь."""
        try:
            from agent.contracts import known_types
            spec = known_types().get(tstore.FREE_GOAL) or {}
            got = (spec.get("limits") or {}).get("max_llm_calls")
            if isinstance(got, int) and got > 0:
                return got
        except Exception:
            pass
        return 8

    def _run(self, task_id: str, title: str) -> None:
        """Работник одной задачи. Никогда не бросает наверх.

        Пропуск ставится ЗДЕСЬ, потому что замер блока 3 показал: контекст в
        новый поток не переносится. Без пропуска учёт не знает, какому делу
        принадлежит вызов модели, и потолок применить нечем.
        """
        from core.task_context import TaskCtx, bind, run_id
        from agent.executor import ToolRefused
        with self._lock:
            flag = self._cancel.get(task_id) or threading.Event()
            speak = self._voices.get(task_id)

        # origin_chain передаётся ЯВНО, и это не украшение (найдено замером
        # 28.08.2026 при подготовке фазы 1б). Без него берётся значение по
        # умолчанию ("owner",) — и цепочка задачи выходила КОРОЧЕ, чем у
        # обычного разговора:
        #     задача агента : owner
        #     диалог        : owner -> main   (task_context.dialog_ctx)
        # То есть работа, идущая без владельца у экрана, выглядела МЕНЕЕ
        # прослеженной, чем разговор при нём, — ровно наоборот от смысла
        # журнала. Задача всегда рождается из разговора, поэтому звено `main`
        # обязано быть на месте: по нему видно, что дело поручил главный, а не
        # оно возникло само.
        #
        # ГЛУБИНА ОСТАЁТСЯ НУЛЁМ, и это исправление моей же ошибки, найденное
        # замером 28.08.2026 — здесь сначала стояла глубина 1 с объяснением
        # «длина цепочки и глубина суть одно». Объяснение было НЕВЕРНЫМ, и
        # опровергает его сосед по этому же файлу: у `dialog_ctx()` цепочка из
        # двух звеньев (owner->main) при depth=0. Значит цепочка отвечает на
        # «через кого пришло», а глубина — на «сколько раз ещё можно поручить».
        # Это разные вопросы, и связывать их нельзя.
        #
        # Чем именно вредила глубина 1 (замерено, не предположено):
        #     depth=1 -> агент 2 -> подчинённый ЗАПРЕЩЁН (MAX_DEPTH=2)
        #     depth=0 -> агент 1 -> подчинённый 2, разрешён
        # То есть лишняя единица молча съедала целый уровень поручения —
        # ровно тот, что схема базы называет своим именем (core/store.py:404:
        # «depth 0 владелец, 1 агент, 2 предел рекурсии»).
        #
        # И второе: единственное живое создание задачи — tstore.create() на
        # строке 138 этого файла, с depth по умолчанию, то есть 0. В базе у
        # задачи лежит 0. Пиши я в журнал 1 — строка журнала спорила бы с
        # строкой базы об одной и той же задаче, а расхождение двух записей
        # об одном хуже отсутствия обеих: обе выглядят достоверными.
        ctx = TaskCtx(run_id=run_id(), task_id=task_id, bucket="task",
                      origin_chain=("owner", "main"))
        cap = self._limits_for(task_id)
        _say(f"[Очередь] ▶ работаю {task_id}: {title[:60]}")

        outcome, reason = ts.FAILED, "error"
        try:
            with bind(ctx):
                if tstore.calls_spent(task_id) >= cap:
                    outcome, reason = ts.PARTIAL, "budget"
                    _say(f"[Очередь] {task_id}: потолок вызовов исчерпан "
                         f"до начала работы")
                else:
                    answer = self._get_executor().execute(
                        goal=title, speak=speak, cancel_flag=flag)
                    spent = tstore.calls_spent(task_id)
                    if flag.is_set():
                        outcome, reason = ts.CANCELLED, "owner_stop"
                    elif spent > cap:
                        # Потолок пробит по ходу дела: работа могла остаться
                        # недоделанной, и назвать это «выполнено» было бы ложью.
                        outcome, reason = ts.PARTIAL, "budget"
                        _say(f"[Очередь] {task_id}: вызовов {spent} при "
                             f"потолке {cap} — считаю выполненной частично")
                    else:
                        outcome, reason = ts.DONE, None
                    _say(f"[Очередь] ✔ {task_id}: {str(answer)[:70]}")
        except ToolRefused as refused:
            # Дверь безопасности не пустила действие. В базу обязан лечь
            # ОТКАЗ, а не «выполнено»: строка живёт вечно, и в фазе 3 её
            # прочитает приёмка. Причина отдельная от 'error' — это не сбой,
            # это запрет, и различать их придётся, когда владелец спросит
            # «почему не сделал».
            outcome, reason = ts.FAILED, "gate"
            _say(f"[Очередь] 🛡 {task_id}: запрещено дверью — "
                 f"{str(refused)[:70]}")
        except Exception as exc:                       # noqa: BLE001
            _say(f"[Очередь] ✖ {task_id}: {exc}")
        finally:
            try:
                tstore.finish(task_id, outcome, reason=reason)
            except Exception:
                pass
            # ЗАКРЫТЬ ЗАПИСЬ ЧЁРНОГО ЯЩИКА ТЕМ ЖЕ ИСХОДОМ.
            #
            # Найдено ЖИВОЙ ПРОБОЙ ВЛАДЕЛЬЦА 20.08.2026, и это дефект, который
            # создал сам блок 8. До него записи чёрного ящика были на ЗАПУСК, и
            # закрывать их посреди работы было некому — их подбирала уборка при
            # следующем старте. С приходом номера дела запись стала на ЗАДАЧУ, и
            # у неё появился естественный момент закрытия: вот он.
            #
            # Что было видно в базе владельца после одной успешной задачи:
            #     задача  -> DONE
            #     запись  -> исход failed, вызовов 0, открыта
            #     учёт    -> 2 вызова
            # Три числа об одном деле, и два из трёх врут. Хуже того: уборка при
            # следующем старте закрыла бы запись как «прервано» — то есть чёрный
            # ящик навсегда запомнил бы, что успешная задача сорвалась. Ровно то,
            # ради чего он и существует, оказалось бы ложью.
            #
            # Исходы совпадают по написанию, но это РАЗНЫЕ списки (у отчёта
            # вместо «прервано» стоит «отказано»), поэтому перевод явный.
            try:
                from core import blackbox
                from core import task_state as _ts
                _bb = {_ts.DONE: "done", _ts.PARTIAL: "partial",
                       _ts.FAILED: "failed", _ts.CANCELLED: "cancelled"}
                mapped = _bb.get(outcome)
                if mapped:
                    blackbox.close_rec("B-" + str(task_id), mapped)
            except Exception:
                pass
            with self._lock:
                self._threads.pop(task_id, None)
                self._voices.pop(task_id, None)
            # Место освободилось — может быть, кто-то ждёт. `_pump` сам
            # увеличит счётчик под нового работника, поэтому свой мы
            # опускаем ПОСЛЕ него: иначе между двумя этими действиями
            # счётчик стал бы нулём при живой работе, и `stop()` решил бы,
            # что уже тихо. Ровно на этом прогон падал дважды.
            try:
                self._pump()
            except Exception:
                pass
            with self._idle:
                self._inflight -= 1
                self._idle.notify_all()


_queue = TaskQueue()
_queue_lock = threading.Lock()


def get_queue() -> TaskQueue:
    """Единственная очередь на процесс. Имя сохранено: его зовёт main.py."""
    with _queue_lock:
        _queue._recover_once()
    return _queue


def reset_for_tests() -> None:
    """Забыть флаги и защёлку уборки. Зовёт tests/conftest.py.

    Защёлка «уборка прошла» живёт на процесс, а весь прогон — один процесс:
    без сброса первый же тест выключил бы уборку для всех следующих, и они
    были бы зелёными по неверной причине. Та же болезнь, что у восьми других
    пунктов списка в conftest.

    Прежняя очередь СНАЧАЛА останавливается, и только потом заменяется. Без
    этого её работники продолжали брать новые задачи и лезли в дом
    предыдущего теста — поймано как плавающее падение прогона (20.08.2026).
    """
    global _queue
    with _queue_lock:
        try:
            _queue.stop()
        except Exception:
            pass
        _queue = TaskQueue()
