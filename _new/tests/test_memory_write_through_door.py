# tests/test_memory_write_through_door.py
"""
Фаза 1в: запись в память проходит через дверь.

ЗАЧЕМ ЭТИ СТОРОЖА
-----------------
До фазы 1в `save_memory` был единственным действием, минующим дверь целиком.
Измерено на машине владельца 28.08.2026: голосовое «запомни, что я не пью
кофе после шести» память записало, а журнал двери не вырос ни на строку.

Правка узкая — точечный вызов двери перед обработчиком `save_memory`. Но
именно узкие правки тихо отваливаются: кто-то через полгода «упростит»
обработчик, вернёт ранний возврат, и запись снова уйдёт в тишину. Эти
сторожа существуют, чтобы такое падение было СЛЫШНО.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ
------------------------
Тестов на `forget_memory` и `recall_memory` у двери. Их в политике
`core/security.py` НЕТ, дверь отвечает им `blocked: Unknown tool`, и это
правильное поведение fail-closed. Проводить их через дверь — отдельное
решение с отдельной калибровкой риска, которое владелец не принимал. Но
сторож «они НЕ сломались» здесь есть: именно эту поломку наивная правка
устроила бы молча.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


# ── Ловушка 2 из исследования: дверь не знает forget/recall ───────────────────

# ── Два сторожа этого места ПЕРЕЕХАЛИ (фаза 1г, 28.08.2026) ──────────────────
#
# Здесь стояли `test_the_door_still_does_not_know_forgetting_and_recalling` и
# `test_forgetting_and_recalling_are_not_routed_through_the_door`. Они
# закрепляли ПРОШЛОЕ положение: дверь не знает forget/recall, и оба
# инструмента ходят мимо неё — иначе умерли бы с ответом "Unknown tool".
#
# Первый из них честно писал в тексте отказа: «Если его добавили в политику
# осознанно — обновите этот тест и калибровку риска». Ровно это и произошло:
# оба инструмента внесены в `core/security.py` с риском low, `forget_memory`
# встал в забор рядом с `save_memory`, а блок двери в `main.py` расширен на
# все три инструмента памяти.
#
# Замена — `tests/test_forgetting_through_the_door.py`, 11 сторожей, из них
# прямые наследники этих двух:
#   * test_the_door_now_knows_forgetting_and_recalling
#   * test_forgetting_and_recalling_are_routed_through_the_door
#
# Тесты не удалены молча и не переписаны «под зелёное»: они проверяли
# настоящее свойство, которое сознательно изменено решением владельца.


# ── Главное: запись в память теперь видна в журнале ───────────────────────────

def test_the_owners_save_leaves_a_line_in_the_journal(tmp_path, monkeypatch):
    """Голосовое «запомни» оставляет строку в журнале двери.

    Это ровно тот критерий, который на машине владельца НЕ выполнялся:
    78 строк до, 78 после.
    """
    main = pytest.importorskip("main")
    from core import audit_log

    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)

    jl = _fake_jarvis(main, monkeypatch)
    saved = []
    monkeypatch.setattr(main, "update_memory",
                        lambda d: saved.append(d), raising=False)

    resp = asyncio.run(jl._execute_tool(SimpleNamespace(
        name="save_memory",
        args={"category": "communication_habits",
              "key": "no_coffee_after_six",
              "value": "Do not suggest coffee after 6 PM",
              "said": "не пью кофе после шести"},
        id="s")))

    assert saved, "запись в память не состоялась — владельцу отказали"
    assert resp.response.get("silent") is True, "потерян silent"

    lines = [json.loads(x) for x in
             log.read_text(encoding="utf-8").splitlines() if x.strip()]
    mem = [x for x in lines if x.get("tool") == "save_memory"]
    assert len(mem) == 1, (
        f"ожидали РОВНО одну строку про save_memory, получили {len(mem)}. "
        "Ноль — правка отвалилась; больше одной — дверь спрашивают дважды.")

    row = mem[0]
    assert row["verdict"] == "run", (
        f"владельцу отказали в записи: {row['verdict']} / {row.get('reason')}")
    assert row["risk"] == "low", (
        f"риск save_memory стал {row['risk']!r}. Это меняет поведение для "
        "владельца и для под-агента — см. core/fences.py:36.")


def test_the_journal_line_carries_no_secrets(tmp_path, monkeypatch):
    """В журнал попадают ИМЕНА параметров, но не то, что владелец сказал.

    Память — самое личное в доме. Правка фазы 1в впервые повела её через
    журнал, и это ровно тот момент, когда личное может утечь в файл.
    """
    main = pytest.importorskip("main")
    from core import audit_log

    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)

    jl = _fake_jarvis(main, monkeypatch)
    monkeypatch.setattr(main, "update_memory", lambda d: None, raising=False)

    secret = "мой пароль от банка 1234"
    asyncio.run(jl._execute_tool(SimpleNamespace(
        name="save_memory",
        args={"category": "notes", "key": "bank", "value": secret,
              "said": secret},
        id="s")))

    raw = log.read_text(encoding="utf-8")
    assert secret not in raw, "СЕКРЕТ ВЛАДЕЛЬЦА УТЁК В ЖУРНАЛ"
    assert "1234" not in raw, "часть секрета утекла в журнал"

    row = [json.loads(x) for x in raw.splitlines() if x.strip()][-1]
    assert row["param_keys"] == ["category", "key", "said", "value"], (
        f"в журнале не имена параметров, а что-то иное: {row['param_keys']}")


# ── Дверь сломалась: память НЕ пишется ────────────────────────────────────────

def test_a_broken_door_stops_the_writing(monkeypatch):
    """Если дверь сломана — запись НЕ происходит (fail-closed).

    До фазы 1в этот случай не существовал: писалось всегда. Теперь у записи
    появился привратник, и правило проекта однозначно — непроверенное
    действие хуже отказанного.
    """
    main = pytest.importorskip("main")
    jl = _fake_jarvis(main, monkeypatch)

    saved = []
    monkeypatch.setattr(main, "update_memory",
                        lambda d: saved.append(d), raising=False)

    import core.gate as gate_mod

    def explode(*a, **k):
        raise RuntimeError("дверь сломана нарочно")

    monkeypatch.setattr(gate_mod, "dispatch", explode)

    resp = asyncio.run(jl._execute_tool(SimpleNamespace(
        name="save_memory", args={"key": "k", "value": "v"}, id="s")))

    assert not saved, (
        "ДВЕРЬ СЛОМАНА, А ПАМЯТЬ ВСЁ РАВНО ЗАПИСАЛАСЬ. Это fail-open: "
        "неизвестно, кто просил, — писать нельзя.")
    assert "SECURITY" in resp.response.get("result", ""), (
        "отказ не назван отказом — модель решит, что записала")
    assert resp.response.get("silent") is True


def test_a_fenced_subagent_cannot_write_through_this_path(monkeypatch):
    """Под-агент, дошедший до этого пути, получает отказ забора I12/Г-3.

    Смысл всей правки: забор живёт ВНУТРИ двери. Пока дверь была
    недостижима, забор для голосового пути не работал вовсе.
    """
    main = pytest.importorskip("main")
    from core import task_context

    jl = _fake_jarvis(main, monkeypatch)
    saved = []
    monkeypatch.setattr(main, "update_memory",
                        lambda d: saved.append(d), raising=False)

    sub = task_context.TaskCtx(
        run_id="r", bucket="task",
        origin_chain=("owner", "main")).child(agent_role="pc_operator")

    with task_context.bind(sub):
        resp = asyncio.run(jl._execute_tool(SimpleNamespace(
            name="save_memory", args={"key": "k", "value": "v"}, id="s")))

    assert not saved, (
        "ПОД-АГЕНТ ЗАПИСАЛ В ПАМЯТЬ ВЛАДЕЛЬЦА. Забор I12/Г-3 не сработал на "
        "этом пути — ровно то, чего фаза 1в должна была не допустить.")
    assert "I12" in resp.response.get("result", "") or \
           "память" in resp.response.get("result", "").lower(), (
        f"отказ есть, но не от забора: {resp.response.get('result')!r}")


# ── Вспомогательное ──────────────────────────────────────────────────────────

def _fake_jarvis(main, monkeypatch):
    """Минимальный Джарвис: только то, что трогает `_execute_tool`.

    Полный `__init__` поднимает звук, окно и сессию — в песочнице этого нет,
    и тесту оно не нужно: проверяется маршрут одного вызова.
    """
    jl = object.__new__(main.JarvisLive)
    jl.ui = SimpleNamespace(
        muted=True, screen_control=False,
        set_state=lambda *a, **k: None,
        write_log=lambda *a, **k: None,
    )
    return jl
