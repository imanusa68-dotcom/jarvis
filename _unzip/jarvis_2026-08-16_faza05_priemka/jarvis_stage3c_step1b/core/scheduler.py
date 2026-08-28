# core/scheduler.py
"""
Правда о сроках напоминаний. Фаза 1, блок 10, шаг 20.

ЗАЧЕМ ЭТОТ ФАЙЛ ПОЯВИЛСЯ
------------------------
До него напоминания жили в `memory/reminders.json` В ПАПКЕ СБОРКИ и правились
чтением-правкой-записью без замка. Замерено 22.08.2026 на песочнице, десять
дефектов; вот главные, с числами:

    владелец ставит напоминание, пока проверялка вычёркивает сработавшее
        -> НОВОЕ ПОТЕРЯНО В 30 СЛУЧАЯХ ИЗ 40 (75%)
    опоздание 31 минута и больше
        -> МОЛЧА НЕ СРАБОТАЕТ НИКОГДА, но из файла не уйдёт
    два напоминания в одно окно
        -> сказал первое, УДАЛИЛ ОБА
    39 просроченных
        -> перечитываются каждые 30 секунд ВЕЧНО, никто их не убирает
    битый файл
        -> молча пусто, потом первая же запись ЗАТИРАЕТ; ни карантина, ни копии
    порядок
        -> вычёркивают ДО произнесения: упал между делом — напоминание исчезло

Потеря в 75% хуже, чем была у памяти (50%, блок 9), и по той же причине:
писателей двое, и один из них дёргается каждые 30 секунд, то есть окно
наложения открыто постоянно.

ЧЕТЫРЕ РЕШЕНИЯ, НА КОТОРЫХ ВСЁ ДЕРЖИТСЯ
---------------------------------------
1. НИЧЕГО НИКОГДА НЕ УДАЛЯЕТСЯ. Сработало -> state='done'. Отменено ->
   state='cancelled'. Строка остаётся. Это одним движением закрывает четыре
   дефекта из десяти: терять нечего, потому что не удаляем. И даёт «покажи, что
   было» бесплатно.

2. ЧИТАЕМ ВНЕ КАССЫ, ПИШЕМ ВНУТРИ. Замерено на копии базы владельца: холостой
   тик 0,0055 мс, за сутки 16 мс, план запроса
   `SEARCH mx_reminder USING INDEX mx_reminder_due_idx`. Касса записи не
   трогается 99,9% времени, значит тик никогда не встанет на пути речи
   владельца. Индекс построен ещё в блоке 2 — новых миграций ноль.

3. ОДИН СРОК, ДВА ПОЛЯ. `due_utc` — настоящий UTC, `due_raw` — исходная строка
   как есть. Старый код кладёт в JSON строку С МЕСТНЫМ СДВИГОМ
   ('2026-04-10T15:00:00+02:00'); записать её прямо в колонку с именем
   `due_utc` — значит сдвинуть все напоминания на часы, и заметили бы это в
   день перевода часов. Перевод живёт в одном месте (`_to_utc`), как «сброс
   11:00 МСК» живёт только в `metering.quota_day`.

4. ОПОЗДАВШЕЕ НАЗЫВАЕТ СВОЙ СРОК. Если в 19:00 просто произнести «работать с
   проектом», владелец услышит «пора сейчас» — то есть Джарвис его
   дезинформирует. Опоздавшее обязано говорить «было на 12:00, опоздал на
   7 часов». Старый код этого не делал вовсе.

НОЛЬ ВЫЗОВОВ МОДЕЛИ (Д4)
Здесь нет и не может быть обращений к облаку: всё решают часы и одна выборка по
индексу. Сторож это проверяет, потому что обещание словами не держится ничем.

ЧЕГО В ПЛАНЕ НЕТ, И Я ЭТО РЕШИЛ САМ
Проверено поиском по плану, а не по памяти: там НЕТ ни периодичности тика, ни
правил часового пояса для напоминаний, ни алгоритма догона пропущенных, ни
срока хранения, ни одного инвариантного номера на напоминания. Пять решений
приняты здесь и названы вслух — числа собраны в одном месте ниже.

ЧЕГО ЗДЕСЬ НЕТ НАРОЧНО
  * ПОВТОРЯЮЩИХСЯ напоминаний («каждый день в 12:00»). В таблице нет колонки
    повторяемости, и добавить её — это правка схемы, то есть отдельный блок.
    Сегодня такая просьба ляжет ОДНОРАЗОВЫМ напоминанием. Записано в долги.
  * внешних календарей (Д21): о встречах только со слов владельца;
  * будильника (Д30): это напоминание, а не звонок до отключения.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# -- Пять чисел, которых в плане нет ---------------------------------------
# Собраны здесь нарочно: решение, размазанное по коду, через месяц нельзя ни
# найти, ни обсудить.

# За сколько до срока предупредить. План (Д30) называет 15 минут.
PRE_MINUTES = 15

# Ближе этого предупреждать бессмысленно: на «напомни через 5 минут»
# предупреждение прозвучало бы почти сразу после постановки, то есть дважды об
# одном за пять минут.
PRE_SKIP_UNDER_MINUTES = 20

# Через сколько повторить, если владелец не отозвался (Д30).
RETRY_MINUTES = 3


# Сколько времени после нашей фразы ответ владельца считается «я услышал».
# Позже этого срока ответ означает «я вернулся и, скорее всего, НЕ слышал».
#
# ЗАЧЕМ ЭТО РАЗЛИЧЕНИЕ. Живая проба владельца 22.08.2026, его слова:
# «бывает такое что я могу вообще отойти куда-то больше этих 3 минут и когда
# вернусь и начну общаться то он мне не напомнит то что должен был».
#
# Так и было: повтор звучал в пустую комнату по таймеру и закрывал напоминание.
# Джарвис считал дело сделанным, а владелец не слышал ни слова. Теперь
# напоминание НЕ ЗАКРЫВАЕТСЯ, пока не появится признак, что владелец был рядом.
#
# Признак один — он заговорил. Скоро после нашей фразы = слышал. Много позже =
# отходил. Различение по времени приблизительное, и это названо вслух: внутри
# трёх минут мы ПРЕДПОЛАГАЕМ, что услышал. Ошибка возможна, но её цена — одна
# лишняя фраза, а цена обратной ошибки — потерянное напоминание.
RETURN_GRACE_S = 180

# 22.08.2026: напоминание, вышедшее через минуту после срока, объявляло «вы
# опоздали на 1 минуту» — и это пугало на ровном месте. Тик ходит раз в 30
# секунд, поэтому опоздание в полминуты-минуту — норма работы, а не событие.
# Смысл слова «опоздал» только один: не дать владельцу услышать «пора сейчас»
# там, где пора было давно. Десять минут — порог, ниже которого «сейчас» и
# «тогда» для человека одно и то же.
LATE_WORDS_FROM_S = 600

# Докакого возраста вообще говорить о просроченном. ВЫБРАНО ВЛАДЕЛЬЦЕМ
# 22.08.2026: он работает вечерами, ноутбук может простоять несколько дней, и
# за это время дело обычно ещё живое. Старше — не произносим, но строку НЕ
# теряем: её назовёт «покажи мои напоминания». Старый код терял всё старше
# 30 минут молча и навсегда.
STALE_DAYS = 7

# Сколько держать отработанные строки. Тридцать дней — то же число, что у
# результатов и отчётов в плане. `armed` не убирается никогда.
KEEP_DAYS = 30

# Больше этого числа за один тик не берём: защита от вечера, когда просроченных
# сотня. Правило залпа всё равно сжимает их в одну фразу, но выборку стоит
# ограничить, а не надеяться.
BATCH = 50

STATE_ARMED = "armed"
STATE_DONE = "done"
STATE_CANCELLED = "cancelled"

_ID = re.compile(r"^R-(\d{8})-(\d+)$")


# -- Мелкая механика --------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Единый вид срока в базе. Секунды нужны: тик ходит раз в 30 секунд."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _to_utc(value) -> datetime | None:
    """ЕДИНСТВЕННОЕ место, где местное время превращается в UTC.

    Второе такое место сойдёт с ума в день перевода часов, и понять это будет
    невозможно — тот же довод, по которому «сброс 11:00 МСК» живёт ровно в
    metering.quota_day.

    Строка без сдвига считается МЕСТНОЙ: так её и писал старый код, и так её
    понимает владелец, когда говорит «в 12:00».
    """
    if isinstance(value, datetime):
        got = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            got = datetime.fromisoformat(text)
        except ValueError:
            return None
    if got.tzinfo is None:
        try:
            from core.time_utils import get_effective_timezone
            got = got.replace(tzinfo=get_effective_timezone())
        except Exception:
            got = got.astimezone()
    return got.astimezone(timezone.utc)


def _ready() -> bool:
    """Есть ли в базе таблица напоминаний. Образец взят у agent/task_store."""
    try:
        from core import store, writer
        writer.ensure_open()
        return bool(store.supports("reminders"))
    except Exception:
        return False


def _next_id(conn, day: str) -> str:
    """Номер за эти местные сутки: 'R-20260822-001'.

    Считается ЧИСЛОМ, а не строкой: при сравнении строк '999' больше '1000', и
    тысячное напоминание за сутки получило бы занятый номер. Та же грабля уже
    была поймана в номерах задач (блок 8).
    """
    rows = conn.execute("SELECT rem_id FROM mx_reminder WHERE rem_id LIKE ?",
                        (f"R-{day}-%",)).fetchall()
    top = 0
    for row in rows:
        got = _ID.match(str(row[0]))
        if got and got.group(1) == day:
            top = max(top, int(got.group(2)))
    return "R-%s-%03d" % (day, top + 1)


def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# -- Поставить --------------------------------------------------------------

def arm(text: str, due, *, due_raw: str | None = None,
        rem_id: str | None = None) -> dict | None:
    """Поставить напоминание. Возвращает поставленное или None.

    Номер выдаётся ВНУТРИ той же сделки, что и вставка: между «посмотреть
    последний номер» и «вставить свой» не должно быть щели, иначе два
    одновременных «напомни» получат один номер. Та же причина и то же лечение,
    что у номеров задач в блоке 8.
    """
    if not _ready():
        return None
    body = str(text or "").strip()
    if not body:
        return None
    moment = _to_utc(due)
    if moment is None:
        return None

    from core import writer
    from core.task_context import today_stamp

    raw = due_raw if due_raw is not None else (
        due if isinstance(due, str) else _iso(moment))
    created = _iso(_now_utc())
    day = today_stamp()

    def job(conn):
        ident = str(rem_id) if rem_id else _next_id(conn, day)
        conn.execute(
            "INSERT OR REPLACE INTO mx_reminder (rem_id, text, due_utc, "
            "due_raw, pre_done, main_done, retry_done, state, created_utc) "
            "VALUES (?,?,?,?,0,0,0,?,?)",
            (ident, body, _iso(moment), raw, STATE_ARMED, created))
        return {"rem_id": ident, "text": body, "due_utc": _iso(moment),
                "due_raw": raw, "state": STATE_ARMED}

    return writer.write(job, label="scheduler.arm")


def rearm(rem_id: str, due, *, due_raw: str | None = None) -> bool:
    """Перенести срок. Отметки о произнесённом сбрасываются: у нового срока
    своя история, иначе перенесённое напоминание не прозвучит вовсе."""
    if not _ready():
        return False
    moment = _to_utc(due)
    if moment is None:
        return False
    from core import writer
    raw = due_raw if due_raw is not None else (
        due if isinstance(due, str) else _iso(moment))

    def job(conn):
        cur = conn.execute(
            "UPDATE mx_reminder SET due_utc=?, due_raw=?, pre_done=0, "
            "main_done=0, retry_done=0, state=? WHERE rem_id=?",
            (_iso(moment), raw, STATE_ARMED, str(rem_id)))
        return cur.rowcount > 0

    return bool(writer.write(job, label="scheduler.rearm"))


# -- Отменить ---------------------------------------------------------------

def cancel(rem_id: str) -> bool:
    """Отменить одно. НЕ удаляет: строка остаётся с state='cancelled'."""
    if not _ready():
        return False
    from core import writer

    def job(conn):
        cur = conn.execute(
            "UPDATE mx_reminder SET state=? WHERE rem_id=? AND state=?",
            (STATE_CANCELLED, str(rem_id), STATE_ARMED))
        return cur.rowcount > 0

    return bool(writer.write(job, label="scheduler.cancel"))


def find(keyword: str, limit: int = BATCH) -> list:
    """Живые напоминания, где встречается слово. Только чтение, вне кассы."""
    if not _ready():
        return []
    from core import writer
    needle = "%" + str(keyword or "").strip().lower() + "%"
    rows = writer.reader().execute(
        "SELECT * FROM mx_reminder WHERE state=? AND lower(text) LIKE ? "
        "ORDER BY due_utc LIMIT ?",
        (STATE_ARMED, needle, int(limit))).fetchall()
    return [_row_to_dict(r) for r in rows]


def alive(limit: int = BATCH) -> list:
    """Все живые напоминания по возрастанию срока. Только чтение."""
    if not _ready():
        return []
    from core import writer
    rows = writer.reader().execute(
        "SELECT * FROM mx_reminder WHERE state=? ORDER BY due_utc LIMIT ?",
        (STATE_ARMED, int(limit))).fetchall()
    return [_row_to_dict(r) for r in rows]


def spoken_recently(hours: int = 12, limit: int = BATCH) -> list:
    """Что уже ПРОЗВУЧАЛО за последние N часов. Только чтение, вне кассы.

    ЗАЧЕМ. Живая проба владельца 23.08.2026:

        You:    ты случайно не должен был мне напомнить кое что?
        Jarvis: Нет активных напоминаний, сэр.

    А напоминал — за пять минут до этого, про воду. Ответ был формально верен и
    по сути бесполезен: `alive()` показывает только то, что ещё ЖДЁТ, и про
    сказанное рассказать было нечем.

    Данные при этом лежали целыми — блок 10 нарочно ничего не удаляет. То есть
    не хватало не данных, а ДВЕРИ к ним. Миграций для этого не нужно.
    """
    if not _ready():
        return []
    from core import writer
    edge = _iso(_now_utc() - timedelta(hours=int(hours)))
    rows = writer.reader().execute(
        "SELECT * FROM mx_reminder WHERE main_done=1 AND due_utc >= ? "
        "ORDER BY due_utc DESC LIMIT ?", (edge, int(limit))).fetchall()
    return [_row_to_dict(r) for r in rows]


def get(rem_id: str) -> dict | None:
    if not _ready():
        return None
    from core import writer
    row = writer.reader().execute(
        "SELECT * FROM mx_reminder WHERE rem_id=?", (str(rem_id),)).fetchone()
    return _row_to_dict(row) if row else None


# -- Отметка «владелец отозвался» -------------------------------------------
# Живёт в памяти процесса, а не в базе: это сведение об этой минуте, писать его
# на диск незачем. Стоит наносекунды.
#
# ДЫРА, КОТОРУЮ Я НАЗЫВАЮ ВСЛУХ. Отметку ставит memory_manager, а main.py:1768
# зовёт разбор памяти только если реплика ДЛИННЕЕ 5 СИМВОЛОВ. Значит «да» и
# «ок» до нас не доходят, и повтор прозвучит зря. Закрыть это можно только в
# ui.py (там реплика пишется всегда, без ограничения длины), но ui.py тоже под
# контрольной суммой, и владелец 22.08.2026 решил: дыру принять, файл окна не
# трогать. Ошибка безопасная: услышать напоминание дважды лучше, чем потерять.
#
# Перезапуск между напоминанием и повтором тоже стирает отметку — повтор
# прозвучит. Та же безопасная сторона.
_last_owner_word: float = 0.0

# Когда главное было ВЫДАНО — по номеру напоминания, время монотонных часов.
# Нужно, чтобы гашение повтора задавало ПРАВИЛЬНЫЙ вопрос.
#
# ПЕРВАЯ ВЕРСИЯ СПРАШИВАЛА НЕВЕРНОЕ, и это нашла живая проба владельца
# 22.08.2026. Она спрашивала «говорил ли владелец за последние три минуты», а
# надо «говорил ли владелец С ТЕХ ПОР, как я сказал». Разница выходит наружу
# всегда, а не в редком случае:
#
#     напоминание на 16:00:00
#     главное прозвучало  ~16:00:20
#     владелец ответил    ~16:00:30   <- ответил сразу, как и делает человек
#     проверка повтора    ~16:03:50   <- тик ходит раз в 30 секунд
#     владелец говорил 200 секунд назад, а гашение смотрело на 180 -> НЕ ПОГАСИЛО
#
# То есть повтор звучал ПОЧТИ ВСЕГДА, потому что естественный ответ приходит
# сразу после напоминания, а проверка — на три с лишним минуты позже. Владелец
# услышал это дважды за одну пробу и написал «работает ужасно». Он был прав.
_main_said_at: dict = {}

# -- Эхо: модель повторяет за нами -----------------------------------------
# Окно, в котором повторная постановка ТОГО ЖЕ текста считается эхом, а не
# просьбой владельца. Живая проба 23.08.2026 показала БЕСКОНЕЧНУЮ ПЕТЛЮ:
#
#     [Reminder] Firing: main выпить воды        <- сработало
#     [JARVIS] reminder {time:+1, action:set, message:выпить воды}
#                                                <- МОДЕЛЬ ПОСТАВИЛА НОВОЕ
#     [Reminder] Firing: main выпить воды        <- сработало снова
#     ... каждую минуту, БЕЗ ЕДИНОЙ реплики владельца
#
# Механизм: когда напоминание срабатывает, модели уходит приказ «Немедленно скажи
# мне вслух следующее напоминание: выпить воды». Модель прочитала его как ПРОСЬБУ
# поставить напоминание — и поставила. Подтолкнула её к этому моя же правка
# подсказки от 22.08.2026 («Call the tool immediately»), то есть ПЕТЛЮ СОЗДАЛ Я.
#
# Подсказку я поправил, но полагаться на неё нельзя: инструкция модели — это
# вероятность, а не логика. Поэтому петлю рвёт КОД, по точному признаку:
# тот же текст И владелец НЕ ПРОИЗНЁС НИ СЛОВА с тех пор, как мы это сказали.
# Настоящая просьба «напомни ещё раз про воду» всегда идёт ПОСЛЕ слов владельца,
# поэтому она проходит.
ECHO_WINDOW_S = 3600

# Сколько текстов помним. Больше не нужно: петля бьёт по одному и тому же.
_ECHO_MAX = 32

_said_texts: dict = {}


def _echo_key(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def note_said_text(text: str) -> None:
    """Запомнить, что ЭТОТ текст мы только что произнесли."""
    import time
    key = _echo_key(text)
    if not key:
        return
    _said_texts[key] = time.monotonic()
    if len(_said_texts) > _ECHO_MAX:
        oldest = sorted(_said_texts, key=lambda k: _said_texts[k])
        for stale in oldest[:len(oldest) // 2]:
            _said_texts.pop(stale, None)


def looks_like_echo(text: str) -> bool:
    """Это модель повторяет за нами, а не владелец просит?

    Два условия вместе, и оба нужны:
      1. этот текст мы сами произнесли недавно;
      2. владелец с того момента НЕ СКАЗАЛ НИ СЛОВА.

    Второе и отличает петлю от настоящей просьбы: владелец физически не мог
    попросить, не заговорив.
    """
    import time
    said = _said_texts.get(_echo_key(text))
    if said is None:
        return False
    if (time.monotonic() - said) > ECHO_WINDOW_S:
        return False
    return _last_owner_word < said


def note_owner_spoke(*, when: float | None = None) -> None:
    """Владелец что-то сказал. Зовёт memory_manager на каждой реплике."""
    global _last_owner_word
    import time
    _last_owner_word = float(when if when is not None else time.monotonic())


def owner_spoke_since(seconds: float) -> bool:
    """Говорил ли владелец за последние N секунд. Оставлено для замеров."""
    import time
    if _last_owner_word <= 0:
        return False
    return (time.monotonic() - _last_owner_word) <= float(seconds)


def note_main_said(rem_id: str) -> None:
    """Запомнить, когда главное было выдано наружу."""
    import time
    _main_said_at[str(rem_id)] = time.monotonic()


def owner_reaction(rem_id: str) -> str | None:
    """Что произошло после НАШЕЙ ПОСЛЕДНЕЙ фразы про это напоминание.

    Возвращает:
      'heard'    — владелец заговорил вскоре после нашей фразы: он был рядом;
      'returned' — заговорил много позже: он отходил и, скорее всего, НЕ слышал;
      None       — молчит с тех пор, как мы сказали.

    Разделение по времени приблизительное (см. RETURN_GRACE_S) и названо вслух.

    ПОСЛЕ ПЕРЕЗАПУСКА момент нашей фразы забыт (он живёт в памяти процесса).
    Тогда считаем, что говорили ДАВНО: первые же слова владельца дадут
    'returned', и напоминание прозвучит ещё раз. Это осознанный выбор в пользу
    владельца: лучше повторить то, что он уже слышал, чем промолчать о том,
    чего он не слышал.
    """
    if _last_owner_word <= 0:
        return None                      # в этом запуске он ещё не говорил
    said = _main_said_at.get(str(rem_id), 0.0)
    if _last_owner_word < said:
        return None
    if (_last_owner_word - said) <= RETURN_GRACE_S:
        return "heard"
    return "returned"


def _since_said(rem_id: str):
    """Сколько секунд прошло с НАШЕЙ фразы. None — если не знаем (перезапуск).

    Повтор отсчитывается ОТ НАШЕЙ ФРАЗЫ, а не от срока напоминания, и это
    исправление найдено замером 22.08.2026. От срока считать нельзя: у
    догнанного напоминания (включил Джарвиса через три часа) опоздание уже
    больше трёх минут, поэтому «пора повторить» выполнялось на следующем же
    тике — через 30 секунд после того, как сказали. Владелец слышал одно и то же
    дважды за полминуты.
    """
    import time
    said = _main_said_at.get(str(rem_id))
    if said is None:
        return None
    return time.monotonic() - said


def forget_main_said(rem_id: str) -> None:
    _main_said_at.pop(str(rem_id), None)


def reset_for_tests() -> None:
    """Забыть отметку об отзыве и моменты выдачи. Зовёт tests/conftest.py.

    Отметки живут на процесс, а весь прогон — один процесс: без сброса первый
    же тест, где владелец «говорил», отменил бы повторы во всех следующих. Та
    же болезнь и тот же рецепт, что у десяти других пунктов списка в conftest.
    """
    global _last_owner_word
    _last_owner_word = 0.0
    _main_said_at.clear()
    _said_texts.clear()


# -- Тик --------------------------------------------------------------------

def due_now(now=None, *, limit: int = BATCH) -> list:
    """Что пора сказать. ТОЛЬКО ЧТЕНИЕ, вне кассы — это и есть холостой тик.

    Замерено на копии базы владельца: 0,0055 мс, за сутки 16 мс. Дешевле, чем
    прочитать и разобрать JSON, как делал старый код.
    """
    if not _ready():
        return []
    from core import writer
    moment = _to_utc(now) if now is not None else _now_utc()
    edge = _iso(moment + timedelta(minutes=PRE_MINUTES))
    rows = writer.reader().execute(
        "SELECT * FROM mx_reminder WHERE state=? AND due_utc <= ? "
        "ORDER BY due_utc LIMIT ?",
        (STATE_ARMED, edge, int(limit))).fetchall()
    return [_row_to_dict(r) for r in rows]


def classify(row: dict, now: datetime) -> str | None:
    """Что именно причитается этой строке прямо сейчас.

    Возвращает 'pre' | 'main' | 'retry' | 'stale' | 'close' | None.
    'close' — сказать нечего, но строку надо закрыть.

    ПОЧЕМУ 'close' СУЩЕСТВУЕТ, И ЭТО НЕ ЛИШНЕЕ СОСТОЯНИЕ. Первая версия этой
    функции возвращала None там, где теперь 'close', и я поймал у себя ровно ту
    утечку, которую этот блок лечит. Смотрите: главное сказано (main_done=1),
    прошло три минуты, владелец ОТОЗВАЛСЯ — значит повтор не нужен. Строка
    остаётся `armed` навсегда: говорить о ней нечего, но она вечно висит в
    «покажи мои напоминания» и никогда не попадёт под уборку, потому что уборка
    трогает только `done` и `cancelled`. Это дефект №7 из десяти замеренных
    («39 просроченных перечитываются вечно»), заново созданный моими руками в
    новой одежде.

    Порядок проверок — от самого позднего события к самому раннему, и это не
    вкус: строка, у которой уже сказано главное, не должна снова попасть в
    предупреждение.
    """
    moment = _to_utc(row.get("due_utc"))
    if moment is None:
        # Срок не разбирается. Молчать и держать вечно нельзя — закрываем,
        # строка останется видимой как 'done'.
        return "close"
    late = (now - moment).total_seconds()

    if int(row.get("main_done") or 0):
        ident = str(row.get("rem_id"))

        # ПОРЯДОК ЗДЕСЬ ЗНАЧИМ. Сначала спрашиваем, что сделал владелец, и только
        # потом смотрим на таймеры: иначе таймер закроет напоминание раньше, чем
        # мы заметим, что владелец вернулся и ничего не слышал.
        reaction = owner_reaction(ident)
        if reaction == "heard":
            # Заговорил вскоре после нашей фразы — значит был рядом и слышал.
            return "close"
        if reaction == "returned":
            # Отходил и вернулся. Он мог не услышать ни главное, ни повтор,
            # поэтому говорим ещё раз — и на этом закрываем.
            return "again"

        # Владелец молчит с нашей последней фразы.
        if late >= STALE_DAYS * 86400:
            # Ждать дольше выбранного владельцем срока незачем: это уже не
            # напоминание. Закрываем МОЛЧА, строка остаётся уликой.
            return "close"
        if int(row.get("retry_done") or 0):
            # Повтор по таймеру уже был, а его так и не слышно. НЕ ЗАКРЫВАЕМ:
            # ждём возвращения. Бесконечности нет — проверка просрочки выше.
            return None

        waited = _since_said(ident)
        if waited is None:
            # Перезапуск: когда говорили — не помним. Повтор по таймеру
            # пропускаем, но возвращение владельца всё равно сработает выше.
            return None
        if waited < RETRY_MINUTES * 60:
            return None                      # окно повтора ещё не наступило
        return "retry"

    if late >= STALE_DAYS * 86400:
        return "stale"
    if late >= 0:
        return "main"
    if int(row.get("pre_done") or 0):
        return None
    if -late <= PRE_MINUTES * 60:
        born = _to_utc(row.get("created_utc"))
        if born is not None and (moment - born).total_seconds() < \
                PRE_SKIP_UNDER_MINUTES * 60:
            # «Напомни через 5 минут»: предупреждать не о чем.
            return None
        return "pre"
    return None


# Что каждый исход делает со строкой. Таблицей, а не ветвями if: перечень из
# пяти строк видно целиком, а пять ветвей — нет.
#   колонка   что отметить, или None
#   state     каким стать, или None — остаться `armed`
_EFFECT = {
    "pre":   ("pre_done", None),           # главное ещё впереди
    "main":  ("main_done", None),          # ждём реакции владельца
    # ПОВТОР БОЛЬШЕ НЕ ЗАКРЫВАЕТ СТРОКУ. Раньше закрывал — и напоминание,
    # прозвучавшее в пустую комнату, считалось доставленным. Владелец
    # 22.08.2026: «отойду больше трёх минут, вернусь — и он мне не напомнит».
    # Теперь строка ждёт признака, что владелец был рядом.
    "retry": ("retry_done", None),
    # Сказали ещё раз вернувшемуся владельцу — и закрыли: он рядом и услышал.
    "again": (None, STATE_DONE),
    # ПРОСРОЧЕННОЕ СТАРШЕ STALE_DAYS: закрываем, но main_done НЕ ставим. Оно
    # действительно НЕ ПРОЗВУЧАЛО, и врать об этом в собственной тетради незачем:
    # по паре (state='done', main_done=0) владелец в «покажи мои напоминания»
    # отличит «сказано» от «просрочено и не сказано». Первая версия ставила здесь
    # main_done=1 — то есть записывала успех там, где его не было.
    "stale": (None, STATE_DONE),
    "close": (None, STATE_DONE),           # сказать нечего, но закрыть надо
}

# Исходы, о которых владельцу НЕ ГОВОРЯТ. Отметить их надо, произносить — нет.
#   close  — сказать попросту нечего;
#   stale  — старше выбранного владельцем срока (7 суток). Поймано своим же
#            сторожем 22.08.2026: тик отдавал 'stale' наружу, и восьмидневное
#            напоминание произносилось как свежее. Владелец выбрал ровно
#            обратное: старше срока — молчать, но СОХРАНИТЬ и показать в списке.
_SILENT = ("close", "stale")


def mark(rem_id: str, kind: str) -> bool:
    """Отметить произнесённое. ОДНА сделка на всё.

    Момент отметки — ВЫДАЧА строки, а не произнесение, и это осознанный выбор.
    `reminder.py` физически не может узнать, дошёл ли голос: это решает
    `_deliver_reminder` в замороженном main.py, и у него два канала (живая
    связь и местный голос), один из которых он использует всегда. Окно риска —
    микросекунды между `return` и `speak`.

    Это строго лучше старого кода: там строку УДАЛЯЛИ до произнесения, и
    падение в этот миг уносило напоминание навсегда (замерено). Здесь строка
    остаётся, и её видно в «покажи мои напоминания».
    """
    if not _ready():
        return False
    effect = _EFFECT.get(str(kind))
    if effect is None:
        return False
    column, state = effect
    from core import writer

    def job(conn):
        sets = []
        args = []
        if column:
            sets.append(f"{column}=1")
        if state:
            sets.append("state=?")
            args.append(state)
        args.append(str(rem_id))
        cur = conn.execute(
            "UPDATE mx_reminder SET " + ", ".join(sets) + " WHERE rem_id=?",
            tuple(args))
        return cur.rowcount > 0

    return bool(writer.write(job, label="scheduler.mark"))


def close(rem_id: str) -> bool:
    """Больше от этой строки ничего не причитается."""
    if not _ready():
        return False
    from core import writer

    def job(conn):
        cur = conn.execute(
            "UPDATE mx_reminder SET state=? WHERE rem_id=? AND state=?",
            (STATE_DONE, str(rem_id), STATE_ARMED))
        return cur.rowcount > 0

    return bool(writer.write(job, label="scheduler.close"))


def purge(now=None) -> int:
    """Убрать отработанные строки старше KEEP_DAYS. `armed` не трогаем никогда.

    Зовётся из тика, а не отдельным потоком: лишний поток ради одной выборки
    раз в сутки — плата без покупки.
    """
    if not _ready():
        return 0
    from core import writer
    moment = _to_utc(now) if now is not None else _now_utc()
    edge = _iso(moment - timedelta(days=KEEP_DAYS))

    def job(conn):
        cur = conn.execute(
            "DELETE FROM mx_reminder WHERE state IN (?,?) AND created_utc < ?",
            (STATE_DONE, STATE_CANCELLED, edge))
        return int(cur.rowcount or 0)

    return int(writer.write(job, label="scheduler.purge") or 0)


def tick(now=None) -> list:
    """Что причитается прямо сейчас. Отмечает всё ОДНОЙ сделкой.

    Возвращает список `{"kind": ..., "row": {...}, "late_s": ...}`, самое раннее
    первым. Слова не сочиняет: их сочиняет `actions/reminder.py`, потому что
    поверхность инструмента и язык владельца — его забота, а не наша.

    ПОЧЕМУ ОТМЕТКА ОДНОЙ СДЕЛКОЙ, А НЕ ПО ОДНОЙ НА СТРОКУ. Старый код на два
    просроченных напоминания говорил первое и УДАЛЯЛ ОБА (замерено). Если
    отмечать по одной сделке на строку, то падение посреди залпа отметит часть
    — и часть напоминаний исчезнет так же молча, только реже. Одна сделка на
    весь залп значит: либо все отмечены, либо ни одно, и тогда следующий тик
    через 30 секунд повторит попытку целиком.
    """
    rows = due_now(now)
    if not rows:
        return []
    moment = _to_utc(now) if now is not None else _now_utc()

    ready: list = []
    for row in rows:
        kind = classify(row, moment)
        if kind is None:
            continue
        due = _to_utc(row.get("due_utc"))
        ready.append({
            "kind": kind,
            "row": row,
            "late_s": (moment - due).total_seconds() if due else 0.0,
        })
    if not ready:
        return []

    from core import writer

    def job(conn):
        for item in ready:
            column, state = _EFFECT[item["kind"]]
            sets = []
            args: list = []
            if column:
                sets.append(f"{column}=1")
            if state:
                sets.append("state=?")
                args.append(state)
            args.append(str(item["row"]["rem_id"]))
            conn.execute(
                "UPDATE mx_reminder SET " + ", ".join(sets) + " WHERE rem_id=?",
                tuple(args))
        return True

    writer.write(job, label="scheduler.tick")

    # Отметки в памяти процесса ставим ПОСЛЕ того, как сделка легла: если она
    # упадёт, следующий тик обязан увидеть прежнюю картину, а не нашу.
    for item in ready:
        ident = str(item["row"]["rem_id"])
        kind = item["kind"]
        # Запоминаем ТЕКСТ всего, что уходит наружу: если модель услышит своё
        # же напоминание и попробует поставить его снова, постановка узнает эхо.
        if kind not in _SILENT:
            note_said_text(item["row"].get("text"))
        if kind == "main":
            note_main_said(ident)
        elif kind == "retry":
            # Отметку ОБНОВЛЯЕМ: «услышал или вернулся» считается от НАШЕЙ
            # ПОСЛЕДНЕЙ фразы, а не от первой. Без этого ответ на повтор
            # выглядел бы как возвращение спустя вечность.
            note_main_said(ident)
        elif kind in ("again", "stale", "close"):
            forget_main_said(ident)

    # Молчаливые исходы наружу не отдаём: отметить их было нужно, произносить —
    # нет. Перечень в _SILENT, а не условием здесь: условие на месте я уже
    # написал неверно один раз (отдавал 'stale' наружу).
    return [i for i in ready if i["kind"] not in _SILENT]
