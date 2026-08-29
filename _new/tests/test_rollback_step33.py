# -*- coding: utf-8 -*-
# Тесты шага 33.5: откат данных на снимок (план Р6, пункт 3).
#
# Каждый тест — один вечер, который может кончиться плохо: Jarvis
# забыли закрыть, снимок битый, замена умерла на втором файле,
# в снимке схема новее кода. Дом всегда временный.
#
# В наборе: python -m pytest -q из корня.
# Без pytest: python tests/test_rollback_step33.py

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import audit_log, instance_lock, state_snapshot, state_version  # noqa: E402
from tools import rollback_state  # noqa: E402


# -- Снасти -----------------------------------------------------------------

class _Home:
    # Дом на выброс. Та же схема, что в тестах 33.1, 33.2 и 33.3.

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jv_rb_"))
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


class _Log:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(str(line))

    @property
    def text(self):
        return " | ".join(self.lines)


class _Patched:
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


def _make_db(target, version=6, facts=2, mark="старое"):
    # База с таблицей фактов: именно её считает отчёт о цене отката.
    # Начинаем с чистого файла: иначе второй вызов допишет строки к
    # старым, и тест будет считать не то, что думает.
    for extra in ("", "-wal", "-shm"):
        try:
            Path(str(target) + extra).unlink(missing_ok=True)
        except OSError:
            pass
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS memory_fact (text TEXT)")
        for i in range(facts):
            conn.execute("INSERT INTO memory_fact (text) VALUES (?)",
                         (mark + str(i),))
        conn.execute("PRAGMA user_version = " + str(int(version)))
        conn.commit()
    finally:
        conn.close()


def _facts(target):
    if not Path(target).exists():
        return None
    conn = sqlite3.connect(str(target))
    try:
        return int(conn.execute("SELECT count(*) FROM memory_fact").fetchone()[0])
    finally:
        conn.close()


def _db_version(target):
    conn = sqlite3.connect(str(target))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _seed(home, *, facts=2, version=6, settings="тише"):
    _make_db(home / "jarvis.db", version=version, facts=facts)
    _make_db(home / "history.db", version=1, facts=1)
    (home / "settings.json").write_text(
        json.dumps({"schema_ver": 1, "voice": settings}, ensure_ascii=False),
        encoding="utf-8", newline="\n")
    (home / "long_term.json").write_text(
        json.dumps({"schema_ver": 1, "notes": ["было"]}, ensure_ascii=False),
        encoding="utf-8", newline="\n")
    (home / "personality.json").write_text(
        json.dumps({"schema_ver": 1, "tone": "сухой"}, ensure_ascii=False),
        encoding="utf-8", newline="\n")


def _snapshot(kind="auto"):
    return state_snapshot.create(kind, reason="тест", printer=_Log())


def _entry():
    known = rollback_state.items()
    return known[-1] if known else None


def _journal_records(event="state_rollback"):
    target = audit_log.path()
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except Exception:
            continue
        if data.get("event") == event:
            out.append(data)
    return out


def _temps(home):
    return sorted(p.name for p in Path(home).glob(rollback_state.TMP_PREFIX + "*"))


# -- Где лежит и что обещано -------------------------------------------

def test_tool_lies_where_the_guard_promises_it():
    # Сторож с шага 33.2 называет ровно этот адрес.
    from core import state_guard
    expected = state_version.project_dir().joinpath(*state_guard.ROLLBACK_SCRIPT)
    assert expected.exists(), "инструмент лежит не там, где обещан"


def test_guard_now_names_the_tool_in_a_refusal():
    from core import state_guard
    hint = state_guard.rollback_hint()
    assert hint and "rollback_state.py" in hint


def test_rollback_restores_both_dbs():
    # Имя теста взято из плана дословно.
    with _Home() as home:
        _seed(home, facts=2)
        assert _snapshot() is not None
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        _make_db(home / "history.db", version=1, facts=7, mark="новое")
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"], report["refused"]
        assert "jarvis.db" in report["restored"]
        assert "history.db" in report["restored"]
        assert _facts(home / "jarvis.db") == 2
        assert _facts(home / "history.db") == 1


def test_settings_memory_and_personality_come_back_too():
    with _Home() as home:
        _seed(home, settings="тише")
        _snapshot()
        (home / "settings.json").write_text(
            json.dumps({"schema_ver": 1, "voice": "громче"}, ensure_ascii=False),
            encoding="utf-8", newline="\n")
        (home / "long_term.json").write_text(
            json.dumps({"schema_ver": 1, "notes": ["стало"]}, ensure_ascii=False),
            encoding="utf-8", newline="\n")
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"], report["refused"]
        settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
        memory = json.loads((home / "long_term.json").read_text(encoding="utf-8"))
        assert settings["voice"] == "тише"
        assert memory["notes"] == ["было"]


def test_without_the_word_nothing_happens():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        log = _Log()
        report = rollback_state.restore(_entry(), word="да", printer=log)
        assert report["ok"] is False
        assert rollback_state.WORD in report["refused"]
        assert _facts(home / "jarvis.db") == 9, "базу тронули без согласия"
        assert "отказ" in log.text


def test_running_jarvis_blocks_the_rollback():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        instance_lock.acquire()
        try:
            report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                            printer=_Log())
        finally:
            instance_lock.release()
        assert report["ok"] is False
        assert "Закройте Jarvis" in report["refused"]
        assert _facts(home / "jarvis.db") == 9


def test_a_snapshot_is_taken_before_the_rollback():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=_Log())
        assert report["pre_rollback"], "снимка перед откатом нет"
        saved = [e for e in rollback_state.items()
                 if e.get("id") == report["pre_rollback"]]
        assert len(saved) == 1
        assert saved[0]["kind"] == state_snapshot.KIND_PRE_ROLLBACK
        assert saved[0]["keep"] is True, "такой снимок ротация стереть не вправе"
        assert _facts(Path(saved[0]["path"]) / "jarvis.db") == 9


def test_no_snapshot_means_no_rollback():
    # Откат без возможности отмены — хуже, чем отсутствие отката.
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        entry = _entry()
        with _Patched(state_snapshot, "create", lambda *a, **k: None):
            report = rollback_state.restore(entry, word=rollback_state.WORD,
                                            printer=_Log())
        assert report["ok"] is False
        assert "отменить" in report["refused"]
        assert _facts(home / "jarvis.db") == 9


def test_rolling_forward_is_refused():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        entry = _entry()
        entry["stores"]["jarvis_db"]["user_version"] = 99
        report = rollback_state.restore(entry, word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"] is False
        assert "будущее" in report["refused"]


def test_a_broken_snapshot_is_refused():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        entry = _entry()
        entry["quick_check"]["jarvis.db"] = "битая страница"
        report = rollback_state.restore(entry, word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"] is False
        assert "битый" in report["refused"]
        assert _facts(home / "jarvis.db") == 9


def test_a_missing_snapshot_folder_is_refused():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        entry = _entry()
        shutil.rmtree(entry["path"], ignore_errors=True)
        report = rollback_state.restore(entry, word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"] is False
        assert "папки снимка" in report["refused"]


def test_unknown_target_is_not_found():
    with _Home() as home:
        _seed(home)
        _snapshot()
        assert rollback_state.pick("такого-нет") is None
        assert rollback_state.pick("") is None


def test_number_and_tail_both_find_the_snapshot():
    with _Home() as home:
        _seed(home)
        _snapshot()
        entry = _entry()
        ident = str(entry["id"])
        assert rollback_state.pick(ident)["id"] == ident
        assert rollback_state.pick(ident[-4:])["id"] == ident
        assert rollback_state.pick(str(entry["seq"]))["id"] == ident


def test_orphan_wal_and_shm_are_removed():
    # Старая база рядом с новым журналом WAL — это база, которая
    # при первом открытии дочитает правки, от которых мы ушли.
    #
    # Факт, который нашёл этот тест: SQLite сам убирает -wal и -shm,
    # когда закрывается последнее соединение, а снимок перед откатом
    # как раз открывает базу. Значит осиротевшие файлы встречаются после
    # аварийного завершения — туда их и кладём.
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        real_create = state_snapshot.create

        def create_then_leave_junk(*a, **k):
            made = real_create(*a, **k)
            (home / "jarvis.db-wal").write_bytes(b"junk")
            (home / "jarvis.db-shm").write_bytes(b"junk")
            return made

        with _Patched(state_snapshot, "create", create_then_leave_junk):
            report = rollback_state.restore(_entry(),
                                            word=rollback_state.WORD,
                                            printer=_Log())
        assert report["ok"], report["refused"]
        assert "jarvis.db-wal" in report["side_removed"]
        assert "jarvis.db-shm" in report["side_removed"]
        # После уборки мы сами открываем базу для пересборки поиска,
        # и SQLite может сделать свежие -wal и -shm. Они уже свои,
        # опасен был старый журнал — его больше нет.
        for extra in ("-wal", "-shm"):
            side = home / ("jarvis.db" + extra)
            if side.exists():
                assert side.read_bytes()[:4] != b"junk", extra


def test_state_file_is_rewritten_not_restored():
    # Если вернуть старый файл состояния, реестр забудет снимок,
    # снятый перед откатом, и отмена отката станет невидимой.
    with _Home() as home:
        _seed(home, facts=2)
        state_version.write()
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"], report["refused"]
        assert report["state"] == "переписан"
        assert state_version.FILE_NAME not in report["restored"]
        data, _report = state_version.load()
        ids = [str(item.get("id")) for item in (data.get("snapshots") or [])]
        assert report["pre_rollback"] in ids, "реестр забыл снимок отката"


def test_the_journal_gets_exactly_one_record():
    with _Home() as home:
        audit_log.reset()
        _seed(home, facts=2)
        _snapshot()
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=_Log())
        records = _journal_records()
        assert len(records) == 1, records
        assert records[0]["verdict"] == "done"
        assert records[0]["to"] == report["id"]
        assert records[0]["pre_rollback"] == report["pre_rollback"]
        assert records[0]["schema_ver"] == audit_log.SCHEMA_VER


def test_a_refusal_is_written_to_the_journal_too():
    with _Home() as home:
        audit_log.reset()
        _seed(home, facts=2)
        _snapshot()
        rollback_state.restore(_entry(), word="да", printer=_Log())
        records = _journal_records()
        assert len(records) == 1
        assert records[0]["verdict"] == "refused"
        assert records[0]["restored"] == []


def test_a_half_rollback_is_loud_and_names_the_way_back():
    with _Home() as home:
        audit_log.reset()
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")

        def boom(src, dst):
            if Path(dst).name == "history.db":
                raise OSError("держит антивирус")
            os.replace(src, dst)

        log = _Log()
        with _Patched(rollback_state, "_replace", boom):
            report = rollback_state.restore(_entry(),
                                            word=rollback_state.WORD,
                                            printer=log)
        assert report["ok"] is False
        assert report["half"] is True
        assert "полуоткат" in report["refused"]
        assert "jarvis.db" in report["restored"]
        assert "ОПАСНО" in log.text
        assert str(report["pre_rollback"]) in log.text, "не сказан путь обратно"
        assert _temps(home) == []
        records = _journal_records()
        assert len(records) == 1 and records[0]["verdict"] == "half"


def test_no_temp_files_are_left_after_success():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        rollback_state.restore(_entry(), word=rollback_state.WORD,
                               printer=_Log())
        assert _temps(home) == []


def test_no_temp_files_are_left_after_a_refusal():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        with _Patched(rollback_state, "_quick_check",
                      lambda target: "битая копия"):
            report = rollback_state.restore(_entry(),
                                            word=rollback_state.WORD,
                                            printer=_Log())
        assert report["ok"] is False
        assert "копии не готовы" in report["refused"]
        assert _temps(home) == []
        assert _facts(home / "jarvis.db") == 9, "дом тронули после отказа"


def test_describe_names_the_price_of_the_rollback():
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        _make_db(home / "jarvis.db", version=6, facts=9, mark="новое")
        text = " | ".join(rollback_state.describe(_entry()))
        assert "jarvis.db" in text
        assert "сейчас 9" in text and "будет 2" in text
        assert "gate-audit.jsonl" in text and "reminders.json" in text
        assert state_version.FILE_NAME in text


def test_a_file_absent_in_the_snapshot_is_named_not_invented():
    with _Home() as home:
        _make_db(home / "jarvis.db", version=6, facts=2)
        _snapshot()
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=_Log())
        assert report["ok"], report["refused"]
        assert "settings.json" in report["absent_in_snapshot"]
        assert not (home / "settings.json").exists(), "пустой файл выдуман"


def test_search_index_failure_is_told_not_hidden():
    # В тестовой базе нет таблиц-указателей, значит пересборка не
    # пройдёт. Откат от этого не отменяется, но и молчать нельзя.
    with _Home() as home:
        _seed(home, facts=2)
        _snapshot()
        log = _Log()
        report = rollback_state.restore(_entry(), word=rollback_state.WORD,
                                        printer=log)
        assert report["ok"], report["refused"]
        assert report["fts"] != "пересобран"
        assert "поиск по памяти" in log.text


def test_the_journal_itself_is_never_restored():
    with _Home() as home:
        audit_log.reset()
        _seed(home, facts=2)
        audit_log.append({"event": "проверка"})
        _snapshot()
        rollback_state.restore(_entry(), word=rollback_state.WORD,
                               printer=_Log())
        assert len(_journal_records("проверка")) == 1, "журнал откатили"
        assert len(_journal_records()) == 1


def test_the_real_home_is_never_touched():
    with _Home() as home:
        real = Path.home() / ".jarvis"
        assert rollback_state._home() == home
        assert Path(home) != real
        assert str(real) not in str(home)
        assert str(audit_log.path()).startswith(str(home))


def test_restore_never_raises_on_nonsense():
    with _Home() as home:
        _seed(home)
        for junk in (None, {}, {"id": "x", "path": str(home / "нету")}):
            report = rollback_state.restore(junk, word=rollback_state.WORD,
                                            printer=_Log())
            assert report["ok"] is False
            assert report["refused"]


def test_the_list_never_pretends_there_are_snapshots():
    with _Home() as home:
        text = " | ".join(rollback_state._list_lines())
        assert "Снимков нет" in text
        _seed(home)
        _snapshot()
        text = " | ".join(rollback_state._list_lines())
        assert str(_entry()["id"]) in text


if __name__ == "__main__":
    failed = 0
    for name in [n for n in sorted(globals()) if n.startswith("test_")]:
        try:
            globals()[name]()
            print("OK   " + name)
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("итог: " + str(len([n for n in globals() if n.startswith("test_")]) - failed)
          + " зелёных, " + str(failed) + " красных")
    sys.exit(1 if failed else 0)
