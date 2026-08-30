# Тесты шага 33.3: снимки состояния дома (план Р6, пункт 4).
#
# Каждый тест — один сценарий из исследования: места нет, копия битая,
# антивирус держит папку, часы прыгнули назад, в папке лежит чужое.
# Дом всегда временный: настоящий ~/.jarvis тесты не трогают.
#
# В наборе: python -m pytest -q из корня.
# Без pytest: python tests/test_state_snapshot_step33.py

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import state_snapshot, state_version  # noqa: E402


# -- Снасти -----------------------------------------------------------------

class _Home:
    # Дом на выброс. Та же схема, что в тестах 33.1 и 33.2.

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jv_snap_"))
        self.old = os.environ.get("JARVIS_STATE_DIR")
        os.environ["JARVIS_STATE_DIR"] = str(self.dir)
        return self.dir

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


class _Log:
    # Собиратель строк вместо print: тесты не шумят в консоль.

    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(str(line))

    @property
    def text(self):
        return " | ".join(self.lines)


class _Patched:
    # Подмена одного имени в модуле на время одного теста.

    def __init__(self, module, name, value):
        self.module = module
        self.name = name
        self.value = value

    def __enter__(self):
        self.old = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.old)
        return False


def _make_db(target, version=6, rows=2):
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO t (x) VALUES (?)", ("row" + str(i),))
        conn.execute("PRAGMA user_version = " + str(int(version)))
        conn.commit()
    finally:
        conn.close()


def _db_version(target):
    conn = sqlite3.connect(str(target))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _rows(target):
    conn = sqlite3.connect(str(target))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0])
    finally:
        conn.close()


def _registry():
    data, _ = state_version.load()
    items = data.get("snapshots")
    return items if isinstance(items, list) else []


def _dirs():
    root = state_snapshot.dir_path()
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _newest():
    return state_snapshot.list_snapshots()[-1]


# -- Что попадает в снимок ------------------------------------------------

def test_snapshot_covers_both_databases():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6, rows=3)
        _make_db(home / "history.db", 1, rows=1)
        entry = state_snapshot.create("auto", printer=_Log())
        assert entry is not None, "снимок не сделан"
        folder = Path(_newest()["path"])
        assert (folder / "jarvis.db").exists(), "главной базы в снимке нет"
        assert (folder / "history.db").exists(), "второй базы в снимке нет"
        assert _rows(folder / "jarvis.db") == 3, "строки не доехали"
        assert _db_version(folder / "jarvis.db") == 6


def test_snapshot_skips_absent_stores():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        entry = state_snapshot.create("auto", printer=_Log())
        assert entry is not None
        assert not (home / "history.db").exists(), "создали в доме пустую базу"
        assert not (home / "settings.json").exists(), "создали пустые настройки"
        folder = Path(_newest()["path"])
        assert not (folder / "history.db").exists(), "в снимке пустышка"


def test_snapshot_never_contains_wal_or_shm():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        state_snapshot.create("auto", printer=_Log())
        folder = Path(_newest()["path"])
        names = [p.name for p in folder.iterdir()]
        bad = [n for n in names if n.endswith("-wal") or n.endswith("-shm")]
        assert not bad, "в снимке служебные файлы WAL: " + str(bad)


def test_snapshot_does_not_change_the_source_schema():
    with _Home() as home:
        _make_db(home / "jarvis.db", 3)
        state_snapshot.create("auto", printer=_Log())
        assert _db_version(home / "jarvis.db") == 3, "снимок правил схему источника"
        folder = Path(_newest()["path"])
        assert _db_version(folder / "jarvis.db") == 3, "в копии другая версия"


def test_snapshot_works_on_a_database_from_the_future():
    with _Home() as home:
        _make_db(home / "jarvis.db", 41)
        log = _Log()
        entry = state_snapshot.create("pre_rollback", printer=log)
        assert entry is not None, "снимок базы из будущего не сделан: " + log.text
        folder = Path(_newest()["path"])
        assert _db_version(folder / "jarvis.db") == 41


def test_json_stores_are_copied_verbatim():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        payload = {"кто": "владелец", "что": ["факт один", "факт два"]}
        (home / "long_term.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        state_snapshot.create("auto", printer=_Log())
        folder = Path(_newest()["path"])
        copied = json.loads((folder / "long_term.json").read_text(encoding="utf-8"))
        assert copied == payload, "память скопирована с искажением"


# -- Манифест --------------------------------------------------------------

def test_manifest_says_what_it_did_not_save():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        state_snapshot.create("auto", printer=_Log())
        folder = Path(_newest()["path"])
        text = (folder / "snapshot.json").read_text(encoding="utf-8")
        for word in ("gate-audit", "staging", "api_usage", "reminders.json"):
            assert word in text, "в манифесте не сказано про " + word
        assert "rebuild_fts" in text, "нет предупреждения про указатели поиска"


def test_manifest_lists_absent_stores():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        state_snapshot.create("auto", printer=_Log())
        manifest = _newest()
        absent = manifest.get("absent") or []
        for name in ("history.db", "settings.json", "personality.json"):
            assert name in absent, "отсутствие не записано: " + name
        assert manifest.get("quick_check", {}).get("jarvis.db") == "ok"
        assert manifest.get("schema_ver") == 1
        assert manifest.get("code_ver") == state_version.CODE_VER


# -- Отказы и аварии ---------------------------------------------------

def test_snapshot_refuses_when_disk_is_low():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        log = _Log()

        def low(target):
            return state_snapshot.MIN_FREE_BYTES + 1000

        with _Patched(state_snapshot, "_free_bytes", low):
            entry = state_snapshot.create("auto", printer=log)
        assert entry is None, "снимок сделан, хотя места не было"
        assert "отказ" in log.text, "отказ не объявлен: " + log.text
        assert state_snapshot.list_snapshots() == []


def test_no_half_snapshot_survives_a_failure():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        _make_db(home / "history.db", 1)
        log = _Log()

        def boom(src, dest):
            raise OSError("на диске не осталось места")

        with _Patched(state_snapshot, "_copy_db", boom):
            entry = state_snapshot.create("auto", printer=log)
        assert entry is None
        assert _dirs() == [], "остался мусор от полуснимка: " + str(_dirs())
        assert _registry() == [], "полуснимок попал в реестр"
        assert "дом не тронут" in log.text, "отказ не объяснён: " + log.text


def test_snapshot_survives_a_busy_folder():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        real = state_snapshot._replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("папка занята антивирусом")
            real(src, dst)

        log = _Log()
        with _Patched(state_snapshot, "_replace", flaky):
            with _Patched(state_snapshot, "REPLACE_PAUSE", 0):
                entry = state_snapshot.create("auto", printer=log)
        assert entry is not None, "трёх попыток не хватило: " + log.text
        assert calls["n"] == 3, "повторы не работают"


def test_broken_copy_is_not_registered():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        log = _Log()

        def bad(target):
            return "*** in database main"

        with _Patched(state_snapshot, "_quick_check", bad):
            entry = state_snapshot.create("auto", printer=log)
        assert entry is None, "битая копия выдана за годную"
        assert state_snapshot.list_snapshots() == []
        assert _registry() == []
        assert any(n.startswith(".broken-") for n in _dirs()), (
            "улика не сохранена: " + str(_dirs()))
        assert "целостности" in log.text


def test_failure_never_raises_into_start():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        log = _Log()

        def explode(home_dir):
            raise RuntimeError("снимок взорвался")

        with _Patched(state_snapshot, "_sources", explode):
            entry = state_snapshot.create("auto", printer=log)
        assert entry is None
        assert "пропущен" in log.text, "отказ молчаливый: " + log.text


# -- Реестр и один писатель -------------------------------------------

def test_snapshot_module_is_not_a_second_writer_of_state():
    src = (ROOT / "core" / "state_snapshot.py").read_text(encoding="utf-8")
    assert state_version.FILE_NAME not in src, "модуль снимков знает имя файла состояния"
    assert "atomic_write_json" not in src, "модуль снимков пишет состояние сам"
    assert "open_store" not in src, "снимок открывает базу рабочим путём"
    assert "migrate(" not in src, "снимок правит схему — это не страховка"


def test_snapshot_is_registered_in_state():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        entry = state_snapshot.create("auto", printer=_Log())
        ids = [str(x.get("id")) for x in _registry()]
        assert entry["id"] in ids, "снимка нет в реестре: " + str(ids)
        record = _registry()[-1]
        assert record.get("bytes", 0) > 0
        assert record.get("files", 0) >= 1
        assert "absent" not in record, "в реестре лишние подробности"


def test_registry_is_capped():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        limit = state_version.REGISTRY_LIMIT
        for i in range(limit + 5):
            state_version.record_snapshot({"id": "snap" + str(i), "seq": i})
        items = _registry()
        assert len(items) == limit, "реестр растёт без границ: " + str(len(items))
        assert items[-1]["id"] == "snap" + str(limit + 4), "обрезали не тот конец"


# -- Ротация -----------------------------------------------------------------

def test_rotation_keeps_three_auto():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        for _ in range(5):
            state_snapshot.create("auto", printer=_Log())
        known = state_snapshot.list_snapshots()
        assert len(known) == state_snapshot.KEEP_AUTO, "снимков " + str(len(known))
        assert [d["seq"] for d in known] == [3, 4, 5], "удалили не самые старые"
        assert len(_registry()) == state_snapshot.KEEP_AUTO, "реестр помнит удалённые"


def test_rotation_never_touches_phase_or_unknown():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        phase = state_snapshot.ensure_phase_snapshot(printer=_Log())
        assert phase is not None
        stranger = state_snapshot.dir_path() / "чужая-папка"
        stranger.mkdir(parents=True)
        (stranger / "важное.txt").write_text("не трогать", encoding="utf-8")
        for _ in range(5):
            state_snapshot.create("auto", printer=_Log())
        names = _dirs()
        assert phase["id"] in names, "фазовый снимок унесла ротация"
        assert "чужая-папка" in names, "автоматика унесла чужое"
        assert (stranger / "важное.txt").exists()
        assert str(stranger) in state_snapshot.unknown_dirs()


def test_rotation_counts_by_number_not_by_clock():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        first = state_snapshot.create("auto", printer=_Log())

        def back():
            return "20200101T000000Z"

        with _Patched(state_snapshot, "_id_stamp", back):
            later = [state_snapshot.create("auto", printer=_Log()) for _ in range(3)]
        names = _dirs()
        assert first["id"] not in names, "самый старый по номеру не удалён"
        for entry in later:
            assert entry is not None, "снимок с прошлым временем не сделался"
            assert entry["id"] in names, "ротация унесла свежий из-за часов"


# -- Когда снимать ---------------------------------------------------------

def test_phase_snapshot_is_made_once():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        first = state_snapshot.ensure_phase_snapshot(printer=_Log())
        second = state_snapshot.ensure_phase_snapshot(printer=_Log())
        assert first is not None
        assert second is None, "фазовый снимок сделали дважды"
        phases = [d for d in state_snapshot.list_snapshots() if d["kind"] == "phase"]
        assert len(phases) == 1
        assert phases[0]["keep"] is True, "фазовый снимок не защищён от ротации"


def test_auto_snapshot_is_not_taken_twice_in_a_row():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        assert state_snapshot.due() is True, "первый снимок не нужен?"
        state_snapshot.create("auto", printer=_Log())
        assert state_snapshot.due() is False, "снимок на каждый запуск — глубина потеряна"
        with _Patched(state_snapshot, "AUTO_EVERY_SECONDS", 0):
            assert state_snapshot.due() is True, "срок не работает"


def test_new_code_version_forces_a_snapshot():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        state_snapshot.create("auto", printer=_Log())
        assert state_snapshot.due() is False
        with _Patched(state_version, "CODE_VER", "9.99"):
            assert state_snapshot.due() is True, "новая версия кода не требует снимка"


def test_temp_leftovers_are_cleaned_only_when_stale():
    with _Home():
        root = state_snapshot.dir_path()
        root.mkdir(parents=True, exist_ok=True)
        fresh = root / ".tmp-свежая"
        stale = root / ".tmp-старая"
        fresh.mkdir()
        stale.mkdir()
        old = time.time() - state_snapshot.TMP_STALE_SECONDS - 60
        os.utime(stale, (old, old))
        removed = state_snapshot.cleanup_temp(printer=_Log())
        assert stale.name in removed, "старый мусор не убран"
        assert fresh.exists(), "убрали работу, которая идёт сейчас"


def test_report_counts_snapshots():
    with _Home() as home:
        _make_db(home / "jarvis.db", 6)
        state_snapshot.create("auto", printer=_Log())
        data = state_snapshot.report()
        assert data["count"] == 1
        assert data["newest_id"], "в сводке нет имени свежего снимка"
        assert data["bytes"] > 0
        assert data["unknown_dirs"] == []


# -- Связь с запуском и безопасность тестов -------------------------

def test_main_takes_a_snapshot_after_recording_the_start():
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "state_snapshot" in text, "снимок не подключён к запуску"
    assert "ensure_phase_snapshot" in text, "фазовый снимок не делается при старте"
    assert text.index("state_guard.record_start()") < text.index("state_snapshot"), (
        "снимок делается раньше проверки версий")
    assert "снимок состояния пропущен" in text, "у снимка нет своей строки отказа"


def test_test_run_never_touches_the_real_home():
    # В Windows временная папка лежит внутри папки пользователя, поэтому
    # сравнивать надо с самим ~/.jarvis, а не с папкой пользователя.
    real = Path.home() / ".jarvis"
    with _Home() as home:
        target = state_snapshot.dir_path()
        assert str(target).startswith(str(home)), "снимок ушёл не во временный дом"
        assert str(real) not in str(target), (
            "снимок метит в настоящий дом владельца")


# -- Запуск без pytest ----------------------------------------------------

if __name__ == "__main__":
    passed = 0
    failed = 0
    for name, func in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            passed += 1
            print("OK   " + name)
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("итог: " + str(passed) + " зелёных, " + str(failed) + " красных")
    sys.exit(1 if failed else 0)
