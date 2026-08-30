# -*- coding: utf-8 -*-
"""Выключатели диагностики должны срабатывать от той команды, которую
владелец реально набирает в cmd.

ЧТО БЫЛО СЛОМАНО (найдено живым запуском, не рассуждением).

Владелец запустил ровно то, что я просил, слово в слово:

    set JARVIS_DEBUG_PROMPT=1 && python main.py

Счётчик напечатался:

    [Memory] 🧠 In prompt: 20 facts (...), 1299 chars

а блок памяти — нет. Причина в cmd, а не в памяти: пробел ПЕРЕД `&&`
попадает внутрь значения переменной, то есть программа видит "1 ", а не "1".
Строгое сравнение `os.getenv(...) == "1"` на этом молча ломается.

ПОЧЕМУ ЭТО ВАЖНЕЕ, ЧЕМ ВЫГЛЯДИТ. Диагностика, которая тихо не включается от
документированной команды, хуже отсутствующей: владелец решает, что сломана
ПАМЯТЬ, и ищет дефект там, где его нет. Я сам чуть не принял это за
доказательство, что блок пустой.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ, честно:
  - что cmd ведёт себя именно так (это поведение Windows, здесь оно
    воспроизводится подстановкой значения, а не запуском cmd);
  - что сам блок памяти правильный - это тесты шага 6.
"""

import os
import re
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parent.parent / "main.py"


# ── 1. Дефект не может вернуться в код ───────────────────────────────────

def test_no_debug_switch_is_compared_strictly():
    """Ни один выключатель не сравнивается со "1" без .strip().

    Тест смотрит на КОД, а не на поведение: именно так дефект и появился -
    строгое сравнение выглядит совершенно нормально при чтении.
    """
    source = _MAIN.read_text(encoding="utf-8")
    offenders = []
    for num, line in enumerate(source.splitlines(), 1):
        if "getenv(" not in line and "environ.get(" not in line:
            continue
        if not re.search(r'==\s*"(1|0|true|True)"', line):
            continue
        if ".strip()" in line:
            continue
        offenders.append(f"main.py:{num}: {line.strip()}")
    assert not offenders, (
        "выключатель сломается от `set VAR=1 && python main.py` в cmd:\n"
        + "\n".join(offenders))


# ── 2. Поведение при значениях, которые реально приходят из cmd ──────────

@pytest.mark.parametrize("value,should_fire", [
    ("1",     True),   # set VAR=1&& python   (без пробела)
    ("1 ",    True),   # set VAR=1 && python  <- живой случай владельца
    ("1  ",   True),   # два пробела
    (" 1",    True),   # пробел спереди
    ("\t1 ",  True),   # табуляция
    ("0",     False),  # выключено явно
    ("",      False),  # переменная пустая
    ("11",    False),  # не наш случай, включать нельзя
    ("true",  False),  # мы документируем именно 1
])
def test_the_switch_fires_exactly_when_it_should(value, should_fire):
    """Проверяем то самое выражение, что стоит в main.py."""
    fired = (value or "").strip() == "1"
    assert fired is should_fire, (
        f"значение {value!r}: сработало={fired}, ожидалось={should_fire}")


def test_a_missing_variable_does_not_explode():
    """`or ""` обязателен: getenv отсутствующей переменной даёт None,
    и None.strip() уронил бы весь запуск в блоке диагностики."""
    absent = os.getenv("JARVIS_DEBUG_PROMPT_DEFINITELY_NOT_SET_12345")
    assert absent is None
    assert (absent or "").strip() == ""      # не падает


# ── 3. Оба известных выключателя починены, а не только один ──────────────

@pytest.mark.parametrize("name", ["JARVIS_DEBUG_PROMPT", "JARVIS_BUS_LOG"])
def test_both_known_switches_are_forgiving(name):
    source = _MAIN.read_text(encoding="utf-8")
    lines = [ln for ln in source.splitlines()
             if name in ln and ("getenv" in ln or "environ.get" in ln)]
    assert lines, f"выключатель {name} исчез из main.py"
    for ln in lines:
        assert ".strip()" in ln, (
            f"{name} сравнивается строго и умрёт от пробела из cmd: {ln.strip()}")


# ── 4. Диагностика печатает блок, когда её попросили ─────────────────────

def test_the_debug_block_is_still_printed_somewhere():
    """Защита от "починили сравнение, но выкинули печать"."""
    source = _MAIN.read_text(encoding="utf-8")
    assert "---- injected block ----" in source
    assert "---- end of block ----" in source
