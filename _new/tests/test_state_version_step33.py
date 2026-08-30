# tests/test_state_version_step33.py
"""Шаг 33.1 — сторожа над единственной версией состояния (Р6).

Каждый тест стережёт одну беду, которая уже случалась или гарантированно
случится. Всё работает на временном доме через JARVIS_STATE_DIR:
ни один тест не имеет права тронуть ~/.jarvis владельца.

Запуск: python -m pytest -q  (из корня) или python tests/test_state_version_step33.py
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import safe_json, state_version  # noqa: E402


class _Home:
    """Временный дом: выставляет JARVIS_STATE_DIR и чисто снимает его."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="jv_state_test_")
        self._old = os.environ.get(safe_json.STATE_DIR_ENV)
        os.environ[safe_json.STATE_DIR_ENV] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc) -> None:
        if self._old is None:
            os.environ.pop(safe_json.STATE_DIR_ENV, None)
        else:
            os.environ[safe_json.STATE_DIR_ENV] = self._old
        self._tmp.cleanup()


def _make_db(target: Path, user_version: int) -> None:
    """Создать базу с заданной версией схемы — без миграций проекта."""
    conn = sqlite3.connect(str(target))
    try:
        conn.execute(f"PRAGMA user_version = {int(user_version)}")
        conn.commit()
    finally:
        conn.close()


# -- Где живёт файл ----------------------------------------------------

def test_state_lives_in_the_home_not_in_the_project():
    """STATE.json лежит в доме. Иначе распаковка архива стирает то самое
    знание, ради которого всё затевалось."""
    with _Home() as home:
        target = state_version.path()
        assert target == home / "STATE.json", target
        assert str(ROOT) not in str(target)


def test_path_is_resolved_at_call_time():
    """Путь — функция, а не константа при импорте (грабли шага 31)."""
    with _Home() as first:
        one = state_version.path()
    with _Home() as second:
        two = state_version.path()
    assert one != two
    assert one.parent == first and two.parent == second


# -- Сбор состояния --------------------------------------------------

def test_collect_covers_all_stores():
    """Версия состояния обязана знать ВСЕ хранилища, а не только базу:
    откатишь одно — остальные останутся из будущего (Х-A1, Х-J5)."""
    with _Home():
        state = state_version.collect()
        for key in ("jarvis_db", "history_db", "settings", "memory_json",
                    "personality", "gate_audit", "reminders_json"):
            assert key in state["stores"], key
        assert state["schema_ver"] == state_version.SCHEMA_VER
        assert state["code_ver"] == state_version.CODE_VER
        assert state["last_run"]["path"] == str(state_version.project_dir())


def test_absent_history_db_is_a_normal_state():
    """history.db физически нет до фазы 7. Первый же откат не должен
    падать на «файл не найден» (Х-U3)."""
    with _Home():
        history = state_version.collect()["stores"]["history_db"]
        assert history["present"] is False
        assert history["user_version"] is None


def test_collect_never_creates_a_database():
    """Сбор состояния только смотрит. sqlite3.connect создаёт файл молча —
    если забыть проверку exists(), doctor сам создаст пустую history.db
    и все решат, что фаза 7 уже началась."""
    with _Home() as home:
        state_version.collect()
        assert not (home / "jarvis.db").exists()
        assert not (home / "history.db").exists()


def test_db_version_is_read_without_migrating():
    """Версия базы читается как есть — включая версию из будущего.
    Если читать через open_store(), он бросит StoreError — и диагностика
    умрёт ровно там, где нужна больше всего."""
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        assert state_version.db_user_version(home / "jarvis.db") == 6
        _make_db(home / "history.db", 99)
        assert state_version.db_user_version(home / "history.db") == 99


def test_code_knows_its_own_migration_ceiling():
    """Потолок берётся из store.py, а не из числа в документе: в коде
    сейчас шесть миграций, а не 18, как говорил черновик плана."""
    knows = state_version.code_migrations()
    assert knows["jarvis_db"] >= 6
    assert knows["history_db"] >= 1


def test_json_store_without_version_reads_as_one():
    """У настроек и памяти номера версии сегодня нет. Отсутствие номера
    должно читаться как 1, иначе старт после шага 33 будет ругаться
    на совершенно здоровые файлы."""
    with _Home() as home:
        (home / "settings.json").write_text('{"timezone": "Europe/Moscow"}',
                                            encoding="utf-8")
        item = state_version.collect()["stores"]["settings"]
        assert item["present"] is True
        assert item["ver"] == 1


def test_unreadable_json_is_reported_not_hidden():
    """Битый файл настроек — повод сказать об этом, а не молча
    считать его версией 1."""
    with _Home() as home:
        (home / "personality.json").write_text("{не json", encoding="utf-8")
        state = state_version.collect()
        assert state["stores"]["personality"]["readable"] is False
        assert any("Личность" in line for line in state_version.problems(state))


# -- Запись и чтение --------------------------------------------------

def test_write_then_load_roundtrip():
    """Записали — прочитали то же самое."""
    with _Home():
        written = state_version.write()
        loaded, report = state_version.load()
        assert report["source"] == "primary"
        assert loaded["code_ver"] == written["code_ver"]
        assert loaded["stores"]["jarvis_db"]["present"] is False


def test_missing_state_is_a_normal_state():
    """Первый запуск после шага 33: файла нет, и это не авария."""
    with _Home():
        data, report = state_version.load()
        assert data == {}
        assert report["source"] == "missing"
        assert state_version.problems() == []


def test_corrupt_state_is_quarantined_not_overwritten():
    """Битый STATE.json уезжает в карантин, а не затирается пустотой:
    в нём могут лежать единственные адреса снимков."""
    with _Home() as home:
        target = home / "STATE.json"
        target.write_text("{это не json", encoding="utf-8")
        data, report = state_version.load()
        assert data == {}
        assert report["quarantined"], report
        kept = list(home.glob("STATE.json.corrupt-*"))
        assert kept, "повреждённый файл не сохранён"
        assert "это не json" in kept[0].read_text(encoding="utf-8")


def test_state_file_has_no_cr():
    """Файл пишется с LF внутри данных. Ровно на этом шаг 32 поймал
    двухмесячный дефект журнала двери."""
    with _Home():
        state_version.write()
        raw = state_version.path().read_bytes()
        assert b"\r" not in raw


def test_previous_path_is_remembered():
    """Запуск из другой папки должен быть виден сразу (Х-J5):
    у владельца на диске лежат две папки с Джарвисом."""
    with _Home() as home:
        state_version.write()
        data = json.loads((home / "STATE.json").read_text(encoding="utf-8"))
        data["last_run"]["path"] = r"C:\\Старая\\папка"
        (home / "STATE.json").write_text(json.dumps(data, ensure_ascii=False),
                                         encoding="utf-8")
        note = state_version.path_changed()
        assert note and "Старая" in note
        again = state_version.write()
        assert again["last_run"]["previous_path"] == r"C:\\Старая\\папка"


def test_snapshots_list_survives_a_rewrite():
    """Список снимков — единственное, что нельзя пересобрать из
    реальности. Потерять его = потерять адреса бэкапов."""
    with _Home() as home:
        state_version.write()
        data = json.loads((home / "STATE.json").read_text(encoding="utf-8"))
        data["snapshots"] = [{"id": "2026-08-14_pre33", "kind": "phase"}]
        (home / "STATE.json").write_text(json.dumps(data, ensure_ascii=False),
                                         encoding="utf-8")
        again = state_version.write()
        assert again["snapshots"] == [{"id": "2026-08-14_pre33",
                                      "kind": "phase"}]


# -- Вердикт -------------------------------------------------------------

def test_problems_flags_a_database_from_the_future():
    """Главный смысл шага: распаковал старый архив — сказали об этом
    человеческими словами, а не трассой стека и не тишиной."""
    with _Home() as home:
        _make_db(home / "jarvis.db", 999)
        lines = state_version.problems()
        assert lines, "база из будущего прошла незамеченной"
        assert any("999" in line and "jarvis.db" in line for line in lines)


def test_problems_are_silent_when_all_is_consistent():
    """Ложная тревога хуже молчания: владелец перестанет читать
    предупреждения."""
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        (home / "settings.json").write_text("{}", encoding="utf-8")
        assert state_version.problems() == []


def test_gate_audit_is_mentioned_but_never_rolled_back():
    """Журнал двери — свидетельство, а не состояние. Откат, стирающий
    записи о том, зачем откатывались, — бесполезный откат."""
    with _Home():
        audit = state_version.collect()["stores"]["gate_audit"]
        assert audit["under_rollback"] is False
        assert audit["schema_ver"] == 1


def test_reminders_now_live_in_the_db_and_the_old_file_is_only_a_leftover():
    """Блок 10 фазы 1 переселил напоминания из папки сборки в mx_reminder.

    ЭТОТ СТОРОЖ РАНЬШЕ ЗАКРЕПЛЯЛ ОБРАТНОЕ («location == 'build'»), и он
    покраснел ровно тогда, когда должен был — на переезде. Не удаляю его, а
    переписываю на новую правду: смысл у него тот же, что был, — «откат не
    имеет права врать, будто вернул всё». Просто теперь правда другая, и
    напоминания под откатом ЕСТЬ, потому что снимок копирует jarvis.db.

    Старый файл остаётся лежать в папке сборки нетронутым: Р-6 требует
    дословно «старый файл сохраняется как есть». Поэтому про него по-прежнему
    сказано вслух — но как про ОСТАТОК, а не как про место хранения.
    """
    with _Home():
        stores = state_version.collect()["stores"]

        live = stores["reminders"]
        assert live["location"] == "db"
        assert live["table"] == "mx_reminder"
        assert live["under_rollback"] is True, (
            "напоминания в базе, а сказано, что откат их не вернёт")

        leftover = stores["reminders_json"]
        assert leftover["location"] == "legacy_leftover", (
            "старый файл снова назван местом хранения — переезд забыт")
        assert leftover["under_rollback"] is False


# -- Правила проекта ---------------------------------------------------

def test_one_writer_only():
    """STATE.json упоминается только в core/state_version.py. Файл, который
    пишут из трёх мест, рано или поздно врёт — а ему верят."""
    guilty = []
    for folder in ("core", "agent", "tools", "config", "memory"):
        root = ROOT / folder
        if not root.exists():
            continue
        for file in root.rglob("*.py"):
            if file.name == "state_version.py":
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "STATE.json" in text:
                guilty.append(str(file.relative_to(ROOT)))
    assert not guilty, f"STATE.json упоминают лишние файлы: {guilty}"


def test_test_run_never_touches_the_real_home():
    """Предохранитель: тест, забывший перенаправить дом, пишет во временную
    папку, а не в ~/.jarvis владельца."""
    if "pytest" not in sys.modules:
        return  # проверяем только под pytest — там это имеет смысл
    saved = os.environ.pop(safe_json.STATE_DIR_ENV, None)
    try:
        target = state_version.path()
        assert Path.home() / ".jarvis" not in target.parents
        assert "jv_state_" in str(target)
    finally:
        if saved is not None:
            os.environ[safe_json.STATE_DIR_ENV] = saved


if __name__ == "__main__":
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    for name, fn in tests:
        fn()
        print(f"OK   {name}")
    print(f"OK: {len(tests)} passed (standalone)")
