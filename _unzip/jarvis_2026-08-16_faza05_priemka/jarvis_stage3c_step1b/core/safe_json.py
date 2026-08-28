# core/safe_json.py
"""
Stage 3.0 - "durability floor" for JSON-backed state.

WHY THIS EXISTS
---------------
Stage 2 made *file actions* atomic, durable and reversible (certified by a
SIGKILL crash-test). Two categories of state were left behind, and an audit
found both of them actively losing data:

  1. Long-term memory (memory/long_term.json) and the personality profile
     (memory/personality.json) lived INSIDE the build folder. Every new build
     is unpacked into a new directory, so memory never survived an update.

  2. Both files were written with a plain `write_text` (truncate-then-write).
     A crash mid-write leaves truncated JSON. Worse: the loaders swallowed the
     parse error and returned an EMPTY object, and the next save then
     overwrote the damaged file with that empty object - silent, permanent,
     total amnesia.

This module is the single fix for both, and the single place any future
JSON-backed state must go through.

GUARANTEES
----------
  * Location  - state lives in ~/.jarvis (same durable dir as jarvis.db),
                never in the build folder. Overridable via JARVIS_STATE_DIR
                so tests never touch the real user directory.
  * Atomicity - write to a temp file in the SAME directory, fsync, then
                os.replace(). On both POSIX and Windows os.replace is atomic,
                so a reader/crash sees either the whole old file or the whole
                new file. Never a half-written one.
  * No silent loss - a corrupt file is NEVER overwritten. It is quarantined
                (renamed to .corrupt-<timestamp>), recovery is attempted from
                rotating snapshots, and the failure is reported loudly.
  * Snapshots - the last N known-good versions are kept (.bak1 ... .bakN),
                which also protects against logical corruption, not just
                crashes.

ЧТО ДОБАВИЛ БЛОК 9 И ПОЧЕМУ АТОМАРНОСТИ БЫЛО НЕДОСТАТОЧНО
---------------------------------------------------------
Инвариант I24 требует дословно «временный файл + os.replace», и это здесь было
сделано ещё в Stage 3.0 — даже с запасом (fsync инвариант не просил). Замер
19.08.2026 подтвердил: процесс, убитый ровно перед переименованием, оставляет
ПРЕЖНИЙ файл целым.

Но данные всё равно терялись, и по двум причинам сразу.

ПЕРВАЯ: ТЕРЯЛАСЬ НЕ ЗАПИСЬ, А ПРАВКА. Память правится так: прочитать всё,
добавить свой факт, записать всё обратно. Два таких потока — и последний
затирает работу первого. Замерено на копии настоящей памяти владельца:
голосовое «запомни» и фоновый извлекатель одновременно -> ФАКТ ВЛАДЕЛЬЦА
ИСЧЕЗ, а Джарвис при этом напечатал «Saved», то есть соврал.

Атомарность тут не помогает вовсе: файл цел и читается, просто в нём не то.
Лечится это не аккуратной записью, а `update()` — прочитать и записать ПОД
ОДНИМ замком.

ВТОРАЯ: НА WINDOWS ОДНОВРЕМЕННАЯ ЗАПИСЬ ПРОСТО ОТКАЗЫВАЛА. Замер, три потока
по 80 записей файла в 241 КБ:

    как было (с копиями)   отказов 39 из 240  (16,2%)  PermissionError
    без копий              отказов  6 из 240  ( 2,5%)
    с замком               отказов  0 из 240  ( 0,0%)

Причина: снятие копии ОТКРЫВАЕТ файл на чтение, а Windows не даёт переименовать
поверх открытого файла. То есть защита мешала сама себе, и тем чаще, чем крупнее
становилась память. Замок убирает отказы полностью, ценой меньше четырёх
миллисекунд на запись.

ПОЧЕМУ ЗАМОК ЖИВЁТ ЗДЕСЬ, А НЕ У ВЫЗЫВАЮЩИХ
У памяти он был, у личности был ОБЪЯВЛЕН И НЕ ИСПОЛЬЗОВАН (main.py:150,
проверено грепом), у настроек и у файла версий состояния его не было вовсе —
при том что шапка config/loader обещает «закрывает гонку двух писателей». Один
замок в одном месте вместо четырёх обещаний разной степени правдивости.

Имя файла версий здесь нарочно НЕ названо: правило проекта — имя файла
состояния упоминает только его единственный писатель, и сторож это проверяет
грепом. Первая версия этой шапки его назвала и покраснела — седьмой случай
«сторож находит сам себя» в проекте.

ПОЧЕМУ ОДИН ЗАМОК НА ВСЕ ФАЙЛЫ, А НЕ ПО ЗАМКУ НА ФАЙЛ
Замок на файл был бы чуть свободнее, но потребовал бы словаря замков и замка
вокруг словаря. Запись состояния — редкая (несколько раз за разговор) и стоит
7 мс, поэтому «настройки ждут память» никто не заметит. Простота здесь дороже
пропускной способности.

Design constraints kept from Stage 2: stdlib only, no network, no new deps.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# Same hidden home dir the Stage 2 store (jarvis.db) already uses.
APP_DIR_NAME = ".jarvis"

# Test seam: point all durable state somewhere else without touching $HOME.
STATE_DIR_ENV = "JARVIS_STATE_DIR"

# How many known-good previous versions to keep alongside each file.
SNAPSHOT_COUNT = 3

# Блок 9. ЕДИНСТВЕННЫЙ замок вокруг записи файлов состояния (см. шапку).
_LOCK = threading.Lock()

# Уже внутри записи в ЭТОМ потоке? Нужно, чтобы вложенный вызов присоединялся,
# а не вставал намертво. RLock был бы короче, но он ПРЯЧЕТ вложенность вместо
# того, чтобы её обработать — тот же выбор, что сделан у кассы записи в базу.
_depth = threading.local()

# Возраст, после которого осиротевший временный файл считается мусором. Час —
# заведомо больше самой долгой мыслимой записи (замер: 16 мс на 384 КБ).
_ORPHAN_AGE_S = 3600.0

_swept = False


# -- Paths --------------------------------------------------------------------

def state_dir() -> Path:
    """The one durable directory. Everything that must survive a restart
    (and a build update) lives here - see Invariant 1: one owner of state."""
    override = os.environ.get(STATE_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / APP_DIR_NAME


def state_path(filename: str) -> Path:
    return state_dir() / filename


def snapshot_path(path: Path, index: int) -> Path:
    return Path(path).with_name(f"{Path(path).name}.bak{index}")


# -- Internal helpers ---------------------------------------------------------

def _fsync_dir(directory: Path) -> None:
    """Make the rename itself durable. Best-effort: Windows cannot open a
    directory handle this way, and that is fine - os.replace is still atomic."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read_json_dict(path: Path) -> dict | None:
    """Parse a JSON object, or return None if unreadable / not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _rotate_snapshots(path: Path, count: int) -> None:
    """Shift .bak1 -> .bak2 -> ... and copy the current file to .bak1.

    Only a file we can actually parse becomes a snapshot: we must never
    enshrine corruption as a 'known-good' version.
    """
    if count <= 0 or not path.exists():
        return
    if _read_json_dict(path) is None:
        return

    for index in range(count, 1, -1):
        src = snapshot_path(path, index - 1)
        if src.exists():
            try:
                os.replace(src, snapshot_path(path, index))
            except OSError:
                pass
    try:
        shutil.copy2(path, snapshot_path(path, 1))
    except OSError:
        pass


def quarantine(path: Path) -> Path | None:
    """Move a damaged file aside instead of destroying it. Returns the new
    location, or None if it could not be moved."""
    path = Path(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    suffix = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
        suffix += 1
    try:
        os.replace(path, dest)
        return dest
    except OSError:
        return None


# -- Public API ---------------------------------------------------------------

def atomic_write_json(path: Path | str, data: Any, *,
                      snapshots: int = SNAPSHOT_COUNT) -> Path:
    """Durably replace `path` with `data`.

    Order matters: serialise first (so a bad object fails before we touch
    disk), snapshot the previous good version, write a temp file in the same
    directory, fsync it, then atomically rename over the target.

    Блок 9: всё это идёт ПОД ЕДИНСТВЕННЫМ ЗАМКОМ (см. шапку). Без него на
    Windows одновременная запись отказывала в 16% случаев: снятие копии
    открывает файл на чтение, а поверх открытого файла переименовать нельзя.

    Разбор данных стоит ДО замка нарочно: превращать объект в текст — работа
    процессора, и держать на ней очередь незачем. Заодно негодный объект падает
    раньше, чем мы вообще коснулись диска.
    """
    path = Path(path)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    if getattr(_depth, "n", 0):
        return _write_locked(path, payload, snapshots)
    with _LOCK:
        _depth.n = 1
        try:
            _sweep_once(path.parent)
            return _write_locked(path, payload, snapshots)
        finally:
            _depth.n = 0


def _write_locked(path: Path, payload: str, snapshots: int) -> Path:
    """Сама запись. Замок держит вызывающий."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_snapshots(path, snapshots)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                    dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    _fsync_dir(path.parent)
    return path


def update(path: Path | str, change: Callable[[dict], Any],
           default_factory: Callable[[], dict] = dict, *,
           label: str = "State",
           snapshots: int = SNAPSHOT_COUNT) -> dict:
    """Прочитать, изменить и записать — ПОД ОДНИМ замком. Возвращает записанное.

    ЗАЧЕМ ЭТА ФУНКЦИЯ СУЩЕСТВУЕТ
    ----------------------------
    Атомарная запись бережёт ФАЙЛ, но не бережёт ПРАВКУ. Память правится так:
    прочитать всё, добавить свой факт, записать всё обратно. Два таких потока —
    и последний затирает работу первого.

    Замерено 21.08.2026 на копии настоящей памяти владельца: голосовое
    «запомни» и фоновый извлекатель одновременно -> ФАКТ ВЛАДЕЛЬЦА ИСЧЕЗ, а
    Джарвис напечатал «Saved». Файл при этом целый и читается — просто в нём
    не то, что владелец сказал.

    `change` получает прочитанный документ и меняет его НА МЕСТЕ (или
    возвращает новый). Работа внутри обязана быть короткой и не трогать сеть:
    пока она идёт, все остальные писатели состояния стоят.

    Вложенный вызов присоединяется к уже взятому замку, а не встаёт намертво.
    """
    path = Path(path)
    if getattr(_depth, "n", 0):
        return _update_locked(path, change, default_factory, label, snapshots)
    with _LOCK:
        _depth.n = 1
        try:
            _sweep_once(path.parent)
            return _update_locked(path, change, default_factory, label,
                                  snapshots)
        finally:
            _depth.n = 0


def _update_locked(path: Path, change, default_factory, label: str,
                   snapshots: int) -> dict:
    data, _report = _load_report_locked(path, default_factory, label, snapshots)
    changed = change(data)
    if isinstance(changed, dict):
        data = changed
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    _write_locked(path, payload, snapshots)
    return data


def _sweep_once(directory: Path) -> int:
    """Убрать осиротевшие временные файлы. Один раз за запуск, под замком.

    Откуда они берутся: если процесс умрёт между созданием временного файла и
    переименованием, файл останется. Имя у него каждый раз новое (mkstemp),
    поэтому следующая запись его НЕ перезапишет — он ляжет рядом навсегда, и
    каждое падение добавит ещё один.

    Проверено: в доме владельца сейчас таких файлов НЕТ, то есть уборка здесь
    предупредительная, а не лечебная. Но существующий тест на убийство процесса
    прямо разрешает один такой файл (`assert len(leftovers) <= 1`) — то есть
    утечка была узаконена, а не поймана.

    КАРАНТИНЫ (`.corrupt-*`) НЕ УДАЛЯЮТСЯ ЗДЕСЬ НИКОГДА, и это не забывчивость.
    Временный файл — мусор по построению: он недописан и не нужен никому.
    Карантин — УЛИКА: единственная копия того, что владелец потерял. Удалять
    улику ради чистоты нельзя. Их считает доктор и называет владельцу вслух.
    """
    global _swept
    if _swept:
        return 0
    _swept = True
    edge = time.time() - _ORPHAN_AGE_S
    gone = 0
    try:
        for item in directory.glob(".*.tmp"):
            try:
                if item.is_file() and item.stat().st_mtime < edge:
                    item.unlink()
                    gone += 1
            except OSError:
                continue
    except OSError:
        return gone
    return gone


def reset_for_tests() -> None:
    """Забыть защёлку уборки. Зовёт tests/conftest.py.

    Защёлка живёт на процесс, а весь прогон — один процесс: без сброса первый
    же тест выключил бы уборку для всех следующих, и они были бы зелёными по
    неверной причине. Та же болезнь и тот же рецепт, что у девяти других
    пунктов списка в conftest.
    """
    global _swept
    _swept = False
    _depth.n = 0


def load_json_report(path: Path | str,
                     default_factory: Callable[[], dict],
                     *, label: str = "State",
                     snapshots: int = SNAPSHOT_COUNT) -> tuple[dict, dict]:
    """Load a JSON object, degrading loudly and never destructively.

    Returns (data, report). `report["source"]` is one of:
      missing                  - no file has ever existed (empty is correct)
      primary                  - loaded normally
      snapshot:<name>          - primary was corrupt, recovered from a backup
      empty_after_corruption   - primary was corrupt and no backup worked

    The critical difference from the old behaviour: a corrupt primary is
    quarantined, so the next save cannot overwrite it with an empty object.

    Блок 9: чтение идёт ПОД ТЕМ ЖЕ ЗАМКОМ, что и запись, и это не
    перестраховка. Эта функция УМЕЕТ ПРАВИТЬ ДИСК: она уносит повреждённый файл
    в карантин и записывает на его место восстановленную копию. Читатель,
    который делает это одновременно с писателем, — второй писатель.
    """
    path = Path(path)
    if getattr(_depth, "n", 0):
        return _load_report_locked(path, default_factory, label, snapshots)
    with _LOCK:
        _depth.n = 1
        try:
            return _load_report_locked(path, default_factory, label, snapshots)
        finally:
            _depth.n = 0


def _load_report_locked(path: Path, default_factory, label: str,
                        snapshots: int) -> tuple[dict, dict]:
    """Само чтение с восстановлением. Замок держит вызывающий."""
    report: dict[str, Any] = {"source": "missing", "quarantined": None,
                              "error": None, "path": str(path)}

    if not path.exists():
        return default_factory(), report

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            report["source"] = "primary"
            return data, report
        report["error"] = "top-level JSON is not an object"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"

    print(f"[{label}] \u26a0\ufe0f Файл повреждён ({report['error']}): {path.name}")
    moved = quarantine(path)
    report["quarantined"] = str(moved) if moved else None
    if moved:
        print(f"[{label}] \U0001f9ca Повреждённая копия сохранена: {moved.name}")

    for index in range(1, max(snapshots, 0) + 1):
        snap = snapshot_path(path, index)
        if not snap.exists():
            continue
        recovered = _read_json_dict(snap)
        if recovered is None:
            continue
        report["source"] = f"snapshot:{snap.name}"
        print(f"[{label}] \u267b\ufe0f Восстановлено из резервной копии {snap.name}")
        try:
            # Замок уже держит вызывающий, поэтому пишем НАПРЯМУЮ. Вызов
            # atomic_write_json здесь взял бы замок второй раз — с обычным
            # замком это мёртвая хватка, и она случилась бы ровно в тот
            # вечер, когда у владельца повредился файл памяти.
            _write_locked(path, json.dumps(recovered, indent=2,
                                           ensure_ascii=False), 0)
        except OSError:
            pass
        return recovered, report

    report["source"] = "empty_after_corruption"
    print(f"[{label}] \u26d4 Рабочих резервных копий нет. "
          f"Стартуем с пустого состояния; повреждённый файл НЕ удалён.")
    return default_factory(), report


def load_json_safe(path: Path | str,
                   default_factory: Callable[[], dict],
                   *, label: str = "State",
                   snapshots: int = SNAPSHOT_COUNT) -> dict:
    """Convenience wrapper around load_json_report when the report is not needed."""
    data, _ = load_json_report(path, default_factory, label=label,
                               snapshots=snapshots)
    return data


def import_legacy_once(legacy_path: Path | str,
                       target_path: Path | str,
                       *, label: str = "State") -> bool:
    """One-time, idempotent lift of a build-folder file into the durable dir.

    Rules that make this safe to call on every start:
      * never runs if the target already exists (the durable copy always wins)
      * never runs twice (a marker is dropped next to the legacy file)
      * NEVER deletes or modifies the user's original file
      * refuses to import anything that is empty or unparseable
    """
    legacy = Path(legacy_path)
    target = Path(target_path)
    marker = legacy.with_name(f"{legacy.name}.imported")

    if target.exists() or marker.exists() or not legacy.exists():
        return False
    if legacy.resolve() == target.resolve():
        return False

    data = _read_json_dict(legacy)
    if not data:
        return False

    try:
        atomic_write_json(target, data, snapshots=0)
    except OSError as exc:
        print(f"[{label}] \u26a0\ufe0f Не удалось перенести {legacy.name}: {exc}")
        return False

    try:
        marker.write_text(datetime.now().isoformat(timespec="seconds"),
                          encoding="utf-8")
    except OSError:
        pass

    print(f"[{label}] \U0001f4e6 Перенесено из папки сборки в {target}")
    return True
