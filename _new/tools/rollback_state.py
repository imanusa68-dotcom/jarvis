# -*- coding: utf-8 -*-
# tools/rollback_state.py — откат данных на снимок. Шаг 33.5, план Р6 пункт 3.
#
# Зачем он есть: снимки с шага 33.3 лежат, но вернуться на них можно
# только руками: скопировать файлы, не забыть про -wal и -shm, пересобрать
# поиск по памяти. Вечером, когда всё уже сломано, это ровно тот момент,
# когда ошибка уставшего человека добивает базу насовсем.
#
# Правила, из которых сделан инструмент:
# 1. Ничего не делает без слова владельца. Не «да» и не Enter:
#    набрать слово целиком. Случайно такое не набирают.
# 2. Перед откатом сам снимает снимок. Не вышло снять — отказ, а не
#    «наверное, обойдётся»: иначе неудачный откат не отменить.
# 3. Отказывается работать, пока Запущен Jarvis. Проверка — попыткой
#    взять тот же замок, а не os.kill: на Windows os.kill убивает процесс
#    даже с нулевым сигналом, то есть «проверка» была бы убийством.
# 4. Сначала копии всех файлов рядом, проверка целостности баз, и
#    только потом замена одна за другой. Самый страшный исход — не
#    отказ, а полуоткат: база старая, память новая.
# 5. Файл состояния из снимка НЕ возвращается (решение моё, не плана):
#    иначе реестр снимков откатился бы в прошлое и забыл тот самый
#    снимок, который снят перед откатом. Он переписывается заново.
# 6. Одна запись в вечный журнал. Откат — событие, о котором через
#    месяц будет важно вспомнить точно.
# 7. Назад в будущее не откатываем: если в снимке схема базы новее,
#    чем знает этот код, — отказ.
#
# Как звать:
#   python tools/rollback_state.py --list
#   python tools/rollback_state.py --to 0003
#   python tools/rollback_state.py --to 20260814T155649Z_p0s33_auto_0003
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import audit_log  # noqa: E402
from core import instance_lock  # noqa: E402
from core import state_snapshot  # noqa: E402
from core import state_version  # noqa: E402

SCHEMA_VER = 1
WORD = "откатить"
TMP_PREFIX = ".tmp-restore-"
SIDE_SUFFIXES = ("-wal", "-shm")
REPLACE_TRIES = 3
REPLACE_PAUSE = 0.2
EVENT = "state_rollback"
LF = chr(10)

# Что возвращаем: две базы и три файла настроек и памяти.
RESTORE_NAMES = tuple(state_snapshot.DB_FILES) + tuple(state_snapshot.JSON_FILES)

# Что НЕ возвращаем никогда (и говорим об этом вслух).
NEVER_RESTORED = (
    (state_version.FILE_NAME, "файл состояния перепишется заново"),
    ("gate-audit.jsonl", "журнал действий не откатывается никогда"),
    ("reminders.json", "напоминания до фазы 1 живут в папке сборки"),
)


# -- Мелкие снасти -------------------------------------------------

def _say(printer, line: str) -> None:
    (printer or print)(line)


def _home() -> Path:
    return state_version.dir_path()


def _quick_check(target: Path) -> str:
    # Самая дешёвая честная проверка: база вообще целая?
    try:
        conn = sqlite3.connect(str(target))
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else "пустой ответ"
    except Exception as exc:
        return type(exc).__name__ + ": " + str(exc)


def _facts(target: Path) -> int | None:
    # Сколько фактов помнит база. Нужно только для честного
    # вопроса владельцу: вы теряете столько-то записей.
    if not target.exists():
        return None
    try:
        conn = sqlite3.connect("file:" + str(target) + "?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT count(*) FROM memory_fact").fetchone()
        finally:
            conn.close()
        return int(row[0]) if row else None
    except Exception:
        return None


def _size(target: Path) -> int | None:
    try:
        return target.stat().st_size
    except OSError:
        return None


def _replace(src: Path, dst: Path) -> None:
    # Замена с повторами: файл может держать антивирус или облако.
    last: Exception | None = None
    for _ in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            last = exc
            time.sleep(REPLACE_PAUSE)
    raise last if last else OSError("замена не вышла")


# -- Какой снимок выбрали -------------------------------------------

def items() -> list:
    return state_snapshot.list_snapshots()


def pick(target: str, known: list | None = None) -> dict | None:
    # Пустить владельца набирать целиком 20260814T155649Z_p0s33_auto_0003
    # было бы издевательством. Принимаем также номер и хвост имени.
    known = items() if known is None else known
    text = (target or "").strip()
    if not text:
        return None
    for entry in known:
        if str(entry.get("id")) == text:
            return entry
    tail = [e for e in known if str(e.get("id", "")).endswith(text)]
    if len(tail) == 1:
        return tail[0]
    if text.isdigit():
        same = [e for e in known if int(e.get("seq") or 0) == int(text)]
        if len(same) == 1:
            return same[0]
    return None


# -- Отказы до всякой записи ------------------------------------------

DB_KEYS = {"jarvis.db": "jarvis_db", "history.db": "history_db"}
JSON_KEYS = {"settings.json": "settings", "long_term.json": "memory_json",
             "personality.json": "personality"}


def busy() -> tuple[bool, str]:
    # Занятость проверяем единственным честным способом: берём тот же
    # замок и сразу отдаём. Побочный эффект называю вслух: взятие
    # замка перепишет файл-визитку нашим pid. Это дешевле, чем
    # надеяться на чужой pid: на Windows проверка живости через os.kill
    # убивает процесс даже с нулевым сигналом.
    try:
        instance_lock.acquire()
    except Exception as exc:
        info = {}
        try:
            info = instance_lock.read_info() or {}
        except Exception:
            info = {}
        who = str(info.get("pid") or "неизвестно")
        return True, "Jarvis сейчас запущен (pid " + who + "): " + str(exc)
    try:
        instance_lock.release()
    except Exception:
        pass
    return False, ""


def forward(entry: dict) -> str:
    # Откат вперёд — самый коварный случай: владелец распаковал
    # старую папку и тянет в неё базу от более нового кода. Старый
    # код не умеет читать новую схему и начнёт её «починку».
    stores = entry.get("stores") if isinstance(entry, dict) else None
    stores = stores if isinstance(stores, dict) else {}
    knows = state_version.code_migrations()
    for name, key in DB_KEYS.items():
        block = stores.get(key)
        block = block if isinstance(block, dict) else {}
        was = block.get("user_version")
        limit = knows.get(key)
        if isinstance(was, int) and isinstance(limit, int) and was > limit:
            return ("в снимке " + name + " версии " + str(was)
                    + ", а этот код знает только " + str(limit)
                    + ": назад в будущее не откатываем")
    return ""


def describe(entry: dict) -> list:
    # Цена отката словами, а не процентами: что вернётся, что
    # останется на месте и сколько записей памяти вы теряете.
    if not isinstance(entry, dict):
        return ["Снимок не найден."]
    folder = Path(str(entry.get("path") or ""))
    home = _home()
    lines = [
        "Снимок: " + str(entry.get("id")),
        "Снят: " + str(entry.get("created_at")) + " (вид "
        + str(entry.get("kind")) + ", шаг " + str(entry.get("step")) + ")",
        "Папка сборки тогда: " + str(entry.get("build_folder")),
        "",
        "Вернётся на место:",
    ]
    for name in RESTORE_NAMES:
        src = folder / name
        if not src.exists():
            lines.append("  " + name + " — в снимке его нет, останется как есть")
            continue
        now = _size(home / name)
        lines.append("  " + name + ": в снимке " + str(_size(src))
                     + " байт, сейчас "
                     + (str(now) + " байт" if now is not None else "файла нет"))
    was = _facts(folder / "jarvis.db")
    current = _facts(home / "jarvis.db")
    if isinstance(was, int) and isinstance(current, int):
        lines.append("")
        lines.append("Записей в памяти: сейчас " + str(current)
                     + ", будет " + str(was)
                     + " (разница " + str(current - was) + ")")
    lines.append("")
    lines.append("Не трогаем:")
    for name, why in NEVER_RESTORED:
        lines.append("  " + name + " — " + why)
    return lines


# -- Главное действие --------------------------------------------------

def _clean_tmp(home: Path) -> None:
    for name in RESTORE_NAMES:
        try:
            (home / (TMP_PREFIX + name)).unlink(missing_ok=True)
        except OSError:
            pass


def _rebuild_fts(db: Path, *, printer=None) -> str:
    # Поиск по памяти — отдельные таблицы-указатели. После подмены
    # файла базы они могут отставать от таблицы фактов, и поиск будет
    # молча находить не то. Молча — хуже всего, поэтому пересборка.
    if not db.exists():
        return "базы нет"
    try:
        from core import store
        from memory import fact_store
        conn = store.connect(db)
        try:
            fact_store.rebuild_fts(conn)
        finally:
            conn.close()
        return "пересобран"
    except Exception as exc:
        text = type(exc).__name__ + ": " + str(exc)
        _say(printer, "[Откат] поиск по памяти не пересобран: " + text)
        return text


def _journal(report: dict, verdict: str) -> bool:
    try:
        return audit_log.append({
            "event": EVENT,
            "tool_ver": SCHEMA_VER,
            "verdict": verdict,
            "to": report.get("id"),
            "pre_rollback": report.get("pre_rollback"),
            "restored": list(report.get("restored") or []),
            "side_removed": list(report.get("side_removed") or []),
            "fts": report.get("fts"),
            "state": report.get("state"),
            "refused": report.get("refused"),
        })
    except Exception:
        return False


def restore(entry, *, word: str, printer=None) -> dict:
    # Возвращает отчёт и никогда не бросает исключение наружу:
    # владелец вечером должен видеть русские слова, а не трассировку.
    report = {
        "ok": False,
        "id": str(entry.get("id")) if isinstance(entry, dict) else "",
        "refused": "",
        "restored": [],
        "absent_in_snapshot": [],
        "side_removed": [],
        "pre_rollback": None,
        "fts": "",
        "state": "",
        "journal": False,
        "half": False,
    }

    def refuse(reason: str) -> dict:
        report["refused"] = reason
        _say(printer, "[Откат] отказ, дом не тронут: " + reason)
        report["journal"] = _journal(report, "refused")
        return report

    if not isinstance(entry, dict):
        return refuse("снимок не найден")
    if word != WORD:
        return refuse("не набрано слово подтверждения " + WORD)
    folder = Path(str(entry.get("path") or ""))
    if not folder.is_dir():
        return refuse("папки снимка нет на диске")
    bad = {n: v for n, v in (entry.get("quick_check") or {}).items() if v != "ok"}
    if bad:
        return refuse("сам снимок помечен как битый: " + str(bad))
    stop = forward(entry)
    if stop:
        return refuse(stop)
    running, who = busy()
    if running:
        return refuse(who + ". Закройте Jarvis и повторите")

    # Шаг 1: снимок перед откатом. Без него откат необратим.
    made = None
    try:
        made = state_snapshot.create(
            state_snapshot.KIND_PRE_ROLLBACK,
            reason="перед откатом на " + report["id"],
            printer=printer)
    except Exception as exc:
        return refuse("снимок перед откатом не вышел: "
                      + type(exc).__name__ + ": " + str(exc))
    if not made:
        return refuse("снимок перед откатом не вышел, а без него "
                      "откат нечем отменить")
    report["pre_rollback"] = made.get("id")

    home = _home()
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return refuse("дома нет и создать не вышло: " + str(exc))

    # Шаг 2: сначала все копии рядом и проверка баз, и только потом
    # замена. Если что-то плохо — дом ещё не тронут.
    staged = {}
    try:
        for name in RESTORE_NAMES:
            src = folder / name
            if not src.exists():
                report["absent_in_snapshot"].append(name)
                continue
            tmp = home / (TMP_PREFIX + name)
            shutil.copy2(src, tmp)
            if name in DB_KEYS:
                verdict = _quick_check(tmp)
                if verdict != "ok":
                    raise RuntimeError("копия " + name
                                       + " не прошла проверку: " + verdict)
            staged[name] = tmp
    except Exception as exc:
        _clean_tmp(home)
        return refuse("копии не готовы: " + type(exc).__name__ + ": " + str(exc))
    if not staged:
        _clean_tmp(home)
        return refuse("в снимке нет ни одного файла, который мы возвращаем")

    # Шаг 3: замена. Самое узкое место всего шага.
    for name in RESTORE_NAMES:
        tmp = staged.get(name)
        if tmp is None:
            continue
        try:
            _replace(tmp, home / name)
            report["restored"].append(name)
        except OSError as exc:
            report["half"] = True
            report["refused"] = ("полуоткат: " + name
                                 + " заменить не вышло: " + str(exc))
            _clean_tmp(home)
            _say(printer, "[Откат] ОПАСНО: откат сделан наполовину.")
            _say(printer, "[Откат] из снимка вернулись: "
                 + (", ".join(report["restored"]) or "ничего"))
            _say(printer, "[Откат] остались прежними: " + name)
            _say(printer, "[Откат] состояние до отката цело в снимке "
                 + str(report["pre_rollback"]))
            report["journal"] = _journal(report, "half")
            return report

    # Шаг 4: осиротевшие -wal и -shm. Старая база с новым журналом —
    # это база, которая при первом открытии дочитает чужие правки.
    for name in DB_KEYS:
        for suffix in SIDE_SUFFIXES:
            side = home / (name + suffix)
            if not side.exists():
                continue
            try:
                side.unlink()
                report["side_removed"].append(side.name)
            except OSError as exc:
                _say(printer, "[Откат] не убрался " + side.name + ": " + str(exc))

    # Шаг 5: поиск по памяти и шаг 6: файл состояния заново.
    report["fts"] = _rebuild_fts(home / "jarvis.db", printer=printer)
    try:
        state_version.write()
        report["state"] = "переписан"
    except Exception as exc:
        report["state"] = type(exc).__name__ + ": " + str(exc)
        _say(printer, "[Откат] файл состояния не переписан: " + report["state"])

    report["ok"] = True
    report["journal"] = _journal(report, "done")
    _say(printer, "[Откат] готово: " + report["id"] + ", вернулось файлов "
         + str(len(report["restored"]))
         + ", состояние до отката в снимке " + str(report["pre_rollback"]))
    return report


# -- Запуск руками -------------------------------------------------------

def _list_lines() -> list:
    known = items()
    if not known:
        return ["Снимков нет. Откатываться не на что."]
    out = ["Снимки (старые сверху):"]
    for entry in known:
        out.append("  " + str(entry.get("id"))
                   + "  — " + str(entry.get("created_at"))
                   + ", вид " + str(entry.get("kind"))
                   + ", файлов " + str(len(entry.get("files") or {}))
                   + ", " + str(max(int(entry.get("bytes") or 0) // 1024, 1)) + " КБ")
    out.append("")
    out.append("Откатиться: python tools/rollback_state.py --to <номер или id>")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Откат данных Jarvis на ранее снятый снимок")
    parser.add_argument("--list", action="store_true",
                        help="показать снимки и выйти")
    parser.add_argument("--to", default="",
                        help="номер снимка, хвост имени или полный id")
    parser.add_argument("--word", default="",
                        help="слово подтверждения без вопроса")
    args = parser.parse_args(argv)

    if args.list or not args.to:
        for line in _list_lines():
            print(line)
        return 0

    entry = pick(args.to)
    if entry is None:
        print("Такого снимка нет или подходят сразу несколько: " + args.to)
        print("")
        for line in _list_lines():
            print(line)
        return 2

    for line in describe(entry):
        print(line)
    print("")

    word = args.word
    if not word:
        print("Чтобы согласиться, наберите слово: " + WORD)
        try:
            word = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            print("Отмена. Дом не тронут.")
            return 1

    report = restore(entry, word=word)
    if report["ok"]:
        print("")
        print("Запускать Jarvis можно.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
