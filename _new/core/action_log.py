# core/action_log.py
"""
Одна касса для записи «что было сделано» (шаг 30, фаза 0.7).

Зачем этот файл появился
------------------------
До него в проекте жили ДВА журнала, которые не знали друг о друге:

  * память — core/dialogue_state.record_action: восемь последних строк,
    умирает вместе с процессом. Туда писал только онлайновый путь main.py.
  * база   — core/journal.Journal.record_action, таблица action_journal
    в ~/.jarvis/jarvis.db. Туда писал только файловый контроллер
    через actions/fileops_bridge.py.

Вопрос владельца «что ты делал» читает БАЗУ (core/offline_core._route_journal),
а оффлайновый исполнитель не писал вообще никуда. Поэтому без сети ответ был
всегда один: «журнал пуст». Джарвис открывал блокнот и тут же об этом забывал.

Правила этого файла
-------------------
1. Касса НИКОГДА не ломает дело. Любая ошибка внутри — тишина и False.
   Записка о действии не стоит того, чтобы из-за неё упало само действие.
2. Под прогоном тестов настоящая база владельца не трогается, и память
   диалога тоже: восемьдесят файлов тестов не должны писать друг другу
   в состояние. Предохранитель снимается переменной JARVIS_STATE_DIR —
   тест выставляет временную папку и получает полноценную кассу.
3. Соединение с базой открывается один раз и живёт с процессом. Старый путь
   открывал новое соединение на каждый вопрос и не закрывал его.
4. Ничего не печатает: кассу зовёт оффлайн-ядро, а там печать запрещена.
"""
from __future__ import annotations

import os
import sys

# Столько строк показывает «что ты делал». Тот же размер, что у памяти
# и у горячего среза core/journal.JOURNAL_MAX — три места не должны спорить.
RECENT_MAX = 8

_conn = None
_conn_path = None


def _enabled() -> bool:
    """
    Работает ли касса прямо сейчас.

    Под pytest — нет, если только тест сам не показал, куда писать.
    Живой запуск (python main.py) — да, всегда.
    """
    if os.environ.get("JARVIS_STATE_DIR", "").strip():
        return True
    return "pytest" not in sys.modules


def reset() -> None:
    """Забыть соединение. Нужно тестам и на случай сломанной базы.

    Блок 7: соединение теперь принадлежит кассе, и закрывать его здесь НЕЛЬЗЯ
    — им пользуются журнал, талоны и файловые операции. Забываем только
    ссылку; закроет соединение тот, кто им владеет.
    """
    global _conn, _conn_path
    _conn = None
    _conn_path = None


def _journal():
    """Журнал на соединении КАССЫ. Ввоз ленивый: ядро не тащит базу при старте.

    Блок 7: раньше здесь было своё соединение. Оно снято не ради экономии
    файловых ручек, а потому что писатель со своим соединением стоит вне общей
    очереди — а очередь и есть весь смысл одной кассы.
    """
    global _conn, _conn_path
    from core import store, writer
    from core.journal import Journal
    path = str(store.db_path())
    if _conn is None or _conn_path != path:
        _conn = writer.conn()
        _conn_path = path
    return Journal(_conn)


def note(tool, action=None, summary="", ok=True, ctx=None) -> bool:
    """
    Записать одно сделанное действие в оба журнала сразу.

    Возвращает True, если запись легла хотя бы в один из них.

    Про `ctx` (фаза 1, блок 3). Колонка `correlation_id` в таблице
    action_journal есть с версии схемы 2, и до этого блока в неё никогда
    ничего не писали. Следствие было не «на будущее»: у журнала не было
    вообще никакой пометки о запуске, поэтому «что ты делал» отвечало
    вперемешку — сегодняшнее и то, что было неделю назад, без возможности
    различить. Теперь каждая строка подписана номером запуска.

    Пропуск берётся у core/task_context: явно переданный сильнее того, что в
    контексте, а если нет ни того ни другого — пропуск разговора. Пустоты не
    бывает: строка без номера — ровно то, что здесь лечится.

    Почему это работает без правок в main.py: там инструменты зовутся через
    `run_in_executor`, а он контекст НЕ переносит (замерено). Но запись сюда
    идёт ПОСЛЕ await, уже вернувшись в цикл, где пропуск виден. Замер
    закреплён тестом test_run_in_executor_does_not_carry_the_context.
    """
    if not tool:
        return False
    text = str(summary).strip().replace("\n", " ")
    if not text:
        text = str(action or tool)
    if not _enabled():
        return False
    landed = False
    try:
        from core.dialogue_state import record_action as _ram
        _ram(tool=str(tool), action=action, summary=text, ok=bool(ok))
        landed = True
    except Exception:
        pass
    try:
        _journal().record_action(str(tool), action, text, ok=bool(ok),
                                 correlation_id=_correlation(ctx))
        landed = True
    except Exception:
        reset()
    return landed


def _correlation(ctx=None) -> str | None:
    """Строка `run:.../task:.../step:N` для журнала. Никогда не мешает делу.

    Отдельной функцией — это шов для теста и место, где ошибка пропуска не
    имеет права уронить запись: правило 1 этого файла сильнее номера.
    """
    try:
        from core import task_context
        return task_context.current(ctx).correlation()
    except Exception:
        return None


def recent(limit=RECENT_MAX):
    """Последние действия из базы. Пусто — значит пусто, врать не будем."""
    if not _enabled():
        return []
    try:
        return _journal().recent_actions(limit)
    except Exception:
        reset()
        return []
