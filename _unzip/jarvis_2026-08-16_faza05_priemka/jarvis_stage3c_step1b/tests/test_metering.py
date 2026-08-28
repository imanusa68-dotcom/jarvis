# -*- coding: utf-8 -*-
"""Фаза 1, блок 5, шаг 5.1 — учёт вызовов моделей.

Имя `test_metering_no_bypass` из плана появится шагом 5.2, когда учёт
подключат к двери: сейчас проверять «мимо чего» ещё нечего.

Главные сторожа этого файла:
  * test_the_quota_day_lives_in_one_place — знание про сутки поставщика
    существует РОВНО в одном месте (иначе раз в полгода счётчики съедут);
  * test_the_reset_moves_with_daylight_saving — считается зона, а не
    смещение. План говорит «11:00 МСК», и это верно только полгода;
  * test_an_unconfirmed_reserve_is_counted_as_spent — вызов, который не
    вернулся, квоту всё равно съел;
  * test_the_daily_total_and_the_call_land_together — итог и подробность в
    одной транзакции, иначе однажды месяц данных исчезнет.
"""
import io
import sqlite3
import sys
import tempfile
import tokenize
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import metering as mt
from core import store

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture()
def db():
    conn = store.open_store(Path(tempfile.mkdtemp(prefix="jv_meter_")) / "jarvis.db")
    yield conn
    conn.close()


def _rows(conn):
    return conn.execute(
        "SELECT * FROM mx_meter_call ORDER BY started_utc").fetchall()


def _day_rows(conn):
    return conn.execute("SELECT * FROM mx_meter_day").fetchall()


# -- Квотные сутки --------------------------------------------------------

def test_quota_day_is_the_date_in_the_providers_zone():
    """Полночь у поставщика — уже новые сутки, хотя в Москве ещё вечер."""
    late = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)   # 23:00 Pacific 17-го
    early = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)  # 01:00 Pacific 18-го
    assert mt.quota_day(late) == "2026-08-17"
    assert mt.quota_day(early) == "2026-08-18"


def test_the_reset_moves_with_daylight_saving():
    """ЗАМЕР, а не пересказ плана. План говорит «сброс в 11:00 МСК» — это
    верно ЗИМОЙ. Летом полночь у поставщика приходится на 10:00 МСК.
    Жёсткое число соврало бы полгода из года."""
    winter = mt.next_reset_local(datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc))
    summer = mt.next_reset_local(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc))
    assert winter.astimezone(MSK).strftime("%H:%M") == "11:00"
    assert summer.astimezone(MSK).strftime("%H:%M") == "10:00"


def test_the_quota_day_lives_in_one_place():
    """Второе вычисление квотных суток — баг, который всплывёт раз в полгода
    и будет неотличим от порчи данных.

    Смотрим на КОД без комментариев и строк: сторож, который ищет запретное
    слово в тексте, находит сам себя в объяснении, почему оно запрещено
    (наступал трижды: доктор, mx_task_check, task_context).
    """
    guilty = []
    for folder in ("core", "agent", "actions", "memory"):
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "metering.py":
                continue
            with io.open(path, "rb") as fh:
                try:
                    code = " ".join(
                        t.string for t in tokenize.tokenize(fh.readline)
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
                except (tokenize.TokenError, SyntaxError):
                    continue
            for needle in ("Los_Angeles", "quota_day"):
                if needle in code:
                    guilty.append(f"{path.relative_to(ROOT)}: {needle}")
    assert not guilty, "знание про квотные сутки утекло: " + "; ".join(guilty)


def test_a_naive_moment_is_read_as_utc():
    """Момент без зоны — это UTC, а не местное: иначе сутки съедут на три
    часа и никто не заметит."""
    assert mt.quota_day(datetime(2026, 8, 18, 8, 0)) == "2026-08-18"


def test_without_time_zones_it_says_so_instead_of_lying(monkeypatch):
    """Обрезанная сборка Python. Считать приблизительно можно, молчать об
    этом — нельзя."""
    monkeypatch.setattr(mt, "_zone", lambda: None)
    mt.reset_for_tests()
    said = []
    day = mt.quota_day(datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
                       printer=said.append)
    assert day == "2026-08-18"
    assert any("приблизительно" in s for s in said), said


# -- Роли и потолки -------------------------------------------------------

def test_the_live_voice_does_not_pay_the_daily_quota():
    """По реестру у живого голоса суток без ограничения. Считать его наравне
    с разовыми вызовами — значит смешивать то, что расходует дефицитный
    ресурс, с тем, что его не расходует. Владелец услышал бы «осталось мало»
    при полном запасе."""
    assert mt.bucket_of("live_voice") == mt.SESSION_BUCKET
    assert mt.bucket_of("aux_light") == mt.PAID_BUCKET
    assert mt.bucket_of("vision") == mt.PAID_BUCKET
    assert mt.bucket_of("aux_cheap") == mt.CHEAP_BUCKET


def test_the_caps_are_the_numbers_from_the_plan():
    """120 из 500 — четырёхкратный запас на ошибки и повторы (13.7.17)."""
    assert mt.DEFAULT_CAPS[mt.PAID_BUCKET] == 120
    assert mt.DEFAULT_CAPS[mt.CHEAP_BUCKET] == 2000


def test_the_owner_can_change_a_cap_without_touching_code(monkeypatch):
    monkeypatch.setattr("config.loader.get_setting",
                        lambda name, default=None: {"paid": 7})
    assert mt.caps()[mt.PAID_BUCKET] == 7


def test_a_nonsense_cap_from_settings_is_ignored(monkeypatch):
    """Опечатка в настройках не имеет права выключить учёт."""
    monkeypatch.setattr("config.loader.get_setting",
                        lambda name, default=None: {"paid": "много"})
    assert mt.caps()[mt.PAID_BUCKET] == 120


# -- Резерв и фиксация ----------------------------------------------------

def test_a_reserve_is_written_before_the_call(db):
    got = mt.reserve("aux_light", model_name="role-model", api_key="secret",
                     conn=db)
    assert got["allowed"] is True and got["call_id"].startswith("C-")
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["ok"] == mt._RESERVED, "резерв выглядит как готовый вызов"
    assert rows[0]["bucket"] == mt.PAID_BUCKET


def test_the_key_is_never_stored_only_its_fingerprint(db):
    mt.reserve("aux_light", api_key="AIzaSy-очень-секретный-ключ", conn=db)
    row = _rows(db)[0]
    assert row["key_fp"] and len(row["key_fp"]) == 12
    assert "AIzaSy" not in row["key_fp"]
    dump = " ".join(str(v) for v in tuple(row))
    assert "секретный" not in dump


def test_commit_closes_the_reserve_with_facts(db):
    got = mt.reserve("aux_light", conn=db)
    assert mt.commit(got["call_id"], in_tokens=120, out_tokens=45, ok=True,
                     conn=db) is True
    row = _rows(db)[0]
    assert row["ok"] == 1 and row["in_tokens"] == 120 and row["out_tokens"] == 45


def test_a_failed_call_is_recorded_with_its_kind(db):
    got = mt.reserve("aux_light", conn=db)
    mt.commit(got["call_id"], ok=False, err_kind="rpd", conn=db)
    row = _rows(db)[0]
    assert row["ok"] == 0 and row["err_kind"] == "rpd"
    assert _day_rows(db)[0]["fail_n"] == 1


def test_committing_an_unknown_call_is_false_not_a_crash(db):
    assert mt.commit("C-нет-такого", conn=db) is False


def test_the_task_number_travels_into_the_meter(db):
    """Решение блока 1: расход задачи считается ОТСЮДА, а не колонкой в
    задаче. Значит номер дела обязан здесь быть."""
    from core.task_context import TaskCtx
    ctx = TaskCtx(run_id="R1", task_id="T-20260818-007", bucket="task")
    mt.reserve("aux_light", ctx, conn=db)
    assert _rows(db)[0]["task_id"] == "T-20260818-007"


# -- Незакрытые резервы ---------------------------------------------------

def test_an_unconfirmed_reserve_is_counted_as_spent(db):
    """Вызов, который не вернулся (процесс убит, свет выключен), квоту всё
    равно съел. Пессимизм правильный: лучше счесть сожжённым то, что могло
    не сгореть, чем узнать об исчерпании от Google."""
    mt.reserve("aux_light", conn=db)          # никто не подтвердил
    said = []
    assert mt.close_lost(conn=db, printer=said.append, older_than_s=0) == 1
    row = _rows(db)[0]
    assert row["ok"] == 0 and row["err_kind"] == "lost"
    assert _day_rows(db)[0]["calls_n"] == 1
    assert any("незакрытых" in s for s in said), said


def test_a_live_reserve_of_a_neighbour_thread_is_never_killed(db):
    """НАЙДЕНО ПОРЧЕЙ КОДА 18.08.2026, и это был настоящий баг.

    Первая версия закрывала любой незакрытый резерв и делала это из
    `reserve()`. На двух параллельных задачах вызов соседнего потока, который
    ещё ИДЁТ, помечался «потерян», а потом при подтверждении считался ВТОРОЙ
    раз: живая проба дала calls_n = 2 там, где вызов был один. Счётчик врал
    бы вдвое — ровно в те дни, когда работают две задачи, то есть когда
    точность нужнее всего.
    """
    a = mt.reserve("aux_light", model_name="m", conn=db)
    mt.reserve("aux_light", model_name="m", conn=db)       # сосед стартует
    row = db.execute("SELECT ok, err_kind FROM mx_meter_call WHERE call_id=?",
                     (a["call_id"],)).fetchone()
    assert row["ok"] == mt._RESERVED and row["err_kind"] is None, (
        "живой резерв соседа объявлен потерянным")
    mt.commit(a["call_id"], in_tokens=10, ok=True, conn=db)
    day = _day_rows(db)[0]
    assert (day["calls_n"], day["fail_n"]) == (1, 0), "двойной учёт вернулся"


def test_a_fresh_reserve_is_not_lost_yet(db):
    """Свежий резерв — это идущий вызов, а не потерянный. Порог возраста
    больше самого длинного мыслимого вызова (122 с по замеру фазы 0.5)."""
    mt.reserve("aux_light", conn=db)
    assert mt.close_lost(conn=db) == 0
    assert _rows(db)[0]["ok"] == mt._RESERVED
    assert mt._LOST_AFTER_S >= 300, "порог короче худшего вызова"


def test_an_old_reserve_is_lost_by_age(db):
    """Проверяем возрастом, а не подменой часов: строка старая по факту."""
    got = mt.reserve("aux_light", conn=db)
    db.execute("UPDATE mx_meter_call SET started_utc=? WHERE call_id=?",
               ("2026-08-01T00:00:00+00:00", got["call_id"]))
    assert mt.close_lost(conn=db) == 1
    assert _rows(db)[0]["err_kind"] == "lost"


def test_closing_lost_twice_changes_nothing(db):
    mt.reserve("aux_light", conn=db)
    mt.close_lost(conn=db, older_than_s=0)
    assert mt.close_lost(conn=db, older_than_s=0) == 0
    assert _day_rows(db)[0]["calls_n"] == 1, "итог посчитан дважды"


def test_a_reserve_is_counted_against_the_cap_immediately(db, monkeypatch):
    """Иначе два потока оба увидят «остался один» и оба пойдут."""
    monkeypatch.setattr(mt, "caps", lambda: {mt.PAID_BUCKET: 1,
                                             mt.CHEAP_BUCKET: 10})
    first = mt.reserve("aux_light", conn=db)
    assert first["allowed"] is True
    second = mt.reserve("aux_light", conn=db)
    assert second["allowed"] is False and second["why"] == "daily_cap"


# -- Суточный итог --------------------------------------------------------

def test_the_daily_total_and_the_call_land_together(db):
    got = mt.reserve("aux_light", model_name="m", api_key="k", conn=db)
    mt.commit(got["call_id"], in_tokens=10, out_tokens=5, conn=db)
    day = _day_rows(db)
    assert len(day) == 1
    assert (day[0]["calls_n"], day[0]["in_tokens"], day[0]["out_tokens"]) == (1, 10, 5)


def test_the_total_survives_the_purge_of_the_details(db):
    """Подробности живут 30 дней, итог — бессрочно. Решение блока 2."""
    got = mt.reserve("aux_light", conn=db)
    mt.commit(got["call_id"], in_tokens=7, conn=db)
    db.execute("DELETE FROM mx_meter_call")
    assert _day_rows(db)[0]["calls_n"] == 1


def test_different_roles_keep_separate_totals(db):
    for role in ("aux_light", "aux_cheap", "vision"):
        got = mt.reserve(role, model_name=role + "-model", conn=db)
        mt.commit(got["call_id"], conn=db)
    assert len(_day_rows(db)) == 3


def test_two_calls_on_the_same_day_and_role_share_one_total_row(db):
    for _ in range(3):
        got = mt.reserve("aux_light", model_name="m", conn=db)
        mt.commit(got["call_id"], in_tokens=1, conn=db)
    day = _day_rows(db)
    assert len(day) == 1 and day[0]["calls_n"] == 3 and day[0]["in_tokens"] == 3


# -- Остаток --------------------------------------------------------------

def test_remaining_answers_with_numbers_not_percents(db):
    for _ in range(3):
        got = mt.reserve("aux_light", conn=db)
        mt.commit(got["call_id"], conn=db)
    left = mt.remaining("aux_light", conn=db)
    assert left["known"] is True
    assert (left["spent"], left["limit"], left["left"]) == (3, 120, 117)
    assert left["reset_local"].tzinfo is not None


def test_remaining_says_it_does_not_know_instead_of_guessing(monkeypatch):
    """База недоступна. «Не знаю» — честно; выдуманное число — нет."""
    def _boom():
        raise RuntimeError("базы нет")
    monkeypatch.setattr(mt, "_conn", lambda conn: _boom())
    left = mt.remaining("aux_light")
    assert left["known"] is False and left["spent"] is None


def test_the_session_bucket_has_no_daily_cap(db):
    got = mt.reserve("live_voice", conn=db)
    assert got["allowed"] is True
    assert mt.remaining("live_voice", conn=db)["limit"] is None


# -- Учёт не ломает дело --------------------------------------------------

def test_a_broken_meter_still_allows_the_call(monkeypatch):
    """Молчаливый отказ работать страшнее неучтённого вызова: первое ломает
    Джарвиса, второе портит статистику.

    После блока 7 запись идёт через кассу, поэтому «базы нет» ломается именно
    там. Раньше ломали шов соединения — теперь он отвечает только за чтение.
    """
    from core import writer

    def _boom(fn, **kw):
        raise RuntimeError("схема не обновилась")
    monkeypatch.setattr(writer, "write", _boom)
    mt.reset_for_tests()
    said = []
    got = mt.reserve("aux_light", printer=said.append)
    assert got["allowed"] is True
    assert got["why"] == "meter_offline"
    assert any("расход не считается" in s for s in said), said


def test_the_broken_meter_complains_once_not_every_call(monkeypatch):
    """Иначе одна поломка превратится в сто одинаковых строк подряд."""
    from core import writer

    def _boom(fn, **kw):
        raise RuntimeError("нет базы")
    monkeypatch.setattr(writer, "write", _boom)
    mt.reset_for_tests()
    said = []
    for _ in range(5):
        mt.reserve("aux_light", printer=said.append)
    assert len(said) == 1, said


def test_the_cap_refusal_is_never_silent(db, monkeypatch):
    """I19: исчерпание никогда не молчаливое. Причина названа кодом, и
    вызывающий обязан её озвучить."""
    monkeypatch.setattr(mt, "caps", lambda: {mt.PAID_BUCKET: 0,
                                             mt.CHEAP_BUCKET: 0})
    got = mt.reserve("aux_light", conn=db)
    assert got["allowed"] is False
    assert got["why"] == "daily_cap" and got["limit"] == 0
    assert got["spent"] == 0


# -- Что метеринг НЕ делает ----------------------------------------------

def test_the_meter_does_not_duplicate_the_cooldown():
    """core/model_guard считает ОТКАЗЫ, метеринг считает РАСХОД. Разные
    вещи; объединить — значит потерять обе."""
    body = (ROOT / "core" / "metering.py").read_text(encoding="utf-8")
    code = " ".join(l for l in body.splitlines()
                    if not l.strip().startswith("#"))
    for needle in ("cooldown", "429", "is_available"):
        assert needle not in code, f"метеринг полез в остывание: {needle}"
