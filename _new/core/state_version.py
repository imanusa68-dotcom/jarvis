# core/state_version.py
"""Одна версия состояния — чем заменяется git (Р6, шаг 33.1).

ЗАЧЕМ ЭТОТ МОДУЛЬ
-----------------
История проекта — это zip-архивы. Архив откатывает КОД. Он не откатывает
данные: база, настройки, память и личность живут в доме ~/.jarvis и при
распаковке никуда не деваются. Значит после отката кода данные могут
оказаться «из будущего» — а узнать об этом сегодня негде: PRAGMA user_version
спрятан внутри базы, а у JSON-файлов дома номера версии нет вообще.

Этот модуль — единственное место, которое ЗНАЕТ и ЗАПИСЫВАЕТ версии всех
хранилищ сразу: ~/.jarvis/STATE.json.

ШЕСТЬ ПРАВИЛ, БЕЗ КОТОРЫХ ФАЙЛ ВРЕДНЕЕ СВОЕГО ОТСУТСТВИЯ
-------------------------------------------------------------
1. Пишет STATE.json только этот модуль. Файл, который пишут из трёх
   мест, рано или поздно врёт, а ему верят. Сторож: test_one_writer_only.
2. Путь считается при каждом вызове, а не константой при импорте: иначе
   JARVIS_STATE_DIR, выставленный тестом после импорта, не действует — и
   прогон тестов пишет в настоящий дом владельца (грабли шага 31).
3. collect() только СМОТРИТ. Он не создаёт базу, не запускает миграции
   и ничего не лечит. Отсутствие файла или базы — законное состояние,
   а не ошибка (history.db физически нет до фазы 7 — Фокт8, Х-U3).
4. Запись — только через safe_json.atomic_write_json: атомарная замена,
   три поколения .bak и карантин битого файла там уже написаны и
   проверены. Своё — не пишем.
5. Формат только дополняется: поля добавляются, schema_ver растёт,
   незнакомые ключи игнорируются. То же правило, что у базы.
6. Журнал двери здесь только УПОМИНАЕТСЯ (его schema_ver). Он —
   свидетельство, а не состояние: откату не подлежит никогда,
   поэтому у него стоит under_rollback = False.

ПОЧЕМУ НЕ ЧЕРЕЗ config/loader.py
----------------------------------
loader хранит НАСТРОЙКИ (что владелец просил), а здесь лежит ФАКТ о том,
что физически лежит в доме. Смешать их нельзя: настройки владелец правит
руками, а STATE.json руками править нельзя никогда.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# -- Числа и имена -------------------------------------------------------

SCHEMA_VER = 1
FILE_NAME = "STATE.json"

# Где мы в плане. Единственное место, откуда версию кода берёт и STATE.json,
# и BUILD.txt (шаг 33.4), и будущие отчёты агентов (фаза 1).
PHASE = "1"
STEP = 21
CODE_VER = f"{PHASE}.{STEP}"

# Версии JSON-хранилищ, которые понимает ЭТОТ код. Сегодня везде 1:
# в самих файлах номера нет, и отсутствие номера читается как «версия 1».
# Как только фаза 1 начнёт менять формат — номер растёт здесь и в файле.
SETTINGS_VER = 1
MEMORY_VER = 1
PERSONALITY_VER = 1

# Ключ версии внутри JSON-файлов дома. Нет ключа — значит версия 1.
VER_KEY = "state_ver"

# Имена файлов дома. Дублируются сознательно: тянуть сюда store,
# memory_manager и personality_engine ради трёх строк — значит тащить весь
# их граф импортов в стартовую память (бюджет 350 МБ, грабли 19).
DB_FILENAME = "jarvis.db"
HISTORY_FILENAME = "history.db"
SETTINGS_FILENAME = "settings.json"
MEMORY_FILENAME = "long_term.json"
PERSONALITY_FILENAME = "personality.json"

# Напоминания ПЕРЕЕХАЛИ в mx_reminder в базе дома (блок 10 фазы 1, Р-6), то есть
# теперь они под откатом вместе с jarvis.db. Этот адрес остался ради одного:
# показать, лежит ли ещё в папке сборки старый файл. Его не удаляют и не правят —
# Р-6 требует дословно «старый файл сохраняется как есть».
REMINDERS_RELATIVE = ("memory", "reminders.json")

_sandbox: Path | None = None


# -- Пути -----------------------------------------------------------------

def _under_tests() -> bool:
    return "pytest" in sys.modules


def _sandbox_dir() -> Path:
    """Предохранитель: тест, забывший перенаправить дом, видит пустую
    временную папку, а не настоящие данные владельца."""
    global _sandbox
    if _sandbox is None:
        _sandbox = Path(tempfile.mkdtemp(prefix="jv_state_"))
    return _sandbox


def dir_path() -> Path:
    """Дом. Тот же, что у базы, настроек, памяти и журнала двери."""
    from core.safe_json import STATE_DIR_ENV, state_dir
    if not os.environ.get(STATE_DIR_ENV, "").strip() and _under_tests():
        return _sandbox_dir()
    return state_dir()


def path() -> Path:
    """Адрес STATE.json. Считается каждый раз — см. правило 2 в шапке."""
    return dir_path() / FILE_NAME


def project_dir() -> Path:
    """Корень папки сборки — то, что меняется каждый вечер."""
    return Path(__file__).resolve().parent.parent


def reminders_path() -> Path:
    return project_dir().joinpath(*REMINDERS_RELATIVE)


# -- Чтение правды -----------------------------------------------------

def _stamp() -> tuple[str, str]:
    return (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _connect_ro(target: Path) -> sqlite3.Connection | None:
    """Открыть базу только на чтение, не создавая её.

    Сначала URI-режим mode=ro (не даёт ни создать файл, ни дограть WAL).
    Если путь не лезет в URI (пробелы, кириллица, решётка в имени папки
    — у владельца папка «jarvis_full_009_done (1)», там есть и то и другое),
    падаем на обычное открытие: файл уже существует, создавать нечего.
    """
    from urllib.parse import quote
    uri = "file:" + quote(target.as_posix(), safe="/:") + "?mode=ro"
    for opener in (lambda: sqlite3.connect(uri, uri=True),
                   lambda: sqlite3.connect(str(target))):
        try:
            return opener()
        except sqlite3.Error:
            continue
    return None


def db_user_version(db_file) -> int | None:
    """PRAGMA user_version без миграций и без создания базы.

    None означает «базы нет или она не читается» — и это не авария:
    history.db до фазы 7 физически не существует (Х-U3).
    """
    target = Path(db_file)
    if not target.exists():
        return None
    conn = _connect_ro(target)
    if conn is None:
        return None
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def code_migrations() -> dict:
    """До какой версии схемы дорос ЭТОТ код. Импорт ленивый."""
    try:
        from core import store
        return {
            "jarvis_db": max((m[0] for m in store.JARVIS_MIGRATIONS), default=0),
            "history_db": max((m[0] for m in store.HISTORY_MIGRATIONS), default=0),
        }
    except Exception:
        return {"jarvis_db": None, "history_db": None}


def _json_store(target: Path, code_knows: int) -> dict:
    """Версия JSON-хранилища дома. Читаем напрямую, без safe_json:
    load_json_report умеет карантин и ротацию, а сбор состояния не имеет
    права ничего трогать (правило 3)."""
    if not target.exists():
        return {"present": False, "ver": None, "code_knows": code_knows}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"present": True, "ver": None, "readable": False,
                "code_knows": code_knows}
    ver = data.get(VER_KEY) if isinstance(data, dict) else None
    return {"present": True, "ver": int(ver) if isinstance(ver, int) else 1,
            "code_knows": code_knows}


def _gate_audit() -> dict:
    """Журнал двери: только упоминание, никогда не откат."""
    try:
        from core import audit_log
        return {"present": audit_log.path().exists(),
                "schema_ver": audit_log.SCHEMA_VER,
                "under_rollback": False}
    except Exception:
        return {"present": None, "schema_ver": None, "under_rollback": False}


def collect() -> dict:
    """Собрать правду о доме. Только смотрит, ничего не меняет."""
    home = dir_path()
    ts, ts_utc = _stamp()
    knows = code_migrations()
    db = home / DB_FILENAME
    history = home / HISTORY_FILENAME
    return {
        "schema_ver": SCHEMA_VER,
        "written_at": ts,
        "written_at_utc": ts_utc,
        "code_ver": CODE_VER,
        "phase": PHASE,
        "step": STEP,
        "build": {
            "folder": project_dir().name,
            "project_path": str(project_dir()),
        },
        "stores": {
            "jarvis_db": {"present": db.exists(),
                          "user_version": db_user_version(db),
                          "code_knows": knows["jarvis_db"]},
            "history_db": {"present": history.exists(),
                           "user_version": db_user_version(history),
                           "code_knows": knows["history_db"]},
            "settings": _json_store(home / SETTINGS_FILENAME, SETTINGS_VER),
            "memory_json": _json_store(home / MEMORY_FILENAME, MEMORY_VER),
            "personality": _json_store(home / PERSONALITY_FILENAME,
                                       PERSONALITY_VER),
            "gate_audit": _gate_audit(),
            # Блок 10 фазы 1: напоминания переехали в mx_reminder в базе дома.
            # Значит они теперь ПОД ОТКАТОМ (снимок копирует jarvis.db) и
            # переживают обновление сборки. Старый файл не удалён и не изменён
            # (Р-6 дословно: «старый файл сохраняется как есть»), поэтому здесь
            # по-прежнему видно, лежит ли он в папке сборки — это улика
            # переезда, а не место хранения.
            "reminders_json": {"present": reminders_path().exists(),
                               "location": "legacy_leftover",
                               "under_rollback": False},
            "reminders": {"location": "db", "table": "mx_reminder",
                          "under_rollback": True},
        },
        "last_run": {
            "path": str(project_dir()),
            "started_at": ts,
            "pid": os.getpid(),
            "clean_exit": False,
        },
        "snapshots": [],
    }


# -- Чтение и запись файла -------------------------------------------

def load() -> tuple[dict, dict]:
    """Прочитать STATE.json. Отсутствие файла — норма (source=missing).

    Вторым значением идёт отчёт safe_json: откуда взяли данные и был ли
    карантин. Его надо показывать в doctor: тихо восстановленный из снимка
    файл выглядит здоровым ровно до первого отката.
    """
    from core.safe_json import load_json_report
    return load_json_report(path(), dict, label="State")


def save(data: dict) -> Path:
    """Атомарная замена файла через safe_json (правило 4)."""
    from core.safe_json import atomic_write_json
    return atomic_write_json(path(), data)


def write(*, clean_exit: bool | None = None) -> dict:
    """Единственная запись STATE.json: собрать правду и положить в дом.

    Сохраняет из прежнего файла то, что нельзя пересобрать из реальности:
    список снимков и путь прошлого запуска (им ловится Х-J5: запуск
    из другой папки).
    """
    old, _ = load()
    fresh = collect()
    snapshots = old.get("snapshots")
    fresh["snapshots"] = snapshots if isinstance(snapshots, list) else []
    previous = (old.get("last_run") or {}).get("path")
    if isinstance(previous, str) and previous:
        fresh["last_run"]["previous_path"] = previous
    if clean_exit is not None:
        fresh["last_run"]["clean_exit"] = bool(clean_exit)
    save(fresh)
    return fresh


# -- Реестр снимков (шаг 33.3) ----------------------------------------
# Снимки делает core/state_snapshot.py, но пишет в STATE.json только
# этот модуль: один файл — один писатель (граница Г-3).

REGISTRY_LIMIT = 20


def _write_snapshots(items):
    # Если состояния ещё нет или оно битое — собираем свежее, а не
    # сочиняем половину. Список обрезается с начала: свежие важнее.
    data, _ = load()
    if not isinstance(data, dict) or VER_KEY not in data:
        data = collect()
    trimmed = list(items)[-REGISTRY_LIMIT:]
    data["snapshots"] = trimmed
    save(data)
    return trimmed


def record_snapshot(entry):
    # Записать состоявшийся снимок. Повтор по id замещает, не двоит.
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    data, _ = load()
    items = data.get("snapshots") if isinstance(data, dict) else None
    items = list(items) if isinstance(items, list) else []
    items = [x for x in items if str(x.get("id")) != str(entry.get("id"))]
    items.append(entry)
    return _write_snapshots(items)


def forget_snapshots(ids):
    # Ротация удалила папки — реестр не должен обещать то, чего нет.
    dead = set(str(x) for x in (ids or []))
    if not dead:
        return None
    data, _ = load()
    items = data.get("snapshots") if isinstance(data, dict) else None
    items = list(items) if isinstance(items, list) else []
    return _write_snapshots([x for x in items if str(x.get("id")) not in dead])


# -- Вердикт -------------------------------------------------------------

def problems(state: dict | None = None) -> list:
    """Список человеческих претензий к состоянию. Пусто — всё согласовано.

    Здесь только текст. Громкий отказ при старте — шаг 33.2 (state_guard):
    решение «не стартовать» принимает одно место, а не каждый желающий.
    """
    data = state if state is not None else collect()
    stores = data.get("stores") or {}
    out = []

    for key, label in (("jarvis_db", "База jarvis.db"),
                       ("history_db", "База history.db")):
        item = stores.get(key) or {}
        have, knows = item.get("user_version"), item.get("code_knows")
        if isinstance(have, int) and isinstance(knows, int) and have > knows:
            out.append(
                f"{label}: данные версии {have}, программа знает только {knows}. "
                "Похоже, запущена более старая сборка: верните новую или "
                "откатите данные на снимок той же версии.")

    for key, label in (("settings", "Настройки"),
                       ("memory_json", "Память"),
                       ("personality", "Личность")):
        item = stores.get(key) or {}
        have, knows = item.get("ver"), item.get("code_knows")
        if isinstance(have, int) and isinstance(knows, int) and have > knows:
            out.append(
                f"{label}: файл версии {have}, программа знает {knows}. "
                "Новые ключи она не поймёт и может их потерять.")
        if item.get("readable") is False:
            out.append(f"{label}: файл есть, но не читается как JSON.")

    return out


def path_changed() -> str | None:
    """Запуск из другой папки, чем в прошлый раз (Х-J5).

    Не ошибка и не повод блокировать старт: владелец каждый вечер
    распаковывает новый архив. Одна строка — чтобы запуск из СТАРОЙ
    папки был виден сразу, а не через час разбора «почему шаг не работает».
    """
    data, _ = load()
    old = (data.get("last_run") or {}).get("path")
    now = str(project_dir())
    if isinstance(old, str) and old.strip() and old != now:
        return (f"Запущено из другой папки, чем в прошлый раз.\n"
                f"  было:  {old}\n"
                f"  стало: {now}")
    return None
