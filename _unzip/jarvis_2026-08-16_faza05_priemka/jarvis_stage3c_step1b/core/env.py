# -*- coding: utf-8 -*-
# core/env.py -- слой окружения (план Р11, шаг 34.1).
#
# ЗАЧЕМ ЭТОТ ФАЙЛ ВООБЩЕ ЕСТЬ
# На машине владельца три РАЗНЫХ канала текста, и кодировки у них разные.
# Проверено живьём 15.08.2026 на его же ноутбуке:
#   1) живая консоль          -> utf-8    работает, трогать НЕЛЬЗЯ
#   2) перенаправление в файл -> cp1251   сломано, чинит этот модуль
#   3) чтение файла командой type -> cp866  это уже cmd, Python бессилен
# Поэтому наивное "поставим utf-8 везде" сделало бы хуже: оно чинит второй
# канал и ломает первый. Здесь лечится ровно то, что болит.
#
# ГЛАВНАЯ ОПАСНОСТЬ -- НЕ КРАКОЗЯБРЫ, А ПАДЕНИЕ
# В cp1251 нет наших значков (например значка запрета в сообщениях модели).
# print со значком в перенаправленный поток кидает UnicodeEncodeError и роняет
# задачу целиком -- посреди работы, без объяснений. Кракозябры некрасивы,
# падение смертельно. Отсюда errors=replace: лучше знак вопроса вместо
# значка, чем труп процесса.
#
# ГРАНИЦЫ (Г-3: один файл -- один писатель)
#   * модуль НИЧЕГО не пишет в дом и не создаёт файлов сам по себе;
#   * setup() трогает только потоки текущего процесса и ничего больше;
#   * зовёт настройку main() один раз в самом начале; инструменты -- по желанию;
#   * набор тестов настройку НЕ зовёт: тестам потоки чинят run_tests.cmd.

import locale
import os
import re
import sys
from pathlib import Path

LF = chr(10)
CR = chr(13)
BACKSLASH = chr(92)
UTF8 = "utf-8"
REPLACE = "replace"
HIDDEN = "<скрыто>"

# Шаблоны ключей собраны из кусков НАРОЧНО. Иначе сторож секретов
# (tests/test_config_loader.py, test_no_key_literal_anywhere) найдёт наш же
# шаблон в исходнике и покраснеет. Та же грабля, что описана у него в шапке.
_KEY_RE = re.compile("A" + "Iza" + "[0-9A-Za-z_-]{10,}")
_SK_RE = re.compile("s" + "k-" + "[0-9A-Za-z_-]{16,}")

# Состояние процесса. Словарь, а не глобальные переменные: так его видно
# целиком в report() и не надо объявлять global в каждой функции.
_state = {
    "setup_done": False,
    "streams": {},
}


# -- Потоки вывода ----------------------------------------------------

def _norm(name):
    # cp1251, CP-1251 и UTF8 должны сравниваться одинаково.
    return (name or "").strip().lower().replace("_", "-")


def _fix_stream(name):
    # Вернуть человеческую строку: что сделали с потоком и почему.
    # Никогда не кидает исключений -- настройка окружения не имеет права
    # уронить запуск из-за экзотического потока.
    stream = getattr(sys, name, None)
    if stream is None:
        # pythonw.exe и служба без консоли: потока просто нет.
        return "потока нет, нечего чинить"
    encoding = _norm(getattr(stream, "encoding", ""))
    if encoding in ("utf-8", "utf8"):
        return "уже utf-8, не трогали"
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        # Кто-то подменил поток своим объектом (pytest, отладчик, наш же тест).
        return "не умеет перенастройку, оставили как есть (" + (encoding or "кодировка неизвестна") + ")"
    try:
        reconfigure(encoding=UTF8, errors=REPLACE)
    except Exception as exc:
        # I19: молчаливого провала быть не должно. Не печатаем (в этот момент
        # печатать ещё некуда), но запоминаем -- доктор покажет.
        return "перенастройка не вышла (" + type(exc).__name__ + ": " + str(exc) + ")"
    return "переведён с " + (encoding or "неизвестной кодировки") + " на utf-8"


def setup(force=False):
    # Идемпотентная настройка окружения. Второй вызов ничего не делает:
    # main() зовёт её один раз, но инструмент может импортировать main.
    if _state["setup_done"] and not force:
        return dict(_state["streams"])
    streams = {}
    for name in ("stdout", "stderr"):
        streams[name] = _fix_stream(name)
    _state["streams"] = streams
    _state["setup_done"] = True
    return dict(streams)


# -- Прятать секреты --------------------------------------------------

def _home_forms():
    # Домашняя папка в двух написаниях: с обратными и с прямыми косыми.
    # Короткие пути ("/", "C:") не заменяем -- изуродуем весь текст.
    try:
        home = str(Path.home())
    except Exception:
        return []
    if len(home) < 4:
        return []
    forms = [home, home.replace(BACKSLASH, "/")]
    out = []
    for form in forms:
        if form and form not in out:
            out.append(form)
    return out


def redact(text):
    # Убрать из текста то, что не должно попасть ни в журнал, ни на экран,
    # ни в архив: ключ модели и имя пользователя внутри пути к дому.
    # Никогда не кидает исключений: её зовут в обработчиках ошибок.
    try:
        value = text if isinstance(text, str) else str(text)
    except Exception:
        return HIDDEN
    try:
        value = _KEY_RE.sub(HIDDEN, value)
        value = _SK_RE.sub(HIDDEN, value)
        for home in _home_forms():
            value = re.sub(re.escape(home), "~", value, flags=re.IGNORECASE)
    except Exception:
        # Лучше отдать заглушку, чем утечь ключом из-за сбоя регулярки.
        return HIDDEN
    return value


def redact_checked(text):
    """Заглушить И ПРОВЕРИТЬ. Возвращает (текст, получилось ли).

    ЗАЧЕМ ВТОРАЯ ФУНКЦИЯ, ЕСЛИ ЕСТЬ redact
    --------------------------------------
    redact() отдаёт только строку, поэтому «вычистил» и «нечего было чистить»
    у неё выглядят одинаково, а «не смог» не выражается вовсе. Для экрана и
    для доктора этого достаточно. Для чёрного ящика — нет: план требует
    fail-closed, дословно «не смог вычистить, значит не пишем» (Х-F1,
    BLOCKER). Чтобы не писать, нужно УЗНАТЬ о неудаче, а узнать сегодня негде.
    Поведение самой redact() не меняется ни на знак: на неё опирается
    tools/doctor.py и четыре теста, которые проверяют её выход дословно.

    ПОЧЕМУ ПРОВЕРКА — ЭТО НЕ ПОВТОР ЗАГЛУШЕНИЯ
    Заглушение заменяет все совпадения; значит после него совпадений быть НЕ
    МОЖЕТ. Если совпадение всё-таки осталось, то отказала сама регулярка
    (испорчена, подменена, переписана кем-то в будущем) — и это ровно тот
    случай, когда писать нельзя. Проверка стоит наносекунды и ловит порчу
    механизма, а не пытается угадать новые виды секретов.

    ЧЕГО ЭТА ФУНКЦИЯ НЕ УМЕЕТ, И ЭТО НАДО ЗНАТЬ ЧЕСТНО
    Она ловит ключи ИЗВЕСТНОЙ формы и домашний путь. Она не ловит и не может
    ловить пароль, номер карты, чужую переписку: узнавание личного по тексту
    — это классификатор чувствительности (он в проекте ещё не написан), а
    поиск запретного грепом почти всегда неверен. Защита личного в чёрном
    ящике держится на другом: тело живёт считанные дни и никуда не уезжает.
    """
    try:
        value = text if isinstance(text, str) else str(text)
    except Exception:
        return HIDDEN, False
    try:
        cleaned = redact(value)
    except Exception:
        return HIDDEN, False
    try:
        for pattern in (_KEY_RE, _SK_RE):
            if pattern.search(cleaned):
                return HIDDEN, False
    except Exception:
        # Проверка сломалась — значит подтвердить чистоту нечем.
        return HIDDEN, False
    return cleaned, True


# -- Текстовые файлы --------------------------------------------------

def read_text(path):
    # Всегда utf-8, всегда с заменой битых байт. Чтение чужого файла
    # (журнал, чей-то конфиг) не имеет права уронить задачу.
    return Path(path).read_text(encoding=UTF8, errors=REPLACE)


def write_text(path, text):
    # newline=LF: наши файлы не обрастают возвратом каретки даже на Windows.
    # Именно так 68 файлов проекта когда-то и обзавелись CR.
    target = Path(path)
    with open(target, "w", encoding=UTF8, newline=LF) as handle:
        handle.write(text)
    return target


# -- Отчёт для доктора ------------------------------------------------

def _isatty(stream):
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def report():
    # Голые факты об окружении. Ничего не чинит, ничего не пишет.
    out = {}
    out["python"] = sys.version.split()[0]
    out["executable"] = sys.executable
    out["platform"] = sys.platform
    out["os_name"] = os.name
    stdout = getattr(sys, "stdout", None)
    stderr = getattr(sys, "stderr", None)
    out["console_encoding"] = _norm(getattr(stdout, "encoding", "")) or "нет потока"
    out["stderr_encoding"] = _norm(getattr(stderr, "encoding", "")) or "нет потока"
    out["console_is_live"] = _isatty(stdout)
    out["fs_encoding"] = _norm(sys.getfilesystemencoding())
    out["utf8_mode"] = int(getattr(sys.flags, "utf8_mode", 0) or 0)
    try:
        out["locale_encoding"] = _norm(locale.getpreferredencoding(False))
    except Exception as exc:
        out["locale_encoding"] = "не спросить (" + type(exc).__name__ + ")"
    out["PYTHONUTF8"] = os.environ.get("PYTHONUTF8", "")
    out["PYTHONIOENCODING"] = os.environ.get("PYTHONIOENCODING", "")
    out["cwd"] = str(Path.cwd())
    out["setup_done"] = bool(_state["setup_done"])
    out["streams"] = dict(_state["streams"])
    return out


def redirection_is_safe():
    # Правда ли, что перенаправление вывода в файл переживёт наши значки.
    # Безопасно, если поток уже utf-8 ИЛИ включён общий режим utf-8.
    if int(getattr(sys.flags, "utf8_mode", 0) or 0) == 1:
        return True
    encoding = _norm(getattr(getattr(sys, "stdout", None), "encoding", ""))
    return encoding in ("utf-8", "utf8")
