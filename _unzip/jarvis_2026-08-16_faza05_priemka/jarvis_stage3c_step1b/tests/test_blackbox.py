# tests/test_blackbox.py
"""
Сторожа чёрного ящика (фаза 1, блок 6).

Правило написания этих тестов: проверяем СТРУКТУРУ, которая работает, а не
наличие слов в тексте. Сторож, который ищет запретную фразу грепом, пять раз
в этом проекте находил сам себя в объяснении, почему фраза запрещена.
"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import blackbox as bb
from core import store

ROOT = Path(__file__).resolve().parent.parent

DAY = "2026-08-19"


def _fresh():
    return Path(tempfile.mkdtemp(prefix="jv_bb_")) / "jarvis.db"


@pytest.fixture()
def db():
    """База ДОМА этого теста, а не боковая.

    Это не мелочь и не вкус. Блок 1 нарочно устроил так, что состояние схемы
    описывает ТОЛЬКО настоящую базу дома: иначе инструмент, открывший чужой
    файл, отвечал бы на вопрос «есть ли у меня такая таблица» про чужой файл.
    Поэтому боковая база (`_fresh()`) даёт `supports("blackbox") == False`, и
    чёрный ящик честно молчит. Первая версия этой фикстуры открывала боковую,
    и шестнадцать тестов падали — сторож блока 1 сработал по делу.
    """
    conn = store.open_store()
    yield conn
    conn.close()


def _head(conn, rec_id):
    return conn.execute("SELECT * FROM mx_bb_head WHERE rec_id=?",
                        (rec_id,)).fetchone()


def _bodies(conn, rec_id=None):
    if rec_id is None:
        return conn.execute(
            "SELECT * FROM mx_bb_body ORDER BY rec_id, seq").fetchall()
    return conn.execute(
        "SELECT * FROM mx_bb_body WHERE rec_id=? ORDER BY seq",
        (rec_id,)).fetchall()


# -- Форма и словарь ------------------------------------------------------

def test_the_two_lists_of_outcomes_stay_apart():
    """Исходы записи и статусы отчёта похожи, но это РАЗНЫЕ списки: агент
    может отказаться, а запись может быть прервана. Кто-нибудь обязательно
    решит, что список один."""
    from agent.contracts import REPORT_STATUS
    assert "cancelled" in bb.OUTCOMES and "cancelled" not in REPORT_STATUS
    assert "refused" in REPORT_STATUS and "refused" not in bb.OUTCOMES
    assert set(bb.OUTCOMES) != set(REPORT_STATUS)


def test_the_kind_vocabulary_matches_the_table_it_was_built_for():
    """Словарь видов строк закрыт и совпадает с тем, под который заведена
    таблица в блоке 2. Незнакомый вид молча испортил бы воспроизведение."""
    assert bb.KINDS == ("speech_in", "prompt", "model_out", "tool_call",
                        "gate_verdict", "report", "spoken")
    # В фазе 1 пишутся ровно два, и это названо вслух, а не умолчано.
    assert bb.WRITTEN_NOW == ("prompt", "model_out")
    assert set(bb.WRITTEN_NOW) <= set(bb.KINDS)


def test_an_unknown_kind_is_refused_instead_of_written(db, monkeypatch):
    assert bb.write("B-x", "нет_такого_вида", {"t": "текст"}) is False
    assert len(_bodies(db)) == 0


def test_the_record_is_born_failed_because_the_table_leaves_no_choice(db, monkeypatch):
    """Колонка исхода обязательна и без умолчания, а значения «открыта» в
    списке нет. Значит запись рождается с худшим предположением, а открытость
    видна по пустому closed_utc."""
    rec = bb.open_rec(day=DAY)
    row = _head(db, rec)
    assert row["outcome"] == bb.BORN_OUTCOME == "failed"
    assert row["closed_utc"] is None, "новорождённая запись обязана быть открытой"
    assert row["body_purged"] == 0 and row["calls_n"] == 0
    assert row["quota_day"] == DAY
    assert row["code_ver"], "версия кода в шапке обязательна"


def test_the_record_number_is_built_from_an_existing_number(db, monkeypatch):
    """Своего счётчика нет: номер строится от номера запуска или дела. Отсюда
    ни гонки за счётчик, ни миграции под него."""
    from core import task_context
    rec = bb.open_rec(day=DAY)
    assert rec == "B-" + task_context.run_id()
    # Второй вызов даёт ТОТ ЖЕ номер и не плодит вторую шапку.
    assert bb.open_rec(day=DAY) == rec
    assert db.execute("SELECT count(*) FROM mx_bb_head").fetchone()[0] == 1

    ctx = task_context.TaskCtx(run_id=task_context.run_id(),
                               task_id="T-20260819-001", bucket="task")
    assert bb.rec_id_for(ctx) == "B-T-20260819-001"


# -- Секреты и fail-closed -----------------------------------------------

def test_a_key_in_the_prompt_never_reaches_the_database(db, monkeypatch):
    """Главный сторож секретов: подсунутый ключ заменён заглушкой ДО записи."""
    fake = "A" + "Iza" + "FAKEfake0123456789_-abcXYZ"
    rec = bb.open_rec(day=DAY)
    assert bb.write(rec, "prompt", {"t": f"ошибка: key={fake} отказано"}) is True
    everything = " ".join(str(tuple(r)) for r in _bodies(db))
    assert fake not in everything, "ключ утёк в запись"
    assert "<скрыто>" in everything, "на месте ключа нет заглушки"
    assert "отказано" in everything, "заглушение съело невиновный текст"


def test_a_payload_that_cannot_be_cleaned_is_not_written_at_all(db, monkeypatch):
    """Fail-closed: не смог вычистить — текста нет, но ДЫРА ВИДНА.

    Молчаливая дыра хуже отсутствия записи: воспроизведение по ней вернуло бы
    не то, и никто бы не понял почему.
    """
    monkeypatch.setattr(bb, "_clean", lambda text: ("<скрыто>", False))
    rec = bb.open_rec(day=DAY)
    assert bb.write(rec, "prompt", {"t": "очень секретный текст"}) is True
    body = json.loads(_bodies(db, rec)[0]["payload"])
    assert body.get("hidden") is True, "дыра в записи обязана быть видимой"
    assert "t" not in body, "текст записан, хотя вычистить его не удалось"
    assert "очень секретный текст" not in str(body)
    # Отпечаток остаётся: по нему воспроизведение всё ещё найдёт пару.
    assert body.get("h"), "отпечаток потерян вместе с текстом"


def test_the_redactor_can_say_that_it_failed():
    """Обёртка в core/env умеет вернуть «не смог» — на этом стоит fail-closed.
    Сама redact() этого не умеет, и поведение её не менялось."""
    from core import env
    clean, ok = env.redact_checked("обычный текст")
    assert (clean, ok) == ("обычный текст", True)
    fake = "A" + "Iza" + "FAKEfake0123456789_-abcXYZ"
    clean, ok = env.redact_checked(f"key={fake}")
    assert ok is True and fake not in clean

    class _Broken:
        def sub(self, *a, **k):
            raise RuntimeError("регулярка испорчена")

        def search(self, *a, **k):
            raise RuntimeError("регулярка испорчена")

    import core.env as envmod
    saved = envmod._KEY_RE
    envmod._KEY_RE = _Broken()
    try:
        clean, ok = env.redact_checked("что угодно")
        assert ok is False, "сломанная регулярка обязана давать «не смог»"
        assert clean == envmod.HIDDEN
    finally:
        envmod._KEY_RE = saved


def test_the_black_box_keeps_no_pattern_of_its_own():
    """Точка вычистки одна на весь проект. Свой шаблон здесь — это второй
    механизм об одном, а два таких всегда расходятся.

    Проверяем КОД токенизатором, а не текст грепом: слово «регулярка» в
    объяснении, почему её здесь нет, красить сторожа не должно.
    """
    path = ROOT / "core" / "blackbox.py"
    with io.open(path, "rb") as fh:
        code = " ".join(
            t.string for t in tokenize.tokenize(fh.readline)
            if t.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "re.compile" not in code, "в чёрном ящике завелась своя регулярка"
    assert "import re" not in code


# -- Потолок и отпечаток --------------------------------------------------

def test_an_open_record_does_not_report_zero_calls_to_the_owner(db):
    """Найдено живой пробой 19.08.2026.

    В шапке число вызовов появляется только при ЗАКРЫТИИ (тело живёт считанные
    дни, шапка вечно, поэтому число материализуется до смерти тела). У открытой
    записи там честный ноль — но показать владельцу «вызовов 0», когда их уже
    три, значит соврать. Показ обязан считать по телу.
    """
    from tools import replay_session as rs
    db.execute(
        "INSERT INTO mx_bb_head (rec_id, code_ver, quota_day, outcome, "
        "created_utc) VALUES ('B-идёт','1.10',?,?,?)",
        (DAY, bb.BORN_OUTCOME, "2026-08-19T10:00:00+00:00"))
    for seq in (1, 2, 3):
        db.execute(
            "INSERT INTO mx_bb_body (rec_id, seq, kind, payload, ts_utc) "
            "VALUES ('B-идёт',?,'model_out','{\"ok\":true}',?)",
            (seq, "2026-08-19T10:00:00+00:00"))
    assert db.execute("SELECT calls_n FROM mx_bb_head WHERE rec_id='B-идёт'"
                      ).fetchone()[0] == 0, "в шапке открытой записи не ноль"
    assert rs._live_calls(db, "B-идёт") == 3, "показ соврал бы владельцу про ноль"


def test_a_purge_that_dies_halfway_changes_nothing_at_all(db):
    """Уборка целиком или никак — вот что на самом деле защищает владельца.

    Этот тест написан ПОСЛЕ порчи кода 19.08.2026, и он исправляет мою же
    ошибку в рассуждении. Я утверждал, что важен порядок строк «сначала тело,
    потом флаг». Порча поменяла их местами — и ни один тест не покраснел,
    потому что и не должен был: пока обе правки в ОДНОЙ транзакции, порядок
    не значит ничего. Держит атомарность, а не порядок. Её и проверяем.

    Без этого свойства случай «диск кончился на середине уборки» оставил бы
    флаг «убрано» при живом теле — то есть речь владельца стала бы
    бессмертной и невидимой одновременно.
    """
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    _seed(db, "старая-закрытая", 31, True, now=now)

    class _DiesOnTheFlag:
        """Соединение, которое отказывает ровно на второй правке."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *args):
            if "body_purged=1" in sql:
                raise sqlite3.OperationalError("диск кончился посреди уборки")
            return self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    assert bb.purge(conn=_DiesOnTheFlag(db), days=7, now_utc=now) == 0

    head = _head(db, "старая-закрытая")
    assert head["body_purged"] == 0, "флаг «убрано» встал, а тело осталось"
    assert len(_bodies(db, "старая-закрытая")) == 1, "тело ушло без флага"


def test_the_ceiling_is_a_fixed_number_and_it_holds(db, monkeypatch):
    """Потолок пришит числом нарочно.

    Первая версия этого теста строила промпт длиной `MAX_PAYLOAD_BYTES` знаков
    — то есть мерила константу этой же константой. Порча 19.08.2026 подняла
    потолок в тысячу раз, и тест остался зелёным: промпт вырос вместе с
    потолком. Теперь и число, и длина промпта заданы независимо.
    """
    assert bb.MAX_PAYLOAD_BYTES == 32 * 1024, "потолок на строку изменили молча"
    rec = bb.open_rec(day=DAY)
    assert bb.write(rec, "prompt", {"t": "я" * 200_000}) is True
    body = json.loads(_bodies(db, rec)[0]["payload"])
    assert body["cut"] is True
    assert len(body["t"].encode("utf-8")) <= 32 * 1024


def test_a_giant_prompt_is_cut_but_replay_still_finds_it(db, monkeypatch):
    """Обрезка НЕ ломает воспроизведение, потому что отпечаток считается от
    полного промпта. Это вся причина, по которой отпечаток вообще есть."""
    import hashlib
    huge = "я" * 200_000
    rec = bb.open_rec(day=DAY)
    assert bb.write(rec, "prompt", {"t": huge}) is True
    body = json.loads(_bodies(db, rec)[0]["payload"])
    assert body["cut"] is True, "гигантский промпт не обрезан"
    assert body["n"] == len(huge), "полная длина потеряна"
    want = hashlib.sha256(huge.encode("utf-8")).hexdigest()
    assert body["h"] == want, "отпечаток считается не от полного промпта"


def test_a_normal_prompt_is_never_cut(db, monkeypatch):
    """Потолок выбран так, чтобы не обрезать ни один законный промпт: самый
    толстый по замеру — около одиннадцати тысяч знаков."""
    rec = bb.open_rec(day=DAY)
    bb.write(rec, "prompt", {"t": "п" * 11000})
    body = json.loads(_bodies(db, rec)[0]["payload"])
    assert "cut" not in body, "обычный промпт обрезан — потолок слишком низкий"


def test_a_failed_answer_carries_a_code_and_never_a_story(db, monkeypatch):
    rec = bb.open_rec(day=DAY)
    bb.write(rec, "model_out", {"ok": False, "e": "rpd"})
    body = json.loads(_bodies(db, rec)[0]["payload"])
    assert body == {"e": "rpd", "ok": False}
    assert "t" not in body, "у отказа нет и не может быть текста ответа"


# -- Сметание брошенных записей ------------------------------------------

def test_a_record_left_by_a_previous_run_is_closed_at_start(db, monkeypatch):
    """Без этого тело брошенной записи лежало бы ВЕЧНО: уборка обязана
    пропускать открытые записи, а закрыть её было бы некому."""
    db.execute(
        "INSERT INTO mx_bb_head (rec_id, code_ver, quota_day, outcome, "
        "created_utc) VALUES ('B-прошлая-жизнь','1.0',?,?,?)",
        (DAY, bb.BORN_OUTCOME, "2026-08-01T10:00:00+00:00"))
    db.execute(
        "INSERT INTO mx_bb_body (rec_id, seq, kind, payload, ts_utc) "
        "VALUES ('B-прошлая-жизнь',1,'model_out','{\"ok\":true}',?)",
        ("2026-08-01T10:00:00+00:00",))

    bb.open_rec(day=DAY)          # первое открытие -> сметание

    orphan = _head(db, "B-прошлая-жизнь")
    assert orphan["closed_utc"] is not None, "брошенная запись осталась открытой"
    assert orphan["outcome"] == bb.ORPHAN_OUTCOME == "cancelled"
    assert orphan["calls_n"] == 1, "число вызовов не посчитано при закрытии"


def test_the_sweep_never_closes_a_record_of_this_very_process(db, monkeypatch):
    """САМАЯ ОПАСНАЯ ЛОВУШКА БЛОКА. В блоке 5 такая же уборка звалась на
    каждом вызове и убивала живой резерв соседнего потока — счётчик врал
    вдвое. Здесь цена ошибки та же: сметание, позванное позже, закроет нашу
    же живую запись."""
    rec = bb.open_rec(day=DAY)
    bb.write(rec, "prompt", {"t": "вопрос"})
    # Много раз подряд — сметание не имеет права сработать второй раз.
    for _ in range(5):
        assert bb.open_rec(day=DAY) == rec
    assert _head(db, rec)["closed_utc"] is None, "сметание закрыло живую запись"


def test_the_sweep_happens_once_per_run(db, monkeypatch):
    bb.open_rec(day=DAY)
    assert bb._swept is True
    db.execute(
        "INSERT INTO mx_bb_head (rec_id, code_ver, quota_day, outcome, "
        "created_utc) VALUES ('B-вторая','1.0',?,?,?)",
        (DAY, bb.BORN_OUTCOME, "2026-08-01T10:00:00+00:00"))
    assert bb._sweep(db) == 0, "сметание сработало второй раз за запуск"
    assert _head(db, "B-вторая")["closed_utc"] is None


# -- Закрытие ------------------------------------------------------------

def test_closing_puts_the_call_count_into_the_head_before_the_body_can_die(db, monkeypatch):
    """Шапка хранит то, что переживёт свой источник: тело живёт считанные дни,
    а шапка вечно. Поэтому число вызовов считается ИЗ ТЕЛА при закрытии."""
    rec = bb.open_rec(day=DAY)
    for i in range(3):
        bb.write(rec, "prompt", {"t": f"вопрос {i}"})
        bb.write(rec, "model_out", {"ok": True, "t": f"ответ {i}"})
    assert bb.close_rec(rec, "done") is True
    row = _head(db, rec)
    assert row["calls_n"] == 3, "число вызовов не легло в шапку"
    assert row["outcome"] == "done" and row["closed_utc"] is not None


def test_an_unknown_outcome_is_refused(db, monkeypatch):
    rec = bb.open_rec(day=DAY)
    assert bb.close_rec(rec, "всё_хорошо") is False
    assert _head(db, rec)["closed_utc"] is None


def test_sequence_numbers_do_not_collide_after_a_restart(db, monkeypatch):
    """У записи, продолженной после перезапуска, строки уже есть. Счётчик с
    единицы налетел бы на первичный ключ и потерял строку."""
    rec = bb.open_rec(day=DAY)
    bb.write(rec, "prompt", {"t": "первый"})
    bb.write(rec, "model_out", {"ok": True, "t": "ответ"})

    bb.reset_for_tests()                       # как новый процесс
    bb.write(rec, "prompt", {"t": "третий"})

    seqs = [r["seq"] for r in _bodies(db, rec)]
    assert seqs == [1, 2, 3], f"номера строк столкнулись: {seqs}"


# -- Уборка --------------------------------------------------------------

def _seed(conn, rec, age_days, closed, *, now):
    born = now - timedelta(days=age_days)
    conn.execute(
        "INSERT INTO mx_bb_head (rec_id, code_ver, quota_day, outcome, "
        "calls_n, created_utc, closed_utc) VALUES (?,?,?,?,?,?,?)",
        (rec, "1.10", DAY, "done", 2, born.isoformat(timespec="seconds"),
         born.isoformat(timespec="seconds") if closed else None))
    # ensure_ascii=False обязательно: так пишет сам чёрный ящик, и без этого
    # кириллица уехала бы в escape-последовательности, а поиск по слову
    # «дословные» ниже не нашёл бы ничего и тест был бы зелёным ни о чём.
    conn.execute(
        "INSERT INTO mx_bb_body (rec_id, seq, kind, payload, ts_utc) "
        "VALUES (?,1,'prompt',?,?)",
        (rec, json.dumps({"t": "дословные слова владельца"}, ensure_ascii=False),
         born.isoformat(timespec="seconds")))


def test_the_purge_obeys_all_four_rules_at_once(db):
    """Четыре случая в одном тесте нарочно: правила уборки осмысленны только
    вместе. Проверено на данных, которые ДЕЙСТВИТЕЛЬНО старше срока — иначе
    тест был бы зелёным, ничего не вычистив."""
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    _seed(db, "старая-закрытая", 31, True, now=now)
    _seed(db, "старая-ОТКРЫТАЯ", 31, False, now=now)
    _seed(db, "свежая-закрытая", 2, True, now=now)
    _seed(db, "за-краем", 8, True, now=now)

    assert bb.purge(conn=db, days=7, now_utc=now) == 2

    want = {"старая-закрытая": (1, 0), "старая-ОТКРЫТАЯ": (0, 1),
            "свежая-закрытая": (0, 1), "за-краем": (1, 0)}
    for rec, (flag, rows) in want.items():
        head = _head(db, rec)
        assert head is not None, f"уборка удалила шапку {rec} — она вечна"
        assert head["body_purged"] == flag, f"{rec}: флаг не тот"
        assert len(_bodies(db, rec)) == rows, f"{rec}: тело не то"
        assert head["calls_n"] == 2, f"{rec}: цифры пропали вместе с речью"

    left = db.execute("SELECT count(*) FROM mx_bb_body "
                      "WHERE payload LIKE '%дословные%'").fetchone()[0]
    assert left == 2, "речь владельца убрана не у тех записей"


def test_the_purge_runs_once_a_day_even_across_restarts(db, monkeypatch):
    """Отметка о запуске лежит в базе, а не только в памяти: иначе десять
    перезапусков за вечер дали бы десять уборок."""
    bb.open_rec(day=DAY)
    marks = db.execute("SELECT count(*) FROM mx_counter WHERE name=?",
                       (bb._PURGE_MARK,)).fetchone()[0]
    assert marks == 1, "отметка об уборке за сутки не поставлена"

    bb.reset_for_tests()                       # как новый процесс, те же сутки
    bb.open_rec(day=DAY)
    again = db.execute("SELECT count(*) FROM mx_counter WHERE name=?",
                       (bb._PURGE_MARK,)).fetchone()[0]
    assert again == 1, "уборка повторилась в тот же день после перезапуска"

    bb.reset_for_tests()
    bb.open_rec(day="2026-08-20")              # назавтра право появляется само
    tomorrow = db.execute("SELECT count(*) FROM mx_counter WHERE name=?",
                          (bb._PURGE_MARK,)).fetchone()[0]
    assert tomorrow == 2, "назавтра уборка не запустилась"


def test_the_retention_is_a_setting_so_the_owner_can_shrink_it(db, monkeypatch):
    """Реакция на «записи раздувают базу» — уменьшить срок, а не править код."""
    assert bb.DEFAULT_BODY_DAYS == 7
    monkeypatch.setattr(bb, "_body_days", lambda: 3)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    _seed(db, "пятидневная", 5, True, now=now)
    assert bb.purge(conn=db, now_utc=now) == 1, "срок из настроек не подействовал"


# -- Ящик не ломает дело -------------------------------------------------

def test_a_broken_database_never_breaks_the_deal(monkeypatch):
    """Потерянная строка записи стоит несравнимо меньше, чем несделанное дело.

    После блока 7 базу держит касса, поэтому ломаем именно её: ящик обязан
    вернуть False и посчитать потерю, а не бросить исключение наверх.
    """
    from core import writer

    def boom(fn, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(writer, "write", boom)
    before = bb.lost_count()
    assert bb.open_rec(day=DAY) is None
    assert bb.write("B-любая", "prompt", {"t": "вопрос"}) is False
    assert bb.close_rec("B-любая", "done") is False
    assert bb.lost_count() > before, "потери не посчитаны"


def test_an_old_schema_means_silence_not_a_crash(db, monkeypatch):
    monkeypatch.setattr(bb, "_ready", lambda: False)
    assert bb.open_rec(day=DAY) is None
    assert bb.write("B-любая", "prompt", {"t": "вопрос"}) is False


def test_the_black_box_uses_one_connection_not_one_per_row(db, monkeypatch):
    """Замер: соединение на каждую строку дороже в разы.

    Тест обязан требовать, чтобы строки ПРИ ЭТОМ легли. Первая версия считала
    только открытия и была бы зелёной, даже если ящик не пишет вовсе —
    ноль строк тоже открывает соединение один раз.

    После блока 7 соединением владеет касса, значит проверяем, что и она
    открывает его один раз на все десять строк.
    """
    opened = []
    real = store.open_store

    def counting(*a, **k):
        opened.append(1)
        return real(*a, **k)

    monkeypatch.setattr(store, "open_store", counting)
    rec = bb.open_rec(day=DAY)
    assert rec, "запись не открылась — тест проверял бы пустоту"
    for i in range(10):
        assert bb.write(rec, "prompt", {"t": f"вопрос {i}"}) is True
    assert len(_bodies(db, rec)) == 10, "строки не легли"
    assert len(opened) <= 1, f"соединение открывали {len(opened)} раз"


def test_the_black_box_says_nothing_out_loud():
    """Запись зовут из двери к модели, а у удачного вызова есть сторож,
    требующий полной тишины. Поэтому здесь порядок журнала двери: молча, а
    потери видны числом.

    Ищем ИМЯ функции печати среди лексем, а не подстроку в тексте. Первая
    версия искала подстроку и краснела на слове `_fingerprint` — в нём тоже
    есть «print». Та же болезнь, что пять раз ловили в проекте: поиск слова
    по тексту почти всегда неверен.
    """
    path = ROOT / "core" / "blackbox.py"
    with io.open(path, "rb") as fh:
        names = [t.string for t in tokenize.tokenize(fh.readline)
                 if t.type == tokenize.NAME]
    assert "print" not in names, "чёрный ящик печатает — удачный вызов замолчать не сможет"
    assert hasattr(bb, "lost_count"), "потери должны быть видны числом"


# -- Воспроизведение ------------------------------------------------------

def test_replay_serves_the_recorded_answer_and_refuses_a_miss():
    from tools import replay_session as rs
    rows = [
        {"kind": "prompt", "body": {"h": rs.hashlib.sha256(
            "два плюс два".encode("utf-8")).hexdigest(), "t": "два плюс два"}},
        {"kind": "model_out", "body": {"ok": True, "t": "Четыре, сэр."}},
    ]
    provider = rs.RecordedProvider(rows)
    payload = provider.build_payload("два плюс два")
    assert provider.generate("любая-роль", payload, "ключ") == "Четыре, сэр."

    with pytest.raises(rs.ReplayMiss):
        provider.generate("любая-роль", provider.build_payload("чего в записи нет"), "к")
    assert provider.misses, "промах не зафиксирован"


def test_replay_refuses_to_serve_a_truncated_answer():
    """Отдать обрубок за настоящий ответ — значит соврать: код пойдёт по
    ветке, по которой в тот раз не шёл."""
    from tools import replay_session as rs
    key = rs.hashlib.sha256("вопрос".encode("utf-8")).hexdigest()
    rows = [{"kind": "prompt", "body": {"h": key, "t": "вопрос"}},
            {"kind": "model_out", "body": {"ok": True, "t": "обрубок", "cut": True}}]
    provider = rs.RecordedProvider(rows)
    with pytest.raises(rs.ReplayMiss):
        provider.generate("роль", provider.build_payload("вопрос"), "ключ")


def test_replay_reproduces_a_recorded_refusal_as_a_refusal():
    from tools import replay_session as rs
    key = rs.hashlib.sha256("вопрос".encode("utf-8")).hexdigest()
    rows = [{"kind": "prompt", "body": {"h": key, "t": "вопрос"}},
            {"kind": "model_out", "body": {"ok": False, "e": "rpd"}}]
    provider = rs.RecordedProvider(rows)
    with pytest.raises(RuntimeError):
        provider.generate("роль", provider.build_payload("вопрос"), "ключ")


def test_replay_never_reaches_for_the_network_on_a_miss():
    """Промах обязан быть громким отказом. Уйди он в сеть — воспроизведение
    начало бы тратить квоту и перестало быть бесплатным."""
    import sys as _sys
    from tools import replay_session as rs
    provider = rs.RecordedProvider([])
    before = "google.genai" in _sys.modules
    with pytest.raises(rs.ReplayMiss):
        provider.generate("роль", provider.build_payload("что угодно"), "ключ")
    assert ("google.genai" in _sys.modules) == before, "воспроизведение ввезло SDK"


def test_replay_reads_the_real_home_only_for_reading():
    """Инструмент, как доктор, смотрит но не трогает. Причина замерена:
    дверь к модели ВСЕГДА берёт талон у учёта, поэтому наивное
    воспроизведение записывало бы в учёт вызовы, которых не было."""
    from tools import replay_session as rs
    src = (ROOT / "tools" / "replay_session.py").read_text(encoding="utf-8")
    assert "mode=ro" in src, "настоящая база открывается не только на чтение"
    assert hasattr(rs, "sandbox_home"), "нет отдельной папки под запись"
    # Открытие несуществующей базы не создаёт файл.
    missing = Path(tempfile.mkdtemp(prefix="jv_ro_")) / "нет.db"
    assert rs.open_readonly(missing) is None
    assert not missing.exists(), "чтение создало базу"
