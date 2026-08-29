# -*- coding: utf-8 -*-
"""ШАГ 1: критерий «что достойно памяти» — в описании инструмента.

ЧТО БЫЛО СЛОМАНО.

В описании save_memory стоял только ЗАПРЕТ:

    "Do NOT call for: passing moods ('I'm tired today'), one-time commands..."

Запрет без умения извлечь свойство превращается в глухоту. Фраза «кот опять
лёг на клавиатуру» — событие, значит под запрет попадает целиком, и не
записывается НИЧЕГО. А внутри спрятано свойство «у владельца есть кот»,
которое будет верно и через год. Джарвис слышал предложение и выбрасывал его
целиком вместе с тем, что стоило помнить.

ЧТО ДОБАВЛЕНО: два вопроса из критерия прямым текстом.
  В1 про владельца или про мир (про мир — это поиск, не память);
  В2 свойство или событие, и главное — событие может ОБНАРУЖИТЬ свойство.

ЗАМЕР, КОТОРЫЙ ОПРОВЕРГ ПЛАН (probe26). План утверждал: «замена НИКОГДА не
удаляет, superseded_by уже есть в схеме». Проверил делом: столбец в схеме
есть, но upsert_fact его НЕ заполняет — повтор той же пары (категория, ключ)
делает UPDATE живой строки, и прежнее значение исчезает без следа. После
записи 'BMW' поверх 'Mercedes-Benz cars' в базе остаётся ОДНА строка. Значит
описание обязано говорить модели правду: замена необратима, при сомнении
берётся новый ключ.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ — честно, чтобы никто не обманулся:
  - что модель СТАЛА писать лучше. Описание — это просьба к модели, а не
    механизм. Проверить это может только живой разговор владельца;
  - что «кот на клавиатуре» теперь сохранится. Здесь проверяется лишь то,
    что инструкция на месте и внутренне непротиворечива;
  - В3 (найти то же свойство перед записью) — это шаг 2, не текст;
  - В4 (догадка не смеет затирать сказанное вслух) НЕ РЕШЁН: замер probe26
    показал, что inferred спокойно перезаписывает explicit. Это названный
    долг, а не сделанная работа.
"""

import re
from pathlib import Path

import pytest

_MAIN_PATH = Path(__file__).resolve().parent.parent / "main.py"
_MAIN = _MAIN_PATH.read_text(encoding="utf-8")


def _save_memory_description() -> str:
    """Собрать текст описания save_memory так же, как его увидит модель."""
    i = _MAIN.index('"name": "save_memory"')
    j = _MAIN.index('"parameters"', i)
    block = _MAIN[i:j]
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"\s*(?:\n|$)', block)
    return " ".join(p for p in parts if len(p) > 15)


DESC = _save_memory_description()


# ── 1. Критерий действительно доехал до модели ───────────────────────────

def test_the_description_is_not_empty():
    """Защита от «тест зелёный, потому что искал в пустой строке»."""
    assert len(DESC) > 500, f"описание собралось подозрительно коротким: {len(DESC)}"


def test_the_property_versus_event_question_is_spelled_out():
    lowered = DESC.lower()
    assert "property" in lowered, "В2 без слова property — это намёк, а не критерий"
    assert "event" in lowered, "В2 без слова event — половина вопроса"


def test_the_owner_versus_world_question_is_spelled_out():
    lowered = DESC.lower()
    assert "about the world" in lowered, \
        "В1 обязан прямо сказать, что факты про мир — не память"


def test_an_event_revealing_a_property_is_taught_by_example():
    """Тот самый случай, из-за которого «кот на клавиатуре» пропадал.

    Проверяю именно ПРИМЕР, а не только правило: абстрактное «извлекай
    свойство» модель уже могла вывести из прежнего текста и не выводила.
    """
    lowered = DESC.lower()
    assert "cat" in lowered, "пример с котом — ровно тот дефект, что чинится"
    assert "reveal" in lowered, "правило «событие обнаруживает свойство» пропало"


# ── 2. Правда про замену, а не то, что предполагал план ──────────────────

def test_the_description_admits_that_replacing_destroys():
    """probe26: старое значение после замены не остаётся нигде.

    Если однажды upsert_fact научится вести историю (superseded_by), этот
    тест надо будет ПЕРЕПИСАТЬ вместе с текстом — но осознанно, а не молча.
    """
    lowered = DESC.lower()
    assert "gone" in lowered or "destroy" in lowered, \
        "модель должна знать, что замена необратима"


def test_when_unsure_the_description_prefers_a_duplicate():
    """Р4: цена ошибки асимметрична. Дубль — мусор, замена — потеря."""
    lowered = DESC.lower()
    assert "new key" in lowered, "нужен прямой совет брать новый ключ при сомнении"
    assert "duplicate" in lowered, "и назвать цену: дубль лечится позже"


def test_the_replacement_advice_matches_the_measured_behaviour():
    """Ключевой тест на ЧЕСТНОСТЬ описания.

    Описание не имеет права обещать историю правок, которой нет в коде.
    Проверяю по коду: если upsert_fact начнёт заполнять superseded_by,
    формулировку «old value is GONE» надо будет пересмотреть.
    """
    fact_store = (_MAIN_PATH.parent / "memory" / "fact_store.py").read_text(
        encoding="utf-8")
    i = fact_store.index("def upsert_fact")
    j = fact_store.index("def forget_fact", i)
    body = fact_store[i:j]
    writes_history = "superseded_by=" in body.replace(" ", "")
    if writes_history:
        pytest.fail(
            "upsert_fact теперь ведёт историю замен — описание save_memory "
            "врёт, что старое значение теряется. Обновить текст и этот тест.")


# ── 3. Старые гарантии не растоптаны ─────────────────────────────────────

def test_throwaway_lines_are_still_refused():
    """Расширение критерия не должно превратить память в стенограмму.

    Эту гарантию охраняет и test_stage3b2, но она настолько важна, что
    проверяется и здесь: именно её легче всего снести, «упрощая» текст.
    """
    lowered = DESC.lower()
    for throwaway in ("passing moods", "one-time commands"):
        assert throwaway in lowered, f"пропало исключение: {throwaway}"


def test_standing_instructions_survived_the_edit():
    lowered = DESC.lower()
    assert "standing instruction" in lowered
    assert "until restart" in lowered, \
        "предупреждение «согласие живёт до перезапуска» пропало"


def test_the_one_reply_per_turn_rule_survived():
    """Владелец слышал ответ дважды 28.08.2026 — правило про это на месте."""
    assert "One reply per turn" in DESC


def test_forget_memory_is_still_the_way_to_delete():
    lowered = DESC.lower()
    assert "forget_memory" in lowered
    assert "disregard" in lowered


def test_verbatim_is_still_required():
    lowered = DESC.lower()
    assert "said" in lowered
    assert "verbatim" in lowered or "word for word" in lowered


# ── 4. Цена изменения названа вслух ──────────────────────────────────────

def test_the_description_stays_within_a_sane_size():
    """Описание уходит КАЖДУЮ сессию, как и системный промпт (29064 знака).

    План обещал «цена 0 знаков промпта» — это было неверно, и я сказал об
    этом вслух: ноль стоят только дополнительные ВЫЗОВЫ. Прирост шага 1 —
    1199 знаков (4.1% системного промпта). Если однажды описание раздуется
    вдвое, этот тест обязан упасть: расход нельзя увеличивать молча.
    """
    assert len(DESC) <= 4000, (
        f"описание save_memory разрослось до {len(DESC)} знаков — это надо "
        "обсуждать с владельцем, а не менять тихо")


def test_the_criterion_is_not_contradicted_by_its_own_prohibition():
    """Внутренняя непротиворечивость: запрет и извлечение свойства в одном
    тексте могли бы спорить. Оговорка «когда внутри не спрятано свойство»
    — то, что их примиряет; без неё модель получает два взаимоисключающих
    указания и выберет любое.
    """
    lowered = DESC.lower()
    assert "no property hides inside" in lowered, (
        "запрет на события без оговорки снова сделает Джарвиса глухим к "
        "свойствам, спрятанным в событиях")
