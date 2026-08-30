"""Фаза 1е — третий путь в память: «дом записал сам» тоже ходит через дверь.

ЧТО ЭТО ЗА ПУТЬ
---------------
После каждой реплики `main._update_memory_async` уходит в фоновый поток и
может САМ, без просьбы владельца, дописать две вещи:

    update_memory(data)          # факты о владельце
    update_personality(p_data)   # КАК с владельцем разговаривать

Оба вызова шли мимо двери. Владелец ничего не просил, его не спрашивали,
в журнале следа не оставалось. На вопрос «почему ты стал так отвечать?»
ответить было нечем.

ПОЧЕМУ ЭТО ХУЖЕ, ЧЕМ ВЫГЛЯДИТ
-----------------------------
Замер живого разговора (фаза 1г): 6 явных сохранений против 0 само-записей.
То есть путь РЕДКИЙ — и я сам раньше ошибочно называл его «самым частым»,
это утверждение отозвано. Но дело не в частоте, а в том, ЧТО он пишет:

  - `personality.json` — манера речи. Тихая правка здесь меняет самого
    Джарвиса, а не сведения о владельце.
  - в `core/fences.py` было написано, что personality не защищён, потому
    что «остальные два сегодня не имеют инструмента, которым их можно
    записать — проверено грепом по политике». Замечание честное, но
    неполное: ИНСТРУМЕНТА правда нет, а ПИСАТЕЛЬ есть. Он просто не
    инструмент, поэтому забор его не видел. Дыра была описана как
    «нечего защищать», хотя защищать было что.

ЛОВУШКА, ПРОВЕРЕННАЯ ЖИВЫМ ЗАМЕРОМ ДВЕРИ (29.08.2026)
-----------------------------------------------------
    save_memory             interactive  -> run     allowed=True
    memory_self_write       interactive  -> blocked  Unknown tool ...
    personality_self_write  interactive  -> blocked  Unknown tool ...

Ровно та же ловушка, что в фазе 1г: незнакомое двери имя получает
`Unknown tool` и БЛОК. Значит наивный порядок «сначала позвать дверь из
main.py» тихо отключил бы авто-запись целиком, а тесты остались бы
зелёными. Поэтому: сперва политика, потом сторожа, потом вызов.

ВТОРОЙ ЗАМЕР — РОЛЬ ФОНОВОГО ПОТОКА
-----------------------------------
    главный поток   agent_role=None  origin=('owner', 'main')
    фоновый поток   agent_role=None  origin=('owner', 'main')

`contextvars` в новый поток не переносится, поэтому фоновая запись
выглядит как владелец. Это ВАЖНО и определяет конструкцию: полагаться на
роль здесь нельзя, она всегда «владелец». Отличить «владелец попросил» от
«дом решил сам» можно только явным пропуском — его и передаём.

ЧТО ЭТА ФАЗА НАМЕРЕННО НЕ ДЕЛАЕТ
--------------------------------
  - НИЧЕГО НЕ ЗАПРЕЩАЕТ. Авто-запись работает как работала. Задача — след
    в журнале и одна точка, где её можно будет выключить, а не «сломать
    полезное поведение ради чистоты».
  - НЕ СПРАШИВАЕТ ВЛАДЕЛЬЦА. Решение владельца от 28.08.2026: «нет, мне
    надоест мне всегда подтверждать ему». risk=low, policy=auto.
  - НЕ ПОКАЗЫВАЕТ ЭТО МОДЕЛИ. `planner_visible=False`: это внутренний
    повод дома, а не инструмент. Иначе модель начнёт звать его сама и
    получится четвёртый путь вместо закрытия третьего.
  - НЕ ВАЛИТ РАЗГОВОР. Это фоновый поток. Дверь сломалась — тихо не пишем
    (fail-closed), но исключение наружу не пускаем.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MAIN = (REPO / "main.py").read_text(encoding="utf-8")


def _subagent_ctx(role: str = "pc_operator"):
    from core import task_context
    root = task_context.TaskCtx(run_id="R-test", task_id="T-1")
    return root.child(agent_role=role, task_id="T-2")


# ── дверь обязана знать про этот путь ───────────────────────────────────

def test_the_door_knows_the_house_writing_by_itself():
    """Живой замер до правки: оба имени -> blocked, Unknown tool."""
    from core import gate
    for tool in ("memory_self_write", "personality_self_write"):
        result = gate.dispatch(tool, {"k": "v"}, mode="interactive")
        assert result.allowed, (
            f"дверь не знает {tool}: {result.verdict} / {result.reason}. "
            "Провести вызов через дверь в таком виде = отключить авто-запись")


def test_the_owner_is_never_asked_about_a_background_write():
    """risk выше low означал бы «спросить владельца» — прямо запрещено:
    «нет, мне надоест мне всегда подтверждать ему»."""
    from core import security
    for tool in ("memory_self_write", "personality_self_write"):
        pol = security.SECURITY_POLICY[tool]
        assert pol.risk == "low", f"{tool}: risk={pol.risk}, владельца спросят"
        assert pol.status == "allowed", f"{tool}: {pol.status}"


def test_the_model_cannot_call_the_house_writer_itself():
    """Это внутренний повод дома. Показать его модели = сделать четвёртый
    путь в память вместо закрытия третьего."""
    from core import security
    for tool in ("memory_self_write", "personality_self_write"):
        pol = security.SECURITY_POLICY[tool]
        assert pol.planner_visible is False, (
            f"{tool} виден планировщику — модель начнёт звать его сама")
    for tool in ("memory_self_write", "personality_self_write"):
        assert f'"name": "{tool}"' not in MAIN, (
            f"{tool} объявлен моделью как инструмент")


# ── оба вызова действительно проведены ──────────────────────────────────
#
# ОШИБКА В ЭТИХ ДВУХ СТОРОЖАХ, НАЙДЕННАЯ И ИСПРАВЛЕННАЯ 29.08.2026
# ----------------------------------------------------------------
# Первая редакция резала тело функции по подстроке "TOOL_DECLARATIONS" и
# считала слово "gate". Оба приёма оказались негодными, и это выяснилось
# сразу после правки main.py:
#
#   1. Новый комментарий в main.py объясняет, что имён НЕТ в
#      TOOL_DECLARATIONS. Слово встретилось внутри функции, блок оборвался
#      на середине комментария, страж покраснел при полностью верном коде.
#      Якорем должно быть НАЧАЛО СТРОКИ "\nTOOL_DECLARATIONS = [" — его
#      комментарий с отступом не подделает.
#
#   2. Проверка велась по тексту вместе с комментариями. То есть страж
#      был бы зелёным, даже если бы дверь не вызывалась вовсе, — достаточно
#      упомянуть имена в комментарии. Страж, который нельзя провалить, не
#      страж. Поэтому теперь комментарии вырезаются и проверяется КОД.
#
# Исправлен СТОРОЖ, а не код: код на момент покраснения был правильным.

def _writer_body() -> str:
    """Тело `_update_memory_async` БЕЗ комментариев.

    Комментарии убраны нарочно: иначе страж пройдёт по одному упоминанию
    имени в комментарии, при том что дверь не вызвана.
    """
    block = MAIN.split("def _update_memory_async")[1].split(
        "\nTOOL_DECLARATIONS = [")[0]
    lines = [ln for ln in block.splitlines()
             if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


def test_both_background_writes_go_through_the_door():
    body = _writer_body()
    assert "memory_self_write" in body, (
        "запись фактов идёт мимо двери")
    assert "personality_self_write" in body, (
        "запись ЛИЧНОСТИ идёт мимо двери — а это манера речи Джарвиса")


def test_the_two_writes_are_judged_separately():
    """Одна проверка на оба вызова была бы хуже: память и личность —
    разные вещи, и по журналу нельзя было бы понять, что именно записали."""
    body = _writer_body()
    assert body.count("memory_self_write") >= 1
    assert body.count("personality_self_write") >= 1
    assert "gate" in body, "дверь из этой функции вообще не вызывается"


def test_the_door_is_asked_before_the_write_not_after():
    """Порядок важнее наличия: вызов двери ПОСЛЕ записи — это не защита,
    а протокол уже случившегося."""
    body = _writer_body()
    for tool, writer in (("memory_self_write", "update_memory("),
                         ("personality_self_write", "update_personality(")):
        assert body.index(tool) < body.index(writer), (
            f"{tool} спрошен после {writer} — запись уже произошла")


# ── журнал: след появился и в нём нет содержимого ───────────────────────

def test_a_background_write_leaves_a_line_in_the_journal(tmp_path,
                                                        monkeypatch):
    import json
    from core import audit_log, gate
    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)
    gate.dispatch("memory_self_write",
                  {"category": "preferences", "key": "кофе"},
                  mode="interactive")
    assert log.exists(), "след авто-записи в журнале не появился"
    line = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line.get("tool") == "memory_self_write"


def test_the_journal_line_carries_no_values(tmp_path, monkeypatch):
    """И45: в журнал идут ИМЕНА полей, не значения. Здесь это особенно
    важно — авто-запись может подхватить что угодно из разговора."""
    import json
    from core import audit_log, gate
    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)
    secret = "пароль от банка 4321"
    gate.dispatch("memory_self_write",
                  {"category": "notes", "value": secret},
                  mode="interactive")
    text = log.read_text(encoding="utf-8")
    assert secret not in text, "значение утекло в журнал"
    line = json.loads(text.strip().splitlines()[-1])
    assert "value" in (line.get("param_keys") or []), (
        "имя поля не записано — по журналу не понять, что писали")


def test_the_background_write_sends_no_content_to_the_journal(tmp_path,
                                                             monkeypatch):
    """Сквозная проверка: содержимое разговора не уходит в журнал.

    ЧЕСТНАЯ ИСТОРИЯ ЭТОГО СТОРОЖА (29.08.2026)
    ------------------------------------------
    Мутация №10 подменила в main.py `{имя: None}` на `{имя: значение}` и
    НЕ БЫЛА ПОЙМАНА. Я решил, что нашёл пробел, и написал этот страж.
    После правки мутация всё равно не ловилась — и это заставило
    посмотреть в дверь вместо того, чтобы добавлять третий страж.

    Оказалось, мутация непробиваема ПО УСТРОЙСТВУ: `core/gate.py:168`
    берёт `sorted(str(k) for k in param_keys)` — только имена, значения не
    читаются вообще. Защита стоит в одном месте и стоит правильно.

    Значит `{имя: None}` в main.py — не защита, а вежливость: даже если бы
    дверь однажды начала писать значения, отсюда ей нечего взять. Пробела
    не было, мутация была плохая. Утверждение «найден пробел» отозвано.

    Страж оставлен: он проверяет РЕАЛЬНЫЙ путь целиком (вызов
    `_update_memory_async` -> дверь -> журнал), а не дверь отдельно. Если
    дверь когда-нибудь начнёт логировать значения, покраснеет здесь — на
    самом опасном из путей, где текст берёт не владелец, а дом.
    """
    import main
    from core import audit_log
    log = tmp_path / "gate-audit.jsonl"
    monkeypatch.setattr(audit_log, "path", lambda: log)

    secret = "пароль-от-банка-4321"
    monkeypatch.setattr(main, "_get_api_key", lambda: "fake-key")
    monkeypatch.setattr(main, "should_extract_memory", lambda *a, **k: True)
    # Секрет лежит ТОЛЬКО в значении. Имя поля ("notes") в журнал уходить
    # обязано — по нему видно, что писали. Первая редакция этого стража
    # ставила секрет и в имя тоже и краснела зря: имя утечкой не является.
    monkeypatch.setattr(main, "extract_memory",
                        lambda *a, **k: {"notes": {"k": secret}})
    monkeypatch.setattr(main, "update_memory", lambda d: None)
    monkeypatch.setattr(main, "should_analyze_personality",
                        lambda *a, **k: False)
    main._last_memory_input = None

    main._update_memory_async("владелец сказал длинную фразу", "ответ")

    assert log.exists(), "авто-запись не оставила следа в журнале"
    text = log.read_text(encoding="utf-8")
    assert secret not in text, (
        "содержимое разговора утекло в журнал через авто-запись")


# ── под-агент не может писать в память ЧУЖИМИ руками ────────────────────

def test_a_subagent_cannot_use_the_house_writer_to_write_memory():
    """Иначе запрет фазы 1г обходится в один шаг: под-агенту нельзя
    save_memory, но можно то же самое под именем дома."""
    from core import fences
    verdict = fences.check("memory_self_write", ctx=_subagent_ctx())
    assert verdict.blocked, (
        "под-агент пишет в память под именем дома — запрет 1г обойдён")


def test_a_subagent_cannot_rewrite_the_personality():
    from core import fences
    verdict = fences.check("personality_self_write", ctx=_subagent_ctx())
    assert verdict.blocked, (
        "под-агент переписывает манеру речи Джарвиса")


def test_the_owner_is_not_touched_by_the_fence():
    from core import fences
    for tool in ("memory_self_write", "personality_self_write"):
        assert not fences.check(tool).blocked, (
            f"{tool}: забор задел владельца")


# ── фоновый поток не имеет права уронить разговор ───────────────────────

def test_a_broken_door_does_not_break_the_conversation(monkeypatch):
    """Это фоновый поток. Упавшее исключение здесь либо съедается молча,
    либо всплывает в чужом месте. Ни то, ни другое не годится.

    Печать перехватываем своей заглушкой, а не `capsys`: этот прогон идёт
    с `-p no:randomly`, и в такой сборке capsys падает на set_fixture.
    Проверять поведение важнее, чем настаивать на удобной фикстуре.
    """
    import builtins
    import main
    from core import gate

    said = []
    monkeypatch.setattr(builtins, "print",
                        lambda *a, **k: said.append(" ".join(map(str, a))))

    def explode(*a, **k):
        raise RuntimeError("дверь сломана нарочно")

    monkeypatch.setattr(gate, "dispatch", explode)
    monkeypatch.setattr(main, "_get_api_key", lambda: "fake-key")
    monkeypatch.setattr(main, "should_extract_memory",
                        lambda *a, **k: True)
    monkeypatch.setattr(main, "extract_memory",
                        lambda *a, **k: {"notes": {"k": "v"}})
    written = []
    monkeypatch.setattr(main, "update_memory",
                        lambda d: written.append(d))
    monkeypatch.setattr(main, "should_analyze_personality",
                        lambda *a, **k: False)
    main._last_memory_input = None

    main._update_memory_async("владелец сказал длинную фразу", "ответ")

    assert written == [], (
        "дверь сломана, а запись всё равно прошла — это не fail-closed")
    assert any("gate" in line.lower() for line in said), (
        "поломка двери прошла молча — владелец не узнает: " + str(said))


def test_a_refused_background_write_does_not_touch_memory(monkeypatch):
    """Отказ двери должен ОСТАНОВИТЬ запись, а не только напечататься."""
    import main
    from core import gate

    class Denied:
        allowed = False
        verdict = "blocked"
        reason = "тест"
        message = "нельзя"

    monkeypatch.setattr(gate, "dispatch", lambda *a, **k: Denied())
    monkeypatch.setattr(main, "_get_api_key", lambda: "fake-key")
    monkeypatch.setattr(main, "should_extract_memory", lambda *a, **k: True)
    monkeypatch.setattr(main, "extract_memory",
                        lambda *a, **k: {"notes": {"k": "v"}})
    written = []
    monkeypatch.setattr(main, "update_memory", lambda d: written.append(d))
    monkeypatch.setattr(main, "should_analyze_personality",
                        lambda *a, **k: False)
    main._last_memory_input = None

    main._update_memory_async("владелец сказал длинную фразу", "ответ")

    assert written == [], "дверь отказала, а память всё равно записали"


def test_the_personality_is_not_written_when_refused(monkeypatch):
    import main
    from core import gate

    class Denied:
        allowed = False
        verdict = "blocked"
        reason = "тест"
        message = "нельзя"

    monkeypatch.setattr(gate, "dispatch", lambda *a, **k: Denied())
    monkeypatch.setattr(main, "_get_api_key", lambda: "fake-key")
    monkeypatch.setattr(main, "should_extract_memory", lambda *a, **k: False)
    monkeypatch.setattr(main, "should_analyze_personality",
                        lambda *a, **k: True)
    monkeypatch.setattr(main, "analyze_personality",
                        lambda *a, **k: {"tone": "short"})
    touched = []
    monkeypatch.setattr(main, "update_personality",
                        lambda d: touched.append(d))
    main._last_memory_input = None

    main._update_memory_async("владелец сказал длинную фразу", "ответ")

    assert touched == [], "дверь отказала, а личность всё равно переписали"


# ── полезное поведение сохранено ────────────────────────────────────────

def test_an_allowed_background_write_still_happens(monkeypatch):
    """Главная проверка «не сломал полезное»: при разрешении дверью
    авто-запись обязана работать точно как раньше."""
    import main
    monkeypatch.setattr(main, "_get_api_key", lambda: "fake-key")
    monkeypatch.setattr(main, "should_extract_memory", lambda *a, **k: True)
    monkeypatch.setattr(main, "extract_memory",
                        lambda *a, **k: {"notes": {"k": "v"}})
    written = []
    monkeypatch.setattr(main, "update_memory", lambda d: written.append(d))
    monkeypatch.setattr(main, "should_analyze_personality",
                        lambda *a, **k: True)
    monkeypatch.setattr(main, "analyze_personality",
                        lambda *a, **k: {"tone": "short"})
    touched = []
    monkeypatch.setattr(main, "update_personality",
                        lambda d: touched.append(d))
    main._last_memory_input = None

    main._update_memory_async("владелец сказал длинную фразу", "ответ")

    assert written == [{"notes": {"k": "v"}}], (
        "дверь разрешила, а факты не записались — сломано полезное")
    assert touched == [{"tone": "short"}], (
        "дверь разрешила, а личность не записалась — сломано полезное")
