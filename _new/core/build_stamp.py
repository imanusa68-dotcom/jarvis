# -*- coding: utf-8 -*-
# BUILD.txt: метка сборки. Шаг 33.4, план Р6 пункт 2.
#
# Зачем он есть: у владельца в Downloads лежат три папки с почти
# одинаковыми именами. Открыв любую, надо за пять секунд понять:
# свежая она или мёртвая. Файл пишет код, потому что руками такие
# записки забывают обновлять, а устаревшая записка врёт хуже, чем её
# отсутствие.
#
# Правила, из которых сделан модуль:
# 1. Два писателя, один файл. Прогон тестов знает число тестов, но НЕ
#    знает настоящий дом: под тестами state_version.dir_path() уходит
#    в песочницу. Старт Jarvis знает дом, но не знает числа тестов.
#    Поэтому каждый пишет только свой блок, а чужие строки переносит.
# 2. Никогда не роняет то, из чего вызван: ни прогон тестов, ни старт.
# 3. Файл состояния в доме модуль не пишет: у него один писатель,
#    core/state_version.py (граница Г-3). Здесь только читаем его цифры.
# 4. Замена файла атомарная и с повторами: Блокнот или антивирус
#    могут держать файл открытым, на Windows это PermissionError.
# 5. Перевод строки LF, как во всех файлах, которые пишем сами.
# 6. Частичный прогон не затирает цифры полного.
from __future__ import annotations

import os
import platform
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VER = 1
FILE_NAME = "BUILD.txt"
DIR_ENV = "JARVIS_BUILD_DIR"
TMP_PREFIX = ".tmp-BUILD-"
REPLACE_TRIES = 3
REPLACE_PAUSE = 0.2
SEP = " = "
LF = chr(10)
UNKNOWN = "неизвестно"
NO_FILE = "нет файла"
NOT_SEEN_BY_TESTS = "не смотрели: прогон тестов не видит настоящий дом"
NOT_SEEN_BY_START = "не смотрели: старт не считает тесты"
SCOPE_FULL = "полный прогон"
BY_TESTS = "tests"
BY_START = "start"
CODE_DIRS = ("core", "agent", "actions", "memory", "tools", "tests")

HEADER = (
    "Jarvis Mark XXXVI. Метка сборки. Этот файл пишет код, а не человек.",
    "Если цифры тестов старше вашей правки, значит тесты после правки не гоняли.",
    "Строки вида ключ = значение читает код. Руками их менять бессмысленно.",
)

TEST_KEYS = ("tests_total", "tests_failed", "tests_seconds", "tests_at",
             "tests_scope", "tests_source")
HOME_KEYS = ("home_at", "home_path", "jarvis_db_ver", "history_db_ver",
             "settings_ver", "memory_ver", "personality_ver",
             "gate_audit_ver")
ORDER = (("stamp_ver", "written_at", "written_at_utc", "written_by",
          "code_ver", "phase", "step", "folder", "project_path",
          "python", "platform") + TEST_KEYS + HOME_KEYS)


# -- Адрес -------------------------------------------------------------

def _sv():
    from core import state_version
    return state_version


def dir_path() -> Path:
    # Куда ложится файл. Переменная окружения нужна тестам модуля:
    # настоящий BUILD.txt проекта трогает только хук прогона и старт.
    forced = os.environ.get(DIR_ENV, "").strip()
    if forced:
        return Path(forced)
    return _sv().project_dir()


def path() -> Path:
    return dir_path() / FILE_NAME


def _now() -> tuple:
    return (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _say(printer, text: str) -> None:
    if printer is None:
        print(text)
        return
    try:
        printer(text)
    except Exception:
        pass


# -- Чтение и запись -----------------------------------------------

def read(*, target: Path | None = None) -> dict:
    # Прочитать прежнюю метку. Отсутствие файла и мусор внутри —
    # норма: вернём то, что разобрали, остальное молча пропустим.
    spot = target if target is not None else path()
    out: dict = {}
    try:
        text = spot.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    for line in text.splitlines():
        if SEP not in line:
            continue
        key, _, value = line.partition(SEP)
        key = key.strip()
        if key and key not in out:
            out[key] = value.strip()
    return out


def _render(data: dict) -> str:
    lines = list(HEADER) + [""]
    for key in ORDER:
        if key in data:
            lines.append(key + SEP + str(data[key]))
    for key in sorted(k for k in data if k not in ORDER):
        lines.append(key + SEP + str(data[key]))
    return LF.join(lines) + LF


def _replace(src: Path, dst: Path) -> None:
    last = None
    for _ in range(REPLACE_TRIES):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as exc:
            last = exc
            time.sleep(REPLACE_PAUSE)
    raise last if last is not None else OSError("replace failed")


def _atomic_write(text: str, spot: Path) -> None:
    folder = spot.parent
    folder.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(prefix=TMP_PREFIX, dir=str(folder))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        _replace(tmp, spot)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# -- Общий вход для обоих писателей -------------------------------

def write(*, written_by: str, fields: dict | None = None, printer=None,
          quiet: bool = False) -> dict | None:
    # Возвращает то, что легло в файл, или None, если не легло.
    # Наружу исключение не пускается никогда (правило 2 в шапке).
    try:
        sv = _sv()
        local, utc = _now()
        data = read()
        data["stamp_ver"] = SCHEMA_VER
        data["written_at"] = local
        data["written_at_utc"] = utc
        data["written_by"] = written_by
        data["code_ver"] = getattr(sv, "CODE_VER", UNKNOWN)
        data["phase"] = getattr(sv, "PHASE", UNKNOWN)
        data["step"] = getattr(sv, "STEP", UNKNOWN)
        data["folder"] = sv.project_dir().name
        data["project_path"] = str(sv.project_dir())
        data["python"] = platform.python_version()
        data["platform"] = platform.platform()
        for key, value in (fields or {}).items():
            data[key] = value
        for key in TEST_KEYS:
            data.setdefault(key, NOT_SEEN_BY_START)
        for key in HOME_KEYS:
            data.setdefault(key, NOT_SEEN_BY_TESTS)
        spot = path()
        _atomic_write(_render(data), spot)
        if not quiet:
            _say(printer, "[Сборка] " + FILE_NAME + ": шаг "
                 + str(data.get("step")) + ", тестов "
                 + str(data.get("tests_total")))
        return data
    except Exception as exc:
        if not quiet:
            _say(printer, "[Сборка] метку не обновили: "
                 + type(exc).__name__ + ": " + str(exc))
        return None


def stamp_tests(*, total, failed, seconds, full: bool,
                source: str = "python -m pytest -q", printer=None):
    # Число тестов знает только прогон. Частичный прогон НЕ затирает
    # цифры полного: иначе после одного файла в метке останется 26
    # тестов, и это будет выглядеть как обвал набора.
    local, _ = _now()
    if full:
        fields = {
            "tests_total": int(total),
            "tests_failed": int(failed),
            "tests_seconds": "{:.2f}".format(float(seconds)),
            "tests_at": local,
            "tests_scope": SCOPE_FULL,
            "tests_source": source,
        }
    else:
        fields = {
            "partial_at": local,
            "partial_total": int(total),
            "partial_failed": int(failed),
        }
    return write(written_by=BY_TESTS, fields=fields, printer=printer)


def _home_text() -> str:
    try:
        return str(_sv().dir_path())
    except Exception:
        return UNKNOWN


def stamp_start(*, printer=None):
    # Старт знает настоящий дом, тесты — нет. Поэтому версии хранилищ
    # записываются только отсюда.
    stores: dict = {}
    try:
        stores = (_sv().collect() or {}).get("stores") or {}
    except Exception:
        stores = {}

    def ver(name: str, key: str) -> str:
        item = stores.get(name) or {}
        if not item.get("present"):
            return NO_FILE
        value = item.get(key)
        return UNKNOWN if value is None else str(value)

    local, _ = _now()
    fields = {
        "home_at": local,
        "home_path": _home_text(),
        "jarvis_db_ver": ver("jarvis_db", "user_version"),
        "history_db_ver": ver("history_db", "user_version"),
        "settings_ver": ver("settings", "ver"),
        "memory_ver": ver("memory_json", "ver"),
        "personality_ver": ver("personality", "ver"),
        "gate_audit_ver": ver("gate_audit", "schema_ver"),
    }
    return write(written_by=BY_START, fields=fields, printer=printer)


# -- Отчёт для doctor --------------------------------------------------

def _newest_code_at() -> float:
    # Самый свежий .py в проекте. Нужен, чтобы честно сказать:
    # код правили после последнего прогона тестов.
    newest = 0.0
    try:
        root = _sv().project_dir()
    except Exception:
        return newest
    spots = [root / "main.py"]
    for name in CODE_DIRS:
        folder = root / name
        if folder.is_dir():
            spots.extend(folder.rglob("*.py"))
    for spot in spots:
        try:
            if spot.is_file():
                newest = max(newest, spot.stat().st_mtime)
        except Exception:
            continue
    return newest


def stale() -> bool | None:
    # True: код моложе метки, то есть после правки тесты не гоняли.
    # None: судить не о чем — метки нет или в ней нет даты тестов.
    data = read()
    raw = str(data.get("tests_at", ""))
    try:
        seen = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        return None
    newest = _newest_code_at()
    if not newest:
        return None
    return newest > seen


def report() -> dict:
    spot = path()
    data = read(target=spot)
    return {
        "path": str(spot),
        "exists": spot.exists(),
        "stamp_ver": data.get("stamp_ver"),
        "written_at": data.get("written_at"),
        "written_by": data.get("written_by"),
        "code_ver": data.get("code_ver"),
        "phase": data.get("phase"),
        "step": data.get("step"),
        "tests_total": data.get("tests_total"),
        "tests_failed": data.get("tests_failed"),
        "tests_at": data.get("tests_at"),
        "stale": stale(),
    }
