# core/metering.py
"""
Учёт вызовов моделей — единственная дверь к дефицитному ресурсу (блок 5).

ПОЧЕМУ КВОТА, А НЕ ПАМЯТЬ И НЕ ПРОЦЕССОР
Всё в проекте бесплатное, значит самый дефицитный ресурс — не гигабайты и не
ядра, а РАЗРЕШЁННЫЕ ЗАПРОСЫ. Их 500 в сутки на платящую квотой роль, и когда
они кончаются, Джарвис перестаёт думать целиком. Поэтому счётчик расхода —
не бухгалтерия, а часть архитектуры.

ЧТО ЗДЕСЬ ЖИВЁТ И БОЛЬШЕ НИГДЕ
1. КВОТНЫЕ СУТКИ. Google считает сутки по своему часовому поясу, поэтому
   `quota_day()` — единственное место во всём проекте, где это знание есть.
   Второе вычисление «квотных суток» — это баг, который всплывёт раз в
   полгода и будет неотличим от порчи данных.

   ВАЖНО, И ЭТО ЗАМЕРЕНО, А НЕ ВЗЯТО ИЗ ПЛАНА: план говорит «сброс в 11:00
   МСК». Проверено 18.08.2026 — полночь в зоне Google это 11:00 МСК ЗИМОЙ и
   10:00 МСК ЛЕТОМ. Жёсткое число соврало бы полгода из года. Поэтому здесь
   считается ЗОНА, а не смещение: переход на летнее время происходит сам.

2. РЕЗЕРВ И ФИКСАЦИЯ — два шага, а не один. Причины (все три настоящие):
     • вызов может не вернуться (процесс убит, свет выключен, сеть повисла),
       а квоту Google уже списал. Без резерва счётчик станет оптимистом, и
       владелец узнает об исчерпании от Google, а не от Джарвиса;
     • потолок надо проверить ДО вызова — после уже поздно;
     • два потока без резерва оба увидят «остался один» и оба пойдут.
   Незакрытый резерв при следующем старте закрывается как «потерян»:
   пессимизм здесь правильный — лучше счесть сожжённым то, что могло не
   сгореть, чем наоборот.

3. СУТОЧНЫЙ ИТОГ обновляется В ТОЙ ЖЕ ТРАНЗАКЦИИ, что и строка расхода.
   Решение принято в блоке 2 вместе с формой таблиц: подробности живут 30
   дней, итог — бессрочно. Если считать итог отдельной ночной задачей, то
   однажды порядок «сначала посчитал, потом почистил» перевернётся, и месяц
   данных исчезнет безвозвратно. В одной транзакции порядок уборки не имеет
   значения вообще.

ЧЕГО ЗДЕСЬ НЕТ НАРОЧНО
   • Остывание после отказов. Это `core/model_guard.py`: он считает ОТКАЗЫ,
     мы считаем РАСХОД. Разные вещи, и объединять их нельзя.
   • Подбюджеты «диалог/задачи/фон» (О23). Отложены владельцем до месяца
     эксплуатации; колонка `bucket` заполняется с первого дня, чтобы решать
     потом было на чём.

ПОЧЕМУ УЧЁТ НЕ ИМЕЕТ ПРАВА ЛОМАТЬ ДЕЛО
База может быть недоступна, диск полон, схема не обновилась. Вызов при этом
РАЗРЕШАЕТСЯ, а факт «учёт не работает» называется вслух один раз. Молчаливый
отказ работать страшнее неучтённого вызова: первое ломает Джарвиса, второе
портит статистику.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

# Зона, по которой Google считает сутки. Единственное упоминание в проекте:
# сторож test_the_quota_day_lives_in_one_place ищет его во всём core/ и agent/.
_QUOTA_ZONE = "America/" + "Los_Angeles"

# Запасное смещение, если zoneinfo в сборке Python обрезан. Приблизительно
# (не знает про летнее время), поэтому применение говорится вслух.
_FALLBACK_OFFSET_HOURS = -8

# Роли, которые платят дефицитной суточной квотой. Живой голос сюда НЕ
# входит: по реестру у него суток без ограничения, и смешивать его с
# разовыми вызовами — значит считать не то. Счётчик, который считает не то,
# вреднее отсутствующего: владелец услышит «осталось мало» при полном запасе.
PAID_BUCKET = "paid"
CHEAP_BUCKET = "cheap"
SESSION_BUCKET = "session"

# Потолки суток (13.7.17). Свои, а не гугловские: 120 из 500 — четырёхкратный
# запас на ошибки и повторы. Владелец правит их в ~/.jarvis/settings.json,
# не залезая в код.
DEFAULT_CAPS = {PAID_BUCKET: 120, CHEAP_BUCKET: 2000}
_CAP_SETTING = "quota_caps"

# Своего замка у учёта больше нет (блок 7). Он стерёг «посмотреть остаток,
# потом занять место» — а теперь эти две вещи лежат В ОДНОЙ ТРАНЗАКЦИИ кассы,
# и очередь стережёт она. Оставить замок рядом было бы хуже, чем снять: два
# замка вокруг одной базы дают разный порядок захвата, то есть мёртвую хватку.
_broken_said = False


class MeterError(RuntimeError):
    """Учёт не может работать. Наверх летит только там, где это важно."""


# -- Квотные сутки --------------------------------------------------------

def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(_QUOTA_ZONE)
    except Exception:
        return None


def quota_day(now_utc=None, *, printer=None) -> str:
    """Квотные сутки как '2026-08-18'. ЕДИНСТВЕННОЕ место этого знания.

    Считается датой в зоне поставщика. Смещение не зашито: иначе дважды в
    год счётчики съезжали бы на час, и понять это было бы невозможно.
    """
    moment = now_utc or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = _zone()
    if zone is not None:
        return moment.astimezone(zone).strftime("%Y-%m-%d")
    global _broken_said
    if printer is not None and not _broken_said:
        _broken_said = True
        printer("[Учёт] часовые зоны недоступны, квотные сутки считаю "
                "приблизительно — граница может уехать на час")
    return (moment + timedelta(hours=_FALLBACK_OFFSET_HOURS)).strftime("%Y-%m-%d")


def next_reset_local(now_utc=None) -> datetime:
    """Когда кончатся текущие квотные сутки — в МЕСТНОМ времени владельца.

    Местное нарочно: человек живёт в местном, а не в тихоокеанском. Число
    здесь не пишется — оно считается, поэтому осенью и весной оно меняется
    само.
    """
    moment = now_utc or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = _zone()
    if zone is None:
        base = moment + timedelta(hours=_FALLBACK_OFFSET_HOURS)
        midnight = (base + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return (midnight - timedelta(hours=_FALLBACK_OFFSET_HOURS)).astimezone()
    there = moment.astimezone(zone)
    midnight = (there + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone()


# -- Потолки --------------------------------------------------------------

def caps() -> dict:
    """Суточные потолки. Числа из настроек владельца, умолчание — из плана."""
    out = dict(DEFAULT_CAPS)
    try:
        from config.loader import get_setting
        got = get_setting(_CAP_SETTING)
        if isinstance(got, dict):
            for key, value in got.items():
                if key in out and isinstance(value, int) and value >= 0:
                    out[key] = value
    except Exception:
        pass
    return out


def bucket_of(role: str) -> str:
    """Какой квотой платит роль. Роль, а не имя модели (I37)."""
    name = str(role or "")
    if name in ("live_voice", "live_screen"):
        return SESSION_BUCKET
    if name == "aux_cheap":
        return CHEAP_BUCKET
    return PAID_BUCKET


# -- Запись ---------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key_fp(api_key) -> str:
    """Отпечаток ключа — НИКОГДА сам ключ.

    Заполняется уже сейчас, хотя ротации ключей ещё нет: иначе при появлении
    второго ключа весь прошлый расход окажется в одной безымянной куче, и
    учёт «на ключ» (Р12) начнётся с нуля.
    """
    import hashlib
    text = str(api_key or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _speak_broken(printer, why: str) -> None:
    global _broken_said
    if printer is None or _broken_said:
        return
    _broken_said = True
    printer(f"[Учёт] расход не считается: {why}. Вызовы разрешаю, но остаток "
            f"назвать не смогу")


def _task_of(ctx):
    """Номер дела для строки расхода. Явный пропуск сильнее контекста.

    ПОЧЕМУ ОТКАТ НА КОНТЕКСТ ОБЯЗАТЕЛЕН (найдено замером 20.08.2026)
    Дверь к модели принимает пропуск параметром, но её вызывающие —
    планировщик, разбор ошибок, сборка ответа — его НЕ передают. Значит на
    настоящем пути `ctx` всегда None, и номер дела в строку расхода не
    попадал: колонка оставалась пустой, а потолок «не больше восьми вызовов на
    задачу» не мог сработать НИКОГДА.

    Мой собственный тест это пропустил, и это грабли №4: он проверял пропуск в
    КОНТЕКСТЕ, а не то, что доехало до базы. Проверять надо результат, а не
    полпути.

    Порядок старшинства взят из шапки core/task_context дословно: явно
    переданный пропуск всегда сильнее того, что в контексте. Поэтому сначала
    параметр, и только если его нет — контекст.
    """
    got = getattr(ctx, "task_id", None)
    if got:
        return got
    try:
        from core import task_context
        return getattr(task_context.current(), "task_id", None)
    except Exception:
        return None


def reserve(role: str, ctx=None, est_in_tokens: int = 0, *, model_name=None,
            api_key=None, conn=None, printer=None, now_utc=None) -> dict:
    """Занять место под вызов ДО обращения к модели.

    Возвращает {'call_id', 'allowed', 'why', 'bucket', 'quota_day'}.
    `allowed=False` означает «потолок суток исчерпан» — и это НИКОГДА не
    молчаливый отказ (I19): причина названа кодом, вызывающий обязан сказать
    её владельцу.
    """
    day = quota_day(now_utc, printer=printer)
    bucket = bucket_of(role)
    call_id = "C-" + secrets.token_hex(8)
    out = {"call_id": call_id, "allowed": True, "why": "", "bucket": bucket,
           "quota_day": day}

    limit = caps().get(bucket)
    from core import writer

    def job(own):
        # ПРОВЕРКА ПОТОЛКА И ЗАНЯТИЕ МЕСТА — В ОДНОЙ ТРАНЗАКЦИИ (блок 7).
        # До кассы порядок стерёг свой замок этого файла, и это работало, но
        # держалось на дисциплине. Теперь атомарность даёт транзакция: два
        # потока не могут оба увидеть «остался один» и оба пойти, потому что
        # второй ждёт, пока первый допишет.
        if limit is not None:
            spent = _spent(own, day, bucket)
            if spent >= limit:
                out["allowed"] = False
                out["why"] = "daily_cap"
                out["spent"] = spent
                out["limit"] = limit
                return out
        own.execute(
            "INSERT INTO mx_meter_call (call_id, quota_day, role, "
            "model_name, key_fp, task_id, bucket, in_tokens, ok, "
            "started_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (call_id, day, str(role), str(model_name or role),
             _key_fp(api_key), _task_of(ctx), bucket,
             int(est_in_tokens or 0), _RESERVED, _now_iso()))
        return out

    try:
        if conn is not None:
            return writer.write_on(conn, job)
        return writer.write(job, label="metering.reserve")
    except Exception as exc:                      # noqa: BLE001
        _speak_broken(printer, str(exc)[:80])
        out["allowed"] = True                     # дело важнее учёта
        out["why"] = "meter_offline"
        return out


# Значение колонки ok у ещё не закрытого резерва. Не 0 и не 1: ноль означал
# бы «вызов провалился», а мы про него пока ничего не знаем.
_RESERVED = 2

# Через сколько незакрытый резерв считается потерянным. Больше самого
# длинного мыслимого вызова: по реестру таймаут 60 с × 2 попытки + пауза,
# то есть худший случай около 122 с (замерено в фазе 0.5). Берём с запасом —
# закрыть ЖИВОЙ резерв хуже, чем закрыть мёртвый на минуту позже.
_LOST_AFTER_S = 600.0


def commit(call_id: str, in_tokens=None, out_tokens=None, ok: bool = True,
           err_kind=None, *, conn=None, printer=None) -> bool:
    """Закрыть резерв фактом. Возвращает True, если строка нашлась.

    Здесь же обновляется суточный итог — В ТОЙ ЖЕ ТРАНЗАКЦИИ (см. шапку).
    """
    from core import writer

    def job(own):
        row = own.execute(
            "SELECT quota_day, role, model_name, key_fp, in_tokens "
            "FROM mx_meter_call WHERE call_id=?", (call_id,)).fetchone()
        if row is None:
            return False
        got_in = int(in_tokens if in_tokens is not None
                     else (row["in_tokens"] or 0))
        got_out = int(out_tokens or 0)
        own.execute(
            "UPDATE mx_meter_call SET in_tokens=?, out_tokens=?, ok=?, "
            "err_kind=?, ms=? WHERE call_id=?",
            (got_in, got_out, 1 if ok else 0,
             None if err_kind is None else str(err_kind), None, call_id))
        _bump_day(own, row["quota_day"], row["role"], row["model_name"],
                  row["key_fp"] or "", got_in, got_out, ok)
        return True

    try:
        if conn is not None:
            return bool(writer.write_on(conn, job))
        return bool(writer.write(job, label="metering.commit"))
    except Exception as exc:                      # noqa: BLE001
        _speak_broken(printer, str(exc)[:80])
        return False


def _bump_day(conn, day, role, model_name, key_fp, in_tokens, out_tokens,
              ok) -> None:
    """Суточный итог. Хранится бессрочно, в отличие от подробностей."""
    conn.execute(
        "INSERT INTO mx_meter_day (quota_day, role, model_name, key_fp, "
        "calls_n, fail_n, in_tokens, out_tokens) VALUES (?,?,?,?,1,?,?,?) "
        "ON CONFLICT(quota_day, role, model_name, key_fp) DO UPDATE SET "
        "calls_n = calls_n + 1, fail_n = fail_n + ?, "
        "in_tokens = in_tokens + ?, out_tokens = out_tokens + ?",
        (day, role, model_name, key_fp, 0 if ok else 1, in_tokens, out_tokens,
         0 if ok else 1, in_tokens, out_tokens))


def close_lost(*, conn=None, printer=None, older_than_s=None,
               now_utc=None) -> int:
    """Закрыть резервы, которые никто не подтвердил. Возвращает сколько.

    Вызов, который не вернулся, квоту всё равно съел. Пессимизм здесь
    правильный: лучше счесть сожжённым то, что могло не сгореть.

    ВОЗРАСТ ОБЯЗАТЕЛЕН, И ЭТО НАЙДЕНО ПОРЧЕЙ КОДА 18.08.2026.
    Первая версия закрывала ЛЮБОЙ незакрытый резерв и вызывалась из
    `reserve()`. На двух параллельных задачах это убивало живой резерв
    соседнего потока: он помечался «потерян», а потом при подтверждении
    считался ВТОРОЙ раз. Живая проба показала `calls_n = 2` там, где вызов
    был один. Счётчик врал бы вдвое — и именно на тех днях, когда работают
    две задачи, то есть когда точность нужнее всего.

    Теперь: закрываем только те, что старше самого длинного мыслимого
    вызова, и зовём это при СТАРТЕ, а не на каждом вызове.
    """
    limit_s = _LOST_AFTER_S if older_than_s is None else float(older_than_s)
    moment = now_utc or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    edge = (moment - timedelta(seconds=limit_s)).isoformat(timespec="seconds")
    from core import writer

    def job(own):
        rows = own.execute(
            "SELECT call_id, quota_day, role, model_name, key_fp, in_tokens "
            "FROM mx_meter_call WHERE ok=? AND started_utc <= ?",
            (_RESERVED, edge)).fetchall()
        for row in rows:
            own.execute(
                "UPDATE mx_meter_call SET ok=0, err_kind='lost' "
                "WHERE call_id=?", (row["call_id"],))
            _bump_day(own, row["quota_day"], row["role"], row["model_name"],
                      row["key_fp"] or "", int(row["in_tokens"] or 0), 0,
                      False)
        return len(rows)

    try:
        if conn is not None:
            found = int(writer.write_on(conn, job) or 0)
        else:
            found = int(writer.write(job, label="metering.close_lost") or 0)
    except Exception:
        return 0
    if found and printer is not None:
        printer(f"[Учёт] незакрытых вызовов с прошлого запуска: {found} "
                f"— считаю их израсходованными")
    return found


# -- Остаток --------------------------------------------------------------

def _spent(conn, day: str, bucket: str) -> int:
    """Сколько вызовов уже занято за эти сутки, включая незакрытые резервы."""
    row = conn.execute(
        "SELECT count(*) FROM mx_meter_call WHERE quota_day=? AND bucket=?",
        (day, bucket)).fetchone()
    return int(row[0]) if row else 0


def remaining(role: str = "aux_light", *, conn=None, now_utc=None) -> dict:
    """Сколько осталось — то, что Джарвис скажет вслух.

    Числами, а не процентами: «19%» не говорит ничего, «23 из 120» говорит
    всё. И числа СВОИ, а не гугловские: своим потолком мы управляем.
    """
    day = quota_day(now_utc)
    bucket = bucket_of(role)
    limit = caps().get(bucket)
    out = {"quota_day": day, "bucket": bucket, "limit": limit, "spent": None,
           "left": None, "known": False,
           "reset_local": next_reset_local(now_utc)}
    try:
        own, close = _conn(conn)
    except Exception:
        return out
    try:
        spent = _spent(own, day, bucket)
        out["spent"] = spent
        out["known"] = True
        if limit is not None:
            out["left"] = max(0, limit - spent)
    except Exception:
        pass
    finally:
        if close:
            own.close()
    return out


def _conn(conn):
    """Соединение для ЧТЕНИЯ остатка. Второе значение — надо ли закрывать.

    После блока 7 своё соединение здесь больше не открывается: чтение идёт на
    соединении кассы, отдельном от записи (Д41: «чтение — своё соединение на
    поток, только чтение»). Замер: открытие соединения на каждый вопрос
    стоило 1,61 мс и делалось дважды на каждый вызов модели.

    Форма ответа сохранена нарочно: за неё держится подмена в шести местах
    tests/test_metering_door.py, и ломать её ради красоты нельзя.
    """
    if conn is not None:
        return conn, False
    from core import writer
    return writer.reader(), False


def reset_for_tests() -> None:
    """Забыть, что про поломку учёта уже говорили. Зовёт conftest."""
    global _broken_said
    _broken_said = False
