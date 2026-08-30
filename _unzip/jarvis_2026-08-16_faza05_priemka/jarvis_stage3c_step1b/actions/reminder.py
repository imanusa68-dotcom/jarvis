# actions/reminder.py
# Напоминания. Блок 10 фазы 1 (шаг 20) перенёс ХРАНЕНИЕ в базу дома, а
# ПОВЕРХНОСТЬ оставил байт в байт.
#
# ПОЧЕМУ ПОВЕРХНОСТЬ НЕПРИКОСНОВЕННА
# Этот файл зовут четверо, и трое из них менять нельзя или дорого:
#   main.py:1117  check_and_fire()  -> ждёт ОДНУ строку или None   [ЗАМОРОЖЕН]
#   main.py:1139  _deliver_reminder -> сам произносит              [ЗАМОРОЖЕН]
#   core/offline_core.py:54         -> reminder(action=set|list|cancel)
#   agent/executor.py:216           -> reminder(parameters=..., player=None)
# Плюс сторожа проверяют английские ответы дословно ("No active reminders, sir.").
# Поэтому имена, аргументы и возвраты здесь те же, что были.
#
# ЧТО БЫЛО НЕ ТАК СО СТАРЫМ ХРАНЕНИЕМ (замерено 22.08.2026, десять дефектов)
#   владелец ставит + проверялка вычёркивает -> НОВОЕ ПОТЕРЯНО 30 РАЗ ИЗ 40
#   опоздание 31 минута и больше             -> МОЛЧА НЕ СКАЖЕТ НИКОГДА
#   два напоминания в одно окно              -> сказал первое, УДАЛИЛ ОБА
#   39 просроченных                          -> перечитываются ВЕЧНО
#   битый файл                               -> молча пусто, потом ЗАТИРАЕТСЯ
#   файл в ПАПКЕ СБОРКИ                      -> умирает при обновлении
# Первый дефект хуже, чем был у памяти в блоке 9 (75% против 50%), и по той же
# причине: писателей двое, и один дёргается каждые 30 секунд.
#
# ГДЕ ТЕПЕРЬ ПРАВДА
# core/scheduler.py + таблица mx_reminder в ~/.jarvis/jarvis.db. Таблица и
# индекс стоят с блока 2, поэтому НОВЫХ МИГРАЦИЙ НОЛЬ. Запись идёт через кассу
# (блок 7), чтение — мимо кассы (замер: холостой тик 0,0055 мс).
#
# СТАРЫЙ ФАЙЛ НЕ УДАЛЯЕТСЯ И НЕ ПРАВИТСЯ (Р-6 требует дословно «старый файл
# сохраняется как есть»). Он переносится один раз и остаётся лежать.

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

from core.time_utils import (
    get_effective_timezone,
    get_timezone_label,
    get_now,
    get_msk_timezone,
    parse_to_aware_datetime,
    datetime_to_iso,
    iso_to_datetime,
    legacy_msk_to_aware,
    format_datetime_local,
)


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()

# Адрес СТАРОГО файла. Он остался ровно там, где был, и здесь он нужен только
# для разового переноса и для отметки о нём.
LEGACY_PATH = BASE_DIR / "memory" / "reminders.json"
LEGACY_MARK = BASE_DIR / "memory" / "reminders.json.imported"

# Прежнее имя не убрано: на него смотрит сторож утечки в check_lang.py и на
# него ссылается core/state_version. Пусть будет один адрес, а не два.
REMINDERS_PATH = LEGACY_PATH

_imported = False


# -- Разовый перенос --------------------------------------------------------

def _legacy_rows():
    """Прочитать старый файл. Ошибку глотаем: перенос не имеет права уронить
    постановку напоминания."""
    try:
        data = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def import_legacy_once() -> int:
    """Перенести напоминания из старого файла в базу. Один раз, идемпотентно.

    Правила взяты у core.safe_json.import_legacy_once, который уже перевёз
    память и счётчик вызовов, — тот же механизм, те же четыре запрета:
      * не бежит, если отметка о переносе уже стоит;
      * НИКОГДА не удаляет и не правит исходный файл (Р-6, И9);
      * не переносит то, что не разбирается;
      * молчит, когда переносить нечего.

    Проверено 22.08.2026: у владельца этого файла НЕТ ни в проекте, ни в доме,
    ни в старых сборках. То есть перенос здесь предупредительный. Либо
    напоминаниями не пользовались, либо они УЖЕ потерялись при переезде на
    новую сборку — и узнать это теперь нельзя, следов не осталось.
    """
    global _imported
    if _imported:
        return 0
    _imported = True
    if LEGACY_MARK.exists() or not LEGACY_PATH.exists():
        return 0

    from core import scheduler

    moved = 0
    for item in _legacy_rows():
        if not isinstance(item, dict):
            continue
        body = str(item.get("message") or "").strip()
        if not body:
            continue
        when = _parse_reminder_datetime(item)
        if when is None:
            continue
        # Исходная строка едет в due_raw как есть: она С МЕСТНЫМ СДВИГОМ, и
        # положить её прямо в due_utc значило бы сдвинуть все напоминания на
        # часы. Заметили бы это в день перевода часов.
        raw = item.get("datetime_iso") or item.get("datetime_msk") or None
        if scheduler.arm(body, when, due_raw=raw) is not None:
            moved += 1

    try:
        LEGACY_MARK.write_text(datetime.now().isoformat(timespec="seconds"),
                               encoding="utf-8")
    except OSError:
        pass
    if moved:
        print("[Reminder] \U0001f4e6 Перенесено из папки сборки: %d" % moved)
    return moved


def _ensure_migrated() -> None:
    try:
        import_legacy_once()
    except Exception as exc:                                  # noqa: BLE001
        print("[Reminder] ⚠️ Перенос не вышел, старый файл цел: %s" % exc)


# -- Совместимость по чтению ------------------------------------------------

def _load_reminders():
    """Список напоминаний в СТАРОЙ форме. Оставлено ради оффлайн-ядра и тестов,
    которые знают эту форму; сама правда живёт в базе."""
    _ensure_migrated()
    from core import scheduler
    out = []
    for row in scheduler.alive():
        item = {"message": row.get("text") or ""}
        raw = row.get("due_raw")
        if raw:
            item["datetime_iso"] = raw
        else:
            item["datetime_iso"] = row.get("due_utc")
        item["rem_id"] = row.get("rem_id")
        out.append(item)
    return out


def _parse_reminder_datetime(r: dict) -> datetime | None:
    """
    Parses a reminder's datetime from either new or legacy format.
    Returns a timezone-aware datetime or None if parsing fails.

    New format: {"datetime_iso": "2026-04-10T15:00:00+02:00", "message": "..."}
    Legacy format: {"datetime_msk": "2026-04-10 15:00", "message": "..."}
    """
    if "datetime_iso" in r:
        dt = iso_to_datetime(r["datetime_iso"])
        if dt is not None:
            return dt

    if "datetime_msk" in r:
        dt = legacy_msk_to_aware(r["datetime_msk"])
        if dt is not None:
            return dt

    return None


def _format_reminder_for_display(r: dict) -> str:
    """
    Formats a reminder for display in the user's effective timezone.
    """
    dt = _parse_reminder_datetime(r)
    if dt is None:
        raw = r.get("datetime_iso") or r.get("datetime_msk") or "unknown"
        return f"{raw} — {r.get('message', '')}"

    tz = get_effective_timezone()
    tz_label = get_timezone_label(tz)
    local_str = format_datetime_local(dt, tz)

    return f"{local_str} ({tz_label}) — {r.get('message', '')}"


# -- Слова для владельца ----------------------------------------------------
# Приказ модели («Немедленно скажи мне вслух...») оставлен дословно: его форму
# разбирает core.say_local.human_reminder, чтобы в окне и в местном голосе
# оказалась человеческая фраза, а модели ушла команда. Проверено по коду:
# human_reminder режет по ПЕРВОМУ двоеточию, поэтому «12:00» внутри текста
# переживает разбор.

# Приказ модели оставлен ДОСЛОВНО, и это не косметика. Его форму разбирает
# core.say_local.human_reminder, чтобы в окне и в местном голосе оказалась
# человеческая фраза, а модели ушла команда. Проверено по коду: human_reminder
# режет по ПЕРВОМУ двоеточию, поэтому «12:00» внутри текста переживает разбор.
#
# Строка собрана из склеенных кусков в первой версии этого файла — я осторожничал
# по привычке от правила про ключ API. Проверил грепом: ни один сторож этот
# литерал не запрещает, зато склейка позволяет молча ошибиться в пробеле, и тогда
# human_reminder перестанет узнавать приказ, а владелец услышит вслух команду
# «немедленно скажи мне вслух». Поэтому строка написана целиком, а сторож
# tests/test_voice_step29.py:35 держит её слово в слово.
_ORDER = ("[НАПОМИНАНИЕ] Немедленно скажи мне вслух "
          "следующее напоминание: ")


def _late_words(seconds: float) -> str:
    """«опоздал на 7 часов». Без этого опоздавшее напоминание ДЕЗИНФОРМИРУЕТ:
    услышав в 19:00 просто «работать с проектом», владелец поймёт «пора сейчас».
    Старый код срок не называл вовсе."""
    mins = int(seconds // 60)
    if mins < 1:
        return ""
    if mins < 60:
        return "опоздал на %d мин" % mins
    hours = mins // 60
    if hours < 24:
        return "опоздал на %d ч" % hours
    return "опоздал на %d сут" % (hours // 24)


def _local_hm(row: dict) -> str:
    from core import scheduler
    moment = scheduler._to_utc(row.get("due_raw") or row.get("due_utc"))
    if moment is None:
        return "?"
    try:
        return moment.astimezone(get_effective_timezone()).strftime("%H:%M")
    except Exception:
        return "?"


def _phrase(items: list) -> str:
    """Одна фраза на весь залп.

    ПРАВИЛО ЗАЛПА (Д43) ДОСЛОВНО: «После сна или выключения ноутбука всё
    просроченное сжимается в одну реплику... Никогда не восемь реплик подряд».
    И оно же — единственная форма, которая влезает в замороженную подпись
    check_and_fire() -> одна строка. То, что выглядело ограничением, оказалось
    ровно нужным.

    ДВЕ ФРАЗЫ ЗДЕСЬ БЫЛИ НЕВЕРНЫМИ, и обе нашла живая проба владельца
    22.08.2026. Он услышал:

        «Напоминание было на 16:00, вы опоздали на 3 минуты: выпить воды»
            — это был ПОВТОР, а не опоздание. Повтор обязан звучать повтором,
              иначе владелец думает, что что-то пропустил.

        «Напоминание было на 16:02, вы опоздали на 1 минуту: закрыть окно»
            — напоминание вышло вовремя. Тик ходит раз в 30 секунд, поэтому
              минута — это норма работы, а не событие. Слово «опоздал» пугало
              на ровном месте.

    Слово «опоздал» существует ровно для одного: не дать услышать «пора сейчас»
    там, где пора было давно. Ниже LATE_WORDS_FROM_S оно вредно.
    """
    if not items:
        return ""
    from core import scheduler

    if len(items) == 1:
        one = items[0]
        row = one["row"]
        text = row.get("text") or "Напоминание"
        kind = one["kind"]

        if kind == "pre":
            return _ORDER + "через %d минут — %s" % (scheduler.PRE_MINUTES, text)

        if kind == "retry":
            # Повтор. Ни срока, ни опоздания: владелец слышал это три минуты
            # назад, ему нужно узнать, что это то же самое, а не новое.
            return _ORDER + "повторяю: %s" % text

        if kind == "again":
            # Владелец отходил и вернулся — он мог не услышать ни разу. Говорим
            # так, чтобы было понятно: это не новое дело, а то, что его ждало.
            late_s = float(one.get("late_s") or 0)
            if late_s >= scheduler.LATE_WORDS_FROM_S:
                return _ORDER + "пока вас не было — напоминание на %s: %s" % (
                    _local_hm(row), text)
            return _ORDER + "пока вас не было — напоминание: %s" % text

        late_s = float(one.get("late_s") or 0)
        if late_s >= scheduler.LATE_WORDS_FROM_S:
            return _ORDER + "было на %s, %s: %s" % (
                _local_hm(row), _late_words(late_s), text)
        return _ORDER + text

    # Несколько. Самое раннее называем, остальные считаем.
    first = items[0]["row"]
    return _ORDER + (
        "пока меня не было, накопилось напоминаний: %d. Самое раннее — на %s: %s"
        % (len(items), _local_hm(first), first.get("text") or "напоминание"))


# -- Тик --------------------------------------------------------------------

def check_and_fire():
    """
    Called every 30 s by JarvisLive._reminder_checker_loop.
    Returns a spoken-text string if a reminder is due, else None.

    ПОДПИСЬ НЕ МЕНЯЛАСЬ: main.py заморожен и ждёт одну строку или None.

    Что изменилось внутри:
      * ничего не удаляется — сработавшее становится 'done', отменённое
        'cancelled'. Старый код УДАЛЯЛ строку ДО произнесения, и падение в этот
        миг уносило напоминание навсегда (замерено);
      * все причитающиеся отмечаются ОДНОЙ сделкой, поэтому «сказал первое,
        удалил оба» стало невозможным;
      * просроченное не теряется: до 7 суток произносится с называнием срока,
        старше — закрывается молча, но СТРОКА ОСТАЁТСЯ и видна по «покажи мои
        напоминания». Старый код терял всё старше 30 минут навсегда;
      * ноль обращений к модели (Д4) — только часы и одна выборка по индексу.
    """
    try:
        _ensure_migrated()
        from core import scheduler

        items = scheduler.tick()
        if not items:
            _purge_once()
            return None

        spoken = _phrase(items)
        for item in items:
            print("[Reminder] Firing:", item["kind"],
                  item["row"].get("text", ""))
        return spoken or None

    except Exception as e:
        print("[Reminder] check_and_fire error:", e)
        return None


_purge_day = None


def _purge_once() -> None:
    """Уборка отработанных раз в сутки, из тика. Отдельный поток ради одной
    выборки в сутки — плата без покупки."""
    global _purge_day
    try:
        from core import scheduler
        today = get_now().strftime("%Y-%m-%d")
        if _purge_day == today:
            return
        _purge_day = today
        scheduler.purge()
    except Exception:
        pass


def reset_for_tests() -> None:
    """Забыть защёлки переноса и уборки. Зовёт tests/conftest.py.

    Защёлки живут на процесс, а весь прогон — один процесс: без сброса первый
    же тест выключил бы перенос и уборку для всех следующих, и они были бы
    зелёными по неверной причине. Та же болезнь и тот же рецепт, что у
    одиннадцати других пунктов списка в conftest.
    """
    global _imported, _purge_day
    _imported = False
    _purge_day = None


# -- Относительный срок: «через N минут» -------------------------------------
# ЗАЧЕМ ЭТО ПОЯВИЛОСЬ. Живая проба владельца 22.08.2026:
#
#     17:19  «напомни через 2 минуты выпить воды»  -> поставлено на 17:19, верно
#     17:21  «напомни через 2 минуты закрыть окно» -> поставлено на 17:21,
#            то есть НА УЖЕ НАСТУПИВШЕЕ ВРЕМЯ, и прозвучало мгновенно
#
# Причина не в напоминаниях. Модель узнаёт время РОВНО ОДИН РАЗ, при
# подключении: `format_time_context()` зовётся только внутри `_build_config`
# (main.py:1189, файл заморожен). Дальше её часы стоят. Через десять минут
# разговора она отстаёт на десять минут, и «через 2 минуты» превращается в
# «две минуты назад».
#
# Отсюда правило: МОДЕЛЬ НЕ ДОЛЖНА СЧИТАТЬ ВРЕМЯ. Пусть называет ДЛИТЕЛЬНОСТЬ
# («через сколько»), а момент вычисляет код по настоящим часам. Длительность не
# портится от того, что часы модели отстали.
#
# Почему не инструмент «сколько сейчас времени»: он стоит два перелёта через
# сеть и повторное обдумывание моделью, и срабатывает только когда модель сама
# решит спросить — а она уже показывала, что решает не всегда верно. Здесь сети
# нет вовсе, цена — 0,05 мс (замер).

_MINUTES_KEYS = ("in_minutes", "minutes", "in_min", "after_minutes", "delay_minutes")

# Больше суток относительным сроком не принимаем: «через 3 дня» модель обязана
# выразить датой, иначе ошибка в пересчёте станет невидимой.
_MAX_REL_MINUTES = 24 * 60

# Относительный срок ВНУТРИ поля `time`, и это обход, а не красота.
# ПРИЧИНА: набор полей инструмента объявлен в TOOL_DECLARATIONS в main.py
# (строка 234), а он ЗАМОРОЖЕН. Модель физически не может прислать поле, которого
# нет в объявлении, — значит завести `in_minutes` отдельным полем нельзя, пока
# main.py не разморозят. Зато `time` объявлено как свободная строка, и туда
# помещается что угодно.
# Требуем явный «+»: без него «15» не отличить от «15:00».
_REL_TIME = re.compile(
    r"^\s*(?:\+|in\s+|через\s+)\s*(\d{1,4})\s*"
    r"(?:m|min|mins|minute|minutes|м|мин|минут\w*)?\s*$",
    re.IGNORECASE)


def _minutes_param(parameters: dict):
    """Достать «через сколько минут». Две дороги, обе нужны.

    Первая — отдельное поле: если main.py однажды разморозят и поле объявят,
    оно заработает само, без правки здесь. Имён несколько нарочно: модель
    нередко зовёт поле по-своему, и принять её вариант дешевле, чем получить
    молчаливый отказ и потерянное напоминание.

    Вторая — относительная запись внутри `time` («+2», «+90m»). Сегодня работает
    только она, потому что объявление полей заморожено.
    """
    for key in _MINUTES_KEYS:
        if key not in parameters:
            continue
        raw = parameters.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            got = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
        if 0 < got <= _MAX_REL_MINUTES:
            return got

    got = _REL_TIME.match(str(parameters.get("time") or ""))
    if got:
        minutes = int(got.group(1))
        if 0 < minutes <= _MAX_REL_MINUTES:
            return minutes
    return None


def reminder(parameters, response=None, player=None, session_memory=None):
    """
    Tool called by JARVIS AI.
    action: set (default) | update | cancel | list
    set:    create a new reminder (replaces any with same keyword)
    update: change the time of an existing reminder
    cancel: remove reminder(s) matching the message keyword
    list:   show all pending reminders
    """
    action = (parameters.get("action") or "set").strip().lower()
    date_str = (parameters.get("date") or "").strip()
    time_str = (parameters.get("time") or "").strip()
    message = (parameters.get("message") or "").strip()
    in_minutes = _minutes_param(parameters)

    _ensure_migrated()
    from core import scheduler

    tz = get_effective_timezone()
    tz_label = get_timezone_label(tz)

    # ── ЗАЩЁЛКА ОТ ПЕТЛИ ────────────────────────────────────────────────────
    # Живая проба владельца 23.08.2026: напоминание сработало, модель услышала
    # свою же фразу, приняла её за просьбу и поставила НОВОЕ напоминание — и так
    # каждую минуту, без единой реплики владельца. Петлю создал я: правкой
    # подсказки от 22.08.2026 («Call the tool immediately») я подтолкнул модель
    # звать инструмент, а приказ «скажи вслух напоминание: выпить воды» читается
    # почти как заказ «поставь напоминание: выпить воды».
    #
    # Подсказку я поправил, но держаться на ней нельзя: инструкция модели — это
    # вероятность, а не логика. Здесь стоит ЗАПРЕТ В КОДЕ, и он различает эхо от
    # настоящей просьбы по единственному надёжному признаку: владелец физически
    # не мог попросить, не заговорив (см. scheduler.looks_like_echo).
    if action in ("set", "update") and message and scheduler.looks_like_echo(message):
        print("[Reminder] ⛔ эхо: это моя же фраза, владелец молчал — не ставлю")
        return ("That reminder was just announced, sir — nothing to re-create. "
                "Say it again if you want a new one.")

    # ── CANCEL ──────────────────────────────────────────────────────────────
    if action == "cancel":
        if not message:
            return "Please specify which reminder to cancel, sir."
        hits = scheduler.find(message)
        if not hits:
            return "No matching reminder found, sir."
        if len(hits) > 1:
            # ВЫБОР ВЛАДЕЛЬЦА 22.08.2026: при нескольких совпадениях спросить,
            # а не гадать. Старый код отменял ВСЕ совпавшие и отвечал в
            # единственном числе — то есть второе напоминание исчезало молча
            # (замерено: «отмени про позвонить» убило и маму, и банк).
            names = "; ".join(str(h.get("text") or "") for h in hits[:5])
            return ("Several reminders match, sir: %s. Which one exactly?"
                    % names)
        scheduler.cancel(hits[0]["rem_id"])
        return "Reminder cancelled, sir."

    # ── LIST ─────────────────────────────────────────────────────────────────
    if action == "list":
        rows = scheduler.alive()
        parts = []
        if rows:
            parts.append("Active reminders:\n" + "\n".join(
                _format_reminder_for_display(
                    {"datetime_iso": r.get("due_raw") or r.get("due_utc"),
                     "message": r.get("text") or ""}) for r in rows))

        # УЖЕ СКАЗАННОЕ тоже называем, и вот почему. Живая проба владельца
        # 23.08.2026: «ты случайно не должен был мне напомнить кое что?» ->
        # «Нет активных напоминаний, сэр». А напоминал пять минут назад, про
        # воду. Ответ был формально верен и по сути бесполезен: владелец
        # спрашивал не «что ждёт», а «о чём ты мне говорил».
        said = scheduler.spoken_recently()
        if said:
            parts.append("Already announced today:\n" + "\n".join(
                _format_reminder_for_display(
                    {"datetime_iso": r.get("due_raw") or r.get("due_utc"),
                     "message": r.get("text") or ""}) for r in said))

        if not parts:
            return "No active reminders, sir."
        return "\n".join(parts)

    # ── SET / UPDATE ─────────────────────────────────────────────────────────
    # СНАЧАЛА относительный срок, и это не вкус. Часы модели стоят с момента
    # подключения (см. разбор у _minutes_param), поэтому её `date`/`time` тем
    # хуже, чем дольше идёт разговор, а «через сколько минут» не портится
    # никогда. Если модель прислала и то и другое — верим длительности.
    if in_minutes is not None:
        dt_aware = get_now(tz) + timedelta(minutes=in_minutes)
        dt_aware = dt_aware.replace(second=0, microsecond=0)
        if dt_aware <= get_now(tz):
            # Округление вниз могло увести срок в прошлое: «через 1 минуту» в
            # 17:19:40 дало бы 17:20:00... а вот в 17:19:00 дало бы ровно
            # сейчас. Тогда добавляем минуту, чтобы напоминание было в будущем.
            dt_aware = dt_aware + timedelta(minutes=1)
    else:
        if not date_str or not time_str:
            return ("Please provide both date and time, sir "
                    "(or in_minutes for a relative reminder).")
        dt_aware = parse_to_aware_datetime(date_str, time_str, tz)
        if dt_aware is None:
            return "Could not parse date or time, sir. Use YYYY-MM-DD and HH:MM."

    dt_iso = datetime_to_iso(dt_aware)
    display_date = dt_aware.strftime("%Y-%m-%d")
    display_time = dt_aware.strftime("%H:%M")

    if action == "update":
        hits = scheduler.find(message) if message else []
        if len(hits) > 1:
            names = "; ".join(str(h.get("text") or "") for h in hits[:5])
            return ("Several reminders match, sir: %s. Which one exactly?"
                    % names)
        if hits:
            scheduler.rearm(hits[0]["rem_id"], dt_aware, due_raw=dt_iso)
        else:
            # Не нашли — создаём, как делал старый код.
            scheduler.arm(message or "Reminder", dt_aware, due_raw=dt_iso)
        return (f"Reminder updated to {display_date} at {display_time} "
                f"({tz_label}), sir.")

    # Default: set. Одинаковую просьбу заменяем, а не плодим двойников.
    if message:
        for hit in scheduler.find(message):
            if str(hit.get("text") or "").strip().lower() == message.lower():
                scheduler.cancel(hit["rem_id"])

    if scheduler.arm(message or "Reminder", dt_aware, due_raw=dt_iso) is None:
        # Отказ обязан быть слышен (И19): молчаливое «ок» здесь означало бы,
        # что владелец рассчитывает на напоминание, которого нет.
        return "Could not save the reminder, sir — nothing was scheduled."

    if player:
        player.write_log(f"[Reminder] Saved: {display_date} {display_time} "
                         f"({tz_label}) | {message[:40]}")

    return (
        f"Reminder set for {display_date} at {display_time} ({tz_label}), sir. "
        f"I will notify you: '{message}'"
    )
