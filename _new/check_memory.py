# check_memory.py
"""
ЧТО ВИДИТ ДЖАРВИС В ВАШЕЙ ПАМЯТИ. Только чтение.

Запуск:
    python check_memory.py

Зачем этот файл существует. Срок годности фактов о событиях (14 дней) в
живом разговоре не пощупать: чтобы увидеть скрытие, нужен факт, которому
уже больше двух недель, а такой в свежей памяти взяться не может. Без
показометра владельцу оставалось бы «поверить на слово» — а обещание
«проблема исчезла» обязано быть проверяемым, а не декларируемым.

Почему имя `check_memory`, а не `memory_report`. В доме УЖЕ есть
`tools/memory_report.py` — он показывает SQLite-зеркало (второй мозг) и
умеет `--search`. Два файла с одинаковым именем означают вопрос «а какой
из них запускать?» у владельца и неоднозначный импорт у кода. Диагностика
здесь зовётся `check_*` (check_lang, check_dialog, check_faza1b) — это имя
и взято. Тот отчёт про зеркало, этот — про то, что уезжает в ПРОМПТ; они
смотрят на разные копии памяти и друг друга не заменяют.

ЭТОТ ФАЙЛ НИЧЕГО НЕ ПИШЕТ. Ни в память, ни в настройки, ни в индекс, ни в
журнал. Он открывает long_term.json на чтение и считает то же, что считает
сборка промпта — через ту же самую функцию `_visible_memory`, а не через
свою копию правил. Копия правил тихо разошлась бы с настоящими и врала бы
успокаивающе, что хуже отсутствия отчёта.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.safe_json import state_path                    # noqa: E402
from memory.memory_manager import (                      # noqa: E402
    _EXPIRY_FRESH_DAYS,
    _EXPIRY_STALE_DAYS,
    _fact_age_days,
    _is_event_key,
    _visible_memory,
    _without_junk,
    format_memory_for_prompt,
)


def _read_only_memory() -> tuple:
    """Прочитать файл памяти НАПРЯМУЮ.

    Намеренно не через `load_memory`: та зовёт миграцию, а миграция имеет
    право писать. Отчёт, который что-то меняет, — уже не отчёт.
    """
    path = state_path("long_term.json")
    if not path.exists():
        return {}, path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"Не смог прочитать {path}: {exc}")
        return {}, path
    return (data if isinstance(data, dict) else {}), path


def main() -> int:
    memory, path = _read_only_memory()
    print("=" * 72)
    print("ЧТО ДЖАРВИС ВИДИТ В ВАШЕЙ ПАМЯТИ")
    print("=" * 72)
    print(f"Файл: {path}")
    if not memory:
        print("\nПамять пуста или файла ещё нет. Это не ошибка: поговорите с")
        print("Джарвисом, потом запустите отчёт снова.")
        return 0

    today = datetime.now().date()

    total = sum(len(v) for v in memory.values() if isinstance(v, dict))
    no_junk = _without_junk(memory)
    junk = total - sum(len(v) for v in no_junk.values() if isinstance(v, dict))

    visible, hidden = _visible_memory(memory)
    shown = sum(len(v) for v in visible.values() if isinstance(v, dict))

    print(f"\nВсего фактов на диске:              {total}")
    print(f"Скрыто как мусор (было и раньше):   {junk}")
    print(f"Скрыто по сроку годности (НОВОЕ):   {hidden}")
    print(f"Уезжает в промпт:                   {shown}")

    if junk:
        # Мусор скрыт ДАВНО и не этой правкой, но «скрыт 1» без имени
        # владелец проверить не может, а непроверяемая цифра — не отчёт.
        print("\nСкрытые как мусор (на диске целы, это старое поведение):")
        for category, entries in memory.items():
            if not isinstance(entries, dict):
                continue
            kept = no_junk.get(category) or {}
            for key in entries:
                if key not in kept:
                    print(f"  {category}/{key}")

    # Что именно скрыто по времени и почему — по одной строке на факт.
    # Без этого списка отчёт нельзя ни проверить, ни оспорить.
    rotten = []
    labelled = []
    for category, entries in memory.items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if not _is_event_key(category, key):
                continue
            age = _fact_age_days(entry, today)
            if age is None:
                continue
            value = entry.get("value") if isinstance(entry, dict) else entry
            # Пороги БЕРУТСЯ ИЗ МОДУЛЯ, а не вписаны числами. Вписанное
            # число разошлось бы с настоящим порогом при первой же его
            # смене, и отчёт стал бы врать успокаивающе — худший вид
            # отчёта. Здесь связь по устройству, а не по памяти автора.
            if age > _EXPIRY_STALE_DAYS:
                rotten.append((age, category, key, value))
            elif age > _EXPIRY_FRESH_DAYS:
                labelled.append((age, category, key, value))

    # ПЕРЕПИСЬ ФАКТОВ О СОБЫТИЯХ. Без неё «скрыто 0» неразличимо с двумя
    # совсем разными положениями дел: «события есть, просто свежие» (всё
    # хорошо) и «механизм вообще не видит ваших данных» (сломано). Отчёт,
    # который эти два случая путает, успокаивает вместо того чтобы мерить.
    events = 0
    dated = 0
    oldest = None
    undated = []
    for category, entries in memory.items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if not _is_event_key(category, key):
                continue
            events += 1
            age = _fact_age_days(entry, today)
            if age is None:
                undated.append(f"{category}/{key}")
                continue
            dated += 1
            if oldest is None or age > oldest:
                oldest = age

    print(f"\nФактов о событиях (за ними следит срок годности): {events}")
    print(f"  из них с читаемой датой:                        {dated}")
    if oldest is not None:
        print(f"  самому старому из них:                          {oldest} дн.")
        print(f"  порог скрытия:                                  "
              f"{_EXPIRY_STALE_DAYS} дн.")
    if undated:
        # Без даты возраст неизвестен, и правило НЕ скрывает — так решено
        # намеренно: молча спрятать факт, про который ничего не известно,
        # хуже, чем оставить его на виду. Но владелец должен знать, что
        # такие есть, иначе он ждёт скрытия, которого не будет.
        print(f"  БЕЗ ДАТЫ (возраст неизвестен, скрытие не сработает): "
              f"{len(undated)}")
        for name in undated[:10]:
            print(f"      {name}")

    if rotten:
        print("\n" + "-" * 72)
        print("СКРЫТО ПО ВРЕМЕНИ (на диске цело, recall_memory найдёт):")
        for age, cat, key, value in sorted(rotten, reverse=True):
            print(f"  [{age:>4} дн.] {cat}/{key}: {value}")
    elif events and dated:
        print("\nПо времени пока ничего не скрыто — и это ОЖИДАЕМО:")
        print(f"самому старому событию {oldest} дн., порог "
              f"{_EXPIRY_STALE_DAYS} дн. Механизм видит ваши факты и ждёт.")
    elif events:
        print("\nСобытия найдены, но ни у одного нет читаемой даты.")
        print("Скрытие по времени на них не сработает — возраст неизвестен.")
    else:
        print("\nФактов о событиях в памяти нет — скрывать нечего.")
        print("Это не поломка: срок годности касается только событий")
        print("(«вчера», «сегодня», «приезд», «звонил»), а не постоянных")
        print("сведений вроде города, привычек и предпочтений.")

    if labelled:
        print("\n" + "-" * 72)
        print("ЕДЕТ С МЕТКОЙ ВОЗРАСТА (модель знает, что деталь не свежая):")
        for age, cat, key, value in sorted(labelled, reverse=True):
            print(f"  [{age:>4} дн.] {cat}/{key}: {value}")

    block = format_memory_for_prompt(memory)
    print("\n" + "-" * 72)
    print(f"БЛОК ПАМЯТИ, КОТОРЫЙ УЙДЁТ МОДЕЛИ ({len(block)} знаков):")
    print("-" * 72)
    print(block if block else "(пусто)")

    # Проверка вслух: файл не тронут. Утверждение «только чтение» должно
    # быть измерено, а не обещано.
    print("-" * 72)
    try:
        st = path.stat()
        print(f"Файл памяти не тронут: {st.st_size} Б, изменён "
              f"{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
