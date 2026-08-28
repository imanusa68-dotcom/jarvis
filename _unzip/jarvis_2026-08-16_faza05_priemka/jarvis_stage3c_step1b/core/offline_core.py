# -*- coding: utf-8 -*-
"""
Оффлайн-ядро — маленький местный исполнитель на случай, когда сети нет.

Зачем он существует. Сегодня руки Джарвиса растут из живой сессии с моделью:
упала сеть — и строка ввода отвечает «not connected», а напоминание пропадает
вместе со своей записью. Этот модуль понимает узкий набор команд по шаблонам,
сам считает время, сам зовёт те же инструменты, что и большая модель, и ровно
через ту же дверь безопасности.

Три правила, которые тут важнее красоты:
  1. Ноль обращений к модели и ноль обращений наружу. Никогда.
  2. Каждое действие — только через core.gate.dispatch. Второй двери нет.
  3. Не уверен, что это команда, — вернуть None и не мешать модели.

Модуль ничего не печатает и никуда не пишет сам: он возвращает текст, а
показывает его вызывающая сторона. Так соблюдается правило «один рот».

Модули действий ввозятся лениво, внутри веток: наверху их нет намеренно, иначе
ядро потянуло бы за собой сетевые библиотеки и перестало быть проверяемым без
сети.

Проверки: python -m pytest -q  или  python tests/test_offline_core_no_network.py
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

# Модулем, а не именем: тесты подменяют core.gate.dispatch, и подмена обязана
# быть услышана здесь.
import core.gate as _gate
import core.lang as _lang

# Длинный абзац — это не команда, а мысль вслух. Не перехватываем.
_MAX_COMMAND_CHARS = 400


@dataclass
class Reply:
    """Ответ ядра. text — что показать владельцу, остальное — для журнала."""

    text: str
    tool: str = ""
    verdict: str = ""
    ok: bool = True


# Единственная таблица «инструмент → откуда его брать». Ленивый ввоз.
_LOADERS = {
    "reminder": ("actions.reminder", "reminder"),
    "open_app": ("actions.open_app", "open_app"),
    "open_path": ("actions.open_path", "open_path"),
    "file_controller": ("actions.file_controller", "file_controller"),
    "volume": ("actions.volume", "volume"),
}

# Человеческие имена папок → ключи, которые понимает резолвер проекта.
_FOLDER_KEY = {
    "рабочий стол": "desktop", "рабочем столе": "desktop", "рабочего стола": "desktop",
    "стол": "desktop", "desktop": "desktop",
    "загрузки": "downloads", "загрузках": "downloads", "скачанное": "downloads",
    "downloads": "downloads",
    "документы": "documents", "документах": "documents", "documents": "documents",
    "картинки": "pictures", "изображения": "pictures", "pictures": "pictures",
    "музыка": "music", "музыку": "music", "music": "music",
    "видео": "videos", "videos": "videos",
}

# «открой мне глаза» — не команда открыть. Местоимение после глагола = мимо.
_PRONOUNS = {
    "мне", "нам", "меня", "нас", "тебе", "себе", "ему", "ей", "им", "его", "её",
    "me", "us", "him", "her", "them", "yourself",
}

# Умения ядра и эталонная фраза к каждому. Меню собирается из этой таблицы,
# а тест гоняет каждый пример через разбор: пункт, под которым нет работающей
# фразы, — это обещание, которого нет. Примеры записаны так, как их говорит
# владелец вслух, и только строчными русскими словами.
_SKILLS = (
    ("время и дата", "сколько время"),
    ("напоминания", "напомни через 20 минут выпить воды"),
    ("открыть приложение, файл или папку", "открой блокнот"),
    ("найти файл", "найди файл отчет"),
    ("заметка в файл", "запиши заметку купить хлеб"),
    ("«что ты делал»", "что ты делал"),
    ("отмена и повтор последнего действия", "отмени последнее действие"),
    ("громкость", "сделай громче"),
)

_MENU = (
    " · ".join(name for name, _ in _SKILLS),
    "time and date, reminders, opening an app, file or folder, finding a file,"
    " writing a note, \"what did you do\", undo and redo of the last action, volume",
)

_PHRASES = {
    "offline_status": (
        "Сейчас я работаю без сети, сэр — разговор и голос даёт облако, их нет."
        " Местным ходом умею: {menu}. Состояние моделей: {guard}",
        "I am running without a connection, sir - speech and voice come from the"
        " cloud and are unavailable. Locally I can do: {menu}. Model state: {guard}",
    ),
    # Числами, а не процентами: «19%» не говорит ничего, «23 из 120» говорит
    # всё. И числа СВОИ, а не гугловские: свой потолок 120 из 500 — запас на
    # ошибки и повторы, и управляем мы своим.
    "quota": (
        "Сегодня {spent} из {limit}, сэр — осталось {left}. Счёт обнулится"
        " в {reset}. Остывание после отказов: {guard}",
        "Today {spent} of {limit}, sir - {left} left. The count resets at"
        " {reset}. Cooldown state: {guard}",
    ),
    "quota_unknown": (
        "Суточный остаток назвать не могу, сэр: {why}. Что знаю точно —"
        " остывание после отказов: {guard}",
        "I cannot state the daily balance, sir: {why}. What I do know is the"
        " cooldown state: {guard}",
    ),
    "now": ("Сейчас {stamp}.", "It is {stamp} now."),
    "no_time": (
        "Скажите время, сэр — например «напомни через 20 минут выпить воды» или"
        " «напомни завтра в 9:00 позвонить в банк». Сам придумывать час не стану.",
        "Tell me the time, sir - for example \"remind me in 20 minutes to drink"
        " water\" or \"remind me tomorrow at 9:00 to call the bank\". I will not"
        " invent an hour on my own.",
    ),
    "no_message": (
        "О чём напомнить, сэр? Скажите одной фразой, что записать.",
        "What should I remind you about, sir? Say it in one short phrase.",
    ),
    "no_note": (
        "Что записать в заметку, сэр? Скажите текст после слова «заметка».",
        "What should go into the note, sir? Say the text after the word \"note\".",
    ),
    "no_target": (
        "Что именно найти, сэр? Назовите часть имени файла.",
        "What exactly should I find, sir? Give me part of the file name.",
    ),
    "no_folder": (
        "Не нашёл настоящий путь папки «{name}», сэр — угадывать не буду.",
        "I could not resolve the real path of the \"{name}\" folder, sir - and I"
        " will not guess it.",
    ),
    "cancel_which": (
        "Какое напоминание отменить, сэр? Назовите пару слов из него.",
        "Which reminder should I cancel, sir? Give me a couple of words from it.",
    ),
    "cancel_what": (
        "Что отменить, сэр? Скажите «отмени последнее действие» или"
        " «отмени напоминание про …». Угадывать я не стану.",
        "Cancel what, sir? Say \"undo the last action\" or \"cancel the reminder"
        " about ...\". I will not guess.",
    ),
    "journal_empty": (
        "Журнал пуст, сэр — за мной пока ничего не записано.",
        "The journal is empty, sir - nothing has been recorded yet.",
    ),
    "journal_head": (
        "Последнее, что я делал, сэр:",
        "The last things I did, sir:",
    ),
    "journal_broken": (
        "Journal недоступен, сэр: {error}. Врать про свои дела не буду.",
        "The journal is unreachable, sir: {error}. I will not invent my history.",
    ),
    "confirm": (
        "Это может затереть уже записанное, сэр, поэтому без сети я так не делаю:"
        " спросить у вас подтверждение сейчас некому. Если файл новый — скажите"
        " «запиши заметку …», я создам его целиком.",
        "This could overwrite something you already have, sir, so I will not do it"
        " while offline: there is nobody here to ask you for confirmation. If the"
        " file is new, say \"write a note ...\" and I will create it in full.",
    ),
    "screen_off": (
        "Для этого нужен включённый экранный доступ, сэр — нажмите SCREEN и"
        " повторите.",
        "This needs screen control switched on, sir - press SCREEN and repeat.",
    ),
    "blocked": (
        "Не разрешено, сэр: {reason}",
        "Not permitted, sir: {reason}",
    ),
    "tool_broken": (
        "Инструмент «{tool}» не отработал, сэр: {error}",
        "The \"{tool}\" tool failed, sir: {error}",
    ),
    "no_model": (
        "Связи с моделью сейчас нет, сэр, поэтому разговора не выйдет и"
        " придумывать ответ я не стану. Своим ходом умею: {menu}.",
        "There is no connection to the model right now, sir, so I cannot hold a"
        " conversation and I will not invent an answer. On my own I can do:"
        " {menu}.",
    ),
    "crash": (
        "Сорвалось, сэр: {error}. Ничего не выполнено.",
        "It broke, sir: {error}. Nothing was carried out.",
    ),
}


# ── мелкие помощники ────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Нижний регистр, «ё» как «е», один пробел, без хвостовых знаков."""
    one_line = re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()
    return one_line.strip(" \t\n\r,.;:!?…")


# Обёртки, с которых владелец начинает живую фразу. Срезаются только с начала,
# только целыми словами: «слушай, открой блокнот» — это «открой блокнот».
# Местоимений здесь нет намеренно, иначе «открой мне глаза» перестало бы быть
# разговором и превратилось в команду.
_HEAD_FILLER = re.compile(
    r"^\s*(?:джарвис|jarvis|сэр|sir|слушай(?:-ка)?|слушайте|эй|ну|а|так|короче"
    r"|бро|братан|братишка|братух(?:а|ан)?|дружище|дружбан|чувак|слышь|слышишь"
    r"|пожалуйста|please|будь добр(?:а|ы)?|будьте добры|можешь(?: ли)?"
    r"|не мог(?:ла)? бы(?: ты)?|давай|hey|okay|ok)\b[\s,.\-—:]*",
    re.IGNORECASE)

# «…, пожалуйста» в конце. Срезаем, только если после этого остаётся хотя бы
# два слова: «открой пожалуйста» — это не команда, а обрывок.
# Живая приёмка 12.08.2026: владелец пишет «плиз», и это слово уезжало в
# лаунчер частью имени программы: «блокнот плиз». «Спасибо» здесь нет
# намеренно: оно часто бывает текстом заметки, а обёрткой команды — почти нет.
_TAIL_PLEASE = re.compile(
    r"[\s,]*\b(?:пожалуйста|пожалуйсто|пожалуста|пжалуйста|пжалуста"
    r"|пжалста|пжлст|пж(?:п|л)?|пли+з+|плз|плс"
    r"|please|plz|pls)\s*$", re.IGNORECASE)

# Вопрос о мире — не команда, даже если дальше в фразе попалось знакомое
# слово: «что такое квота», «зачем ты открыл блокнот». Список нарочно
# короткий: сюда входят только начала фраз, с которых не начинается ни одно
# умение из меню.
_NOT_A_COMMAND = re.compile(
    r"^(?:что такое|что значит|чем отличается|расскажи|объясни|опиши"
    r"|как работает|как работают|как сделать|почему|зачем|отчего"
    r"|ты умеешь|умеешь ли|можно ли|правда ли|переведи|посчитай"
    r"|what is|what does|what are|explain|tell me|describe|why|how does"
    r"|how do|translate|can you)\b")


def _strip_fillers(text: str) -> str:
    """
    Убрать обращение и вежливую обёртку, не тронув сути и заглавных букв.

    Работаем по исходной строке, а не по приведённой к нижнему регистру: из
    этой же строки ветки потом берут имя файла и текст заметки.
    """
    out = text.strip()
    for _ in range(4):                   # предел, чтобы не крутиться вечно
        shorter = _HEAD_FILLER.sub("", out, count=1).strip()
        if shorter == out:
            break
        out = shorter
    trimmed = _TAIL_PLEASE.sub("", out).strip()
    if trimmed and len(trimmed.split()) >= 2:
        out = trimmed
    return out


def _tongue(text: str) -> int:
    """0 — отвечаем по-русски, 1 — по-английски. Решает общий определитель."""
    try:
        return 0 if _lang.detect(text or "") == "ru" else 1
    except Exception:
        return 0


def _say(key: str, tongue: int, **kw) -> str:
    template = _PHRASES[key][tongue]
    return template.format(**kw) if kw else template


def _import_tool(tool: str):
    """Ленивый ввоз модуля действия. Отдельная функция — это шов для тестов."""
    module_name, func_name = _LOADERS[tool]
    return getattr(importlib.import_module(module_name), func_name)


def _run_tool(tool: str, params: dict, tongue: int) -> Reply:
    """Единственный путь к любому инструменту: сначала дверь, потом действие."""
    result = _gate.dispatch(tool, params, mode="interactive", screen_control=False)
    if not result.allowed:
        return Reply(text=_refusal(result, tongue), tool=tool,
                     verdict=result.verdict, ok=False)
    try:
        answer = _import_tool(tool)(parameters=params)
    except Exception as exc:
        _note(tool, params, _short(exc), False)
        return Reply(
            text=_say("tool_broken", tongue, tool=tool, error=_short(exc)),
            tool=tool, verdict=result.verdict, ok=False,
        )
    _note(tool, params, answer, True)
    return Reply(text=str(answer).strip(), tool=tool, verdict=result.verdict)


def _note(tool: str, params: dict, summary, ok: bool) -> None:
    """
    Записка о сделанном — в одну кассу на весь проект.

    Отдельная функция это шов: тест подменяет её и видит, что именно ядро
    собиралось записать. Отказ двери сюда не доходит намеренно — записывают
    только то, что действительно случилось.

    Ошибка записи не имеет права утопить само действие, поэтому любое
    исключение здесь умирает молча.
    """
    try:
        from core.action_log import note
        note(tool=tool, action=(params or {}).get("action"),
             summary=summary, ok=ok)
    except Exception:
        pass


def _refusal(result, tongue: int) -> str:
    """Отказ словами владельца, а не служебным текстом, написанным для модели."""
    if result.verdict == "confirm":
        return _say("confirm", tongue)
    if result.verdict == "screen_off":
        return _say("screen_off", tongue)
    reason = (getattr(result, "reason", "") or "").strip()
    return _say("blocked", tongue, reason=reason or result.verdict)


def _short(exc: Exception) -> str:
    return (type(exc).__name__ + ": " + str(exc)).strip()[:200]


def _now():
    from core.time_utils import get_now
    return get_now()


def _stamp() -> str:
    now = _now()
    text = now.strftime("%d.%m.%Y, %H:%M")
    try:
        # Метку пояса собирает один общий описатель. Раньше здесь склеивались
        # имя пояса и смещение, а у пояса Windows имени нет — и владелец
        # видел одно и то же дважды: «(UTC+03:00, UTC+03:00)».
        from core.time_utils import describe_timezone
        return "%s (%s)" % (text, describe_timezone())
    except Exception:
        return text


def _resolve_folder(key: str):
    """
    Настоящий путь личной папки — с учётом переноса в OneDrive.

    Берём тот же источник правды, что и файловый инструмент, но на слой ниже:
    сам файловый инструмент наверху ввозит корзину и весь файловый стек, а ради
    перевода слова «загрузки» в путь это лишнее. Своей таблицы папок здесь нет
    и быть не должно — иначе два места будут расходиться в ответе.
    """
    module = importlib.import_module("core.awareness._known_folders")
    found = module.folder(key)
    return str(found) if found else None


def _looks_like_path(target: str) -> bool:
    return ("\\" in target) or ("/" in target) or (":" in target) or target.startswith("~")


# ── разбор времени для напоминаний ──────────────────────────────────────────

_IN_MIN = re.compile(r"через (\d{1,3}) ?(минут\w*|мин)\b|in (\d{1,3}) ?(minutes?|mins?)\b")
_IN_HOUR = re.compile(r"через (\d{1,2}) ?(час\w*)\b|in (\d{1,2}) ?(hours?|hrs?)\b")
_AT_HHMM = re.compile(r"(?:^| )(?:в|at) (\d{1,2})[:.](\d{2})\b")
_AT_HOUR = re.compile(r"(?:^| )(?:в|at) (\d{1,2}) ?(?:час\w*|o'?clock)\b")
_TOMORROW = re.compile(r"\bзавтра\b|\btomorrow\b")
# «через час», «через полчаса» — без числа. Владелец говорит так чаще, чем
# «через 60 минут», а без этих трёх строк час не разбирался вовсе.
_IN_PLAIN = (
    (re.compile(r"через полчаса\b|in half an hour\b"), 30),
    (re.compile(r"через час\b|in an hour\b"), 60),
    (re.compile(r"через минут(?:у|ку)\b|in a minute\b"), 1),
)


def _when(low: str):
    """Момент времени из фразы или None. Считается на месте, без модели."""
    now = _now()

    found = _IN_MIN.search(low)
    if found:
        return now + timedelta(minutes=int(found.group(1) or found.group(3)))

    found = _IN_HOUR.search(low)
    if found:
        return now + timedelta(hours=int(found.group(1) or found.group(3)))

    for pattern, minutes in _IN_PLAIN:
        if pattern.search(low):
            return now + timedelta(minutes=minutes)

    hour = minute = None
    found = _AT_HHMM.search(low)
    if found:
        hour, minute = int(found.group(1)), int(found.group(2))
    else:
        found = _AT_HOUR.search(low)
        if found:
            hour, minute = int(found.group(1)), 0

    if hour is None or hour > 23 or minute > 59:
        return None

    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if _TOMORROW.search(low):
        moment = moment + timedelta(days=1)
    elif moment <= now:
        # Час уже прошёл — значит владелец говорит про завтра, а не про вчера.
        moment = moment + timedelta(days=1)
    return moment


def _strip_time_words(text: str) -> str:
    # Единицу времени называем поимённо. Прежний жадный кусок хватал любое
    # непробельное слово: на слипшемся «1 минутвыключить» он съедал и слово
    # владельца. Теперь съесть можно только настоящую единицу времени.
    out = re.sub(r"(?i)\b(через \d{1,3} ?(?:минут\w*|мин|час\w*|секунд\w*|сек)\b"
                 r"|in \d{1,3} ?(?:minutes?|mins?|hours?|hrs?|seconds?|secs?)\b"
                 r"|через полчаса|через час|через минут(?:у|ку)"
                 r"|in half an hour|in an hour|in a minute)", " ", text)
    out = re.sub(r"(?i)\b(завтра|сегодня|tomorrow|today)\b", " ", out)
    out = re.sub(r"(?i)(?:^| )(?:в|at) \d{1,2}(?:[:.]\d{2})?(?: ?(?:час\w*|o'?clock))?", " ", out)
    return re.sub(r"\s+", " ", out).strip(" ,.;:—-")


def _tail(raw: str, pattern: str) -> str:
    """
    Хвост фразы после командного оборота.

    Оборот заменяется ПРОБЕЛОМ, а не пустотой. Живая фраза владельца
    12.08.2026 «через 1 минут напомни выключить чайник» вырезала глагол
    изнутри строки — соседние слова слипались в «минутвыключить», и от
    записки оставалось одно слово «чайник».
    """
    cut = re.sub(pattern, " ", raw, count=1, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cut).strip(" ,.;:—-\"'«»")


# ── ветки ───────────────────────────────────────────────────────────────────

def _route_now(raw, low, match, tongue):
    return Reply(text=_say("now", tongue, stamp=_stamp()))


def _route_quota(raw, low, match, tongue):
    """«Сколько осталось» — теперь правдой, а не признанием в незнании.

    До блока 5 здесь честно говорилось «счётчика расхода в проекте ещё нет».
    Счётчик появился, и фраза заменена, а не оставлена рядом: две фразы про
    одно и то же — это вопрос «какая из них сегодня правда».

    Если учёт недоступен, говорим «не могу назвать» и причину. Выдуманное
    число хуже отсутствующего: по нему владелец примет решение.
    """
    try:
        from core.model_guard import get_guard
        guard = get_guard().status_summary()
    except Exception as exc:
        guard = _short(exc)
    try:
        from core import metering
        left = metering.remaining("aux_light")
    except Exception as exc:
        return Reply(text=_say("quota_unknown", tongue, why=_short(exc),
                               guard=guard))
    if not left.get("known") or left.get("limit") is None:
        why = ("учёт расхода недоступен" if tongue == 0
               else "the usage meter is unavailable")
        return Reply(text=_say("quota_unknown", tongue, why=why, guard=guard))
    return Reply(text=_say("quota", tongue,
                           spent=left["spent"], limit=left["limit"],
                           left=left["left"],
                           reset=left["reset_local"].strftime("%H:%M"),
                           guard=guard))


def _route_status(raw, low, match, tongue):
    try:
        from core.model_guard import get_guard
        guard = get_guard().status_summary()
    except Exception as exc:
        guard = _short(exc)
    return Reply(text=_say("offline_status", tongue, menu=_MENU[tongue], guard=guard))


def _route_journal(raw, low, match, tongue):
    try:
        from core.journal import Journal
        from core import writer
        # БЛОК 7: раньше здесь база открывалась и НИКОГДА не закрывалась —
        # утечка соединения на каждый вопрос «что ты делал» без сети. Теперь
        # это соединение чтения: одно на поток, живёт с процессом, и запись
        # через него физически невозможна.
        entries = Journal(writer.reader()).recent_actions()
    except Exception as exc:
        return Reply(text=_say("journal_broken", tongue, error=_short(exc)), ok=False)
    if not entries:
        return Reply(text=_say("journal_empty", tongue))
    lines = [_say("journal_head", tongue)]
    for item in entries:
        mark = "\u2713" if item.get("ok") else "\u2717"
        lines.append("  %s %s" % (mark, item.get("summary", "")))
    return Reply(text="\n".join(lines))


def _route_reminder_list(raw, low, match, tongue):
    return _run_tool("reminder", {"action": "list"}, tongue)


def _route_reminder_cancel(raw, low, match, tongue):
    message = _tail(raw, r"^\s*(отмени|удали|убери)\s+(напоминание|напоминания|напоминалку)?\s*(про|о|об)?\s*")
    message = _tail(message, r"^\s*cancel\s+(the\s+)?reminder\s*(about)?\s*")
    if not message:
        return Reply(text=_say("cancel_which", tongue))
    return _run_tool("reminder", {"action": "cancel", "message": message}, tongue)


def _route_reminder_set(raw, low, match, tongue):
    moment = _when(low)
    if moment is None:
        return Reply(text=_say("no_time", tongue))
    # Оборот срезаем где бы он ни стоял: владелец говорит и «напомни через
    # 10 минут…», и «через 10 минут напомни…». Якорь начала строки во втором
    # случае оставлял сам глагол внутри записки.
    message = _tail(raw, r"\s*\b(напомни(?:те|ть)?(?:\s+мне)?"
                         r"|(?:поставь|заведи|создай|добавь)\s+напоминание"
                         r"|remind\s+me(?:\s+to)?)\b\s*")
    message = _strip_time_words(message)
    message = _tail(message, r"^\s*(что|чтобы|о\s+том,?\s+что|про\s+то,?\s+что|про|to)\s+")
    if not message:
        return Reply(text=_say("no_message", tongue))
    params = {
        "action": "set",
        "date": moment.strftime("%Y-%m-%d"),
        "time": moment.strftime("%H:%M"),
        "message": message,
    }
    return _run_tool("reminder", params, tongue)


def _route_note(raw, low, match, tongue):
    body = _tail(raw, r"^\s*(запиши|сделай|создай|добавь|напиши)\s+(себе\s+)?(в\s+)?"
                      r"(заметку|заметка|заметки|заметках)\s*(про|о|об)?\s*[:\-]?\s*")
    body = _tail(body, r"^\s*заметк\w*\s+(сделай|запиши|создай)\s*[:\-]?\s*")
    body = _tail(body, r"^\s*(заметка|note)\s*[:\-]\s*")
    body = _tail(body, r"^\s*(write|make)\s+a\s+note\s*(about)?\s*[:\-]?\s*")
    if not body:
        return Reply(text=_say("no_note", tongue))
    name = "zametka_%s.txt" % _now().strftime("%Y-%m-%d_%H-%M-%S")
    params = {
        "action": "create_file",
        "path": "desktop",
        "name": name,
        "content": body,
    }
    return _run_tool("file_controller", params, tongue)


def _route_find(raw, low, match, tongue):
    # Слово «файл» больше не обязательно: «найди отчет» — тоже поиск. Но без
    # него берём только короткую просьбу, иначе «найди мне место в этой
    # жизни» уедет в файловый поиск вместо модели.
    named = re.search(r"\b(файл|папк|документ|file|folder|document)", low)
    if not named and len(low.split()) > 4:
        return None
    if re.search(r"\bв (?:интернете|сети|гугле|браузере|ютубе)\b", low):
        return None                      # это не наш поиск: наш — по диску
    term = _tail(raw, r"^\s*(найди|найти|поищи|ищи|разыщи)\s+(мне\s+)?((?:файл|файлы|папку|документ)\w*)?\s*(с\s+именем|по\s+имени|называется)?\s*")
    term = _tail(term, r"^\s*где\s+(лежит|находится|найти)?\s*((?:файл|файлы|папку|документ)\w*)?\s*")
    term = _tail(term, r"^\s*(find|search\s+for|where\s+is)\s+(the\s+|a\s+)?(file|folder|document)\s*(named|called)?\s*")
    if not term:
        return Reply(text=_say("no_target", tongue))
    return _run_tool("file_controller",
                     {"action": "find", "name": term, "path": "home"}, tongue)


def _route_undo(raw, low, match, tongue):
    return _run_tool("file_controller", {"action": "undo"}, tongue)


def _route_redo(raw, low, match, tongue):
    return _run_tool("file_controller", {"action": "redo"}, tongue)


def _route_history(raw, low, match, tongue):
    return _run_tool("file_controller", {"action": "history"}, tongue)


def _route_cancel_what(raw, low, match, tongue):
    """Голое «отмени». Отменить не то страшнее, чем переспросить."""
    return Reply(text=_say("cancel_what", tongue))


# Без якоря начала строки: обёртку вроде «слушай,» уже срезали, но глагол
# может стоять и не первым. Цель берётся отсюда — из исходной строки, чтобы
# имя файла не потеряло заглавные буквы.
_OPEN_VERB = re.compile(
    r"\b(?:открой|открыть|откройте|запусти|запустить|запустите|open|launch|start)"
    r"\s+(.+)$", re.IGNORECASE)


def _route_open(raw, low, match, tongue):
    # Цель берём из исходной строки, а не из приведённой к нижнему регистру:
    # иначе путь и имя программы теряют заглавные буквы.
    found = _OPEN_VERB.search(raw)
    target = (found.group(1) if found else match.group(2) or "").strip(" ,.;:—-\"'«»")
    if not target:
        return None
    words = target.split()
    if words[0].lower() in _PRONOUNS:
        return None                      # «открой мне глаза» — не команда

    key = _FOLDER_KEY.get(_norm(target))
    if not key:
        # «открой папку документы» — слово «папку» здесь служебное.
        bare = re.sub(r"(?i)^\s*(?:папку|папка|каталог|директорию)\s+", "", target)
        key = _FOLDER_KEY.get(_norm(bare))
    if key:
        try:
            path = _resolve_folder(key)
        except Exception as exc:
            return Reply(text=_say("tool_broken", tongue, tool="open_path",
                                   error=_short(exc)), ok=False)
        if not path:
            return Reply(text=_say("no_folder", tongue, name=target), ok=False)
        return _run_tool("open_path", {"path": path}, tongue)

    if _looks_like_path(target):
        return _run_tool("open_path", {"path": target}, tongue)

    if len(words) > 3:
        return None                      # длинная фраза — это не имя программы
    return _run_tool("open_app", {"app_name": target}, tongue)


# Громкость — про СИСТЕМНЫЙ звук, а не про плеер. «Включи музыку
# погромче» стоит в анти-корпусе четырёх тестов и обязана уйти большой
# модели: включить песню ядро всё равно не умеет, а покрутить громкость
# вместо этого — значит сделать не то, о чём просили.
_VOLUME_ALIEN = re.compile(r"музык|песн|трек|плеер|плейлист|видео|фильм"
                           r"|youtube|ютуб|радио|сериал|подкаст|микрофон")
_VOLUME_OFF = re.compile(r"выключи звук|отключи звук|убери звук|заглуш"
                         r"|\bmute\b")
_VOLUME_ON = re.compile(r"включи звук|верни звук|размут|\bunmute\b")
_VOLUME_ASK = re.compile(r"как(?:ая|ой) (?:сейчас )?громкост|громкость сейчас"
                         r"|сколько громкост|volume level|current volume")
_VOLUME_DOWN = re.compile(r"тише|убав|поменьше|quieter|lower|volume down")
_VOLUME_NUM = re.compile(r"(\d{1,3})")


def _route_volume(raw: str, low: str, match, tongue: int) -> Optional[Reply]:
    """
    Громкость системы: громче, тише, точное число, выключить, включить,
    сказать текущее. Возвращает None, если речь шла не о системном звуке:
    это законный ответ маршрута, _handle отдаёт его наружу как «не моё».

    Число берём ТОЛЬКО вместе со словом «громкость»: иначе «сделай громче
    на 5 минут» превратится в «поставь 5 процентов».
    """
    if _VOLUME_ALIEN.search(low):
        return None
    if _VOLUME_OFF.search(low):
        return _run_tool("volume", {"action": "mute"}, tongue)
    if _VOLUME_ON.search(low):
        return _run_tool("volume", {"action": "unmute"}, tongue)
    if _VOLUME_ASK.search(low):
        return _run_tool("volume", {"action": "status"}, tongue)
    number = _VOLUME_NUM.search(low)
    if number and re.search(r"громкост|volume", low):
        return _run_tool("volume",
                         {"action": "set", "level": int(number.group(1))}, tongue)
    if _VOLUME_DOWN.search(low):
        return _run_tool("volume", {"action": "down"}, tongue)
    return _run_tool("volume", {"action": "up"}, tongue)


# Порядок важен: узкое раньше широкого. «отмени напоминание» обязано пройти
# раньше, чем «отмени действие», иначе владелец потеряет не то.
_ROUTES = (
    (re.compile(r"сколько (?:сейчас |там |уже |щас )?врем\w*|который час"
                r"|сколько на часах|^врем\w*$|время сейчас"
                # «бро какое время?» — живая фраза 12.08.2026. Граница слова
                # после «врем...» обязательна: без неё разбор откатывается на
                # «врем» и обходит запрет, а «какое время года» — это разговор.
                r"|как(?:ое|ая|ой) (?:сейчас |там |уже |щас )?"
                r"врем\w*\b(?!\s+(?:года|суток|дня|ночи|месяца|недели))"
                r"|как(?:ое|ая) (?:сейчас |сегодня )?(?:число|дата)"
                r"|сегодня какое(?: число)?$|какой сегодня день"
                r"|what time is it|what'?s the time"
                r"|current time|what'?s (todays?|the) date"), _route_now),
    (re.compile(r"\bквот\w*|остаток лимита|лимит\w* модел\w*|\bquota\b|rate limit"), _route_quota),
    (re.compile(r"(^| )(статус|status)( |$)|ты (сейчас )?онлайн|есть ли сеть"
                r"|are you online|что ты умеешь без сети|what can you do offline"
                r"|что (?:ты )?умеешь|ты живой|сеть есть|связь есть"), _route_status),
    (re.compile(r"(список|какие|покажи|мои)[^.]{0,20}напомин|list (my )?reminders"
                r"|my reminders"), _route_reminder_list),
    (re.compile(r"^(отмени|удали|убери)[^.]{0,20}напомин|^cancel (the )?reminder"), _route_reminder_cancel),
    (re.compile(r"\bнапомни\b|\bнапомните\b|\bнапомнить\b"
                r"|(?:поставь|заведи|создай|добавь) напоминание"
                r"|\bremind me\b"), _route_reminder_set),
    (re.compile(r"^(запиши|сделай|создай|добавь|напиши) (себе )?(в )?заметк"
                r"|^заметк\w* (сделай|запиши|создай)|^заметка ?[:\-]"
                r"|^(write|make) a note|^note ?[:\-]"), _route_note),
    (re.compile(r"^(найди|найти|поищи|ищи|разыщи)\b"
                r"|^где (лежит|находится|найти)\b|^где (файл|папк|документ)"
                r"|^(find|search for) (the |a )?(file|folder|document)"
                r"|^where is (the |a )?file"), _route_find),
    (re.compile(r"что ты (делал|сделал)|что было сделано|последние действия"
                r"|what did you do|your last actions|recent actions"), _route_journal),
    (re.compile(r"истори\w* (действий|изменений)|action history"), _route_history),
    (re.compile(r"отмени (последнее |предыдущее )?действие|верни как было"
                r"|отмена последнего действия|\bundo\b"), _route_undo),
    (re.compile(r"верни вперед|повтори (последнее )?действие|\bredo\b"), _route_redo),
    (re.compile(r"^(?:отмени|отмена|отменить)$"), _route_cancel_what),
    (re.compile(r"(?:по)?громче\b|(?:по)?тише\b"
                r"|прибав\w*\s+(?:звук|громкост)"
                r"|убав\w*\s+(?:звук|громкост)|громкост\w*"
                r"|выключи звук|отключи звук|включи звук|верни звук"
                r"|убери звук|заглуш|\bmute\b|\bunmute\b|volume"), _route_volume),
    (re.compile(r"^(открой|открыть|откройте|запусти|запустить|запустите"
                r"|open|launch|start) (.+)$"), _route_open),
)


def _handle(text: str) -> Optional[Reply]:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or len(raw) > _MAX_COMMAND_CHARS:
        return None
    # Язык считаем по ИСХОДНОЙ фразе, до причёсывания: если решать по копии,
    # «Джарвис, status» превратится в «status» и ответ уедет на английский.
    tongue = _tongue(raw)
    raw = _strip_fillers(raw)
    if not raw:
        return None
    low = _norm(raw)
    if not low or _NOT_A_COMMAND.search(low):
        return None
    for pattern, route in _ROUTES:
        found = pattern.search(low)
        if found:
            return route(raw, low, found, tongue)
    return None


def offline_notice(text: str = "") -> str:
    """
    Что сказать владельцу про фразу, которую без сети исполнить некому.

    Язык берётся из самой фразы тем же определителем, что и у остальных
    ответов ядра: отказ на чужом языке — тоже непонятый отказ.
    """
    tongue = _tongue(text if isinstance(text, str) else "")
    return _say("no_model", tongue, menu=_MENU[tongue])


def handle(text: str) -> Optional[Reply]:
    """
    Разобрать команду владельца без сети и без модели.

    Возвращает Reply, если команда опознана; None — если это не наша ��оманда
    (тогда решать должна большая модель). Не бросает исключений: внутренняя
    поломка возвращается словами, а не тишиной.
    """
    try:
        return _handle(text)
    except Exception as exc:
        sample = text if isinstance(text, str) else ""
        return Reply(text=_say("crash", _tongue(sample), error=_short(exc)), ok=False)
