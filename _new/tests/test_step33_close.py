# -*- coding: utf-8 -*-
# Тесты шага 33.6: закрытие шага 33 (план Р6).
#
# Новых возможностей здесь нет. Здесь то, что должно пережить месяц:
# плановые имена тестов, восемь строк о доме в метке сборки и
# проверка, что все пять опор шага 33 на месте.
#
# В наборе: python -m pytest -q из корня.
# Без pytest: python tests/test_step33_close.py

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import build_stamp, instance_lock, state_snapshot  # noqa: E402
from core import state_version, store  # noqa: E402
from tools import rollback_state  # noqa: E402


# -- Снасти -----------------------------------------------------------

class _Home:
    # Дом на выброс: настоящий дом владельца тесты не видят.

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jv_close_"))
        self.old = os.environ.get("JARVIS_STATE_DIR")
        os.environ["JARVIS_STATE_DIR"] = str(self.dir)
        return self.dir

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = self.old
        try:
            instance_lock.release()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class _Spot:
    # Папка на выброс вместо корня проекта: настоящий BUILD.txt цел.

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jv_close_b_"))
        self.old = os.environ.get(build_stamp.DIR_ENV)
        os.environ[build_stamp.DIR_ENV] = str(self.dir)
        return self.dir

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop(build_stamp.DIR_ENV, None)
        else:
            os.environ[build_stamp.DIR_ENV] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class _Log:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(str(line))

    @property
    def text(self):
        return " | ".join(self.lines)


def _quiet(line):
    return None


INSERT_FACT = ("INSERT INTO memory_fact "
               "(category, key, value, search_text, created_at, updated_at) "
               "VALUES ('notes', 'kot', 'Barsik', 'kot barsik', "
               "'2026-08-15', '2026-08-15')")

FIND_FACT = ("SELECT count(*) FROM memory_fact_word "
             "WHERE memory_fact_word MATCH 'barsik'")

STORE_NAMES = ("jarvis_db", "history_db", "settings", "memory_json",
               "personality", "gate_audit", "reminders_json")

PILLARS = ("core/state_version.py", "core/state_guard.py",
           "core/state_snapshot.py", "core/build_stamp.py",
           "tools/rollback_state.py")


# -- Имя из плана: отчёт знает про все хранилища -----------------------

def test_state_version_covers_all_stores():
    # Плановое имя из Р6. По смыслу повторяет test_collect_covers_all_stores
    # из 33.1 — намеренно: поиск по имени из плана должен что-то находить.
    with _Home() as home:
        (home / "long_term.json").write_text("{}", encoding="utf-8")
        data = state_version.collect() or {}
        stores = data.get("stores") or {}
        for name in STORE_NAMES:
            assert name in stores, "хранилище пропало из отчёта: " + name


# -- Имя из плана: после отката поиск по памяти жив --------------------

def test_fts_rebuild_after_restore():
    # Плановое имя дыры Х-A4. Тест 33.5 проверяет сторону отказа,
    # этот — сторону успеха: настоящая схема, настоящая пересборка.
    with _Home() as home:
        db = home / "jarvis.db"
        conn = store.open_store(db)
        try:
            conn.execute(INSERT_FACT)
            conn.commit()
        finally:
            conn.close()
        made = state_snapshot.create("auto", reason="тест", printer=_Log())
        assert made is not None, "снимок не снялся, проверять нечего"
        known = rollback_state.items()
        assert known, "снимок снялся, но в списке снимков его нет"
        log = _Log()
        report = rollback_state.restore(known[-1], word=rollback_state.WORD,
                                        printer=log)
        assert report["ok"], report["refused"]
        assert report["fts"] == "пересобран", "указатель не пересобран: " + str(report["fts"])
        conn = sqlite3.connect(str(db))
        try:
            found = int(conn.execute(FIND_FACT).fetchone()[0])
        finally:
            conn.close()
        assert found == 1, "после отката поиск по памяти не находит факт"


# -- Восемь строк о доме -------------------------------------------------

def test_home_block_is_eight_lines_until_the_first_start():
    # Отложенный долг 33.4: сколько строк метки честно сознаются,
    # что прогон тестов не видел настоящий дом. Ответ обязан быть 8.
    assert len(build_stamp.HOME_KEYS) == 8, "ключей о доме стало не восемь: " + str(len(build_stamp.HOME_KEYS))
    with _Spot() as spot:
        build_stamp.stamp_tests(total=7, failed=0, seconds=1.0, full=True,
                                printer=_quiet)
        text = (spot / build_stamp.FILE_NAME).read_text(encoding="utf-8")
        lines = [line for line in text.splitlines()
                 if "настоящий дом" in line]
        assert len(lines) == 8, "строк про настоящий дом не восемь: " + str(len(lines))
        for line in lines:
            assert build_stamp.SEP in line, "строка без разделителя: " + line


def test_the_home_block_stops_confessing_after_a_start():
    # Обратная сторона: после старта заглушки обязаны смениться
    # цифрами. Без этого восьмёрка выше стерегла бы вечную заглушку.
    with _Home():
        with _Spot() as spot:
            build_stamp.stamp_tests(total=7, failed=0, seconds=1.0, full=True,
                                    printer=_quiet)
            build_stamp.stamp_start(printer=_quiet)
            text = (spot / build_stamp.FILE_NAME).read_text(encoding="utf-8")
            left = [line for line in text.splitlines()
                    if "настоящий дом" in line]
            assert left == [], "после старта остались строки-заглушки: " + str(len(left))


# -- Пять опор шага 33 на месте -------------------------------------

def test_step_33_leaves_its_tools_in_place():
    # Дешёвый сторож против тихого удаления при будущей уборке.
    for name in PILLARS:
        target = ROOT / name
        assert target.exists(), "опора шага 33 пропала: " + name
    assert callable(getattr(state_version, "collect", None)), "отчёт о состоянии больше не собирается"
    assert callable(getattr(state_snapshot, "create", None)), "снимки больше не снимаются"
    assert callable(getattr(rollback_state, "restore", None)), "откат больше не вызывается"
    assert callable(getattr(build_stamp, "stamp_tests", None)), "метку сборки больше не пишут"


if __name__ == "__main__":
    passed = 0
    failed = 0
    for name in sorted(globals()):
        if not name.startswith("test_"):
            continue
        func = globals()[name]
        try:
            func()
            passed += 1
            print("OK   " + name)
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("итог: " + str(passed) + " зелёных, " + str(failed) + " красных")
    sys.exit(1 if failed else 0)
