# -*- coding: utf-8 -*-
"""
Сторожа фазы 1б: по журналу двери видно, КТО просил действие (I11).

ЗАЧЕМ ЭТОТ ФАЙЛ
---------------
До фазы 1б строка журнала гейта отвечала на «что сделали и почему разрешили»,
но не на «кто просил». Ни task_id, ни роли агента, ни цепочки в ней не было —
проверено грепом, а не по документам: план утверждал, что origin_chain «уже
есть», и это оказалось расхождением Р-3.

Пока этого нет, агентов пускать нельзя: на вопрос «кто удалил папку» ответить
будет нечем, а это граница Г-2 проекта. Ворота плана (строка 1925) сказаны
прямо: «Нет цепочки → не начинать агентов».

ГЛАВНАЯ ОПАСНОСТЬ, ОТ КОТОРОЙ ЗДЕСЬ ЗАЩИТА
------------------------------------------
Грабли №4, уже случившиеся в учёте расхода (core/metering.py:199, замер
20.08.2026): тест проверял пропуск В КОНТЕКСТЕ и был зелёным, а до базы
номер дела не доезжал НИКОГДА, потому что живые вызывающие пропуск не
передают. Дословно оттуда: «Проверять надо результат, а не полпути».

Здесь та же мина. Живых вызовов двери четыре (main.py:1334,
agent/executor.py:171, core/offline_core.py:285, check_lang.py:840) и НИ ОДИН
не передаёт ctx. Поэтому каждый сторож ниже читает СТРОКУ ЖУРНАЛА после
вызова, а не проверяет, что параметр принят. Тест, проверяющий приём
параметра, был бы зелёным при полностью пустых полях.

Запуск:  python -m pytest tests/test_gate_origin_chain.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import audit_log                      # noqa: E402
from core import task_context                   # noqa: E402
from core.gate import dispatch                  # noqa: E402
from core.safe_json import STATE_DIR_ENV        # noqa: E402
from core.task_context import TaskCtx, bind, run_id   # noqa: E402


class _Home:
    """Временный дом, полностью возвращаемый на выходе.

    Тот же приём, что в tests/test_audit_log_step32.py: настоящий ~/.jarvis
    не трогается ни на чтение, ни на запись.
    """

    def __enter__(self) -> "_Home":
        self.tmp = Path(tempfile.mkdtemp(prefix="jv_chain_test_"))
        self._env = os.environ.get(STATE_DIR_ENV)
        os.environ[STATE_DIR_ENV] = str(self.tmp)
        audit_log.reset()
        return self

    def __exit__(self, *exc) -> bool:
        if self._env is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = self._env
        audit_log.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    @property
    def journal(self) -> Path:
        return self.tmp / "logs" / "gate-audit.jsonl"

    def records(self) -> list:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in
                self.journal.read_text(encoding="utf-8").splitlines() if line.strip()]

    def last(self) -> dict:
        rows = self.records()
        assert rows, "журнал пуст — касса молчит, а решение было"
        return rows[-1]


# ── Главный сторож: цепочка доезжает БЕЗ явного ctx ──────────────────────

def test_chain_reaches_the_journal_without_an_explicit_ctx():
    """Дверь без ctx внутри `with bind(...)` — цепочка всё равно в журнале.

    ЭТО САМЫЙ ВАЖНЫЙ ТЕСТ ФАЙЛА, и он написан именно так нарочно. Живые
    вызывающие двери пропуск не передают, значит проверять надо ровно этот
    случай. Если однажды кто-то уберёт откат на контекст в gate._origin,
    оставив только параметр, — покраснеет здесь, а не через полгода на
    вопросе владельца «кто это сделал».
    """
    with _Home() as home:
        ctx = TaskCtx(run_id=run_id(), task_id="T-20260828-777", bucket="task",
                      origin_chain=("owner", "main"))
        with bind(ctx):
            dispatch("web_search", {"query": "x"}, mode="autonomous")
        line = home.last()
        assert line["task_id"] == "T-20260828-777", \
            "номер дела не доехал до журнала — вопрос «кто просил» без ответа"
        assert line["origin_chain"] == ["owner", "main"], \
            f"цепочка не та: {line['origin_chain']}"
        assert line["depth"] == 0


def test_an_explicit_ctx_wins_over_the_context():
    """Явно переданный пропуск сильнее того, что в контексте.

    Порядок старшинства взят из шапки core/task_context (правило 1),
    записанной ДО этой фазы именно ради неё: два источника правды разошлись
    бы молча, и понять это было бы негде.
    """
    with _Home() as home:
        in_context = TaskCtx(run_id=run_id(), task_id="T-CONTEXT", bucket="task",
                             origin_chain=("owner", "main"))
        explicit = TaskCtx(run_id=run_id(), task_id="T-EXPLICIT", bucket="task",
                           origin_chain=("owner", "main"))
        with bind(in_context):
            dispatch("web_search", {"query": "x"}, mode="autonomous",
                     ctx=explicit)
        assert home.last()["task_id"] == "T-EXPLICIT", \
            "контекст пересилил явный пропуск — появилось два источника правды"


def test_the_agent_chain_carries_the_agent_role():
    """У подчинённого агента в журнале стоит его роль и полная цепочка."""
    with _Home() as home:
        task = TaskCtx(run_id=run_id(), task_id="T-20260828-001", bucket="task",
                       origin_chain=("owner", "main"))
        agent = task.child(agent_role="pc_operator")
        with bind(agent):
            dispatch("web_search", {"query": "x"}, mode="autonomous")
        line = home.last()
        assert line["agent_role"] == "pc_operator"
        assert line["origin_chain"] == ["owner", "main", "pc_operator"], \
            f"цепочка агента не полна: {line['origin_chain']}"
        assert line["depth"] == 1


# ── Дефект, найденный замером при подготовке фазы ────────────────────────

def test_a_queued_task_is_not_less_traceable_than_a_chat():
    """Цепочка задачи не короче, чем у разговора.

    НАЙДЕНО ЗАМЕРОМ 28.08.2026, а не вычитано в плане. agent/task_queue.py
    создавал пропуск без origin_chain, брал значение по умолчанию ("owner",)
    и получал цепочку КОРОЧЕ, чем у обычного диалога:

        задача агента : owner
        диалог        : owner -> main

    То есть работа без владельца у экрана выглядела МЕНЕЕ прослеженной, чем
    разговор при нём. Ворота фазы («в журнале есть цепочка») открылись бы на
    цепочке, теряющей звено `main`, — зелёный свет по неверной причине.

    Сторож сравнивает две цепочки между собой, а не с записанной строкой:
    так он останется верным, даже если состав звеньев однажды изменится.
    """
    from agent import task_queue as tq

    chat = task_context.dialog_ctx().origin_chain
    src = Path(tq.__file__).read_text(encoding="utf-8")
    assert 'origin_chain=("owner", "main")' in src, \
        ("agent/task_queue не задаёт origin_chain явно — цепочка задачи "
         "потеряет звено `main` и станет короче, чем у разговора")
    assert list(chat) == ["owner", "main"], \
        f"цепочка разговора изменилась ({chat}) — сверить с task_queue"


# ── Совместимость: старое поведение не тронуто ───────────────────────────

def test_without_any_context_the_line_still_has_all_the_fields():
    """Без пропуска поля есть, но пусты — «не знаем» отличимо от «нет поля».

    Пустое место и отсутствующее поле — разные вещи: по первому видно, что
    подпись пытались поставить, по второму — что писал старый код. Читателю
    журнала это различие нужнее, чем экономия ста байт.
    """
    with _Home() as home:
        with bind(None):
            dispatch("web_search", {"query": "x"}, mode="interactive")
        line = home.last()
        for key in ("task_id", "agent_role", "origin_chain", "depth"):
            assert key in line, f"поля {key} нет вовсе"
        # Дела нет — номера нет. А цепочка есть всегда: пропуск разговора
        # существует всегда (task_context.dialog_ctx).
        assert line["task_id"] is None
        assert line["origin_chain"] == ["owner", "main"]


def test_the_old_fields_are_all_still_there():
    """Формат только ДОПОЛНЯЕТСЯ (audit_log, правило 5).

    Тот же список, что в tests/test_audit_log_step32.py. Продублирован
    нарочно: если новая подпись однажды вытеснит старое поле, покраснеть
    должны оба файла, а не только соседний.
    """
    with _Home() as home:
        dispatch("web_search", {"query": "x"}, mode="interactive")
        line = home.last()
        for key in ("schema_ver", "ts", "ts_utc", "tool", "action", "mode",
                    "verdict", "risk", "policy", "reason", "param_keys"):
            assert key in line, f"старое поле {key} пропало из строки"


def test_the_schema_version_did_not_move():
    """Версия формата НЕ поднята, и это осознанно.

    Поля добавляются — читатели, сверяющие версию на равенство
    (core/state_version.py:210), не должны сломаться. Поднять «на всякий
    случай» значило бы сломать их без причины.
    """
    with _Home() as home:
        dispatch("web_search", {"query": "x"}, mode="interactive")
        assert home.last()["schema_ver"] == 1


def test_values_still_never_reach_the_journal():
    """Значения параметров не текут — подпись не ослабила правило 6.

    Проверяется вместе с подписью нарочно: новая подпись — это новый повод
    случайно записать лишнее, а журнал теперь вечный.
    """
    with _Home() as home:
        ctx = TaskCtx(run_id=run_id(), task_id="T-20260828-001", bucket="task",
                      origin_chain=("owner", "main"))
        with bind(ctx):
            dispatch("web_search", {"query": "marker-secret-xyz"},
                     mode="autonomous")
        text = home.journal.read_text(encoding="utf-8")
        assert "marker-secret-xyz" not in text, "значение параметра утекло"


# ── Надёжность: подпись не имеет права ломать дело ───────────────────────

def test_a_broken_context_does_not_break_the_verdict():
    """Пропуск сломан — решение двери всё равно принято и записано.

    Правило кассы (audit_log, правило 1) сильнее полноты подписи: записка о
    решении не стоит того, чтобы из-за неё не выполнилось действие. Здесь
    ломается сам источник пропуска, то есть худший случай.
    """
    with _Home() as home:
        broken = object()   # ни task_id, ни origin_chain
        r = dispatch("web_search", {"query": "x"}, mode="interactive",
                     ctx=broken)
        assert r.verdict == "run", "сломанный пропуск изменил вердикт двери"
        line = home.last()
        assert line["tool"] == "web_search", "решение не записано вовсе"


def test_the_gate_verdict_never_depends_on_the_chain():
    """Пропуск подписывает строку, но НЕ меняет решение.

    Как только цепочка начнёт влиять на вердикт, у политики появится второй
    хозяин, и разобрать «почему отказал» станет негде. Решает
    core/security.py, и только он. Заборы фазы 1б-2 будут отдельным слоем и
    будут возвращать свой отказ явно, а не подкручивать этот.
    """
    with _Home():
        plain = dispatch("web_search", {"query": "x"}, mode="autonomous")
        deep = TaskCtx(run_id=run_id(), task_id="T-1", bucket="task",
                       origin_chain=("owner", "main")
                       ).child(agent_role="pc_operator")
        with bind(deep):
            signed = dispatch("web_search", {"query": "x"}, mode="autonomous")
        assert plain.verdict == signed.verdict
        assert plain.risk == signed.risk and plain.policy == signed.policy


# ── Правило 2 шапки task_context: поток пула не смешивает дела ───────────

def test_two_tasks_in_one_pooled_thread_do_not_mix():
    """Два дела по очереди в ОДНОМ потоке — подписи не перепутались.

    Прямо из шапки core/task_context (правило 2): потоки в пуле
    переиспользуются, и забытый пропуск всплыл бы в следующем чужом деле.
    Проверяется на журнале, потому что именно там ошибка была бы видна
    владельцу.
    """
    with _Home() as home:
        seen = []

        def one(task_id):
            ctx = TaskCtx(run_id=run_id(), task_id=task_id, bucket="task",
                          origin_chain=("owner", "main"))
            with bind(ctx):
                dispatch("web_search", {"query": "x"}, mode="autonomous")
            seen.append(task_id)

        # Один и тот же поток обслуживает два дела подряд.
        t = threading.Thread(target=lambda: (one("T-AAA"), one("T-BBB")))
        t.start()
        t.join()

        rows = [r["task_id"] for r in home.records()]
        assert rows == ["T-AAA", "T-BBB"], \
            f"подписи перепутались между делами в одном потоке: {rows}"
        # И после выхода из обоих `with` в потоке не осталось чужого пропуска.
        assert seen == ["T-AAA", "T-BBB"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_a_task_does_not_burn_a_level_of_delegation():
    """Задача не съедает уровень поручения.

    НАЙДЕНО ЗАМЕРОМ 28.08.2026 В МОЕЙ ЖЕ ПРАВКЕ, и это главная причина, по
    которой сторож существует. Сначала я поставил задаче depth=1, объяснив
    это тем, что «длина цепочки и глубина суть одно». Объяснение неверно, и
    опровергает его `dialog_ctx()`: цепочка owner->main при depth=0.

    Цена ошибки замерена:
        depth=1 -> агент 2 -> подчинённый ЗАПРЕЩЁН (MAX_DEPTH=2)
        depth=0 -> агент 1 -> подчинённый 2, разрешён
    Лишняя единица молча отбирала целый уровень поручения — тот самый, что
    схема базы называет прямо (core/store.py: «0 владелец, 1 агент, 2
    предел»). Ошибка тихая: журнал выглядел исправным, а делегирование
    ломалось бы только в фазе 2, далеко от причины.

    Сторож считает БЮДЖЕТ, а не сверяет число: так он останется верным, даже
    если MAX_DEPTH однажды поднимут.
    """
    from agent import task_queue as tq

    src = Path(tq.__file__).read_text(encoding="utf-8")
    # Ищем ПРИСВОЕНИЕ в вызове TaskCtx, а не слово в прозе: иначе сторож
    # ловил бы собственный объясняющий комментарий.
    #
    # Разбор скобок СЧИТАЕТ ГЛУБИНУ, и это тоже исправление ошибки, пойманной
    # мутацией 28.08.2026: первая версия делала split(")") и обрывалась на
    # `run_id()` — сторож проверял строку 'run_id=run_id(' и был зелёным при
    # любой глубине. Грабли №4 проекта в чистом виде: проверялись полпути.
    head = src.split("TaskCtx(", 1)[1]
    level, end = 1, len(head)
    for i, ch in enumerate(head):
        level += (ch == "(") - (ch == ")")
        if level == 0:
            end = i
            break
    call = head[:end]
    assert "TaskCtx(" in src, "пропуск задачи больше не создаётся здесь"
    assert "depth=" not in call, \
        ("agent/task_queue задаёт глубину задачи вручную — она съедает "
         f"уровень поручения, и подчинённый агента невозможен: {call!r}")

    # Настоящий бюджет: из пропуска задачи должно хватить на агента И на его
    # подчинённого, иначе предел рекурсии израсходован до начала работы.
    task = task_context.TaskCtx(run_id="r", task_id="T-1", bucket="task",
                                origin_chain=("owner", "main"))
    agent = task.child(agent_role="pc_operator")
    sub = agent.child(agent_role="researcher")
    assert list(sub.origin_chain) == ["owner", "main", "pc_operator",
                                      "researcher"]
    assert sub.depth == task_context.MAX_DEPTH
