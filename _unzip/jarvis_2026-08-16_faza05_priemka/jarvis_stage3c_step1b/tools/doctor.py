# -*- coding: utf-8 -*-
# tools/doctor.py -- один экран правды о среде и доме. План Р11, шаг 34.2.
#
# Кому он нужен: владельцу в тот вечер, когда "что-то не так", и мне в тот
# вечер, когда надо узнать состояние машины одной командой, а не восемью.
#
# ПЯТЬ ЗАПРЕТОВ. Нарушение любого превращает градусник в болезнь:
#   1. НИЧЕГО не пишет: ни файла, ни папки, ни строки в журнал.
#   2. НЕ берёт замок. Один раз мы уже наступили: инструмент отката взял
#      замок, и jarvis.lock.info стал рассказывать про pid инструмента.
#   3. НЕ открывает базу через sqlite3. Версия читается из первых 100 байт
#      заголовка файла: у базы в режиме WAL любое открытие плодит рядом
#      -shm и -wal, а то и подбирает хвост журнала.
#   4. НЕ зовёт state_version.load() и вообще safe_json: при битом файле
#      safe_json уносит его в карантин и пишет восстановленную копию -- то есть
#      меняет то, что пришёл измерить. Здесь файл состояния читается голым json.loads.
#      (Из safe_json берётся только state_dir() -- это чистая функция пути,
#      папку она не создаёт, проверено.)
#   5. НЕ падает: каждый раздел в своём try. Ошибка раздела -- это строка
#      отчёта, а не конец осмотра.
#
# Вывод всегда идёт через say() -- единственную дверь наружу, которая прячет
# ключи и имя пользователя: экран доктора всегда куда-то копируют.

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import env                      # noqa: E402
from core import build_stamp               # noqa: E402
from core import instance_lock             # noqa: E402
from core import state_snapshot            # noqa: E402
from core.safe_json import state_dir       # noqa: E402

SQLITE_MAGIC = b"SQLite format 3" + bytes([0])
DB_NAME = "jarvis.db"
from core import state_version  # только ради имени файла, ничего больше

# Литерала имени здесь быть не должно: сторож test_one_writer_only
# (шаг 33.1) требует одного хозяина у имени файла состояния.
# Доктор его только читает, но правило одно для всех.
STATE_NAME = state_version.FILE_NAME
NOTES = []


def say(text=""):
    # Единственный print во всём файле. Сторож это проверяет.
    print(env.redact(text))


def note(text):
    NOTES.append(text)


def head(number, title):
    say("")
    say("== " + str(number) + ". " + title + " ==")


def size_of(number):
    try:
        number = int(number)
    except Exception:
        return "?"
    if number < 1024:
        return str(number) + " Б"
    if number < 1024 * 1024:
        return str(round(number / 1024.0, 1)) + " КБ"
    return str(round(number / 1048576.0, 1)) + " МБ"


def guard(number, title, body):
    # Запрет 5 живёт здесь.
    head(number, title)
    try:
        body()
    except Exception as exc:
        say("   раздел упал: " + type(exc).__name__ + ": " + str(exc))
        note("раздел " + str(number) + " (" + title + ") не смог отработать")


# -- 1 -----------------------------------------------------------------

def part_python():
    facts = env.report()
    say("   Python " + facts["python"] + "  (" + facts["platform"] + ")")
    say("   запущен: " + str(facts["executable"]))
    say("   папка запуска: " + str(facts["cwd"]))
    say("   корень проекта: " + str(ROOT))
    if not facts["python"].startswith("3.12"):
        note("Python " + facts["python"] + ", а проект живёт на 3.12")
    if Path(facts["cwd"]).resolve() != ROOT:
        note("запущено не из корня проекта -- тесты из такой папки не запускаются")


# -- 2 -----------------------------------------------------------------

def part_encodings():
    facts = env.report()
    say("   канал 1, живая консоль: " + facts["console_encoding"] +
        ("  (это консоль)" if facts["console_is_live"] else "  (вывод куда-то перенаправлен)"))
    say("   канал 2, перенаправление в файл: " + facts["locale_encoding"] +
        "   (режим utf-8: " + str(facts["utf8_mode"]) + ")")
    say("   канал 3, имена файлов: " + facts["fs_encoding"])
    say("   PYTHONUTF8=" + (facts["PYTHONUTF8"] or "не задан") +
        "   PYTHONIOENCODING=" + (facts["PYTHONIOENCODING"] or "не задан"))
    if facts["setup_done"]:
        for name, what in sorted(facts["streams"].items()):
            say("   " + name + ": " + what)
    if env.redirection_is_safe():
        say("   запись вывода в файл: безопасна")
    else:
        say("   запись вывода в файл: ОПАСНА -- значок в сообщении уронит задачу")
        note("перенаправление вывода в файл небезопасно: запускайте через run_tests.cmd или с PYTHONUTF8=1")


# -- 3 -----------------------------------------------------------------

def part_home():
    home = state_dir()
    say("   дом: " + str(home))
    if not home.exists():
        say("   дома пока нет -- это норма до первого запуска")
        return
    files = 0
    folders = 0
    for item in sorted(home.iterdir()):
        if item.is_dir():
            folders += 1
            continue
        files += 1
        say("   " + item.name + "  " + size_of(item.stat().st_size))
    say("   итого: файлов " + str(files) + ", папок " + str(folders))


# -- 4 -----------------------------------------------------------------

def part_database():
    try:
        from core import store
        spot = Path(store.db_path())
    except Exception:
        spot = state_dir() / DB_NAME
    say("   файл: " + str(spot))
    if not spot.exists():
        say("   базы ещё нет -- норма до первого запуска")
        return
    say("   размер: " + size_of(spot.stat().st_size))
    with open(spot, "rb") as handle:
        header = handle.read(100)
    if header[:16] != SQLITE_MAGIC:
        say("   ЭТО НЕ БАЗА SQLite: заголовок чужой")
        note("файл базы не похож на базу SQLite")
        return
    version = int.from_bytes(header[60:64], "big")
    say("   версия схемы (user_version): " + str(version) + "   [читали заголовок, базу не открывали]")
    for suffix in ("-wal", "-shm"):
        side = spot.with_name(spot.name + suffix)
        if side.exists():
            say("   рядом лежит " + side.name + "  " + size_of(side.stat().st_size) +
                "  (норма, пока Джарвис запущен)")


# -- 5 -----------------------------------------------------------------

def part_state():
    spot = state_dir() / STATE_NAME
    say("   файл: " + str(spot))
    if not spot.exists():
        say("   файла состояния нет -- норма до первого запуска")
        return
    raw = spot.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except Exception as exc:
        say("   ФАЙЛ БИТЫЙ: " + type(exc).__name__ + ": " + str(exc))
        say("   доктор его НЕ трогал и НЕ чинил -- это дело запуска Джарвиса")
        note(STATE_NAME + " не разбирается -- будет восстановлен из снимка при запуске")
        return
    if not isinstance(data, dict):
        say("   внутри не словарь, а " + type(data).__name__)
        note(STATE_NAME + " странного вида")
        return
    versions = data.get("versions")
    if isinstance(versions, dict):
        pairs = [str(k) + "=" + str(v) for k, v in sorted(versions.items())]
        say("   версии хранилищ: " + (", ".join(pairs) if pairs else "пусто"))
    last = data.get("last_run")
    if isinstance(last, dict):
        say("   прошлый запуск: " + str(last.get("at", "?")) +
            ", чистый выход: " + str(last.get("clean_exit", "?")))
        say("   папка прошлого запуска: " + str(last.get("path", "?")))
    registry = data.get("snapshots")
    say("   снимков в реестре: " + str(len(registry) if isinstance(registry, list) else "?"))


# -- 6 -----------------------------------------------------------------

def part_snapshots():
    known = state_snapshot.list_snapshots()
    say("   понятных снимков на диске: " + str(len(known)))
    for item in known:
        say("   " + str(item.get("id", "?")) + "   " + str(item.get("kind", "?")) +
            "   " + str(item.get("reason", "")))
    strangers = state_snapshot.unknown_dirs()
    if strangers:
        say("   папки без манифеста (автоматика их не трогает): " + str(len(strangers)))
        for item in strangers:
            say("   " + str(item))
        note("в папке снимков есть чужие папки: " + str(len(strangers)))


# -- 7 -----------------------------------------------------------------

def part_lock():
    spot = instance_lock.info_path()
    say("   записка замка: " + str(spot))
    if not spot.exists():
        say("   записки нет -- Джарвис с этого момента ни разу не запускался")
        return
    info = instance_lock.read_info()
    if not info:
        say("   записка нечитаема: " + str(instance_lock.last_read_error()))
        return
    say("   pid " + str(info.get("pid", "?")) + ", запись от " + str(info.get("at", "?")))
    say("   чей запуск: " + str(info.get("note", "?")))
    say("   жив ли этот процесс -- НЕ ПРОВЕРЯЛИ: доктор не берёт замок и не стучится в чужие процессы")


# -- 8 -----------------------------------------------------------------

def part_build():
    spot = build_stamp.path()
    say("   метка сборки: " + str(spot))
    if not spot.exists():
        say("   метки нет -- её пишет прогон тестов и запуск Джарвиса")
        return
    data = build_stamp.read()
    for key in ("code_ver", "phase", "step", "tests_total", "tests_failed", "tests_at", "started_at"):
        if key in data:
            say("   " + key + " = " + str(data[key]))
    failed = str(data.get("tests_failed", "")).strip()
    if failed not in ("", "0", "?"):
        note("последний прогон тестов был красным: " + failed)


# -- 9 -----------------------------------------------------------------

def part_journal_and_disk():
    try:
        from core import audit_log
        spot = audit_log.path()
    except Exception:
        spot = state_dir() / "logs" / "gate-audit.jsonl"
    say("   журнал согласий: " + str(spot))
    if spot.exists():
        raw = spot.read_bytes()
        lines = raw.count(bytes([10]))
        say("   размер " + size_of(len(raw)) + ", записей " + str(lines))
        tail = raw.splitlines()[-1:] if lines else []
        for line in tail:
            try:
                item = json.loads(line.decode("utf-8", "replace"))
                say("   последняя запись: " + str(item.get("event", "?")) +
                    " в " + str(item.get("at", "?")) + ", итог " + str(item.get("result", "?")))
            except Exception:
                say("   последняя запись не разбирается")
    else:
        say("   журнала ещё нет -- норма, пока ни одного согласия не спрашивали")
    where = state_dir() if state_dir().exists() else ROOT
    usage = shutil.disk_usage(str(where))
    say("   диск: свободно " + size_of(usage.free) + " из " + size_of(usage.total))
    if usage.free < 2 * 1024 * 1024 * 1024:
        note("меньше 2 ГБ свободно -- снимки состояния будут отказывать")


# -- 10 ----------------------------------------------------------------
# Блок 9. Раздел появился, потому что доктор смотрел на служебный файл версий,
# а на САМУ ПАМЯТЬ владельца -- нет. Битый файл памяти доктору был невидим: он
# попадал только в общий список раздела «Дом» как имя и размер.
#
# Здесь же считаются три вида соседних файлов, и у каждого своя судьба:
#   .bak1..bak3  -- страховочные копии. Их отсутствие само по себе не беда
#                   (файл могли записать один раз), но знать это полезно.
#   .corrupt-*   -- КАРАНТИН, то есть улика: единственная копия того, что
#                   владелец потерял. Их не удаляет никто и никогда, поэтому
#                   доктор обязан называть их вслух -- иначе они лежат молча.
#   .*.tmp       -- мусор от прерванной записи. Убирается сам при следующей
#                   записи; если их много, значит Джарвис часто умирает.

MEMORY_FILES = (
    ("long_term.json", "память: факты о владельце"),
    ("personality.json", "личность: как разговаривать"),
    ("settings.json", "настройки владельца"),
)


def part_memory():
    home = state_dir()
    if not home.exists():
        say("   дома пока нет -- норма до первого запуска")
        return
    for name, what in MEMORY_FILES:
        spot = home / name
        if not spot.exists():
            say("   " + name + ": нет файла (" + what + ")")
            continue
        size = spot.stat().st_size
        raw = spot.read_text(encoding="utf-8", errors="replace")
        data = None
        try:
            data = json.loads(raw)
        except Exception as exc:
            say("   " + name + ": ФАЙЛ БИТЫЙ -- " + type(exc).__name__)
            say("      доктор его НЕ трогал; при запуске Джарвис возьмёт копию")
            note(name + " не разбирается -- проверьте резервные копии")
        else:
            if isinstance(data, dict):
                say("   " + name + ": " + str(size) + " б, разделов " +
                    str(len(data)) + " (" + what + ")")
            else:
                say("   " + name + ": внутри не словарь, а " +
                    type(data).__name__)
                note(name + " странного вида")
                data = None
        good = 0
        for index in (1, 2, 3):
            snap = home / (name + ".bak" + str(index))
            if not snap.exists():
                continue
            try:
                json.loads(snap.read_text(encoding="utf-8", errors="replace"))
                good += 1
            except Exception:
                say("      копия .bak" + str(index) + " не читается")
        say("      годных резервных копий: " + str(good))
        if data is None and good == 0:
            note(name + " битый И копий нет -- данные могут быть потеряны")

    quarantined = sorted(p.name for p in home.glob("*.corrupt-*"))
    say("   карантинов (повреждённые файлы, НЕ удаляются): " +
        str(len(quarantined)))
    for item in quarantined[:5]:
        say("      " + item)
    if quarantined:
        note("в доме " + str(len(quarantined)) +
             " повреждённых файлов -- посмотрите и удалите сами")

    leftovers = sorted(p.name for p in home.glob(".*.tmp") if p.is_file())
    say("   мусора от прерванной записи: " + str(len(leftovers)))
    if len(leftovers) > 3:
        note("осиротевших временных файлов " + str(len(leftovers)) +
             " -- Джарвис часто умирает во время записи")


def main():
    say("ДОКТОР ДЖАРВИСА -- только смотрит, ничего не меняет")
    guard(1, "Python и запуск", part_python)
    guard(2, "Кодировки -- три канала", part_encodings)
    guard(3, "Дом", part_home)
    guard(4, "База", part_database)
    guard(5, "Состояние", part_state)
    guard(6, "Снимки состояния", part_snapshots)
    guard(7, "Замок", part_lock)
    guard(8, "Сборка", part_build)
    guard(9, "Журнал и диск", part_journal_and_disk)
    guard(10, "Память, личность, настройки", part_memory)
    say("")
    if NOTES:
        say("ЗАМЕЧАНИЯ: " + str(len(NOTES)))
        for item in NOTES:
            say("   - " + item)
    else:
        say("ЗАМЕЧАНИЙ НЕТ")
    say("")
    say("Доктор ничего не записал и не взял замок.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
