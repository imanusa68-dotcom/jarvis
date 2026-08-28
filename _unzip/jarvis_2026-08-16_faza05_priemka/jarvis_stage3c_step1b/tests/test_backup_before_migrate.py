# -*- coding: utf-8 -*-
"""Фаза 1, шаг 1.1 — копия базы ПЕРЕД правкой схемы (план Р6, 13.5:2513).

Имя файла взято из плана дословно (`test_backup_before_migrate`): через
полгода поиск по плану обязан приводить к настоящему файлу, а не в пустоту.

Решение владельца, которое эти сторожа держат:
    НЕ ВЫШЛО СНЯТЬ — НЕ ПРАВИМ СХЕМУ.
Причём «вечер без новой возможности», а НЕ «вечер без Джарвиса»: базу
открывают журнал, память, подтверждения и файловые операции, поэтому
неудача копии обязана оставить Джарвиса рабочим на старой схеме.

Почему миграции здесь придуманные, а не настоящие: шаг 1.1 механизм, а не
таблица. Настоящая миграция 7 приезжает шагом 1.2 — и её сторожа лежат в
tests/test_migrations_7_18.py. Механизм проверяется своим списком миграций
через шов, который уже есть в сигнатуре migrate(conn, migrations).
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import state_snapshot, store


# -- Оснастка -----------------------------------------------------------

# Настоящий список миграций и настоящий потолок запоминаются ОДИН раз, при
# ввозе файла. Иначе помощник, вызванный после подмены, посчитает потолок от
# подменённого списка и тест начнёт мерить сам себя (наступил 17.08.2026).
_REAL = list(store.JARVIS_MIGRATIONS)
_REAL_LATEST = max(m[0] for m in _REAL)
_NEXT = _REAL_LATEST + 1


# Придуманная миграция на одну версию вперёд от настоящего потолка.
def _plus_one() -> list:
    return _REAL + [
        (_NEXT, "тест: одна таблица", ["CREATE TABLE IF NOT EXISTS t_probe (x)"]),
    ]


def _seed_home_db() -> Path:
    """Настоящая база дома на текущем потолке и с данными внутри."""
    path = store.db_path()
    conn = store.open_store()
    store.config_set(conn, "keep_me", "данные владельца")
    conn.close()
    return path


def _version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _snapshots(kind=None) -> list:
    return [s for s in state_snapshot.list_snapshots()
            if kind is None or s.get("kind") == kind]


def _snapshot_ok(monkeypatch) -> None:
    """Сказать «копия снята», не снимая её.

    Настоящую копию делает РОВНО ОДИН тест этого файла — тот, который её и
    проверяет. Замерено 17.08.2026: настоящий снимок стоит 0,14 с в одиночку
    и до 5,4 с в общем прогоне (спор за диск с тысячей других тестов).
    Четыре одинаковые копии ничего не доказывают, а бюджет прогона едят.
    """
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: True, raising=True)


def _open_home_with(monkeypatch, migrations) -> sqlite3.Connection:
    """Открыть настоящую базу дома так, будто в коде есть эти миграции."""
    monkeypatch.setattr(store, "JARVIS_MIGRATIONS", migrations, raising=True)
    return store.open_store()


# -- Главный случай: копия появляется, и ТОЛЬКО потом меняется схема ----

def test_snapshot_is_taken_before_the_schema_changes(monkeypatch):
    base = _seed_home_db()
    was = _version(base)
    assert not _snapshots(state_snapshot.KIND_PRE_MIGRATE)

    conn = _open_home_with(monkeypatch, _plus_one())
    try:
        assert store._user_version(conn) == _NEXT, "схема не обновилась"
        assert store._table_exists(conn, "t_probe")
    finally:
        conn.close()

    made = _snapshots(state_snapshot.KIND_PRE_MIGRATE)
    assert len(made) == 1, "копии перед правкой схемы нет"
    copy = Path(made[0]["path"]) / "jarvis.db"
    assert copy.exists(), "в копии нет самой базы"
    assert _version(copy) == was, (
        "копия снята ПОСЛЕ правки: она бесполезна как откат")


def test_the_owner_data_survives_the_upgrade(monkeypatch):
    _seed_home_db()
    _snapshot_ok(monkeypatch)
    conn = _open_home_with(monkeypatch, _plus_one())
    try:
        assert store.config_get(conn, "keep_me") == "данные владельца"
    finally:
        conn.close()


# -- Не вышло снять — не правим схему -----------------------------------

def test_a_failed_snapshot_leaves_the_schema_alone(monkeypatch):
    base = _seed_home_db()
    was = _version(base)
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: False, raising=True)

    conn = _open_home_with(monkeypatch, _plus_one())
    try:
        assert _version(base) == was, "схема изменилась без копии"
        assert not store._table_exists(conn, "t_probe")
        # Джарвис ОСТАЁТСЯ рабочим: соединение живое, старые данные видны.
        assert store.config_get(conn, "keep_me") == "данные владельца"
    finally:
        conn.close()

    state = store.schema_state()
    assert state["ready"] is False
    assert state["reason"], "причина отказа не названа"
    assert state["have"] == was and state["knows"] == _NEXT


def test_a_thrown_snapshot_is_a_refusal_not_a_crash(monkeypatch):
    """Снимок упал с исключением — это отказ, а не падение запуска."""
    base = _seed_home_db()
    was = _version(base)

    def _boom(**kw):
        raise OSError("диск ушёл")

    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        _boom, raising=True)
    conn = _open_home_with(monkeypatch, _plus_one())
    try:
        assert _version(base) == was
    finally:
        conn.close()
    assert store.schema_state()["ready"] is False


def test_the_reason_survives_a_restart(monkeypatch):
    """Причина записана в саму базу: молчаливой неудачи быть не может."""
    _seed_home_db()
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: False, raising=True)
    conn = _open_home_with(monkeypatch, _plus_one())
    conn.close()

    fresh = sqlite3.connect(str(store.db_path()))
    try:
        row = fresh.execute("SELECT value FROM config_kv WHERE key=?",
                            (store.SCHEMA_BLOCK_KEY,)).fetchone()
    finally:
        fresh.close()
    assert row is not None and row[0], "причина не дожила до перезапуска"


def test_seven_openers_try_the_snapshot_once(monkeypatch):
    """Базу открывают семь мест. Отказ обязан прозвучать один раз."""
    _seed_home_db()
    tries: list = []

    def _count(**kw):
        tries.append(1)
        return False

    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        _count, raising=True)
    migrations = _plus_one()
    for _ in range(7):
        _open_home_with(monkeypatch, migrations).close()
    assert len(tries) == 1, f"попыток снять копию: {len(tries)}, а нужна одна"


def test_a_successful_upgrade_clears_an_old_reason(monkeypatch):
    """Схема поднялась — старая жалоба не должна остаться в базе навсегда."""
    _seed_home_db()
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: False, raising=True)
    _open_home_with(monkeypatch, _plus_one()).close()

    store.reset_schema_state()
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: True, raising=True)
    conn = _open_home_with(monkeypatch, _plus_one())
    try:
        assert store.config_get(conn, store.SCHEMA_BLOCK_KEY) is None
    finally:
        conn.close()
    assert store.schema_state()["reason"] is None


# -- Пять предохранителей -----------------------------------------------

def test_a_fresh_database_is_not_snapshotted(monkeypatch):
    """Терять нечего — копии нет. Этот же сторож держит скорость прогона:
    в 1395 существующих тестах база рождается с нуля."""
    tries: list = []
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: tries.append(1) or True, raising=True)
    _open_home_with(monkeypatch, _plus_one()).close()
    assert not tries, "снимок на пустой базе — потерянное время каждого теста"
    assert not _snapshots(state_snapshot.KIND_PRE_MIGRATE)


def test_a_database_passed_by_hand_is_not_snapshotted(monkeypatch):
    """Шов для тестов и инструментов: чужую базу не страхуем."""
    tries: list = []
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: tries.append(1) or True, raising=True)
    side = Path(tempfile.mkdtemp(prefix="jv_side_")) / "jarvis.db"
    store.open_store(side).close()          # довести до потолка
    store.reset_schema_state()
    monkeypatch.setattr(store, "JARVIS_MIGRATIONS", _plus_one(), raising=True)
    store.open_store(side).close()          # и поднять ещё на одну
    assert not tries, "страхуем базу, переданную параметром"
    assert _version(side) == _NEXT


def test_the_home_database_is_recognised_in_any_letter_case(monkeypatch):
    """Windows не различает регистр, а сравнение строк различает —
    забор просто не сработал бы (план Р7)."""
    _seed_home_db()
    tries: list = []
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: tries.append(1) or True, raising=True)
    monkeypatch.setattr(store, "JARVIS_MIGRATIONS", _plus_one(), raising=True)
    shouted = Path(str(store.db_path()).upper())
    store.open_store(shouted).close()
    assert len(tries) == 1, "та же база в другом регистре не узнана"


def test_nothing_happens_when_the_schema_is_current(monkeypatch):
    """Обычный запуск: ни копии, ни правки, ни лишней работы."""
    _seed_home_db()
    tries: list = []
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: tries.append(1) or True, raising=True)
    conn = store.open_store()
    try:
        assert not tries
        assert store.schema_state()["ready"] is True
    finally:
        conn.close()


# -- База из будущего ---------------------------------------------------

def test_a_database_from_the_future_refuses_loudly_and_in_russian():
    """Единственное сообщение подсистемы, которое владелец увидит живьём."""
    _seed_home_db()
    conn = sqlite3.connect(str(store.db_path()))
    conn.execute("PRAGMA user_version = 999")
    conn.close()

    with pytest.raises(store.StoreError) as caught:
        store.open_store()
    text = str(caught.value)
    assert "новее программы" in text, "отказ не по-русски: " + text
    assert "rollback_state.py" in text, "отказ без выхода: " + text
    assert store.schema_state()["ready"] is False


def test_a_future_database_is_not_snapshotted(monkeypatch):
    """Схему не правим — значит и страховать нечего."""
    _seed_home_db()
    conn = sqlite3.connect(str(store.db_path()))
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    tries: list = []
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: tries.append(1) or True, raising=True)
    with pytest.raises(store.StoreError):
        store.open_store()
    assert not tries


# -- Слитый журнал: доктор не должен врать ------------------------------

def test_the_file_header_tells_the_truth_after_an_upgrade(monkeypatch):
    """В режиме WAL заголовок отстаёт от PRAGMA (замерено 17.08.2026).
    tools/doctor.py нарочно читает заголовок и НЕ открывает базу — без
    слива журнала он показал бы владельцу старую версию."""
    _seed_home_db()
    conn = _open_home_with(monkeypatch, _plus_one())
    try:
        pragma = store._user_version(conn)
    finally:
        conn.close()
    head = open(store.db_path(), "rb").read(100)
    assert head[:15] == b"SQLite format 3"
    assert int.from_bytes(head[60:64], "big") == pragma, (
        "заголовок отстал: доктор соврёт про версию схемы")


# -- Известный момент правки --------------------------------------------

def test_ensure_schema_lifts_the_schema_and_closes_behind_itself(monkeypatch):
    _seed_home_db()
    _snapshot_ok(monkeypatch)
    monkeypatch.setattr(store, "JARVIS_MIGRATIONS", _plus_one(), raising=True)
    said: list = []
    state = store.ensure_schema(printer=said.append)
    assert state["ready"] is True
    assert state["have"] == _NEXT
    assert not [f for f in os.listdir(store.app_dir())
                if f.startswith("jarvis.db-wal") and
                (store.app_dir() / f).stat().st_size > 0], \
        "журнал не слит после известной правки"


def test_ensure_schema_says_the_reason_out_loud(monkeypatch):
    _seed_home_db()
    monkeypatch.setattr(state_snapshot, "ensure_pre_migrate_snapshot",
                        lambda **kw: False, raising=True)
    monkeypatch.setattr(store, "JARVIS_MIGRATIONS", _plus_one(), raising=True)
    said: list = []
    state = store.ensure_schema(printer=said.append)
    assert state["ready"] is False
    assert any("Схема" in line for line in said), said


def test_ensure_schema_is_quiet_on_an_ordinary_start(monkeypatch):
    """Мелочь голосом никогда: обычный запуск не печатает ничего."""
    _seed_home_db()
    said: list = []
    store.ensure_schema(printer=said.append)
    assert said == [], said


# -- Способность спрашивать «а моя таблица уже есть?» -------------------

def test_supports_answers_by_schema_version(monkeypatch):
    _seed_home_db()
    monkeypatch.setitem(store.FEATURE_MIN_VERSION, "probe", _REAL_LATEST)
    monkeypatch.setitem(store.FEATURE_MIN_VERSION, "later", _NEXT + 5)
    store.open_store().close()
    assert store.supports("probe") is True
    assert store.supports("later") is False


def test_an_unknown_feature_is_never_supported():
    """Спросили про то, чего в карте нет — ответ «нет», а не падение."""
    assert store.supports("такого-нет") is False


def test_schema_state_cannot_be_edited_from_outside():
    _seed_home_db()
    store.open_store().close()
    store.schema_state()["have"] = 999
    assert store.schema_state()["have"] != 999


def test_a_side_database_never_overwrites_what_we_know():
    """Баг, найденный перечитыванием своего же кода 17.08.2026.

    Инструмент открывает постороннюю базу третьей версии — и состояние
    схемы описывало ЕЁ. Ответ на «а моя таблица уже есть?» становился
    неверным, и через полгода выключенная возможность объяснялась бы чужим
    файлом. Состояние обязано описывать только настоящую базу дома.
    """
    _seed_home_db()
    store.open_store().close()
    mine = store.schema_state()
    assert mine["ready"] is True and mine["have"] == _REAL_LATEST

    side = Path(tempfile.mkdtemp(prefix="jv_side_")) / "jarvis.db"
    conn = store.connect(side)
    store.migrate(conn, [m for m in _REAL if m[0] <= 3])
    conn.close()
    store.open_store(side).close()

    assert store.schema_state() == mine, "чужая база переписала наше знание"
