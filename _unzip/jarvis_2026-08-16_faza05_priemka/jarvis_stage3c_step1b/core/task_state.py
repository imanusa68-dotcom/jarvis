# core/task_state.py
"""
Автомат состояний задачи (фаза 1, блок 8). Обещан комментарием миграции 7.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ СПИСОК ВНУТРИ ОЧЕРЕДИ
-------------------------------------------------
Колонка `state` в `mx_task` объявлена без CHECK, и это решение НАВСЕГДА:
SQLite не снимет CHECK без пересборки таблицы, а пересборка запрещена
правилом «только добавлять». Комментарий миграции 7 говорит прямо, почему
CHECK там нет: «автомат состояний ещё вырастет». Значит целостность держит
код — и держать её должно ОДНО место, иначе через полгода в базе окажется
состояние, которого не знает никто.

Проверено перед написанием: сегодня `contracts.insert_task` принимает в
`state` ЛЮБУЮ строку (`str(state)`, ни списка, ни проверки). То есть дыра
существует прямо сейчас, а не «на будущее».

ПОЧЕМУ ПЕРЕХОДЫ, А НЕ ТОЛЬКО СПИСОК
-----------------------------------
Список запрещает выдуманные слова, но разрешает бессмыслицу: задача из
`DONE` снова в `RUNNING`, отменённая — в `VERIFYING`. Такая бессмыслица не
падает, она тихо ломает подсчёты и приёмку. Поэтому здесь таблица законных
переходов, и незаконный переход — громкий отказ.

ДВА СЛОВАРЯ, КОТОРЫЕ НЕЛЬЗЯ ПУТАТЬ
----------------------------------
Здесь состояние ЗАДАЧИ. В `agent/contracts.py` есть `REPORT_STATUS` — статус
ОТЧЁТА, и в `core/blackbox.py` есть `OUTCOMES` — исход ЗАПИСИ. Три похожих
списка, три разные обязанности:

    задача:  чем она занята сейчас         (QUEUED, RUNNING, ...)
    отчёт:   что подчинённый сообщил       (done, partial, failed, refused)
    запись:  чем кончилась сессия записи   (done, partial, failed, cancelled)

Кто-нибудь обязательно решит, что это один список. Держит сторож.

ПОЧЕМУ СОСТОЯНИЯ ПРОПИСНЫМИ
---------------------------
Так они записаны в плане и в тестах, которые уже есть (`state == "QUEUED"` в
`tests/test_contracts.py` и `tests/test_migrations_7_18.py`). Старая очередь
в памяти писала строчными (`pending`, `running`) — и это ровно тот второй
словарь, который блок 8 удаляет, а не оставляет рядом.
"""
from __future__ import annotations

# Полный список состояний. Взят из раздела 3.4 плана дословно.
NEW = "NEW"                       # строка родилась, в очередь ещё не встала
QUEUED = "QUEUED"                 # ждёт свободного места
RUNNING = "RUNNING"               # выполняется прямо сейчас
VERIFYING = "VERIFYING"           # работа кончилась, идёт приёмка (фаза 3)
DONE = "DONE"                     # выполнена целиком
PARTIAL = "PARTIAL"               # выполнена частично, и это ЧЕСТНЫЙ исход
FAILED = "FAILED"                 # не выполнена
CANCELLED = "CANCELLED"           # владелец отменил
SUPERSEDED = "SUPERSEDED"         # вытеснена задачей той же формы (Д18)
FROZEN = "FROZEN"                 # владелец сказал «стоп», можно вернуть
WAITING_OWNER = "WAITING_OWNER"   # нужен ответ человека
WAITING = "WAITING"               # нет свободного места из потолка

STATES = (NEW, QUEUED, RUNNING, VERIFYING, DONE, PARTIAL, FAILED,
          CANCELLED, SUPERSEDED, FROZEN, WAITING_OWNER, WAITING)

# Состояния, из которых задача уже никуда не уйдёт. Отдельным списком, потому
# что на нём стоит вся уборка и весь подсчёт «сколько сейчас живых».
FINAL = (DONE, PARTIAL, FAILED, CANCELLED, SUPERSEDED)

# Живые состояния — те, что занимают место из потолка одновременных задач.
# VERIFYING сюда входит: приёмка тоже работа, и пока она идёт, задача не
# освободила слот.
ALIVE = (NEW, QUEUED, RUNNING, VERIFYING, WAITING_OWNER, WAITING, FROZEN)

# Причины отмены. Список из комментария миграции 7, плюс две добавленные
# кодом, каждая по замеру:
#   restart — задача была в работе, когда процесс умер (I15);
#   gate    — дверь безопасности не пустила действие. Отдельно от 'error'
#             нарочно: сбой и запрет — разные вещи, и различать их придётся в
#             тот день, когда владелец спросит «почему не сделал». Сваленные в
#             одну причину, они дадут ответ «что-то сломалось» вместо правды.
CANCEL_REASONS = ("owner_stop", "superseded", "budget", "error", "restart",
                  "gate")

# Законные переходы. Читать так: из состояния-ключа можно уйти только в
# перечисленные. Каждый переход обоснован, лишних нет.
LEGAL: dict = {
    NEW: (QUEUED, WAITING, CANCELLED, SUPERSEDED),
    # WAITING -> QUEUED: место освободилось.
    WAITING: (QUEUED, CANCELLED, SUPERSEDED, FROZEN),
    QUEUED: (RUNNING, WAITING, CANCELLED, SUPERSEDED, FROZEN, FAILED),
    # FAILED из RUNNING — это и рестарт (I15), и обычный провал.
    RUNNING: (VERIFYING, DONE, PARTIAL, FAILED, CANCELLED, FROZEN,
              WAITING_OWNER),
    # Приёмка выдаёт только эти три исхода (13.7.17 п.3).
    VERIFYING: (DONE, PARTIAL, FAILED, WAITING_OWNER),
    # Ответил владелец — возвращаемся в работу; не ответил — отказ (I23).
    WAITING_OWNER: (QUEUED, RUNNING, CANCELLED, FAILED, FROZEN),
    # 13.4 п.12: FROZEN -> QUEUED и PARTIAL -> QUEUED существуют нарочно.
    FROZEN: (QUEUED, CANCELLED, SUPERSEDED),
    PARTIAL: (QUEUED,),
    # Остальные окончательны. Пустой кортеж — это не «забыли», а решение.
    DONE: (),
    FAILED: (),
    CANCELLED: (),
    SUPERSEDED: (),
}


class StateError(ValueError):
    """Незаконный переход или незнакомое состояние.

    Громкий отказ нарочно. Тихо записанное неверное состояние не падает — оно
    ломает подсчёты и приёмку через несколько недель, и найти концы будет
    негде.
    """


def is_state(value) -> bool:
    return isinstance(value, str) and value in STATES


def is_final(value) -> bool:
    return value in FINAL


def is_alive(value) -> bool:
    return value in ALIVE


def can(src: str, dst: str) -> bool:
    """Законен ли переход. Ничего не бросает — для вопросов, а не для правок."""
    if not is_state(src) or not is_state(dst):
        return False
    return dst in LEGAL.get(src, ())


def check(src: str, dst: str) -> None:
    """Разрешить переход или отказать вслух. Зовётся ПЕРЕД записью."""
    if not is_state(src):
        raise StateError(f"незнакомое состояние-источник: {src!r}")
    if not is_state(dst):
        raise StateError(f"незнакомое состояние-цель: {dst!r}")
    if dst not in LEGAL.get(src, ()):
        allowed = ", ".join(LEGAL.get(src, ())) or "никуда, состояние конечное"
        raise StateError(
            f"переход {src} -> {dst} незаконен; из {src} можно: {allowed}")


def check_reason(reason) -> None:
    """Причина отмены — из закрытого списка. Свободный текст запрещён ФОРМОЙ.

    Причина уезжает в колонку, которую потом будут читать глазами и считать
    машиной. Свободная строка там означает, что через полгода на вопрос
    «почему задача умерла» будет двести разных ответов вместо пяти.
    """
    if reason is None:
        return
    if reason not in CANCEL_REASONS:
        raise StateError(
            f"причина {reason!r} не из списка {CANCEL_REASONS}")
