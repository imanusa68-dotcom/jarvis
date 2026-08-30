"""Шаг 33.2: громкий отказ при старте и конец тихого глотания StoreError.

Сторожа проверяем на временном доме и никогда на настоящем: прогон тестов
не имеет права касаться ~/.jarvis владельца.

Стандартная библиотека, без сети. Можно запустить и без pytest:
    python tests/test_state_guard_step33.py
"""

import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import state_guard, state_version, store   # noqa: E402


class _Home:
    """Временный дом через JARVIS_STATE_DIR — одна переменная на всё состояние."""

    def __enter__(self) -> Path:
        self.dir = Path(tempfile.mkdtemp(prefix="jv_guard_"))
        self._old = os.environ.get("JARVIS_STATE_DIR")
        os.environ["JARVIS_STATE_DIR"] = str(self.dir)
        return self.dir

    def __exit__(self, *exc) -> bool:
        if self._old is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def _make_db(target: Path, user_version: int) -> None:
    conn = sqlite3.connect(str(target))
    try:
        conn.execute(f"PRAGMA user_version = {int(user_version)}")
        conn.commit()
    finally:
        conn.close()


def _raiser(exc):
    def _fail(*args, **kwargs):
        raise exc
    return _fail


class _Patched:
    """Временная подмена атрибута с гарантированным возвратом на место."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc) -> bool:
        setattr(self.obj, self.name, self.old)
        return False


def _reset_bridge() -> None:
    from actions import fileops_bridge as bridge
    bridge._instance = None
    bridge._conn = None


def _ensure_send2trash() -> bool:
    """actions/file_controller.py импортирует send2trash безусловно, строкой 8.

    Без этого пакета мост падает на импорте РАНЬШЕ, чем доходит до базы, и
    проверка «StoreError не глотается» молча проверяла бы совсем другой отказ.
    Заглушка ставится только там, где пакета нет; на машине владельца эти
    строки не выполняются вообще.
    """
    import importlib.machinery
    import importlib.util
    import types
    if "send2trash" in sys.modules:
        # Заглушка из соседнего теста или настоящий пакет — оба годятся.
        return False
    try:
        if importlib.util.find_spec("send2trash") is not None:
            return False
    except Exception:
        pass

    class TrashPermissionError(Exception):
        pass

    stub = types.ModuleType("send2trash")
    stub.__spec__ = importlib.machinery.ModuleSpec("send2trash", loader=None)
    stub.send2trash = lambda *a, **k: None
    stub.TrashPermissionError = TrashPermissionError
    sys.modules["send2trash"] = stub
    return True


# --- Сторож при старте -------------------------------------------------------

def test_a_consistent_home_is_allowed_to_start():
    with _Home():
        result = state_guard.check()
        assert result["ok"] is True, result


def test_absent_stores_are_not_a_refusal():
    """В доме владельца нет ни history.db, ни settings.json, ни personality.json."""
    with _Home():
        assert state_guard.check()["problems"] == []


def test_startup_refuses_a_database_from_the_future():
    with _Home() as home:
        _make_db(home / "jarvis.db", 999)
        result = state_guard.check()
        assert result["ok"] is False
        text = "\n".join(result["lines"])
        assert "999" in text, text
        assert state_guard.HEADER in text


def test_refusal_says_what_to_do_and_that_nothing_is_lost():
    with _Home() as home:
        _make_db(home / "jarvis.db", 42)
        text = "\n".join(state_guard.check()["lines"])
        assert state_guard.WHAT_TO_DO in text
        assert state_guard.NOTHING_LOST in text


def test_refusal_never_promises_a_tool_that_does_not_exist():
    """Команда отката появится в 33.5; до тех пор о ней нельзя говорить.

    Версия «из будущего» считается от потолка кода, а не зашита числом.
    Здесь стояла семёрка — и она умерла в тот вечер, когда фаза 1 довела
    схему до 7: тест потребовал отказа там, где отказывать больше не надо.
    Любое литеральное число здесь снова сломается на миграции 8.
    """
    from core import store
    future = max(m[0] for m in store.JARVIS_MIGRATIONS) + 1
    script = state_version.project_dir() / "tools" / "rollback_state.py"
    with _Home() as home:
        _make_db(home / "jarvis.db", future)
        text = "\n".join(state_guard.check()["lines"])
        if script.exists():
            assert "rollback_state.py" in text
        else:
            assert "rollback_state.py" not in text


def test_a_changed_folder_is_a_note_not_a_refusal():
    with _Home():
        fresh = state_version.collect()
        fresh["last_run"]["path"] = "C:/совсем/другая/папка"
        state_version.save(fresh)
        result = state_guard.check()
        assert result["ok"] is True
        assert result["notes"], "смена папки обязана быть видна"
        assert "другой папки" in "\n".join(result["notes"])


def test_a_broken_guard_never_locks_the_owner_out():
    said = []
    with _Home(), _Patched(state_version, "collect",
                           _raiser(RuntimeError("сборщик сломался"))):
        allowed = state_guard.verify_or_refuse(printer=said.append)
    assert allowed is True, "сломанный сторож не имеет права запирать дом"
    assert any("проверка версий" in line for line in said), said


def test_every_refusal_line_reaches_the_owner():
    said = []
    with _Home() as home:
        _make_db(home / "jarvis.db", 999)
        allowed = state_guard.verify_or_refuse(printer=said.append)
    assert allowed is False
    assert len(said) >= 4, said


def test_guard_never_opens_the_database():
    src = (state_version.project_dir() / "core" / "state_guard.py").read_text(
        encoding="utf-8")
    assert "open_store" not in src
    assert "migrate(" not in src


def test_guard_is_not_a_second_writer_of_state():
    src = (state_version.project_dir() / "core" / "state_guard.py").read_text(
        encoding="utf-8")
    assert "atomic_write_json" not in src
    assert "STATE.json" not in src


def test_record_start_marks_the_run():
    with _Home():
        written = state_guard.record_start()
        assert written is not None and Path(written).exists()


# --- StoreError больше не глотается -------------------------------------------

def test_store_error_is_not_swallowed_by_the_file_bridge():
    """Тихий уход на старый путь = работа без журнала и без отмены."""
    from actions import fileops_bridge as bridge
    _ensure_send2trash()
    with _Home():
        _reset_bridge()
        try:
            with _Patched(store, "open_store",
                          _raiser(store.StoreError("база из будущего"))):
                raised = False
                try:
                    bridge.get_fileops()
                except store.StoreError:
                    raised = True
                assert raised, "отказ проглотили и ушли на legacy"
        finally:
            _reset_bridge()


def test_an_ordinary_failure_still_uses_the_legacy_path():
    """Расширять громкость на все ошибки нельзя: так умирают рабочие вечера."""
    from actions import fileops_bridge as bridge
    _ensure_send2trash()
    with _Home():
        _reset_bridge()
        try:
            with _Patched(store, "open_store", _raiser(RuntimeError("диск занят"))):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    assert bridge.get_fileops() is None
                assert "legacy" in buf.getvalue()
        finally:
            _reset_bridge()


def test_store_error_is_not_swallowed_by_consent_tickets():
    """Одноразовость подтверждений — свойство безопасности, не удобство."""
    from actions import fileops_bridge as bridge
    from core import consent_runtime as cr
    with _Home():
        cr.reset()
        try:
            with _Patched(bridge, "get_fileops",
                          _raiser(store.StoreError("база из будущего"))):
                raised = False
                try:
                    cr.get_conn()
                except store.StoreError:
                    raised = True
                assert raised
        finally:
            cr.reset()


def test_consent_fallback_also_refuses_loudly():
    from actions import fileops_bridge as bridge
    from core import consent_runtime as cr
    with _Home():
        cr.reset()
        try:
            with _Patched(bridge, "get_fileops", lambda: None), \
                 _Patched(store, "open_store",
                          _raiser(store.StoreError("база из будущего"))):
                raised = False
                try:
                    cr.get_conn()
                except store.StoreError:
                    raised = True
                assert raised
        finally:
            cr.reset()


def test_consent_still_returns_nothing_on_an_ordinary_failure():
    from actions import fileops_bridge as bridge
    from core import consent_runtime as cr
    with _Home():
        cr.reset()
        try:
            with _Patched(bridge, "get_fileops", lambda: None), \
                 _Patched(store, "open_store", _raiser(RuntimeError("диск занят"))):
                assert cr.get_conn() is None
        finally:
            cr.reset()


def test_memory_index_stays_non_fatal_but_says_it_out_loud():
    """Здесь громкость — словами, а не падением: JSON уже сохранён,
    и падение превратило бы честное «запомнил» в неправду."""
    from memory import fact_store as fs
    with _Home():
        with _Patched(store, "open_store",
                      _raiser(store.StoreError("база из будущего"))):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                fs.note_fact("notes", "k", "v")      # не должно бросать
            said = buf.getvalue()
    assert "новее" in said, said


# --- Проводка в точке старта --------------------------------------------

def test_main_checks_versions_before_the_window():
    """Сторож, которого не зовут, — мёртвый код. Проверка порядка."""
    src = (state_version.project_dir() / "main.py").read_text(encoding="utf-8")
    guard_at = src.find("state_guard")
    window_at = src.find("JarvisUI(\"face.png\")")
    assert guard_at != -1, "main.py не зовёт сторожа вообще"
    assert window_at != -1
    assert guard_at < window_at, "отказ должен случаться до окна и микрофона"
    assert "verify_or_refuse" in src
    assert "record_start" in src


def test_a_refusal_releases_the_lock():
    """Иначе второй запуск скажет «уже запущен», хотя первый ушёл."""
    src = (state_version.project_dir() / "main.py").read_text(encoding="utf-8")
    block = src[src.find("state_guard"):src.find("JarvisUI(\"face.png\")")]
    assert "instance_lock.release()" in block, block


def test_test_run_never_touches_the_real_home():
    if "pytest" not in sys.modules:
        return
    assert "jv_guard_" in str(state_version.dir_path()) or \
        "jv_state_" in str(state_version.dir_path()) or \
        os.environ.get("JARVIS_STATE_DIR"), state_version.dir_path()


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK   {name}")
            passed += 1
    print(f"OK: {passed} passed (standalone)")
