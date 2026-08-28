"""Снимки состояния дома — шаг 33.3 (план: Р6, пункт 4).

Что это такое простыми словами. Копия всего, что живёт в ~/.jarvis, в
отдельной папке state_backups/<номер>. Сторож шага 33.2 обещает владельцу
вслух: «откатите данные на снимок той же версии». До этого шага обещание
было пустым — снимков не существовало.

Шесть правил, которые здесь нельзя нарушать.

1. Базы копируются ТОЛЬКО родным механизмом SQLite (store.backup). Обычное
   копирование файла при включённом WAL даёт битую копию: часть правок
   лежит в отдельном хвосте, которого копия не увидит (дыра Х-A3).
2. Источник открывается напрямую (store.connect), а не рабочим путём с
   правкой схемы. Снимок «до правки схемы», который сам правит схему, —
   это не страховка, а причина аварии.
3. Отсутствующего хранилища не касаемся вообще. sqlite3 создал бы в доме
   пустой файл, и честный диагноз «второй базы нет» превратился бы в ложь.
4. Файл состояния этот модуль НЕ пишет никогда. Список снимков — часть
   состояния, а у состояния один писатель: core/state_version.py. Мы
   отдаём ему готовую запись через record_snapshot.
5. Снимок собирается в папке .tmp-, проверяется и лишь потом получает
   настоящее имя. Полусделанный снимок не имеет права выглядеть годным.
6. Ни одной аварии наружу. Не смогли — громкая строка и None. Снимок,
   который мешает Джарвису запуститься, вреднее отсутствия снимков.

Внимание тому, кто будет править прозу. Два теста шага 33.3 читают этот
файл как текст и ищут запрещённые слова: имя файла состояния, рабочее
открытие базы с правкой схемы, атомарную запись json. Греп тупой нарочно:
так он никогда не пропустит настоящий вызов. Не поминайте эти слова даже
в комментариях — тест не умеет отличать разговор от дела.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

# -- Числа и имена -------------------------------------------------------

SCHEMA_VER = 1
DIR_NAME = "state_backups"
MANIFEST_NAME = "snapshot.json"
TMP_PREFIX = ".tmp-"
BROKEN_PREFIX = ".broken-"

KIND_AUTO = "auto"
KIND_PHASE = "phase"
KIND_PRE_MIGRATE = "pre_migrate"
KIND_PRE_ROLLBACK = "pre_rollback"

# Сколько автоснимков держим. Три — это три последних рабочих вечера.
KEEP_AUTO = 3

# Как часто снимаем сам собой. Двенадцать часов = примерно один снимок на
# вечер. Редко НАРОЧНО, и причина не в скорости: снимок дома владельца
# весит около 0,9 МБ и делается за миллисекунды. Причина в глубине. Если
# снимать каждый запуск, то за вечер отладки все три хранимых снимка
# окажутся из последних десяти минут — то есть все три уже с той правкой,
# от которой они обязаны страховать.
AUTO_EVERY_SECONDS = 12 * 3600

# Ниже этого остатка на диске снимок не делаем (дыра Х-A5). У владельца
# свободно около 46 ГБ при диске 477 ГБ — то есть заполнено на 90%, и
# расти будет чёрный ящик фазы 2, а не снимки.
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024

# Копия базы бывает чуть больше источника (страницы, выравнивание).
SIZE_MARGIN = 1.15

# Антивирус умеет подержать только что созданную папку.
REPLACE_TRIES = 3
REPLACE_PAUSE = 0.2

# Мусор от прерванной попытки убираем, но не чужую свежую работу.
TMP_STALE_SECONDS = 3600

DB_FILES = ("jarvis.db", "history.db")
JSON_FILES = ("long_term.json", "settings.json", "personality.json")

# Что в снимок НЕ входит и почему. Список едет внутрь каждого манифеста:
# через месяц никто не вспомнит, чего в копии нет, а откат без этого знания
# опаснее отсутствия отката.
NOT_INCLUDED = [
    "logs/gate-audit.jsonl — журнал двери: улику не откатывают",
    "staging/ — до 500 МБ копий для отмены файловых операций",
    "backups/ — копии отмены Stage-1, чужое хозяйство (core/staging.py)",
    "api_usage.json — израсходованная квота принадлежит Google, а не нам",
    "*.bak1-3 — резервные копии, которые safe_json держит сам",
    "jarvis.lock, jarvis.lock.info — замок живого процесса",
    "*-wal, *-shm — служебные файлы WAL: в копии их быть не должно",
    "*.corrupt-* — карантин повреждённых файлов",
    "memory/reminders.json — ОСТАТОК от переезда: сами напоминания переехали "
    "в mx_reminder внутри jarvis.db (блок 10, Р-6) и откатываются вместе с "
    "базой. Старый файл не удаляют и не правят, но и не копируют",
]

FTS_NOTE = (
    "указатели поиска внутри копии могут отставать: после восстановления "
    "нужен fact_store.rebuild_fts (дыра Х-A4, шаг 33.5)"
)


# -- Пути и мелкая механика ---------------------------------------------

def _sv():
    """Ленивый импорт: держатель правды о состоянии (грабли 19)."""
    from core import state_version
    return state_version


def _home() -> Path:
    """Дом. Берём у state_version, а не у safe_json: там уже есть
    предохранитель «тест, забывший перенаправить дом, видит песочницу»."""
    return _sv().dir_path()


def dir_path() -> Path:
    return _home() / DIR_NAME


def _now() -> tuple[str, str]:
    return (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _id_stamp() -> str:
    """Метка для имени папки. Без двоеточий: Windows их в именах не терпит."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _say(printer, line: str) -> None:
    say = printer if printer is not None else print
    try:
        say(line)
    except UnicodeEncodeError:
        say(line.encode("ascii", "replace").decode("ascii"))


def _free_bytes(target: Path) -> int:
    """Отдельной функцией нарочно: тест подменяет её, не трогая shutil."""
    return int(shutil.disk_usage(str(target)).free)


def _replace(src: Path, dst: Path) -> None:
    """Тоже отдельной функцией: так тест умеет сыграть занятую папку."""
    os.replace(str(src), str(dst))


def _copy_db(src: Path, dest: Path) -> None:
    """Копия базы родным механизмом SQLite — единственный правильный путь.

    store.connect ставит журнальный режим и таймаут ожидания, но схему не
    трогает. Живой Джарвис в это время может писать: копия получится
    согласованной на момент начала, а не рваной.
    """
    from core import store
    conn = store.connect(src)
    try:
        store.backup(conn, dest)
    finally:
        conn.close()


def _quick_check(target: Path) -> str:
    """Быстрая проверка целостности копии.

    quick_check, а не integrity_check: полная проверка читает всю базу и на
    слабом ноутбуке при выросшей базе съест бюджет старта. Нам нужно
    поймать «копия не открывается», а не аудит страниц.
    """
    conn = sqlite3.connect(str(target))
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else "нет ответа"


# -- Чтение того, что уже лежит -----------------------------------------

def _read_manifest(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def list_snapshots() -> list:
    """Снимки, которые мы понимаем, по порядку номеров.

    Правда о снимках лежит на диске, а не в реестре: реестр может отстать,
    а папка с копией — вот она.
    """
    root = dir_path()
    out: list = []
    if not root.exists():
        return out
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        data = _read_manifest(folder)
        if data is None:
            continue
        data["path"] = str(folder)
        out.append(data)
    out.sort(key=lambda d: int(d.get("seq") or 0))
    return out


def unknown_dirs() -> list:
    """Папки без понятного манифеста. Автоматика их не удаляет НИКОГДА.

    Может, это чужая копия, может, наш будущий формат, может, владелец сам
    что-то положил. Молча уносить чужое — худшее, что может делать чистка.
    Их дело — попасть в отчёт доктора (Р11), а не в корзину.
    """
    root = dir_path()
    if not root.exists():
        return []
    return [str(p) for p in sorted(root.iterdir())
            if p.is_dir() and not p.name.startswith(".")
            and _read_manifest(p) is None]


def _next_seq(known: list) -> int:
    return max((int(d.get("seq") or 0) for d in known), default=0) + 1


def _sources(home: Path) -> tuple[list, list]:
    present: list = []
    absent: list = []
    names = list(DB_FILES) + list(JSON_FILES) + [_sv().FILE_NAME]
    for name in names:
        (present if (home / name).exists() else absent).append(name)
    return present, absent


def _age_seconds(stamp, *, now=None) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        made = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    made = made.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - made).total_seconds()


# -- Главное действие ----------------------------------------------------

def create(kind: str = KIND_AUTO, *, reason: str = "", printer=None) -> dict | None:
    """Сделать снимок. Возвращает короткую запись реестра или None.

    Никогда не бросает исключение: вызывается из пути запуска.
    """
    try:
        home = _home()
        root = dir_path()
        root.mkdir(parents=True, exist_ok=True)

        present, absent = _sources(home)
        if not present:
            _say(printer, "[Снимок] в доме нечего сохранять — снимок не нужен")
            return None

        planned = int(sum((home / n).stat().st_size for n in present) * SIZE_MARGIN)
        free = _free_bytes(root)
        if free - planned < MIN_FREE_BYTES:
            _say(printer,
                 f"[Снимок] отказ: свободно {free // (1024 * 1024)} МБ, "
                 f"снимку нужно {max(planned // (1024 * 1024), 1)} МБ, "
                 f"а запас ниже {MIN_FREE_BYTES // (1024 * 1024 * 1024)} ГБ "
                 f"трогать нельзя. Данные не тронуты")
            return None

        sv = _sv()
        known = list_snapshots()
        seq = _next_seq(known)
        ident = f"{_id_stamp()}_p{sv.PHASE}s{sv.STEP}_{kind}_{seq:04d}"
        final = root / ident
        tmp = root / f"{TMP_PREFIX}{ident}"
        if final.exists():
            _say(printer, f"[Снимок] отказ: {ident} уже существует")
            return None
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

        files: dict = {}
        checks: dict = {}
        manifest: dict = {}
        tmp.mkdir(parents=True)
        try:
            for name in present:
                src = home / name
                dest = tmp / name
                if name in DB_FILES:
                    _copy_db(src, dest)
                    checks[name] = _quick_check(dest)
                else:
                    shutil.copy2(src, dest)
                files[name] = dest.stat().st_size

            bad = {n: v for n, v in checks.items() if v != "ok"}
            if bad:
                broken = root / f"{BROKEN_PREFIX}{ident}"
                try:
                    _replace(tmp, broken)
                    where = broken.name
                except OSError:
                    shutil.rmtree(tmp, ignore_errors=True)
                    where = "удалена"
                _say(printer,
                     f"[Снимок] копия не прошла проверку целостности: {bad}. "
                     f"Не зарегистрирована ({where})")
                return None

            stamp, stamp_utc = _now()
            manifest = {
                "schema_ver": SCHEMA_VER,
                "id": ident,
                "seq": seq,
                "kind": kind,
                "keep": kind != KIND_AUTO,
                "reason": str(reason)[:200],
                "created_at": stamp,
                "created_at_utc": stamp_utc,
                "code_ver": sv.CODE_VER,
                "phase": sv.PHASE,
                "step": sv.STEP,
                "build_folder": sv.project_dir().name,
                "stores": sv.collect().get("stores", {}),
                "files": files,
                "bytes": sum(files.values()),
                "quick_check": checks,
                "absent": absent,
                "not_included": list(NOT_INCLUDED),
                "fts_note": FTS_NOTE,
            }
            (tmp / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8", newline="\n")
        except Exception as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            _say(printer,
                 f"[Снимок] не вышло, дом не тронут: {type(exc).__name__}: {exc}")
            return None

        moved = False
        last: Exception | None = None
        for _ in range(REPLACE_TRIES):
            try:
                _replace(tmp, final)
                moved = True
                break
            except OSError as exc:
                last = exc
                time.sleep(REPLACE_PAUSE)
        if not moved:
            shutil.rmtree(tmp, ignore_errors=True)
            _say(printer, f"[Снимок] папку не удалось положить на место: {last}")
            return None

        entry = {key: manifest[key] for key in (
            "id", "seq", "kind", "keep", "created_at", "created_at_utc",
            "code_ver", "phase", "step", "bytes")}
        entry["files"] = len(files)
        try:
            sv.record_snapshot(entry)
        except Exception as exc:
            _say(printer,
                 f"[Снимок] копия готова, но в реестре не отмечена: {exc}")
        _say(printer,
             f"[Снимок] {ident}: {max(entry['bytes'] // 1024, 1)} КБ, "
             f"файлов {len(files)}"
             + (f", не было: {', '.join(absent)}" if absent else ""))
        rotate(printer=printer)
        return entry
    except Exception as exc:
        _say(printer, f"[Снимок] пропущен: {type(exc).__name__}: {exc}")
        return None


def rotate(*, printer=None) -> list:
    """Оставить KEEP_AUTO самых новых автоснимков. Чужого не удаляем.

    Считаем по номеру seq, а НЕ по времени файла и не по имени. Часы умеют
    прыгать назад (хвост шага 24), и ротация по времени в такой день унесёт
    самый свежий снимок как самый старый. Номер монотонен по построению.

    Порядок важен: сначала создали новый, потом удаляем старый. Обратный
    порядок означает мгновение, когда снимков нет вовсе.
    """
    known = list_snapshots()
    autos = [d for d in known
             if d.get("kind") == KIND_AUTO and not d.get("keep")]
    extra = autos[:-KEEP_AUTO] if len(autos) > KEEP_AUTO else []
    removed: list = []
    for item in extra:
        folder = Path(str(item.get("path") or ""))
        try:
            shutil.rmtree(folder)
            removed.append(str(item.get("id")))
        except OSError as exc:
            _say(printer, f"[Снимок] старый снимок не удалился: {exc}")
    if removed:
        try:
            _sv().forget_snapshots(removed)
        except Exception as exc:
            _say(printer, f"[Снимок] реестр не обновлён: {exc}")
    return removed


def due(*, now=None) -> bool:
    """Пора ли делать автоснимок.

    Да, если снимков нет вовсе; если сменилась версия кода, версия любой
    базы или папка сборки; или если прошло больше AUTO_EVERY_SECONDS.
    Отрицательный возраст (часы прыгнули назад) поводом не считается:
    лучше пропустить снимок, чем нащёлкать их по кругу.
    """
    sv = _sv()
    autos = [d for d in list_snapshots() if d.get("kind") == KIND_AUTO]
    if not autos:
        return True
    newest = autos[-1]
    if newest.get("code_ver") != sv.CODE_VER:
        return True
    if newest.get("build_folder") != sv.project_dir().name:
        return True
    then = newest.get("stores") or {}
    current = sv.collect().get("stores", {})
    for name in ("jarvis_db", "history_db"):
        was = (then.get(name) or {}).get("user_version")
        now_v = (current.get(name) or {}).get("user_version")
        if was != now_v:
            return True
    age = _age_seconds(newest.get("created_at_utc"), now=now)
    if age is None:
        return True
    return age >= AUTO_EVERY_SECONDS


def ensure_phase_snapshot(*, printer=None) -> dict | None:
    """Один несменяемый снимок на фазу (план Р6, пункт 4).

    Фазовый снимок ротация не трогает: это точка «как было до всей фазы»,
    и стоит она меньше мегабайта.
    """
    sv = _sv()
    for item in list_snapshots():
        if item.get("kind") == KIND_PHASE and str(item.get("phase")) == str(sv.PHASE):
            return None
    return create(KIND_PHASE, reason=f"начало фазы {sv.PHASE}", printer=printer)


def ensure_pre_migrate_snapshot(*, printer=None) -> bool:
    """Снимок перед правкой схемы базы (дыра Х-A3).

    ВАЖНО: сегодня НЕ подключён к открытию базы, и это названный долг, а не
    забывчивость. Правок схемы 7-18 ещё не существует, а лезть в
    единственную дверь к базе без нужды — верный способ покрасить тысячу
    зелёных тестов. Подключение — фаза 1, вместе с первой новой таблицей.

    Решение владельца на будущее записано здесь, чтобы не переспорить его
    задним числом: НЕ ВЫШЛО СНЯТЬ — НЕ ПРАВИМ СХЕМУ. Лучше вечер без новой
    возможности, чем вечер без данных.
    """
    return create(KIND_PRE_MIGRATE, reason="перед правкой схемы",
                  printer=printer) is not None


def cleanup_temp(*, printer=None) -> list:
    """Убрать мусор от прерванных попыток.

    Только .tmp- и только старше часа: свежую папку может собирать другой
    процесс прямо сейчас.
    """
    root = dir_path()
    if not root.exists():
        return []
    removed: list = []
    now = time.time()
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not folder.name.startswith(TMP_PREFIX):
            continue
        try:
            if now - folder.stat().st_mtime < TMP_STALE_SECONDS:
                continue
            shutil.rmtree(folder)
            removed.append(folder.name)
        except OSError as exc:
            _say(printer, f"[Снимок] недоделанная папка не удалилась: {exc}")
    return removed


def report() -> dict:
    """Короткая сводка для доктора (Р11) и для отчёта вечера."""
    known = list_snapshots()
    root = dir_path()
    free = None
    try:
        free = _free_bytes(root if root.exists() else _home())
    except OSError:
        free = None
    newest = known[-1] if known else None
    return {
        "dir": str(root),
        "count": len(known),
        "newest_id": (newest or {}).get("id"),
        "newest_at": (newest or {}).get("created_at"),
        "kinds": sorted({str(d.get("kind")) for d in known}),
        "bytes": sum(int(d.get("bytes") or 0) for d in known),
        "unknown_dirs": unknown_dirs(),
        "free_bytes": free,
        "due": due(),
    }
