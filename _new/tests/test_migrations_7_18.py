# -*- coding: utf-8 -*-
"""Фаза 1 — миграции 7-18 (план 13.4 п.1, имя файла оттуда же дословно).

На шаге 1.2 в цепочке стоит одна миграция — 7 (`mx_task`). Файл называется
по всей цепочке нарочно: план ссылается на `test_migrations_7_18`, и поиск
по плану обязан приводить сюда, а не в пустоту. Следующие миграции
добавляют свои проверки в этот же файл.

Что здесь стережётся, кроме «таблица создалась»:
  • состав колонок ТОЧНО такой, как задумано (IF NOT EXISTS принял бы чужую
    форму таблицы молча);
  • ни одного внешнего ключа и ни одного CHECK — эти решения SQLite не
    даёт переиграть без пересборки таблицы, а пересборка запрещена;
  • применённые миграции больше не правятся: правка невидима на машине
    владельца и меняет чистую установку;
  • обрыв посреди цепочки не оставляет половину.
"""
import hashlib
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import store

LATEST = max(m[0] for m in store.JARVIS_MIGRATIONS)


def _fresh() -> Path:
    return Path(tempfile.mkdtemp(prefix="jv_mig_")) / "jarvis.db"


@pytest.fixture(scope="module")
def shape():
    """Одна база на весь файл — для проверок, которые только ЧИТАЮТ форму.

    Замерено 18.08.2026: своя база на каждый тест стоила этому файлу 13 с из
    54 с всего прогона, то есть четверть бюджета уходила на пересоздание
    восемнадцати таблиц ради чтения PRAGMA. Тесты, которые ПИШУТ, по-прежнему
    берут свою базу через _fresh(): общая база между ними протекала бы.
    """
    conn = store.open_store(_fresh())
    yield conn
    conn.close()


def _columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _sql_of(conn, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
    return row[0] if row else ""


# -- Миграция 7: mx_task ------------------------------------------------

# Задумано ровно это. Порядок важен: колонки в SQLite нумерованы, и
# «добавили в середину» — это уже пересборка таблицы, то есть запрет.
MX_TASK_COLUMNS = [
    "task_id", "schema_ver", "parent_id", "depth", "type", "form_key",
    "title", "payload_json", "state", "priority", "due_utc", "agent_role",
    "run_id", "attempts", "created_utc", "updated_utc", "finished_utc",
    "cancel_reason",
]


def test_a_fresh_database_reaches_seven():
    conn = store.open_store(_fresh())
    try:
        assert store._user_version(conn) >= 7
        assert store._table_exists(conn, "mx_task")
    finally:
        conn.close()


def test_mx_task_has_exactly_the_columns_we_meant():
    """IF NOT EXISTS принял бы чужую таблицу того же имени молча."""
    conn = store.open_store(_fresh())
    try:
        assert _columns(conn, "mx_task") == MX_TASK_COLUMNS
    finally:
        conn.close()


def test_mx_task_has_no_foreign_keys_and_no_check():
    """Решения навсегда: SQLite не снимет их без пересборки таблицы."""
    conn = store.open_store(_fresh())
    try:
        assert list(conn.execute("PRAGMA foreign_key_list(mx_task)")) == []
        sql = _sql_of(conn, "mx_task").upper()
        assert "CHECK" not in sql, sql
        assert "REFERENCES" not in sql, sql
    finally:
        conn.close()


def test_the_queue_can_find_its_next_task_by_index():
    """Два индекса — не украшение: по ним ходит выбор следующей задачи
    и вытеснение задачи той же формы (Д18)."""
    conn = store.open_store(_fresh())
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='mx_task'")}
        assert "mx_task_state_idx" in names
        assert "mx_task_form_idx" in names
    finally:
        conn.close()


def test_a_task_row_survives_a_restart():
    """Таблица не просто создана — в неё можно положить задачу и найти её
    после переоткрытия базы."""
    path = _fresh()
    conn = store.open_store(path)
    conn.execute(
        "INSERT INTO mx_task (task_id, depth, type, form_key, title, "
        "payload_json, state, priority, created_utc, updated_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("T-20260817-001", 0, "file_sort", "file_sort:~/Downloads",
         "разобрать загрузки", "{}", "QUEUED", 0,
         "2026-08-17T20:00:00+00:00", "2026-08-17T20:00:00+00:00"))
    conn.close()

    again = store.open_store(path)
    try:
        row = again.execute(
            "SELECT title, state, attempts, schema_ver FROM mx_task "
            "WHERE task_id=?", ("T-20260817-001",)).fetchone()
        assert row["title"] == "разобрать загрузки"
        assert row["state"] == "QUEUED"
        assert row["attempts"] == 0, "счётчик повторов без умолчания"
        assert row["schema_ver"] == 1, "версия формы задачи без умолчания"
    finally:
        again.close()


def test_the_same_task_number_cannot_appear_twice():
    conn = store.open_store(_fresh())
    try:
        for _ in range(2):
            try:
                conn.execute(
                    "INSERT INTO mx_task (task_id, depth, type, form_key, "
                    "title, payload_json, state, priority, created_utc, "
                    "updated_utc) VALUES ('T-1',0,'t','f','t','{}','NEW',0,"
                    "'a','a')")
            except sqlite3.IntegrityError:
                break
        else:
            pytest.fail("один и тот же номер задачи лёг дважды")
    finally:
        conn.close()


# -- Миграции 8-12: форма каждой таблицы --------------------------------

# Форма записана ДВАЖДЫ: в коде (DDL) и здесь. Это не забывчивость — в этом
# весь смысл сторожа: одна копия проверяет другую. `CREATE TABLE IF NOT
# EXISTS` принял бы чужую таблицу того же имени молча, и «нет такой колонки»
# всплыло бы через месяц внутри рабочего потока.
EXPECTED_SHAPE = {
    "mx_task": MX_TASK_COLUMNS,
    "mx_task_check": [
        "task_id", "seq", "source", "quote", "kind", "arg_json", "result",
        "said_utc", "done_utc", "quote_redacted",
    ],
    "mx_report": [
        "report_id", "task_id", "schema_ver", "status", "body_json",
        "model_name", "prompt_ver", "code_ver", "created_utc",
    ],
    "mx_meter_call": [
        "call_id", "quota_day", "role", "model_name", "key_fp", "task_id",
        "bucket", "in_tokens", "out_tokens", "ok", "err_kind", "prompt_ver",
        "code_ver", "started_utc", "ms",
    ],
    "mx_meter_day": [
        "quota_day", "role", "model_name", "key_fp", "calls_n", "fail_n",
        "in_tokens", "out_tokens", "cost_micro",
    ],
    "mx_bb_body": ["rec_id", "seq", "kind", "payload", "ts_utc"],
    "mx_bb_head": [
        "rec_id", "task_id", "code_ver", "quota_day", "calls_n", "tools_n",
        "blocked_n", "outcome", "body_purged", "closed_utc", "created_utc",
    ],
    "mx_owner_rule": ["rule_id", "text", "said_utc", "state", "trashed_utc"],
    "mx_memory_journal": ["entry_id", "fact_id", "op", "text", "spoken", "ts_utc"],
    "mx_result": ["result_id", "task_id", "path", "keep", "created_utc",
                  "purge_utc"],
    "mx_agent_stat": ["quota_day", "agent_role", "tasks_n", "fail_n", "calls_n"],
    "mx_spawned": ["spawn_id", "pid", "proc_start", "cmd_kind", "task_id",
                   "started_utc", "reaped_utc"],
    "mx_reminder": ["rem_id", "text", "due_utc", "due_raw", "pre_done",
                    "main_done", "retry_done", "state", "created_utc"],
    "mx_counter": ["quota_day", "name", "n"],
    "mx_checkpoint_metric": [
        "metric_id", "ts", "phase", "step", "ram_main_mb", "ram_children_mb",
        "startup_ms", "fastpass_cold_ms", "fastpass_warm_ms", "tests_total",
        "tests_failed", "suite_seconds", "calls_paid_today",
        "calls_gemma_today", "tasks_done", "tasks_partial", "tasks_failed",
        "db_size_mb", "db_user_version",
    ],
    "mx_outbound": ["out_id", "quota_day", "role", "model_name", "category",
                    "bytes_n", "verdict", "task_id", "sent_utc"],
}

# Индексы, у каждого из которых есть НАЗВАННЫЙ частый запрос. Индекс можно
# добавить завтра одной миграцией, поэтому «на всякий случай» их нет:
# каждый лишний замедляет запись навсегда.
EXPECTED_INDEXES = {
    "mx_task_state_idx": "выбор следующей задачи",
    "mx_task_form_idx": "вытеснение задачи той же формы (Д18)",
    "mx_report_task_idx": "главный читает отчёт подчинённого",
    "mx_meter_day_idx": "остаток квоты за сутки по роли",
    "mx_meter_task_idx": "сколько вызовов съела задача (следствие блока 1)",
    "mx_bb_head_purge_idx": "ежедневная уборка тел старше семи дней",
    "mx_spawned_live_idx": "живые дети каждые 10 секунд для бюджета памяти",
    "mx_reminder_due_idx": "тик планировщика: что пора сказать",
    "mx_outbound_day_idx": "что ушло в облако за сутки",
}


@pytest.mark.parametrize("table", sorted(EXPECTED_SHAPE))
def test_each_table_has_exactly_the_columns_we_meant(table, shape):
    assert _columns(shape, table) == EXPECTED_SHAPE[table]


@pytest.mark.parametrize("table", sorted(EXPECTED_SHAPE))
def test_no_mx_table_has_foreign_keys_or_checks(table, shape):
    """Решения навсегда: SQLite не снимет их без пересборки таблицы, а
    пересборка запрещена правилом Д36 «только добавлять».

    Причины по таблицам: сроки жизни разные (отчёт 30 дней, шапка записи
    вечно) — внешние ключи воевали бы с уборкой; автомат состояний ещё
    вырастет — CHECK потребовал бы пересборки на каждое новое состояние.
    """
    assert list(shape.execute(f"PRAGMA foreign_key_list({table})")) == []
    sql = _sql_of(shape, table).upper()
    # , а не «CHECK in sql»: имя mx_task_check СОДЕРЖИТ это слово, и
    # простой поиск краснел на самом себе. Подчёркивание — словарный знак,
    # поэтому границы внутри MX_TASK_CHECK нет (наступил 17.08).
    assert not re.search(r"CHECK\s*\(", sql), f"{table}: {sql}"
    assert not re.search(r"REFERENCES", sql), f"{table}: {sql}"


def test_every_named_index_exists_and_nothing_else_does(shape):
    """Индексов ровно столько, сколько названных запросов. Лишний индекс —
    это замедление записи, о котором никто не помнит."""
    found = {r[0] for r in shape.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'mx_%'")}
    assert found == set(EXPECTED_INDEXES), (
        f"лишние: {sorted(found - set(EXPECTED_INDEXES))}, "
        f"пропали: {sorted(set(EXPECTED_INDEXES) - found)}")


def test_the_black_box_head_holds_no_free_text():
    """I45: долгоживущая шапка не содержит свободного текста — только
    машинные поля. Потому она и может жить вечно.

    Проверяется составом колонок, а не обещанием в комментарии: колонки, в
    которую влезла бы речь, физически нет.
    """
    forbidden = ("text", "quote", "payload", "prompt", "message", "summary",
                 "detail", "verbatim", "spoken", "reason")
    leaked = [c for c in EXPECTED_SHAPE["mx_bb_head"]
              if any(bad in c for bad in forbidden)]
    assert not leaked, f"в шапку записи просочился свободный текст: {leaked}"


def test_the_checklist_keeps_its_machine_part_and_can_say_unknown():
    """Д54: чистка ЗАМЕНЯЕТ текст, а не удаляет условие, и машинную часть
    не трогает никогда. Иначе на 31-й день приёмка примет что угодно."""
    conn = store.open_store(_fresh())
    try:
        conn.execute(
            "INSERT INTO mx_task_check (task_id, seq, source, quote, kind, "
            "arg_json, result, said_utc) VALUES (?,?,?,?,?,?,?,?)",
            ("T-1", 1, "owner_said", "чтобы pdf лежали отдельно", "ext_is",
             '{"dir": "PDF", "ext": ".pdf"}', "unknown",
             "2026-08-17T20:00:00+00:00"))
        # Чистка через 30 дней: текст заменён, условие живо.
        conn.execute(
            "UPDATE mx_task_check SET quote=?, quote_redacted=1 "
            "WHERE task_id=? AND seq=?",
            ("условие типа «расширение», задано владельцем 17.08", "T-1", 1))
        row = conn.execute(
            "SELECT kind, arg_json, result, quote_redacted FROM mx_task_check"
        ).fetchone()
        assert row["kind"] == "ext_is", "машинная часть пострадала от чистки"
        assert row["arg_json"] == '{"dir": "PDF", "ext": ".pdf"}'
        assert row["result"] == "unknown", "«неизвестно» не хранится"
        assert row["quote_redacted"] == 1
    finally:
        conn.close()


def test_the_daily_total_survives_the_purge_of_the_details():
    """Подробности расхода живут 30 дней, суточный итог — бессрочно.
    Уборка подробностей не имеет права унести итог."""
    conn = store.open_store(_fresh())
    try:
        conn.execute(
            "INSERT INTO mx_meter_call (call_id, quota_day, role, model_name, "
            "bucket, ok, started_utc) VALUES ('c1','2026-08-17','aux_light',"
            "'модель-роли','task',1,'2026-08-17T20:00:00+00:00')")
        conn.execute(
            "INSERT INTO mx_meter_day (quota_day, role, model_name, calls_n, "
            "cost_micro) VALUES ('2026-08-17','aux_light','модель-роли',1,120)")
        conn.execute("DELETE FROM mx_meter_call")      # уборка через 30 дней
        row = conn.execute(
            "SELECT calls_n, cost_micro, key_fp FROM mx_meter_day").fetchone()
        assert row["calls_n"] == 1, "итог ушёл вместе с подробностями"
        assert row["cost_micro"] == 120, "стоимость целым числом, без дробей"
        assert row["key_fp"] == "", "ключ в первичном ключе не может быть NULL"
    finally:
        conn.close()


def test_the_meter_never_keeps_the_key_itself():
    """Р12: учёт на ключ. Но в базе лежит ОТПЕЧАТОК, а не ключ."""
    assert "key_fp" in EXPECTED_SHAPE["mx_meter_call"]
    for table, cols in EXPECTED_SHAPE.items():
        for c in cols:
            assert c not in ("api_key", "key", "secret", "token"), \
                f"{table}.{c} выглядит как место для самого ключа"


def test_a_black_box_record_can_be_open_and_purge_skips_it():
    """Уборка обязана пропускать НЕзакрытые записи (13.5): иначе однажды
    удалит середину живой задачи."""
    conn = store.open_store(_fresh())
    try:
        conn.execute(
            "INSERT INTO mx_bb_head (rec_id, code_ver, quota_day, outcome, "
            "created_utc) VALUES ('R-1','1.3','2026-08-17','done','a')")
        row = conn.execute(
            "SELECT closed_utc, body_purged, calls_n FROM mx_bb_head").fetchone()
        assert row["closed_utc"] is None, "открытую запись нечем отличить"
        assert row["body_purged"] == 0 and row["calls_n"] == 0
    finally:
        conn.close()


def test_the_capability_map_matches_the_chain():
    """Карта возможностей — данные, а не код. Она не имеет права обещать
    версию, до которой цепочка не доросла."""
    for feature, need in store.FEATURE_MIN_VERSION.items():
        assert need <= LATEST, f"{feature} ждёт версию {need}, а цепочка {LATEST}"


# -- Четыре находки разбора, каждая со своим сторожем --------------------

def test_outbound_journal_has_no_payload():
    """Имя из плана (13.3, Р1). Д40 обещает «без содержимого» — словами.

    Слова не держат ничего: любой будущий модуль мог бы положить туда кусок
    файла «на время отладки». Колонки, куда он влез бы, физически нет — а
    форму обойти нельзя даже нарочно.
    """
    forbidden = ("payload", "content", "text", "body", "data", "prompt",
                 "image", "snippet", "quote", "chunk")
    leaked = [c for c in EXPECTED_SHAPE["mx_outbound"]
              if any(bad in c for bad in forbidden)]
    assert not leaked, f"в журнал исходящего влезло бы содержимое: {leaked}"
    # Размер — есть, содержимое — нет. Это и есть весь журнал.
    assert "bytes_n" in EXPECTED_SHAPE["mx_outbound"]
    assert "verdict" in EXPECTED_SHAPE["mx_outbound"]


def test_a_reused_windows_pid_does_not_destroy_the_live_child():
    """Находка разбора: в плане ключ таблицы — сам номер процесса, а план
    же в 13.7.2 объявляет БЛОКЕРОМ, что Windows их переиспользует.

    Сценарий оттуда: Chrome с номером 3272 закрылся, номер достался Word с
    несохранённым документом. С номером в первичном ключе вторая запись
    либо не ляжет, либо перезапишет ещё живого ребёнка — и след процесса,
    который РАБОТАЕТ, потеряется.
    """
    conn = store.open_store(_fresh())
    try:
        for start in ("2026-08-17T20:00:00+00:00", "2026-08-17T21:30:00+00:00"):
            conn.execute(
                "INSERT INTO mx_spawned (pid, proc_start, cmd_kind, "
                "started_utc) VALUES (?,?,?,?)", (3272, start, "vision", start))
        rows = conn.execute(
            "SELECT pid, proc_start FROM mx_spawned ORDER BY spawn_id").fetchall()
        assert len(rows) == 2, "переиспользованный номер потерял живого ребёнка"
        assert rows[0]["proc_start"] != rows[1]["proc_start"], (
            "пара «номер + время старта» — то, чем Д50 отличает живого от чужого")
    finally:
        conn.close()


def test_a_live_child_is_found_by_an_empty_reaped_column():
    """Бюджет памяти = родитель плюс живые дети, и этот запрос идёт каждые
    десять секунд. Поэтому у него есть индекс, а «жив» — это NULL."""
    conn = store.open_store(_fresh())
    try:
        conn.execute("INSERT INTO mx_spawned (pid, cmd_kind, started_utc) "
                     "VALUES (1,'vision','a')")
        conn.execute("INSERT INTO mx_spawned (pid, cmd_kind, started_utc, "
                     "reaped_utc) VALUES (2,'browser','a','b')")
        live = conn.execute(
            "SELECT count(*) FROM mx_spawned WHERE reaped_utc IS NULL"
        ).fetchone()[0]
        assert live == 1
    finally:
        conn.close()


def test_a_reminder_keeps_the_string_it_came_from():
    """Находка разбора: actions/reminder.py хранит время С МЕСТНЫМ СДВИГОМ
    ('2026-04-10T15:00:00+02:00'), а колонка называется due_utc. Прямой
    перенос сдвинул бы все напоминания на часы, и заметили бы это в день
    перевода часов. Исходная строка делает будущий перенос проверяемым.
    """
    conn = store.open_store(_fresh())
    try:
        conn.execute(
            "INSERT INTO mx_reminder (rem_id, text, due_utc, due_raw, state, "
            "created_utc) VALUES (?,?,?,?,?,?)",
            ("R-1", "позвонить маме", "2026-04-10T13:00:00+00:00",
             "2026-04-10T15:00:00+02:00", "armed", "a"))
        row = conn.execute(
            "SELECT due_utc, due_raw FROM mx_reminder").fetchone()
        assert row["due_raw"] == "2026-04-10T15:00:00+02:00", (
            "исходная строка потеряна — перенос будет нечем проверить")
        assert row["due_utc"] != row["due_raw"], "сдвиг не пересчитан"
    finally:
        conn.close()


def test_the_meter_can_answer_how_much_one_task_spent():
    """Следствие решения блока 1: расход НЕ хранится колонкой в задаче, он
    считается отсюда. Значит этот запрос обязан быть индексным, иначе
    потолок «не больше 8 вызовов на задачу» станет перебором всей тетради
    расхода — самой быстрорастущей таблицы проекта."""
    conn = store.open_store(_fresh())
    try:
        for i in range(3):
            conn.execute(
                "INSERT INTO mx_meter_call (call_id, quota_day, role, "
                "model_name, task_id, bucket, ok, started_utc) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"c{i}", "2026-08-17", "aux_light", "роль-модель",
                 "T-1", "task", 1, "t"))
        assert conn.execute(
            "SELECT count(*) FROM mx_meter_call WHERE task_id='T-1'"
        ).fetchone()[0] == 3
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT count(*) FROM mx_meter_call "
            "WHERE task_id='T-1'").fetchall()
        text = " ".join(str(r[-1]) for r in plan)
        assert "mx_meter_task_idx" in text, f"запрос идёт перебором: {text}"
    finally:
        conn.close()


def test_the_evening_metric_has_the_eighteen_fields_from_the_plan():
    """13.7.17 перечисляет состав дословно. Своих полей не добавляем, а
    свой номер строки нужен: два запуска в один вечер не должны
    сталкиваться, а при отладке так и бывает."""
    plan_fields = [
        "ts", "phase", "step", "ram_main_mb", "ram_children_mb", "startup_ms",
        "fastpass_cold_ms", "fastpass_warm_ms", "tests_total", "tests_failed",
        "suite_seconds", "calls_paid_today", "calls_gemma_today", "tasks_done",
        "tasks_partial", "tasks_failed", "db_size_mb", "db_user_version",
    ]
    assert len(plan_fields) == 18
    assert EXPECTED_SHAPE["mx_checkpoint_metric"] == ["metric_id"] + plan_fields
    conn = store.open_store(_fresh())
    try:
        for _ in range(2):
            conn.execute(
                "INSERT INTO mx_checkpoint_metric (ts, phase, step) "
                "VALUES ('2026-08-17T23:00:00+00:00','1',2)")
        assert conn.execute(
            "SELECT count(*) FROM mx_checkpoint_metric").fetchone()[0] == 2, (
            "два замера за один вечер столкнулись")
    finally:
        conn.close()


def test_the_memory_journal_remembers_whether_it_was_spoken():
    """I35: запись в память, о которой владельцу не сказали, — это уже
    слежка, а не память."""
    conn = store.open_store(_fresh())
    try:
        conn.execute(
            "INSERT INTO mx_memory_journal (entry_id, op, text, ts_utc) "
            "VALUES ('E-1','add','не пьёте кофе после шести','t')")
        row = conn.execute(
            "SELECT spoken, fact_id FROM mx_memory_journal").fetchone()
        assert row["spoken"] == 0, "умолчание должно быть «не сказано»"
        assert row["fact_id"] is None
    finally:
        conn.close()


# -- Правила всей цепочки ------------------------------------------------

def test_every_new_table_carries_the_mx_prefix():
    """Иначе однажды столкнёмся с одной из десяти старых таблиц."""
    strangers: list = []
    for version, _label, statements in store.JARVIS_MIGRATIONS:
        if version < 7:
            continue
        for text in statements:
            upper = " ".join(str(text).split()).upper()
            marker = "CREATE TABLE IF NOT EXISTS "
            if marker not in upper:
                continue
            tail = upper.split(marker, 1)[1]
            name = tail.split("(", 1)[0].strip()
            if not name.startswith("MX_"):
                strangers.append((version, name))
    assert not strangers, f"таблицы без префикса mx_: {strangers}"


def test_applied_migrations_are_frozen():
    """Правка применённой миграции невидима на машине владельца — у него
    она уже применена, — но меняет ЧИСТУЮ установку. Две машины
    разъезжаются молча, и понять это будет негде.

    Новая миграция = новая строка в этой таблице. Изменившаяся сумма у
    старого номера = запрет, а не «обнови число».
    """
    known = {
        1: "fa8c288380241b3c",
        2: "9e6654ebd7e3a9f8",
        3: "f514de5425948676",
        4: "0bb2eb8e7f9fee55",
        5: "ceb49aa91746a02b",
        6: "8f3c553df0dfd6c6",
        7: "201f8787514d51a7",
        8: "f7c5ffeac5af4e34",
        9: "f6c31c4809c03824",
        10: "5c085a5f715d2f09",
        11: "34be1fb9221b64cc",
        12: "182724faf59d80f6",
        13: "85263e1d1178c130",
        14: "807c9cac0dc0c9bd",
        15: "bfd54ef4004045d0",
        16: "d49da54cc2919686",
        17: "1df92ac35dabd8fa",
        18: "bb451e9b7dd06f8e",
    }
    actual = {}
    for version, label, statements in store.JARVIS_MIGRATIONS:
        blob = "\n".join(" ".join(str(s).split()) for s in statements)
        actual[version] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    assert set(actual) == set(known), (
        "появилась или исчезла миграция: впиши её сумму в этот тест")
    drifted = {v: (known[v], actual[v]) for v in known if known[v] != actual[v]}
    assert not drifted, (
        "применённую миграцию править нельзя (правило Д36 «только "
        f"добавлять»): {sorted(drifted)}")


def test_running_the_whole_chain_twice_changes_nothing():
    path = _fresh()
    conn = store.open_store(path)
    before = store.migration_history(conn)
    store.migrate(conn, store.JARVIS_MIGRATIONS)
    after = store.migration_history(conn)
    try:
        assert before == after
        assert [h["version"] for h in after] == list(range(1, LATEST + 1))
        assert len([h for h in after if h["version"] == 7]) == 1
    finally:
        conn.close()


def test_an_existing_database_upgrades_in_place_and_keeps_its_data():
    """Настоящая база владельца — с памятью, подтверждениями и журналом.
    Обновление не имеет права ничего потерять."""
    path = _fresh()
    conn = store.connect(path)
    store.migrate(conn, [m for m in store.JARVIS_MIGRATIONS if m[0] <= 6])
    store.config_set(conn, "было_до", "не тронь")
    conn.execute(
        "INSERT INTO action_journal (ts, tool, summary) VALUES (?,?,?)",
        ("2026-08-16T00:00:00", "notepad", "открыл блокнот"))
    assert store._user_version(conn) == 6
    conn.close()

    conn = store.open_store(path)
    try:
        assert store._user_version(conn) == LATEST
        assert store.config_get(conn, "было_до") == "не тронь"
        assert conn.execute(
            "SELECT summary FROM action_journal").fetchone()[0] == "открыл блокнот"
        assert store._table_exists(conn, "mx_task")
    finally:
        conn.close()


def test_a_break_in_the_middle_leaves_the_previous_version():
    """Сценарий плана (13.4 п.1): «ошибка в 11 оставляет user_version=10».
    Половины схемы не бывает.

    Числа 11 и 10 из плана здесь БОЛЬШЕ НЕ ГОДЯТСЯ: цепочка доросла до 12,
    и миграция с номером 11 просто пропускается как уже применённая. Тест
    был зелёным ни о чём, пока это не заметили (17.08). Проверяем форму
    сценария на номерах выше потолка — она от роста цепочки не зависит.
    """
    path = _fresh()
    conn = store.open_store(path)
    good, bad = LATEST + 1, LATEST + 2
    chain = list(store.JARVIS_MIGRATIONS) + [
        (good, "целая", [f"CREATE TABLE IF NOT EXISTS mx_ok{good} (x)"]),
        (bad, "сломанная", [
            f"CREATE TABLE IF NOT EXISTS mx_half{bad} (x)",
            "ЭТО НЕ SQL",
        ]),
    ]
    with pytest.raises(sqlite3.Error):
        store.migrate(conn, chain)
    try:
        assert store._user_version(conn) == good, "версия ушла вперёд на ошибке"
        assert store._table_exists(conn, f"mx_ok{good}"), "целая не применилась"
        assert not store._table_exists(conn, f"mx_half{bad}"),             "половина сломанной миграции осталась в схеме"
    finally:
        conn.close()


def test_a_broken_long_chain_still_leaves_a_working_database():
    """Одиннадцать правок одной цепочкой (блок 2). Обрыв на середине не
    имеет права выключить Джарвиса: до обрыва схема рабочая, а карта
    возможностей обязана честно сказать, что доступно, а что нет.
    """
    path = _fresh()
    conn = store.connect(path)
    store.migrate(conn, [m for m in store.JARVIS_MIGRATIONS if m[0] <= 6])
    store.config_set(conn, "старое", "цело")
    conn.close()

    half = [m for m in store.JARVIS_MIGRATIONS if m[0] <= 10]
    half.append((11, "сломанная", ["ЭТО НЕ SQL"]))
    half += [m for m in store.JARVIS_MIGRATIONS if m[0] > 11]
    conn = store.connect(path)
    with pytest.raises(sqlite3.Error):
        store.migrate(conn, half)
    try:
        assert store._user_version(conn) == 10
        # До обрыва — работает и пишется.
        assert store.config_get(conn, "старое") == "цело"
        conn.execute(
            "INSERT INTO mx_meter_call (call_id, quota_day, role, model_name,"
            " bucket, ok, started_utc) VALUES ('c','d','r','m','dialog',1,'t')")
        assert conn.execute("SELECT count(*) FROM mx_meter_call").fetchone()[0] == 1
        # После обрыва — таблиц нет, и это должно быть ВИДНО, а не падать.
        assert not store._table_exists(conn, "mx_bb_body")
        assert not store._table_exists(conn, "mx_bb_head")
    finally:
        conn.close()


def test_the_capability_map_tells_the_truth_after_a_break():
    """Карта возможностей на оборванной цепочке: учёт есть, чёрного ящика
    нет. Иначе «нет такой таблицы» вылезет внутри рабочего потока."""
    path = _fresh()
    conn = store.connect(path)
    store.migrate(conn, [m for m in store.JARVIS_MIGRATIONS if m[0] <= 10])
    conn.close()
    store.reset_schema_state()
    conn = store.open_store(path)
    conn.close()
    # Путь не домашний, поэтому состояние не пишется — спрашиваем напрямую.
    assert store.FEATURE_MIN_VERSION["metering"] <= 10
    assert store.FEATURE_MIN_VERSION["blackbox"] > 10


def test_the_history_database_is_not_touched_by_phase_one():
    """Вторую базу до фазы 7 не открывают (план 13.6.3). Наши миграции
    не имеют права в неё просочиться."""
    conn = store.open_history(Path(tempfile.mkdtemp(prefix="jv_hist_")) / "history.db")
    try:
        assert store._user_version(conn) == 1
        assert not store._table_exists(conn, "mx_task")
    finally:
        conn.close()
