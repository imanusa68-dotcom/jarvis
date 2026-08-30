# tests/test_fences_memory.py
"""
Сторожа заборов: под-агент не пишет в память и не смотрит на экран (1б-2).

Что охраняется: I12 («под-агенты не пишут в память и личность»), Г-3
(«агенты — никогда»), Х-P2 (vision агентам без контекста — никогда).

ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА
Сторож спрашивает ДВЕРЬ, а не забор. Проверять `fences.check()` напрямую
означало бы проверять полпути: забор может отвечать правильно, а дверь его
не спрашивать — и тест останется зелёным при полностью открытой дыре. Это
грабли №4 проекта (core/metering.py:199), уже случившиеся здесь однажды.
Поэтому почти каждый сторож ниже читает ВЕРДИКТ ДВЕРИ.

Замер, из которого выросли эти сторожа (28.08.2026, до забора):
    save_memory      -> run  (низкий риск)  ПРОПУЩЕН
    screen_process   -> run  (низкий риск)  ПРОПУЩЕН
То есть дыра была настоящей, а не гипотетической.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from core import task_context


class _Home:
    """Свой дом на время сторожа: журнал двери не пачкает настоящий.

    Приём взят из tests/test_audit_log_step32.py дословно — один способ
    подменять дом на весь проект, а не свой в каждом файле.
    """

    def __init__(self):
        self.dir = None
        self._old = None

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="fences-")
        self._old = os.environ.get("JARVIS_STATE_DIR")
        os.environ["JARVIS_STATE_DIR"] = self.dir
        from core import audit_log
        audit_log.reset()
        return Path(self.dir)

    def __exit__(self, *exc):
        from core import audit_log
        audit_log.reset()
        if self._old is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)
        task_context.reset_for_tests()
        return False


def _subagent():
    """Пропуск под-агента: задача владельца, внутри неё — поручение роли."""
    task = task_context.TaskCtx(run_id="r-fence", task_id="T-fence",
                                bucket="task",
                                origin_chain=("owner", "main"))
    return task.child(agent_role="pc_operator")


def _last_line(home: Path) -> dict:
    """Последняя строка журнала двери — то, что реально записано."""
    p = home / "logs" / "gate-audit.jsonl"
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "дверь не записала ни одной строки"
    return json.loads(lines[-1])


# ── Собственно заборы ────────────────────────────────────────────────────

def test_a_subagent_cannot_write_to_memory():
    """I12/Г-3: под-агент не пишет в память. Спрашиваем ДВЕРЬ.

    Замер до забора давал здесь `run` — то есть фоновая задача могла
    записать владельцу в память что угодно, и строка журнала выглядела бы
    законной. Сторож закрепляет именно вердикт двери.
    """
    from core import gate

    with _Home():
        with task_context.bind(_subagent()):
            r = gate.dispatch("save_memory",
                              {"content": "владелец не пьёт кофе"},
                              mode="autonomous")
    assert not r.allowed, "под-агент прошёл в память — I12 нарушен"
    assert r.verdict == "blocked"


def test_a_subagent_cannot_look_at_the_screen():
    """I12/Х-P2: под-агент не смотрит на экран. Тоже через дверь."""
    from core import gate

    with _Home():
        with task_context.bind(_subagent()):
            r = gate.dispatch("screen_process", {"text": "что на экране"},
                              mode="autonomous")
    assert not r.allowed, "под-агент прошёл к экрану — Х-P2 нарушен"
    assert r.verdict == "blocked"


def test_the_owner_can_still_save_a_memory():
    """Обратная сторона: владельцу память по-прежнему доступна СРАЗУ.

    Ради этого сторожа забор сделан отдельной проверкой, а не поднятием
    риска в core/security.py. Поднять риск было короче — и владелец на
    «запомни, что я не пью кофе после шести» услышал бы «Подтвердите»
    вместо «Записал». Критерий готовности фазы (план, строка 1045) требует
    ровно обратного, поэтому этот сторож охраняет удобство, а не запрет.
    """
    from core import gate

    with _Home():
        r = gate.dispatch("save_memory", {"content": "не пью кофе после шести"})
    assert r.allowed, "владельцу закрыли память — критерий фазы нарушен"
    assert r.verdict == "run"


def test_a_plain_task_of_the_owner_is_not_a_subagent():
    """Задача, поставленная владельцем, — не под-агент.

    Забор смотрит на РОЛЬ, а не на режим, и вот зачем: у задачи из очереди
    роли нет, она поручена владельцем. Запрети мы ей запись — «Джарвис,
    напомни мне вечером и запомни, что я перешёл на чай» перестало бы
    работать без единого сообщения об ошибке.
    """
    from core import gate

    task = task_context.TaskCtx(run_id="r", task_id="T-1", bucket="task",
                                origin_chain=("owner", "main"))
    with _Home():
        with task_context.bind(task):
            r = gate.dispatch("save_memory", {"content": "перешёл на чай"},
                              mode="autonomous")
    assert r.allowed, ("задаче владельца закрыли память — забор смотрит на "
                       "режим вместо роли")


def test_the_refusal_is_written_down_with_its_reason():
    """Отказ виден в журнале, и по нему понятно, ЧТО именно запретило.

    Иначе через полгода «почему он не записал» останется без ответа:
    строка `blocked` без причины ничем не отличается от любого другого
    отказа двери.
    """
    from core import gate

    with _Home() as home:
        with task_context.bind(_subagent()):
            gate.dispatch("save_memory", {"content": "x"}, mode="autonomous")
        line = _last_line(home)

    assert line["verdict"] == "blocked"
    assert "I12" in line["reason"], f"причина не называет инвариант: {line['reason']!r}"
    # Подпись фазы 1б-1 на месте: видно не только «отказано», но и КОМУ.
    assert line["agent_role"] == "pc_operator"
    assert line["origin_chain"] == ["owner", "main", "pc_operator"]


def test_the_refusal_is_explained_to_a_human():
    """Отказ объясняется словами, а не кодом.

    План (строка 1043) требует «отклоняется с понятной причиной». Понятная —
    значит та, которую можно произнести вслух владельцу.
    """
    from core import gate

    with _Home():
        with task_context.bind(_subagent()):
            r = gate.dispatch("save_memory", {"content": "x"},
                              mode="autonomous")
    assert r.message, "отказ без объяснения"
    assert "память" in r.message.lower()
    # Не техножаргон: сообщение адресовано человеку, а не разработчику.
    for ugly in ("Traceback", "None", "I12", "assert"):
        assert ugly not in r.message, f"в объяснении владельцу утекло {ugly!r}"


def test_the_values_of_the_memory_do_not_leak_into_the_journal():
    """Отказ не превращается в утечку.

    Забор отказывает записи «владелец принимает такие-то таблетки» — и было
    бы издевательством, если бы сам текст при этом лёг в журнал. Правило
    двери «только КЛЮЧИ параметров, никогда значения» должно пережить
    появление забора.
    """
    from core import gate

    secret = "принимает-таблетки-от-давления"
    with _Home() as home:
        with task_context.bind(_subagent()):
            gate.dispatch("save_memory", {"content": secret},
                          mode="autonomous")
        raw = (home / "logs" / "gate-audit.jsonl").read_text(encoding="utf-8")

    assert secret not in raw, "значение параметра утекло в журнал"
    assert "content" in raw, "ключи параметров пропали из журнала"


def test_a_broken_fence_denies_instead_of_letting_through():
    """Сломанный забор ОТКАЗЫВАЕТ (fail-closed).

    Самый важный сторож файла. Забор, который при своей поломке пропускает
    вызов, — хуже отсутствующего: он создаёт ложное чувство защиты. Ломаю
    забор нарочно и требую отказа.
    """
    from core import gate, fences

    def boom(*a, **k):
        raise RuntimeError("забор сломан нарочно")

    saved = fences.check
    fences.check = boom
    try:
        with _Home():
            r = gate.dispatch("web_search", {"query": "погода"})
    finally:
        fences.check = saved

    assert not r.allowed, ("сломанный забор пропустил вызов — это не "
                           "fail-closed")
    assert "fence" in r.reason.lower()


def test_the_fence_lets_everything_else_through_untouched():
    """Забор не мешает работать.

    Он про два инструмента из двадцати четырёх. Если из-за него под-агент
    перестанет искать в сети, фаза 2 не начнётся вовсе.
    """
    from core import gate

    with _Home():
        with task_context.bind(_subagent()):
            r = gate.dispatch("web_search", {"query": "погода в Москве"},
                              mode="autonomous")
    assert r.allowed, "забор перекрыл обычную работу под-агента"


def test_the_fence_knows_no_agent_names():
    """I21: ядро не знает имён агентов. Забор — часть ядра.

    Проверяется исходником нарочно: стоит однажды написать
    `if role == "pc_operator"`, и добавление третьего агента станет правкой
    ядра вместо правки одного yaml.
    """
    from core import fences

    src = Path(fences.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines()
        # Комментарии и строки документации из проверки исключены: имена
        # ролей в ОБЪЯСНЕНИЯХ полезны, запрещены они в РЕШЕНИЯХ.
        if not line.lstrip().startswith("#")
    ).split('"""')
    body = "".join(code[::2])  # чётные куски — код, нечётные — docstring
    for name in ("pc_operator", "researcher", "main_agent"):
        assert name not in body, (
            f"забор решает по имени агента ({name}) — нарушен I21")


def test_the_fence_is_a_question_not_an_action():
    """Забор ничего не делает: его можно спросить дважды с тем же ответом.

    Если бы он писал в журнал или менял состояние, дверь не могла бы
    спрашивать его до принятия решения — а именно так она и устроена.
    """
    from core import fences

    ctx = _subagent()
    first = fences.check("save_memory", {"content": "x"}, ctx=ctx)
    second = fences.check("save_memory", {"content": "x"}, ctx=ctx)
    assert first == second
    assert first.blocked is True


def test_the_list_of_guarded_tools_is_closed_and_real():
    """Охраняемые инструменты существуют на самом деле.

    Сторож от самой тихой ошибки: опечатка в имени («save_memorу» с русской
    «у») превратила бы забор в украшение, и ни один тест выше не покраснел
    бы, потому что все они ходят через дверь тем же неверным именем.
    Поэтому имена сверяются с реестром политики, а не сами с собой.
    """
    from core import fences
    from core.security import SECURITY_POLICY

    guarded = fences.MEMORY_TOOLS | fences.VISION_TOOLS
    assert guarded, "список охраняемых инструментов пуст"
    unknown = sorted(t for t in guarded if t not in SECURITY_POLICY)
    assert not unknown, (
        f"забор охраняет инструменты, которых нет в политике: {unknown}")
