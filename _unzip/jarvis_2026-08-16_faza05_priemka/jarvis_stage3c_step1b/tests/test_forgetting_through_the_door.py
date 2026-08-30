# tests/test_forgetting_through_the_door.py
"""
Забывание и вспоминание ходят через дверь (фаза 1г).

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ И ПОЧЕМУ ЭТОГО НЕ БЫЛО РАНЬШЕ
---------------------------------------------------
Фаза 1в провела через дверь ТОЛЬКО `save_memory` и оставила записку
(main.py, ловушка №1): переносить остальные два инструмента памяти нельзя,
потому что `forget_memory` и `recall_memory` ОТСУТСТВУЮТ в политике
`core/security.py`, а дверь по правилу fail-closed отвечает неизвестному
инструменту `blocked: Unknown tool`. Перенос убил бы забывание насмерть,
и 1837 сторожей остались бы зелёными — потому что этих путей у двери
никто не проверял.

ЗАМЕР, СДЕЛАННЫЙ ДО ПРАВКИ (28.08.2026, живой вызов двери)
----------------------------------------------------------
    save_memory      interactive  allowed=True   verdict=run
    save_memory      autonomous   allowed=True   verdict=run
    forget_memory    interactive  allowed=False  blocked: Unknown tool
    forget_memory    autonomous   allowed=False  blocked: Unknown tool
    recall_memory    interactive  allowed=False  blocked: Unknown tool
    recall_memory    autonomous   allowed=False  blocked: Unknown tool

Ловушка подтверждена не рассуждением, а вызовом: третья и пятая строки —
это то, что владелец услышал бы как «Джарвис забыл, как забывать».

РЕШЕНИЕ ВЛАДЕЛЬЦА, КОТОРОЕ ЭТИ СТОРОЖА ЗАКРЕПЛЯЮТ (28.08.2026)
--------------------------------------------------------------
Дословно: «я хочу чтобы когда owner (то есть я) говорю запомнить или
забыть и т.д. (касаемо сохранить, удалить или обновить в памяти) то дверь
без проблем пропускала».

    | кто           | запомнить | забыть/обновить | прочитать |
    | владелец      | молча     | молча           | молча     |
    | главный ИИ    | молча     | молча           | молча     |
    | под-агент     | НЕЛЬЗЯ    | НЕЛЬЗЯ          | можно     |

Ни одной клетки «спросить подтверждение» — на прямой вопрос владелец
ответил: «нет, мне надоест мне всегда подтверждать ему». Поэтому риск
обоих инструментов — `low`, как у `save_memory`: вердикт `run`, вопросов
владельцу нет, опыт не меняется. Появляется ровно две вещи: строка в
журнале двери и замок для под-агента.

ПОЧЕМУ ЧТЕНИЕ РАЗРЕШЕНО ПОД-АГЕНТУ, А УДАЛЕНИЕ НЕТ
---------------------------------------------------
I12/Г-3 запрещает под-агентам ПИСАТЬ в память, потому что запись меняет
будущее поведение Джарвиса. Удаление меняет его точно так же — факт
исчез, поведение поехало, — поэтому `forget_memory` встаёт в забор
рядом с `save_memory`. Чтение поведения не меняет и помогает под-агенту
сделать работу лучше (знать, что владелец предпочитает NVIDIA, полезно
при поиске цены), поэтому `recall_memory` в забор НЕ входит. Это решение
владельца, а не догадка: «да, я согласен» на предложенную таблицу.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


# ── Помощник: поддельный Джарвис, как в сторожах фазы 1в ──────────────────────
def _fake_jarvis(main, monkeypatch):
    """Живой `_execute_tool` на пустышке вместо настоящего Джарвиса.

    Тот же приём, что в tests/test_memory_write_through_door.py: нам нужен
    ровно один метод, а не звук, окна и сеть.
    """
    jl = main.JarvisLive.__new__(main.JarvisLive)
    jl.ui = SimpleNamespace(muted=True, screen_control=False,
                            set_state=lambda *a, **k: None)
    jl.session = None
    return jl


def _subagent_ctx(role: str = "pc_operator"):
    """Пропуск под-агента — строго через `.child()`, как в живом коде.

    Собирать `TaskCtx(agent_role=...)` руками нельзя: у настоящего под-агента
    заполнены ещё `depth`, `parent_id` и `origin_chain`, и забор в фазе 2
    может смотреть на любое из них. Тест, который лепит пропуск сам,
    проверял бы конфигурацию, какой в жизни не бывает.
    """
    from core import task_context

    root = task_context.TaskCtx(run_id="R-test", task_id="T-1")
    return root.child(agent_role=role, task_id="T-2")


# ── 1. Политика знает оба инструмента ────────────────────────────────────────
def test_the_door_now_knows_forgetting_and_recalling():
    """Дверь пропускает forget/recall владельцу — вердикт `run`, без вопросов.

    Прямая замена сторожа фазы 1в
    `test_the_door_still_does_not_know_forgetting_and_recalling`, который
    закреплял ПРОШЛОЕ положение («дверь их не знает»). Тот сторож честно
    предупреждал: «Если его добавили в политику осознанно — обновите этот
    тест». Ровно это и произошло.
    """
    from core import gate

    for tool, params in (("forget_memory", {"key": "k"}),
                         ("recall_memory", {"query": "кофе"})):
        r = gate.dispatch(tool, params, mode="interactive")
        assert r.allowed, (
            f"{tool} закрыт для владельца. Решение владельца от 28.08.2026: "
            "«дверь без проблем пропускала». Если риск подняли — это "
            "нарушение прямого указания.")
        assert r.verdict == "run", (
            f"{tool} получил вердикт {r.verdict!r}, а не 'run'. Если это "
            "'confirm' — владельца начали переспрашивать, чего он прямо "
            "просил не делать: «мне надоест мне всегда подтверждать».")


def test_the_owner_is_never_asked_to_confirm_forgetting():
    """Ни один инструмент памяти не требует подтверждения у владельца.

    Отдельный сторож, а не ассерт внутри предыдущего: он охраняет НАМЕРЕНИЕ
    владельца, а не механику двери. Если кто-то из лучших побуждений
    поставит `forget_memory` риск high («удаление же опасно»), тест назовёт
    причину, по которой так делать нельзя.
    """
    from core.security import SECURITY_POLICY

    for tool in ("save_memory", "forget_memory", "recall_memory"):
        pol = SECURITY_POLICY.get(tool)
        assert pol is not None, f"{tool} исчез из политики"
        assert pol.risk == "low", (
            f"{tool} имеет риск {pol.risk!r}. Риск выше low означает "
            "«спросить владельца» в разговоре — прямо запрещено решением "
            "от 28.08.2026. Запрет под-агенту живёт в core/fences.py, а не "
            "в риске: это разные вопросы («кто просит» vs «насколько "
            "опасно»), см. шапку fences.py.")


# ── 2. Оба инструмента реально ходят через дверь ─────────────────────────────
def test_forgetting_and_recalling_are_routed_through_the_door(monkeypatch):
    """`forget_memory` и `recall_memory` СПРАШИВАЮТ дверь.

    Обратная замена сторожа фазы 1в
    `test_forgetting_and_recalling_are_not_routed_through_the_door`.
    Тогда проверялось «мимо двери» (иначе они умирали), теперь — «через
    дверь». Сторож смотрит на факт вызова `core.security.check_tool_call`,
    потому что это единственное место, где принимается решение (Г-2).
    """
    main = pytest.importorskip("main")
    jl = _fake_jarvis(main, monkeypatch)

    hits = []
    import core.security as sec
    real = sec.check_tool_call
    monkeypatch.setattr(sec, "check_tool_call",
                        lambda *a, **k: hits.append(a[0]) or real(*a, **k))

    monkeypatch.setattr("memory.memory_manager.forget",
                        lambda *a, **k: "Forgotten: k", raising=False)
    monkeypatch.setattr("memory.fact_store.recall",
                        lambda *a, **k: "нашёл: кофе", raising=False)

    asyncio.run(jl._execute_tool(SimpleNamespace(
        name="forget_memory", args={"key": "k"}, id="1")))
    asyncio.run(jl._execute_tool(SimpleNamespace(
        name="recall_memory", args={"query": "кофе"}, id="2")))

    assert "forget_memory" in hits, (
        "forget_memory прошёл МИМО двери — удаление факта снова без следа")
    assert "recall_memory" in hits, (
        "recall_memory прошёл МИМО двери — чтение памяти без следа")


def test_forgetting_still_works_and_reports_the_truth(monkeypatch):
    """Забывание не сломалось: результат `forget()` доезжает до модели дословно.

    Самый важный сторож файла. Именно эту поломку предсказывала записка
    фазы 1в: провести через дверь и получить «Unknown tool» вместо
    удаления. Тест проверяет не «дверь пропустила», а КОНЕЧНЫЙ результат —
    что факт действительно удалён и модель получила правду, а не бодрое
    «ок».
    """
    main = pytest.importorskip("main")
    jl = _fake_jarvis(main, monkeypatch)

    called = []
    monkeypatch.setattr(
        "memory.memory_manager.forget",
        lambda k, c=None: called.append((k, c)) or "Forgotten: preferences/cake",
        raising=False)

    resp = asyncio.run(jl._execute_tool(SimpleNamespace(
        name="forget_memory", args={"key": "cake"}, id="1")))

    assert called == [("cake", None)], (
        f"настоящее удаление не вызвано: {called!r}. Дверь съела вызов — "
        "это ровно та поломка, о которой предупреждала фаза 1в")
    assert "Forgotten" in str(resp.response.get("result", "")), (
        f"модель получила {resp.response!r} вместо правды об удалении. "
        "Джарвис скажет «забыл», не забыв — врать нельзя")


def test_recalling_still_returns_what_the_search_found(monkeypatch):
    """Вспоминание не сломалось и НЕ стало молчаливым.

    `recall_memory` — единственный инструмент памяти без `silent: True`:
    модель обязана озвучить найденное. Если правка случайно добавит
    silent, Джарвис начнёт молча искать и не отвечать.
    """
    main = pytest.importorskip("main")
    jl = _fake_jarvis(main, monkeypatch)

    monkeypatch.setattr("memory.fact_store.recall",
                        lambda q: "preferences/cake = Meringue cake",
                        raising=False)

    resp = asyncio.run(jl._execute_tool(SimpleNamespace(
        name="recall_memory", args={"query": "торт"}, id="1")))

    assert "Meringue" in str(resp.response.get("result", "")), (
        f"найденное не доехало до модели: {resp.response!r}")
    assert not resp.response.get("silent"), (
        "recall_memory стал silent — модель не озвучит найденное, и "
        "владелец услышит тишину вместо ответа")


# ── 3. Забор: под-агент не стирает, но читать может ──────────────────────────
def test_a_subagent_cannot_forget_the_owners_facts(monkeypatch):
    """Под-агенту забывание ЗАПРЕЩЕНО забором — дыра фазы 1в закрыта.

    До этой правки под-агент не мог ДОБАВИТЬ факт (забор I12/Г-3 на
    save_memory), но мог его СТЕРЕТЬ: forget_memory двери не спрашивал
    вовсе. Запрет писать при разрешении удалять — не защита, а дырка в
    форме двери.
    """
    from core import fences

    ctx = _subagent_ctx("pc_operator")
    f = fences.check("forget_memory", {"key": "k"}, ctx=ctx)

    assert f.blocked, (
        "под-агент может стереть факт владельца. Запись ему запрещена "
        "(I12/Г-3), а удаление меняет будущее поведение точно так же")
    assert "I12" in f.reason, (
        f"причина отказа не ссылается на правило: {f.reason!r}")


def test_a_subagent_may_still_read_the_memory(monkeypatch):
    """Под-агенту чтение РАЗРЕШЕНО — это решение владельца, не недосмотр.

    Сторож нужен именно потому, что «закрыть всё» выглядит безопаснее и
    кто-нибудь захочет так сделать. Чтение поведения не меняет, а работу
    улучшает: под-агент, знающий предпочтения владельца, ищет точнее.
    """
    from core import fences

    ctx = _subagent_ctx("pc_operator")
    f = fences.check("recall_memory", {"query": "кофе"}, ctx=ctx)

    assert not f.blocked, (
        "под-агенту закрыли ЧТЕНИЕ памяти. Владелец согласился на "
        "«читать можно»: чтение ничего не портит, а помогает сделать "
        "задачу лучше. Если решение изменилось — обновите этот сторож")


def test_the_owner_is_not_touched_by_the_fence():
    """У владельца забора нет ни на одном инструменте памяти.

    Забор смотрит на роль, а у разговора роль пуста. Сторож ловит правку,
    которая перепутает «кто просит» с «насколько опасно» и заблокирует
    владельца заодно с под-агентом.
    """
    from core import fences

    for tool in ("save_memory", "forget_memory", "recall_memory"):
        f = fences.check(tool, {"key": "k"})
        assert not f.blocked, (
            f"забор встал на пути ВЛАДЕЛЬЦА для {tool}: {f.reason!r}. "
            "Забор про под-агентов, а не про всякую работу")


# ── 4. Журнал: след есть, секретов нет ───────────────────────────────────────
def test_forgetting_leaves_a_line_in_the_journal(tmp_path, monkeypatch):
    """Удаление факта появляется в журнале двери с подписью «кто просил».

    Это и есть ответ на вопрос владельца «через месяц откуда факт?» —
    в обратную сторону: почему факта БОЛЬШЕ НЕТ. До правки удаление не
    оставляло следа нигде.
    """
    import json

    from core import audit_log, gate

    # Подменяем ИМЕННО `audit_log.path` — так делает работающий сторож фазы
    # 1в. Через переменную среды JARVIS_HOME не выйдет: путь журнала уже
    # вычислен при импорте, и подмена среды на него не влияет (проверено —
    # файл не появлялся).
    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)

    gate.dispatch("forget_memory", {"key": "cake", "category": "preferences"},
                  mode="interactive")

    assert log.exists(), "журнал не создан — удаление прошло без следа"

    lines = [json.loads(x) for x in
             log.read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [r for r in lines if r.get("tool") == "forget_memory"]
    assert len(mine) == 1, (
        f"ожидали РОВНО одну строку про forget_memory, получили {len(mine)}. "
        "Ноль — правка отвалилась; больше одной — дверь спрашивают дважды.")

    rec = mine[0]
    assert rec["verdict"] == "run", (
        f"владельцу отказали в удалении: {rec['verdict']} / {rec.get('reason')}")
    assert rec["risk"] == "low", (
        f"риск forget_memory стал {rec['risk']!r} — владельца начнут "
        "переспрашивать, чего он прямо просил не делать")
    assert rec.get("origin_chain"), (
        "нет цепочки «кто просил» — на вопрос «кто стёр факт» ответить "
        "будет нечем")


def test_the_journal_line_of_forgetting_carries_no_values(tmp_path, monkeypatch):
    """В журнале — только ИМЕНА полей, не значения (I45).

    Иначе журнал станет стенограммой жизни владельца: что удалял, из какой
    категории, какими словами. Правило проекта — имена без содержимого.
    """
    from core import audit_log, gate

    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)

    secret = "sk-VERY-SECRET-VALUE-42"
    gate.dispatch("forget_memory", {"key": secret, "category": "preferences"},
                  mode="interactive")

    body = log.read_text(encoding="utf-8")
    assert secret not in body, (
        "значение параметра утекло в журнал — нарушение I45. Журнал станет "
        "стенограммой жизни владельца: что удалял и какими словами.")
    assert "param_keys" in body, "имена полей не записаны вовсе"


# ── 5. Сломанная дверь: fail-closed, но БЕЗ потери забывания ─────────────────
def test_a_broken_door_does_not_delete_anything(monkeypatch):
    """Ошибка двери => факт НЕ удаляется (fail-closed).

    Симметрично сторожу фазы 1в про запись. Логика та же: неизвестно, кто
    просит стереть факт — значит не стираем. Молча удалять при сломанной
    двери хуже, чем отказать.
    """
    main = pytest.importorskip("main")
    jl = _fake_jarvis(main, monkeypatch)

    deleted = []
    monkeypatch.setattr("memory.memory_manager.forget",
                        lambda *a, **k: deleted.append(a) or "Forgotten",
                        raising=False)

    import core.gate as g
    monkeypatch.setattr(g, "dispatch",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("дверь сломана")))

    resp = asyncio.run(jl._execute_tool(SimpleNamespace(
        name="forget_memory", args={"key": "cake"}, id="1")))

    assert deleted == [], (
        "сломанная дверь — а факт всё равно удалён. Правило fail-closed "
        "нарушено ровно в том месте, где оно важнее всего")
    assert "SECURITY" in str(resp.response.get("result", "")), (
        f"модель не узнала об отказе: {resp.response!r}")
