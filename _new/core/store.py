# core/store.py
"""
Stage 2 - store: persistent SQLite foundation for Jarvis (jarvis.db / history.db).

Stage 2.1 laid the connection + WAL + migration framework (PRAGMA user_version)
+ backup/restore. Stage 2.2 adds migration v2: the journal / saga / undo-stack /
execution_log tables that core/journal.py builds on. NOTHING in the live request
pipeline uses these yet - they are additive and covered by tests only.

Design goals (free - fast - offline):
  - stdlib sqlite3 ONLY: no external deps, no network, instant start.
  - One writer per file (engine -> jarvis.db, ingest worker -> history.db later).
  - WAL for concurrent readers; migrations are atomic (all-or-nothing).
  - Downgrade protection: refuse to open a DB newer than the code understands.

Test seam: every public opener accepts an explicit `path=`, so tests never
touch the real ~/.jarvis directory.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


APP_DIR_NAME = ".jarvis"
DB_FILENAME = "jarvis.db"
HISTORY_FILENAME = "history.db"


class StoreError(RuntimeError):
    """Unrecoverable store problem (e.g. DB newer than the code)."""


# -- Paths --------------------------------------------------------------------
# Reuse the SAME hidden home dir the Stage-1 undo backups already use
# (actions/file_controller.py -> ~/.jarvis), so all runtime state lives together.

def app_dir() -> Path:
    """Where Jarvis keeps its private state. Seam: tests pass explicit paths.

    JARVIS_STATE_DIR wins when set. core/safe_json.py has always honoured that
    variable, and this module did not, so JSON state and the database could end
    up in two different places - a test could redirect memory v1 and still write
    the index into the real ~/.jarvis. One state dir, one variable.
    """
    override = os.environ.get("JARVIS_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / APP_DIR_NAME


def db_path() -> Path:
    return app_dir() / DB_FILENAME


def history_path() -> Path:
    return app_dir() / HISTORY_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- Migrations ---------------------------------------------------------------
# Each migration is (version, label, [sql statements]). Statements run one by
# one inside a SINGLE explicit transaction, so a failure rolls the whole step
# back and user_version never advances past a half-applied schema.
#
#   v1  foundation : config_kv + applied_migrations
#   v2  journal    : action_journal + saga + undo_stack + execution_log
#
# The chain is applied in order; a fresh DB jumps straight to the latest.

JARVIS_MIGRATIONS: list = [
    (
        1,
        "foundation: config_kv + applied_migrations",
        [
            """
            CREATE TABLE IF NOT EXISTS config_kv (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS applied_migrations (
                version    INTEGER PRIMARY KEY,
                label      TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """,
        ],
    ),
    (
        2,
        "journal: action_journal + saga + undo_stack + execution_log",
        [
            # Persistent superset of the RAM ring buffer in core/dialogue_state.py.
            """
            CREATE TABLE IF NOT EXISTS action_journal (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ts             TEXT NOT NULL,
                tool           TEXT NOT NULL,
                action         TEXT,
                summary        TEXT NOT NULL,
                ok             INTEGER NOT NULL DEFAULT 1,
                correlation_id TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_action_journal_id ON action_journal (id)",
            # Saga spine: one row per reversible mutation. `inverse` is an
            # idempotent compensation (how to undo), stored as JSON.
            """
            CREATE TABLE IF NOT EXISTS saga (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                correlation_id TEXT,
                tool           TEXT NOT NULL,
                action         TEXT,
                intent         TEXT,
                inverse        TEXT,
                status         TEXT NOT NULL DEFAULT 'intent',
                label          TEXT,
                created_at     TEXT NOT NULL,
                completed_at   TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_saga_status ON saga (status)",
            # LIFO of reversible sagas ready to be undone by voice.
            # undone_at IS NULL  ->  still on the undo stack.
            """
            CREATE TABLE IF NOT EXISTS undo_stack (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                saga_id    INTEGER NOT NULL REFERENCES saga(id),
                label      TEXT NOT NULL,
                inverse    TEXT,
                created_at TEXT NOT NULL,
                undone_at  TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_undo_open ON undo_stack (undone_at)",
            # Step-level trail for the future execution loop (Stage 4).
            """
            CREATE TABLE IF NOT EXISTS execution_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                saga_id  INTEGER REFERENCES saga(id),
                phase    TEXT NOT NULL,
                detail   TEXT
            )
            """,
        ],
    ),
    (
        3,
        "redo: redo_stack for symmetric undo/redo",
        [
            # Mirror of undo_stack: entries land here when something is UNDONE,
            # carrying how to RE-APPLY it (redo). A brand-new forward action
            # clears any open rows (you cannot redo after diverging history).
            """
            CREATE TABLE IF NOT EXISTS redo_stack (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                saga_id    INTEGER,
                label      TEXT NOT NULL,
                redo       TEXT,
                created_at TEXT NOT NULL,
                redone_at  TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_redo_open ON redo_stack (redone_at)",
        ],
    ),
    (
        4,
        "session: tag undo/redo entries with a session id",
        [
            # Per-file history now PERSISTS across restarts (movie-Jarvis memory);
            # session_id lets unscoped 'undo last' stay inside the current run so
            # it never wanders into a previous session's edits (report.txt bug).
            "ALTER TABLE undo_stack ADD COLUMN session_id TEXT",
            "ALTER TABLE redo_stack ADD COLUMN session_id TEXT",
            "CREATE INDEX IF NOT EXISTS ix_undo_session ON undo_stack (session_id)",
            "CREATE INDEX IF NOT EXISTS ix_redo_session ON redo_stack (session_id)",
        ],
    ),
    (
        5,
        "memory v2: memory_fact + dual FTS index",
        [
            # Stage 3B. Memory v1 stored LABELS in a JSON blob and shoved the
            # whole file into the prompt. This table stores meaning instead:
            #   verbatim    - the user's ORIGINAL wording, so recall can answer
            #                 in their words, not 'schedule was updated'
            #   source      - 'explicit' (they said it) vs 'inferred' (the
            #                 model guessed). A guess must never be stated as
            #                 fact.
            #   confidence  - recognised junk is HIDDEN, never deleted.
            #   search_text - normalised blob, computed in PYTHON. SQLite's
            #                 lower() is ASCII-only and silently ignores
            #                 Cyrillic, so folding case in SQL is a no-op.
            """
            CREATE TABLE IF NOT EXISTS memory_fact (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                key           TEXT NOT NULL,
                category      TEXT NOT NULL,
                value         TEXT NOT NULL,
                verbatim      TEXT,
                search_text   TEXT NOT NULL DEFAULT '',
                lang          TEXT,
                source        TEXT NOT NULL DEFAULT 'explicit',
                confidence    REAL NOT NULL DEFAULT 1.0,
                pinned        INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                last_used_at  TEXT,
                use_count     INTEGER NOT NULL DEFAULT 0,
                superseded_by INTEGER REFERENCES memory_fact(id)
            )
            """,
            # One LIVE row per (category, key); superseded history may repeat.
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_fact_live
                ON memory_fact (category, key) WHERE superseded_by IS NULL
            """,
            "CREATE INDEX IF NOT EXISTS ix_memory_fact_conf ON memory_fact (confidence)",
            # TWO indexes on purpose - measured on real data, not assumed:
            #   word: the only one that can answer a 2-char query like 'AI'
            #   tri : the only one that survives Russian inflection
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fact_word USING fts5(
                search_text,
                content='memory_fact',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
            """,
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fact_tri USING fts5(
                search_text,
                content='memory_fact',
                content_rowid='id',
                tokenize='trigram'
            )
            """,
            # External-content FTS does not follow the table on its own.
            # Without these triggers the index drifts and search returns
            # NOTHING while the data sits right there - a silent failure,
            # which is the same class of bug as losing the data outright.
            """
            CREATE TRIGGER IF NOT EXISTS memory_fact_ai AFTER INSERT ON memory_fact
            BEGIN
                INSERT INTO memory_fact_word(rowid, search_text)
                    VALUES (new.id, new.search_text);
                INSERT INTO memory_fact_tri(rowid, search_text)
                    VALUES (new.id, new.search_text);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS memory_fact_ad AFTER DELETE ON memory_fact
            BEGIN
                INSERT INTO memory_fact_word(memory_fact_word, rowid, search_text)
                    VALUES ('delete', old.id, old.search_text);
                INSERT INTO memory_fact_tri(memory_fact_tri, rowid, search_text)
                    VALUES ('delete', old.id, old.search_text);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS memory_fact_au AFTER UPDATE ON memory_fact
            BEGIN
                INSERT INTO memory_fact_word(memory_fact_word, rowid, search_text)
                    VALUES ('delete', old.id, old.search_text);
                INSERT INTO memory_fact_tri(memory_fact_tri, rowid, search_text)
                    VALUES ('delete', old.id, old.search_text);
                INSERT INTO memory_fact_word(rowid, search_text)
                    VALUES (new.id, new.search_text);
                INSERT INTO memory_fact_tri(rowid, search_text)
                    VALUES (new.id, new.search_text);
            END
            """,
        ],
    ),
    (
        6,
        "stage 3A: consent_ticket - durable, single-use confirmations",
        [
            # Until now a confirmation was a boolean the MODEL put into its own
            # call (confirmed=true). It bound to nothing, expired never, could
            # be reused, and died with the process. A dropped websocket in the
            # middle of a confirmed delete therefore lost the user's answer.
            #
            # A consent is now a ROW, and the row is the authority:
            #   fingerprint       binds the answer to ONE operation (core/consent.py).
            #                     This is the anti-TOCTOU property: we execute
            #                     what was described out loud, or nothing.
            #   preview           the exact wording the user heard. Stored so it
            #                     can be re-read verbatim after a restart and
            #                     never paraphrased ('340 files' must not come
            #                     back as '3 files').
            #   status            pending -> consumed | declined | expired | revoked.
            #                     Single-use lives here: consuming is an UPDATE
            #                     guarded by status='pending', so two racing
            #                     calls cannot both win.
            #   expires_at        UTC wall clock. monotonic() cannot survive a
            #                     restart, which is the whole point of 3A.
            #   consumed_saga_id  ties the consent to the journal entry of the
            #                     action it authorised: 'what did I approve last
            #                     Tuesday' becomes answerable.
            #   scope_root/uses   replaces the in-RAM delete-burst dict, which
            #                     refreshed on every delete and so could stretch
            #                     one 'yes' across hours. A scope is bounded in
            #                     BOTH folder and count, and dies with the row.
            """
            CREATE TABLE IF NOT EXISTS consent_ticket (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket           TEXT NOT NULL UNIQUE,
                session_id       TEXT,
                tool             TEXT NOT NULL,
                action           TEXT,
                fingerprint      TEXT NOT NULL,
                payload          TEXT NOT NULL,
                preview          TEXT NOT NULL,
                risk             TEXT NOT NULL DEFAULT 'high',
                reversible       INTEGER NOT NULL DEFAULT 0,
                status           TEXT NOT NULL DEFAULT 'pending',
                origin           TEXT NOT NULL DEFAULT 'interactive',
                created_at       TEXT NOT NULL,
                expires_at       TEXT NOT NULL,
                decided_at       TEXT,
                consumed_at      TEXT,
                consumed_saga_id INTEGER REFERENCES saga(id),
                scope_root       TEXT,
                scope_uses_left  INTEGER,
                CHECK (status IN ('pending','consumed','declined','expired','revoked'))
            )
            """,
            # Sweeping expired tickets and finding a live one are the only two
            # hot queries; both are covered here.
            "CREATE INDEX IF NOT EXISTS ix_consent_live ON consent_ticket (status, expires_at)",
            "CREATE INDEX IF NOT EXISTS ix_consent_fp ON consent_ticket (fingerprint, status)",
            # One live question per operation per session. Without this a model
            # that re-asks in a loop could mint a pile of pending tickets for
            # the same delete, and a later 'yes' would have several rows to
            # match - ambiguity is how the wrong one gets consumed.
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_consent_pending
                ON consent_ticket (fingerprint, session_id) WHERE status='pending'
            """,
        ],
    ),
    # -- Фаза 1. Дальше миграции наши: 7-18, префикс таблиц mx_ --------------
    # Префикс обязателен: рядом живут десять старых таблиц, и «task» или
    # «report» однажды столкнулись бы. Правило Д36 действует на всё ниже:
    # только ДОБАВЛЯТЬ. Ни одна применённая миграция больше не правится —
    # правка невидима на машине владельца (у него она уже применена), но
    # меняет чистую установку, и две машины разъезжаются молча. Держит
    # tests/test_migrations_7_18.py::test_applied_migrations_are_frozen.
    (
        7,
        "mx_task: очередь задач, сквозной номер",
        [
            # Ни одного ВНЕШНЕГО КЛЮЧА и ни одного CHECK — решение принято
            # навсегда, потому что SQLite не умеет их снять без пересборки
            # таблицы, а пересборка запрещена правилом «только добавлять».
            #   внешние ключи: сроки жизни таблиц разные (отчёт 30 дней,
            #     шапка записи навсегда) — уборка старых задач упёрлась бы
            #     в чужие ссылки. Каскадная отмена детей — правило кода;
            #   CHECK на state: автомат состояний ещё вырастет (13.4 п.12
            #     добавляет FROZEN->QUEUED, PARTIAL->QUEUED, истечение
            #     талона). Каждое новое состояние требовало бы пересборки;
            #   уникальность по form_key: Д18 «новая задача той же формы
            #     вытесняет старую» — в момент перехода обе живы, и
            #     уникальный индекс запретил бы сам переход.
            # Проверку целостности делает код и сторожа, а не база.
            """
            CREATE TABLE IF NOT EXISTS mx_task (
                task_id       TEXT PRIMARY KEY,
                schema_ver    INTEGER NOT NULL DEFAULT 1,
                parent_id     TEXT,
                depth         INTEGER NOT NULL,
                type          TEXT NOT NULL,
                form_key      TEXT NOT NULL,
                title         TEXT NOT NULL,
                payload_json  TEXT NOT NULL,
                state         TEXT NOT NULL,
                priority      INTEGER NOT NULL,
                due_utc       TEXT,
                agent_role    TEXT,
                run_id        TEXT,
                attempts      INTEGER NOT NULL DEFAULT 0,
                created_utc   TEXT NOT NULL,
                updated_utc   TEXT NOT NULL,
                finished_utc  TEXT,
                cancel_reason TEXT
            )
            """,
            # Что означает каждое поле и почему оно здесь:
            #   task_id      'T-20260817-001' — сквозной номер (план Р2)
            #   schema_ver   версия формы задачи. Есть с первого дня, иначе
            #                через полгода не отличить старую от битой
            #   parent_id    задача-родитель; NULL = попросил владелец
            #   depth        0 владелец, 1 агент, 2 предел рекурсии
            #   type         из ЗАКРЫТОГО списка config/task_types.yaml
            #   form_key     отпечаток «той же формы» для вытеснения (Д18)
            #   title        как задачу назовёт голос при перечислении
            #   payload_json задача по схеме v1; НЕИЗМЕНЯЕМА после создания
            #   state        автомат состояний (core/task_state.py, блок 9)
            #   priority     меньше = раньше; задачи со временем ниже 0 (Д19)
            #   due_utc      ISO; NULL если без времени
            #   agent_role   РОЛЬ исполнителя, не имя: ядро имён не знает (I21)
            #   run_id       в каком запуске строка родилась. Без него правило
            #                «после перезапуска задача в работе становится
            #                провалена из-за рестарта» нечем проверить: живая
            #                задача и задача из прошлой жизни выглядят одинаково
            #   attempts     повторы, потолок 3 (13.5). В payload его держать
            #                нельзя: задача неизменяема
            #   cancel_reason 'owner_stop' | 'superseded' | 'budget' | 'error'
            # Расхода вызовов модели здесь НЕТ нарочно: он считается из
            # таблицы метеринга по номеру задачи. Две копии одного числа
            # рано или поздно разойдутся, и тогда непонятно, какая права.
            "CREATE INDEX IF NOT EXISTS mx_task_state_idx ON mx_task (state, priority, due_utc)",
            "CREATE INDEX IF NOT EXISTS mx_task_form_idx ON mx_task (form_key, state)",
        ],
    ),
    # Правило колонок для всех миграций ниже (вывод разбора блока 2):
    # добавить колонку потом МОЖНО (ALTER ADD COLUMN со значением по
    # умолчанию), поэтому колонки «на будущее» не выдумываем. Но добавлять
    # СЕЙЧАС обязана каждая колонка, значение которой потом не восстановить:
    # старые строки получат умолчание, и это будет тихая потеря. Отметка
    # времени создания — не восстановима. Флаг «беречь» — восстановим (у
    # старых строк ноль и есть правда), поэтому его нет там, где план его
    # не требует.
    (
        8,
        "mx_task_check: чек-лист приёмки (контур A, Д39)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_task_check (
                task_id        TEXT NOT NULL,
                seq            INTEGER NOT NULL,
                source         TEXT NOT NULL,
                quote          TEXT,
                kind           TEXT NOT NULL,
                arg_json       TEXT NOT NULL,
                result         TEXT,
                said_utc       TEXT NOT NULL,
                done_utc       TEXT,
                quote_redacted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (task_id, seq)
            )
            """,
            #   source          'owner_said' | 'tech_integrity', иных нет (I41).
            #                   Своё требование к количеству в чек-лист не
            #                   попадает никогда
            #   quote           дословные слова владельца, если source=owner_said
            #   kind/arg_json   МАШИННАЯ часть условия. Чистка по возрасту её
            #                   НЕ трогает никогда (Д54): иначе на 31-й день
            #                   приёмка начнёт принимать что угодно, и это
            #                   будет тихо
            #   result          'pass' | 'fail' | 'unknown'. При сомнении
            #                   'unknown' и НЕ блокируем; задача становится
            #                   PARTIAL, а неизвестные пункты перечисляются
            #   said_utc        когда владелец это сказал. Добавлено мной: в
            #                   плане у таблицы нет ни одной колонки времени,
            #                   а Д45 требует чистить дословные слова по
            #                   возрасту 30 дней. Возраст нельзя приделать
            #                   задним числом — у старых строк его негде взять
            #   done_utc        когда пункт выполнен (13.4 п.12): переделка
            #                   PARTIAL -> QUEUED пропускает уже сделанное
            #   quote_redacted  1 = текст заменён служебной формулировкой,
            #                   условие живо. Чистка ЗАМЕНЯЕТ, а не удаляет
            # Индексов нет намеренно: все запросы идут по началу ключа
            # (task_id), а первичный ключ и есть индекс.
        ],
    ),
    (
        9,
        "mx_report: отчёты рабочих агентов (граница Г-1)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_report (
                report_id   TEXT PRIMARY KEY,
                task_id     TEXT NOT NULL,
                schema_ver  INTEGER NOT NULL,
                status      TEXT NOT NULL,
                body_json   TEXT NOT NULL,
                model_name  TEXT,
                prompt_ver  TEXT,
                code_ver    TEXT,
                created_utc TEXT NOT NULL
            )
            """,
            #   status      'done' | 'partial' | 'failed' | 'refused'
            #   body_json   отчёт по схеме v1: ДАННЫЕ, не инструкции (I14).
            #               Только перечисления с числами и кодами; поля со
            #               свободным текстом, который главный мог бы принять
            #               за команду, в схеме отчёта нет вообще
            #   model_name/prompt_ver/code_ver — 13.4 п.14. Восстановить их
            #               потом нельзя: они описывают тот запуск, которого
            #               уже нет. Без них нельзя понять, почему после
            #               правки промпта стало хуже
            # Разбора body_json по колонкам НЕТ намеренно: причина отказа
            # (attribution) выводится из тела и при надобности достанется
            # отдельной колонкой позже — она ВОССТАНОВИМА из тела, а две
            # копии одного факта рано или поздно разойдутся.
            "CREATE INDEX IF NOT EXISTS mx_report_task_idx ON mx_report (task_id)",
        ],
    ),
    (
        10,
        "mx_meter_call + mx_meter_day: единственный учёт вызовов моделей (I16)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_meter_call (
                call_id     TEXT PRIMARY KEY,
                quota_day   TEXT NOT NULL,
                role        TEXT NOT NULL,
                model_name  TEXT NOT NULL,
                key_fp      TEXT,
                task_id     TEXT,
                bucket      TEXT NOT NULL,
                in_tokens   INTEGER,
                out_tokens  INTEGER,
                ok          INTEGER NOT NULL,
                err_kind    TEXT,
                prompt_ver  TEXT,
                code_ver    TEXT,
                started_utc TEXT NOT NULL,
                ms          INTEGER
            )
            """,
            #   quota_day   квотные сутки. Сброс живёт РОВНО В ОДНОМ месте
            #               (metering.quota_day) и больше нигде: второе
            #               вычисление сойдёт с ума раз в полгода, когда в
            #               США переведут часы
            #   role        роль, а не имя модели (I37). Имена моделей живут
            #               только в реестре — здесь они данные, не код
            #   key_fp      ОТПЕЧАТОК ключа, никогда сам ключ. Учёт квоты по
            #               Р12 идёт на ключ, а не на программу: при ротации
            #               «RPD = 500» на систему перестаёт что-то значить.
            #               В плане этой колонки нет — добавлена по Р12
            #   bucket      'dialog' | 'task' | 'background'. Заготовка под
            #               О23: подбюджеты выключены до месяца эксплуатации,
            #               но заполнять поле надо с первого дня, иначе
            #               решать будет не на чем
            #   ok/err_kind 'rpd' | 'rpm' | 'tpm' | 'network' | 'other'.
            #               Исчерпание никогда не молчит (I19)
            #   ms          сколько владелец ЖДАЛ: от занятия места до
            #               закрытия, включая повторы и паузы между ними. Это
            #               НЕ чистое время ответа модели, и путать нельзя.
            #               NULL и 0 — разные вещи: 0 значит «быстрее
            #               миллисекунды» (факт), NULL — «не знаем»
            #               (перезапуск между reserve и commit, закрытие
            #               уборкой close_lost, падение кассы на закрытии).
            #               Заполняется с 28.08.2026; у строк старше этой
            #               даты здесь NULL, и это не порча — колонку
            #               объявили в миграции 10, а писать забыли.
            #               Считается монотонными часами, не по started_utc:
            #               календарные прыгают при сверке времени
            "CREATE INDEX IF NOT EXISTS mx_meter_day_idx ON mx_meter_call (quota_day, role)",
            # Второй индекс — следствие решения блока 1: расход вызовов НЕ
            # хранится колонкой в задаче, он считается отсюда по номеру
            # задачи. Значит этот запрос частый, а таблица самая быстрорастущая
            # в проекте. Без индекса потолок «не больше 8 вызовов на задачу»
            # проверялся бы перебором всей тетради расхода.
            "CREATE INDEX IF NOT EXISTS mx_meter_task_idx ON mx_meter_call (task_id)",
            """
            CREATE TABLE IF NOT EXISTS mx_meter_day (
                quota_day  TEXT NOT NULL,
                role       TEXT NOT NULL,
                model_name TEXT NOT NULL,
                key_fp     TEXT NOT NULL DEFAULT '',
                calls_n    INTEGER NOT NULL DEFAULT 0,
                fail_n     INTEGER NOT NULL DEFAULT 0,
                in_tokens  INTEGER NOT NULL DEFAULT 0,
                out_tokens INTEGER NOT NULL DEFAULT 0,
                cost_micro INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (quota_day, role, model_name, key_fp)
            )
            """,
            # Суточный итог. Номера миграции план ему не выдал (13.4 п.11
            # требует таблицу, нумерация 7-18 её не знает) — поэтому он едет
            # внутрь той же миграции, что и сам расход: цепочка остаётся
            # ровно 7-18, как записано в плане.
            #   хранится БЕССРОЧНО, в отличие от подробностей (30 дней).
            #   Без этого контрольная точка «+3 месяца» и решение по О23
            #   остались бы без данных.
            #   cost_micro  стоимость в миллионных долях, ЦЕЛЫМ числом.
            #   Дробные деньги накапливают ошибку округления, а этот итог
            #   живёт годами.
            #   key_fp в ключе имеет умолчание '' нарочно: в первичный ключ
            #   NULL не годится, а вызовы без ключа (локальные, будущие)
            #   должны попадать в общую строку, а не терять учёт.
            # Итог обязан обновляться В ТОЙ ЖЕ транзакции, что и строка
            # расхода. Иначе ночная уборка подробностей и ночной подсчёт
            # итога однажды поменяются местами, и месяц данных исчезнет
            # безвозвратно.
        ],
    ),
    (
        11,
        "mx_bb_body: чёрный ящик, ТЕЛО (живёт 7 дней)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_bb_body (
                rec_id  TEXT NOT NULL,
                seq     INTEGER NOT NULL,
                kind    TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts_utc  TEXT NOT NULL,
                PRIMARY KEY (rec_id, seq)
            )
            """,
            #   kind     'speech_in'|'prompt'|'model_out'|'tool_call'|
            #            'gate_verdict'|'report'|'spoken'
            #   payload  секреты заглушены ДО записи (I40), единой точкой
            #            вычистки. Не смогли вычистить — не пишем вовсе
            # Индексов нет: воспроизведение сессии читает по началу ключа, а
            # уборка идёт от шапки (там и стоит её индекс). Тело живёт семь
            # дней, поэтому большим не бывает.
        ],
    ),
    (
        12,
        "mx_bb_head: чёрный ящик, ШАПКА (живёт вечно, без свободного текста, I45)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_bb_head (
                rec_id      TEXT PRIMARY KEY,
                task_id     TEXT,
                code_ver    TEXT NOT NULL,
                quota_day   TEXT NOT NULL,
                calls_n     INTEGER NOT NULL DEFAULT 0,
                tools_n     INTEGER NOT NULL DEFAULT 0,
                blocked_n   INTEGER NOT NULL DEFAULT 0,
                outcome     TEXT NOT NULL,
                body_purged INTEGER NOT NULL DEFAULT 0,
                closed_utc  TEXT,
                created_utc TEXT NOT NULL
            )
            """,
            #   code_ver     версия кода. Закрывает подмену эпох: подсчёты не
            #                смешивают разные версии программы
            #   outcome      'done'|'partial'|'failed'|'cancelled'
            #   body_purged  1 = тело убрано по возрасту, шапка осталась
            #   closed_utc   NULL = запись ещё открыта. Уборка ОБЯЗАНА
            #                пропускать открытые записи, иначе однажды
            #                удалит середину живой задачи
            # Свободного текста здесь нет ни в одной колонке — потому шапка и
            # может жить вечно (I45). Проверяется сторожем на состав колонок.
            # Единственная наша таблица, которая растёт вечно И чистится
            # ежедневно, поэтому единственная, которой нужен индекс уборки.
            "CREATE INDEX IF NOT EXISTS mx_bb_head_purge_idx ON mx_bb_head (body_purged, created_utc)",
        ],
    ),
    (
        13,
        "mx_owner_rule: правила владельца (контур D запрещён)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_owner_rule (
                rule_id     TEXT PRIMARY KEY,
                text        TEXT NOT NULL,
                said_utc    TEXT NOT NULL,
                state       TEXT NOT NULL,
                trashed_utc TEXT
            )
            """,
            #   text        дословно; предел длины проверяет код, не база
            #   state       'active' | 'trashed'. Корзина 30 дней
            # Писать сюда имеет право ТОЛЬКО главный и ТОЛЬКО по явной
            # команде владельца (граница Г-3). Собственных выводов из
            # поведения здесь не бывает: контур D закрыт решением владельца.
            # Индекса нет: правил десятки, перебор дешевле индекса.
        ],
    ),
    (
        14,
        "mx_memory_journal: что запомнил и что забыл (Д31)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_memory_journal (
                entry_id TEXT PRIMARY KEY,
                fact_id  INTEGER,
                op       TEXT NOT NULL,
                text     TEXT NOT NULL,
                spoken   INTEGER NOT NULL DEFAULT 0,
                ts_utc   TEXT NOT NULL
            )
            """,
            #   fact_id  ссылка на memory_fact. Тип INTEGER, а не TEXT:
            #            у memory_fact ключ целый (миграция 5). Внешнего
            #            ключа нет — сроки жизни у таблиц разные
            #   op       'add' | 'forget'
            #   spoken   1 = проговорено владельцу вслух (I35). Запись в
            #            память, о которой не сказали, — это уже слежка,
            #            а не память
        ],
    ),
    (
        15,
        "mx_result: указатель на файлы результатов (Д13, Д34)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_result (
                result_id   TEXT PRIMARY KEY,
                task_id     TEXT NOT NULL,
                path        TEXT NOT NULL,
                keep        INTEGER NOT NULL DEFAULT 0,
                created_utc TEXT NOT NULL,
                purge_utc   TEXT
            )
            """,
            #   keep       1 = владелец сказал «сохрани», живёт всегда
            #   purge_utc  создание + 30 дней, потом в корзину Windows.
            #              Не удаление, а корзина: своё владельцу не
            #              уносят молча
        ],
    ),
    (
        16,
        "mx_agent_stat + mx_spawned + mx_reminder",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_agent_stat (
                quota_day  TEXT NOT NULL,
                agent_role TEXT NOT NULL,
                tasks_n    INTEGER NOT NULL DEFAULT 0,
                fail_n     INTEGER NOT NULL DEFAULT 0,
                calls_n    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (quota_day, agent_role)
            )
            """,
            # Роль, а не имя (I21): ядро имён агентов не знает.
            """
            CREATE TABLE IF NOT EXISTS mx_spawned (
                spawn_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                pid         INTEGER NOT NULL,
                proc_start  TEXT,
                cmd_kind    TEXT NOT NULL,
                task_id     TEXT,
                started_utc TEXT NOT NULL,
                reaped_utc  TEXT
            )
            """,
            # ПЕРВИЧНЫЙ КЛЮЧ ЗДЕСЬ ИСПРАВЛЕН ПРОТИВ ПЛАНА, и это не вкус.
            # В плане стоит `pid INTEGER PRIMARY KEY`, но сам план в 13.7.2
            # объявляет блокером: Windows переиспользует номера процессов.
            # Сценарий оттуда: Chrome с номером 3272 закрылся сам, номер
            # достался Word с несохранённым документом. С номером в ключе
            # вторая запись либо не ляжет, либо перезапишет живого ребёнка —
            # и мы потеряем след процесса, который ещё работает.
            #   spawn_id    свой номер: единственное, что не переиспользуется
            #   pid         номер процесса, обычная колонка
            #   proc_start  время старта процесса. Вместе с pid даёт ту самую
            #               пару, по которой Д50 отличает живого от чужого
            #   reaped_utc  NULL = ребёнок ещё жив. Бюджет памяти считается
            #               как родитель плюс живые дети, и этот запрос идёт
            #               каждые десять секунд — отсюда индекс
            "CREATE INDEX IF NOT EXISTS mx_spawned_live_idx ON mx_spawned (reaped_utc)",
            """
            CREATE TABLE IF NOT EXISTS mx_reminder (
                rem_id      TEXT PRIMARY KEY,
                text        TEXT NOT NULL,
                due_utc     TEXT NOT NULL,
                due_raw     TEXT,
                pre_done    INTEGER NOT NULL DEFAULT 0,
                main_done   INTEGER NOT NULL DEFAULT 0,
                retry_done  INTEGER NOT NULL DEFAULT 0,
                state       TEXT NOT NULL,
                created_utc TEXT NOT NULL
            )
            """,
            #   due_utc    срок в UTC — так правильно хранить
            #   due_raw    ИСХОДНАЯ строка как есть. Добавлено мной после
            #              проверки живого кода: actions/reminder.py кладёт в
            #              JSON строку вида '2026-04-10T15:00:00+02:00', то
            #              есть С МЕСТНЫМ СДВИГОМ. Перенос прямо в колонку
            #              с именем due_utc сдвинул бы все напоминания на
            #              часы, и заметили бы это в день перевода часов.
            #              Исходная строка делает будущий перенос
            #              проверяемым, а не «на глаз»
            #   pre_done   предупреждение за 15 минут
            #   main_done  в сам момент
            #   retry_done один повтор через 3 минуты
            #   state      'armed' | 'done' | 'cancelled'
            # Перенос данных из JSON — НЕ здесь: это движение живых данных,
            # ему нужен свой шаг и свои сторожа. Таблица пока пустая.
            "CREATE INDEX IF NOT EXISTS mx_reminder_due_idx ON mx_reminder (state, due_utc)",
        ],
    ),
    (
        17,
        "mx_counter + mx_checkpoint_metric",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_counter (
                quota_day TEXT NOT NULL,
                name      TEXT NOT NULL,
                n         INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (quota_day, name)
            )
            """,
            # Счётчики суток: инициативные реплики, самонаблюдение. Имя
            # счётчика — данные, поэтому новый счётчик не требует миграции.
            """
            CREATE TABLE IF NOT EXISTS mx_checkpoint_metric (
                metric_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                phase           TEXT NOT NULL,
                step            INTEGER NOT NULL,
                ram_main_mb     INTEGER,
                ram_children_mb INTEGER,
                startup_ms      INTEGER,
                fastpass_cold_ms INTEGER,
                fastpass_warm_ms INTEGER,
                tests_total     INTEGER,
                tests_failed    INTEGER,
                suite_seconds   REAL,
                calls_paid_today INTEGER,
                calls_gemma_today INTEGER,
                tasks_done      INTEGER,
                tasks_partial   INTEGER,
                tasks_failed    INTEGER,
                db_size_mb      INTEGER,
                db_user_version INTEGER
            )
            """,
            # Восемнадцать полей из 13.7.17 дословно, плюс свой номер строки.
            # Номера миграции план этой таблице не выдал — едет внутрь 17-й,
            # к счётчикам, чтобы цепочка осталась ровно 7-18.
            # Ключ — свой номер, а НЕ дата: два запуска в один вечер не
            # должны сталкиваться, а именно так и бывает при отладке.
            # Пишет только код в конце вечера; руками не заполняется никогда.
            # Строка в сутки: за 90 вечеров меньше ста килобайт, индекс не
            # нужен.
            # Два разных счётчика вызовов нарочно: платный пул и Gemma живут
            # на разных лимитах, и один общий счётчик скрыл бы, какой из
            # двух кончается.
        ],
    ),
    (
        18,
        "mx_outbound: что ушло в облако (Д40, карантин исходящего)",
        [
            """
            CREATE TABLE IF NOT EXISTS mx_outbound (
                out_id     TEXT PRIMARY KEY,
                quota_day  TEXT NOT NULL,
                role       TEXT NOT NULL,
                model_name TEXT NOT NULL,
                category   TEXT NOT NULL,
                bytes_n    INTEGER NOT NULL DEFAULT 0,
                verdict    TEXT NOT NULL,
                task_id    TEXT,
                sent_utc   TEXT NOT NULL
            )
            """,
            # DDL этой таблицы в плане нет — только словами в Д40: «дата,
            # роль, модель, категория, размер, вердикт владельца. Без
            # содержимого».
            #   category  'owner_phrase' | 'metadata' | 'file_content' |
            #             'screen_image' | 'web_text'
            #   verdict   'allowed' | 'asked_yes' | 'asked_no' | 'blocked'
            #   bytes_n   РАЗМЕР, а не сам кусок
            # КОЛОНКИ ПОД СОДЕРЖИМОЕ ЗДЕСЬ НЕТ, И ЭТО ГЛАВНОЕ СВОЙСТВО
            # ТАБЛИЦЫ. Обещание «без содержимого» словами не держится ничем;
            # отсутствие колонки не обойти даже нарочно. Сторож из плана
            # test_outbound_journal_has_no_payload проверяет форму, а не
            # поведение.
            "CREATE INDEX IF NOT EXISTS mx_outbound_day_idx ON mx_outbound (quota_day)",
        ],
    ),
]

# history.db is populated for real in Stage 7 (world-model ingest); we lay the
# split NOW so we never have to migrate data across files later.
HISTORY_MIGRATIONS: list = [
    (
        1,
        "foundation: observations skeleton",
        [
            """
            CREATE TABLE IF NOT EXISTS observations (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                category TEXT NOT NULL,
                payload  TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_observations_ts ON observations (ts)",
        ],
    ),
]


# -- Connection ---------------------------------------------------------------

def connect(path) -> sqlite3.Connection:
    """Open a SQLite connection tuned for our single-writer WAL model.

    isolation_level=None -> autocommit; we drive migration transactions by hand
    with explicit BEGIN/COMMIT so DDL is atomic and user_version stays consistent.
    """
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_user_version(conn: sqlite3.Connection, v: int) -> None:
    # PRAGMA cannot be parameterised; v is always an int we control.
    conn.execute(f"PRAGMA user_version = {int(v)}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection, migrations: list) -> int:
    """Apply every migration newer than the DB's current user_version.

    - Refuses to run if the DB is NEWER than the code (downgrade protection).
    - Each migration runs in its OWN transaction: all statements + the version
      bump + the history row commit together, or nothing does.
    Returns the resulting user_version.
    """
    current = _user_version(conn)
    latest = max((m[0] for m in migrations), default=0)
    if current > latest:
        # Единственное сообщение этой подсистемы, которое владелец реально
        # когда-нибудь увидит: в тот вечер, когда распакует старый архив на
        # новую базу. Поэтому по-русски и с выходом, а не только с диагнозом.
        raise StoreError(
            f"База данных новее программы: в базе версия {current}, "
            f"эта сборка знает {latest}. Запуск остановлен, чтобы данные "
            f"не испортились. Два выхода: распаковать более новый архив "
            f"проекта, либо вернуть базу командой  "
            f"python tools\\rollback_state.py  и выбрать снимок "
            f"«перед правкой схемы»."
        )
    for version, label, statements in sorted(migrations, key=lambda m: m[0]):
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                conn.execute(stmt)
            # Record history only where that table exists (jarvis.db, not history.db).
            if _table_exists(conn, "applied_migrations"):
                conn.execute(
                    "INSERT OR REPLACE INTO applied_migrations "
                    "(version, label, applied_at) VALUES (?, ?, ?)",
                    (version, label, _now()),
                )
            _set_user_version(conn, version)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _user_version(conn)


def migration_history(conn: sqlite3.Connection) -> list:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT version, label, applied_at FROM applied_migrations "
            "ORDER BY version"
        )
    ]


# == Подготовка схемы (фаза 1, шаг 1.1) ======================================
# Дальше по-русски: с этого шага в файл приедут миграции 7-18, и владелец
# должен уметь прочитать, ПОЧЕМУ решено так, а не только ЧТО сделано.
# Выше (версии 1-6) текст английский и остаётся как есть: правило Д36
# «только добавлять» относится и к комментариям применённых миграций.
#
# Решение владельца, записанное в core/state_snapshot.py дословно:
#   НЕ ВЫШЛО СНЯТЬ — НЕ ПРАВИМ СХЕМУ.
# Ключевое слово — «вечер без новой возможности», а НЕ «вечер без Джарвиса».
# Поэтому неудача копии не бросает исключение: базу открывают журнал, память,
# подтверждения и файловые операции, и StoreError выключил бы Джарвиса
# целиком. Схема просто остаётся старой, а причина называется вслух.
#
# Почему авторитетная версия читается из УЖЕ открытого соединения, а не из
# заголовка файла (замерено в песочнице 17.08.2026, не взято из головы):
# в режиме WAL заголовок отстаёт. После зафиксированной миграции PRAGMA
# отдаёт 7, а первые сто байт файла всё ещё говорят 6, пока журнал не слит.
# Чтение заголовка было бы «быстрым и бесплатным» способом ошибиться.

# Ключ в config_kv: почему схема осталась старой. Переживает перезапуск,
# новых файлов состояния не заводит, таблица есть с версии 1.
SCHEMA_BLOCK_KEY = "schema_block_reason"

# Какая возможность с какой версии схемы живёт. Данные, не код: новая
# возможность — одна строка здесь, ни одной правки ниже.
FEATURE_MIN_VERSION: dict = {
    "tasks": 7,          # очередь задач: mx_task
    "acceptance": 8,     # чек-лист приёмки: mx_task_check
    "reports": 9,        # отчёты агентов: mx_report
    "metering": 10,      # учёт вызовов + суточный итог
    "blackbox": 12,      # чёрный ящик целиком: и тело, и шапка
    "owner_rules": 13,   # правила владельца
    "memory_journal": 14,
    "results": 15,       # указатель на файлы результатов
    "reminders": 16,     # напоминания в базе (перенос из JSON — отдельный шаг)
    "spawned": 16,       # учёт порождённых процессов
    "counters": 17,      # счётчики суток + замеры вечера
    "outbound": 18,      # журнал исходящего в облако
}

# changed: версия схемы сменилась ИМЕННО В ЭТОМ запуске. Нужно, чтобы
# состояние дома переписали один раз и только по делу, а не каждый старт.
_SCHEMA_STATE: dict = {"have": None, "knows": None, "ready": None,
                       "reason": None, "changed": False, "lines": []}

# Защёлка на запуск: базу открывают семь мест, и семь попыток снять копию
# на диске, где для неё нет места, — это семь одинаковых отказов подряд.
_SCHEMA_REFUSED = False


def reset_schema_state() -> None:
    """Забыть решение про схему. Зовёт tests/conftest.py на каждый тест.

    Имя нарочно длинное: store.reset() читалось бы как «стереть базу».
    """
    global _SCHEMA_REFUSED
    _SCHEMA_REFUSED = False
    _SCHEMA_STATE.update({"have": None, "knows": None, "ready": None,
                          "reason": None, "changed": False, "lines": []})


def schema_state() -> dict:
    """Что известно про схему: есть / знает / готова / причина / строки.

    Копия, а не сам словарь: состояние подсистемы никто не правит снаружи.
    """
    out = dict(_SCHEMA_STATE)
    out["lines"] = list(_SCHEMA_STATE.get("lines") or [])
    return out


def supports(feature: str) -> bool:
    """Есть ли в базе то, что нужно этой возможности.

    Заводится вместе с первой новой таблицей, а не «когда понадобится»:
    иначе восемь блоков фазы 1 будут ловить «нет такой таблицы» каждый
    по-своему, и однажды — молча.
    """
    need = FEATURE_MIN_VERSION.get(feature)
    if need is None:
        return False
    have = _SCHEMA_STATE.get("have")
    return isinstance(have, int) and have >= int(need)


def _is_home_db(path: Path) -> bool:
    """Это настоящая база дома, а не переданная параметром?

    Через normcase: Windows не различает регистр, а сравнение строк
    различает — забор просто не сработал бы (план Р7).
    """
    try:
        want = os.path.normcase(str(db_path().resolve()))
        got = os.path.normcase(str(Path(path).resolve()))
        return want == got
    except OSError:
        return False


def _pre_migrate_snapshot(collect) -> bool:
    """Копия дома перед правкой схемы. Никогда не бросает.

    Ленивый импорт (грабли 19): на обычном запуске, когда правки нет,
    модуль снимков не ввозится вообще и стартовый бюджет не двигается.
    """
    try:
        from core import state_snapshot
        return bool(state_snapshot.ensure_pre_migrate_snapshot(printer=collect))
    except Exception as exc:                      # noqa: BLE001 - старт важнее
        collect(f"[Схема] копия не вышла: {type(exc).__name__}: {exc}")
        return False


def _remember_block(conn: sqlite3.Connection, reason: str) -> None:
    """Записать причину отказа в саму базу. Молчаливой неудачи быть не может."""
    try:
        config_set(conn, SCHEMA_BLOCK_KEY, f"{_now()} {reason}")
    except sqlite3.Error:
        pass


def _forget_block(conn: sqlite3.Connection) -> None:
    try:
        if config_get(conn, SCHEMA_BLOCK_KEY) is not None:
            config_set(conn, SCHEMA_BLOCK_KEY, None)
    except sqlite3.Error:
        pass


def _checkpoint(conn: sqlite3.Connection) -> None:
    """Слить журнал WAL в саму базу после смены версии.

    Без этого tools/doctor.py, который нарочно читает заголовок и НЕ
    открывает базу, показал бы владельцу версию 6 после успешного
    обновления — то есть соврал бы. Занято другим читателем — вернёт
    busy и просто ничего не сделает, это не ошибка.
    """
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def _prepare_schema(conn: sqlite3.Connection, path, migrations: list) -> None:
    """Единственное место, где схема настоящей базы меняется.

    Порядок ровно такой и другим быть не может: сначала копия, потом правка.

    Состояние (`schema_state`) описывает ТОЛЬКО настоящую базу дома. Иначе
    любой инструмент, открывший постороннюю базу третьей версии, молча
    переписал бы ответ на вопрос «а моя таблица уже есть?» — и через полгода
    выключенная возможность объяснялась бы чужим файлом. История (history.db)
    сюда не заходит: у неё своя цепочка и до фазы 7 её не открывают.
    """
    global _SCHEMA_REFUSED
    home = _is_home_db(path)
    current = _user_version(conn)
    latest = max((m[0] for m in migrations), default=0)
    if home:
        _SCHEMA_STATE.update({"have": current, "knows": latest})

    if current == latest:                     # обычный путь: ноль работы
        if home:
            _SCHEMA_STATE.update({"ready": True, "reason": None})
        return
    if current < latest and home and current >= 1:
        # Настоящая база, в ней есть что терять, и правка предстоит.
        if _SCHEMA_REFUSED:
            _SCHEMA_STATE["ready"] = False
            return
        lines: list = []
        if not _pre_migrate_snapshot(lines.append):
            _SCHEMA_REFUSED = True
            _SCHEMA_STATE.update({
                "ready": False,
                "reason": "не вышла копия базы; схема осталась старой",
                "lines": lines,
            })
            _remember_block(conn, _SCHEMA_STATE["reason"])
            return
        _SCHEMA_STATE["lines"] = lines

    # current > latest тоже приходит сюда: пусть migrate() скажет своё
    # «база новее кода». Перехватывать это здесь нельзя — отказ обязан
    # быть громким.
    try:
        migrate(conn, migrations)
    except Exception:
        if home:
            _SCHEMA_STATE.update({"have": _user_version(conn), "ready": False})
        raise
    after = _user_version(conn)
    if not home:
        return
    _SCHEMA_STATE.update({"have": after, "ready": after == latest,
                          "changed": after > current})
    if after > current:
        # Слив журнала нужен только настоящей базе: её заголовок читает
        # доктор, не открывая файл. Свежим базам тестов он не нужен.
        _checkpoint(conn)
        _forget_block(conn)
        _SCHEMA_STATE["reason"] = None


def ensure_schema(*, printer=None) -> dict:
    """Поднять схему настоящей базы в известный момент запуска.

    Зачем отдельный вызов, если open_store и так мигрирует: иначе схему
    меняет тот, кто первым захотел журнал или память, — то есть в
    непредсказуемый момент, некому доложить владельцу, и метка сборки
    может записать одну версию, а файл состояния дома — другую.
    """
    conn = None
    try:
        conn = open_store()
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    state = schema_state()
    if printer is not None:
        for line in state.get("lines") or []:
            printer(line)
        if state.get("reason"):
            printer(f"[Схема] {state['reason']}")
    return state


# -- Public openers -----------------------------------------------------------

def open_store(path=None) -> sqlite3.Connection:
    """Open (creating + migrating if needed) the engine DB jarvis.db."""
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    _prepare_schema(conn, p, JARVIS_MIGRATIONS)
    return conn


def open_history(path=None) -> sqlite3.Connection:
    """Open (creating + migrating if needed) the ingest DB history.db."""
    p = Path(path) if path else history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    migrate(conn, HISTORY_MIGRATIONS)
    return conn


# -- config_kv helpers --------------------------------------------------------

def config_set(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO config_kv (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, None if value is None else str(value), _now()),
    )


def config_get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM config_kv WHERE key=?", (key,)).fetchone()
    return row[0] if row is not None else default


# -- Backup / restore ---------------------------------------------------------

def backup(conn: sqlite3.Connection, dest_path) -> Path:
    """Copy the live DB to dest via the SQLite backup API (WAL-safe, online).

    The backup API is the officially blessed path (not a file copy): it produces
    a consistent snapshot even while the source is open in WAL mode.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(str(dest_path))
    try:
        with dest:
            conn.backup(dest)
    finally:
        dest.close()
    return dest_path
