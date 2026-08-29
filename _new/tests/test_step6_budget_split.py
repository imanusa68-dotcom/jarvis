# -*- coding: utf-8 -*-
"""ШАГ 6: правила поведения и факты о владельце платят из РАЗНЫХ кошельков.

ЧТО БЫЛО СЛОМАНО (замерено, не предположено).

Живой запуск владельца с JARVIS_DEBUG_PROMPT=1 показал:

    [Memory] 🧠 In prompt: 20 facts (communication_habits=8, hobbies=1,
             identity=1, notes=2, preferences=6, projects=1, relationships=1),
             1319 chars
    ...
    (3 more saved facts did not fit here...)

Арифметика: 1319 - 148 (длина хвоста) - 1 = 1170 знаков блока при бюджете 1200.
Упёрлись. Счётчик перечисляет hobbies=1 и notes=2, но НИ ОДНОЙ из этих секций
в блоке нет - значит выпали ровно они, целыми секциями, потому что рендерятся
последними.

Причина глубже, чем "бюджет маловат". Замер probe20: 8 правил поведения
владельца занимали 751 знак из 1200, то есть 63% бюджета. Сколько бы фактов он
ни надиктовал - до модели доходило ровно 9:

    добавлено фактов сверх 6 | доходило с правилами | доходило без правил
                          10 |          9           |        16
                          30 |          9           |        22
                          90 |          9           |        22

То есть это был не "кончается место", а ПОТОЛОК: правила и рассказ владельца о
себе платили из одного кармана, и правила его выпили. Джарвис не мог узнать
человека лучше, сколько бы тот ни рассказывал.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ - честно, чтобы никто не обманулся:
  - что модель ПОЛЬЗУЕТСЯ дошедшими фактами (это про поведение модели, не про код);
  - что владелец доволен новым бюджетом 4000 (это его приёмка, не тест);
  - качество самих фактов: дубли в правилах (Р12) шаг 6 не чистит.
"""

import re
import pytest

import memory.memory_manager as mm


def V(x):
    return {"value": x}


# ── Живые данные владельца ────────────────────────────────────────────────
# Ровно то, что он прислал в выводе JARVIS_DEBUG_PROMPT=1. Значения notes и
# hobbies он не показал (их выкинуло), поэтому взяты реалистичной длины: если
# тест зелёный на таких, на коротких он зелёный тем более.

_OWNER_RULES = {
    "explanation_style": V("Technical explanations without condescension or jargon"),
    "warn_before_file_action": V(
        "Always confirm file moves/deletes and specify exactly what will be "
        "affected due to past folder loss incident"),
    "leave_terms_untranslated": V("Do not translate English terms or names in responses"),
    "never_suggest": V("Never suggest reinstalling Windows"),
    "no_coffee_after_six": V("Do not suggest coffee after 6 PM"),
    "no_repeated_clarification": V("avoid repeated clarification, act immediately if clear"),
    "concise_answers": V("provide concise, short answers"),
    "act_autonomously": V("act autonomously when possible, avoid repeated permissions"),
}


def _owner_memory():
    return {
        "identity": {"city": V("Moscow")},
        "preferences": {
            "favorite_color": V("blue"),
            "favorite_drink": V("Water"),
            "favorite_cars": V("Mercedes-Benz cars"),
            "favorite_cake": V("Meringue cake"),
            "favorite_fruit": V("Strawberry"),
            "game_preference": V("prefers shooters, dislikes strategies"),
        },
        "projects": {"tg_bot_creation": V("Create a Telegram bot tomorrow morning")},
        "relationships": {
            "brother_city_job": V("brother lives in St. Petersburg, is a programmer")},
        "notes": {
            "folder_loss_incident": V("lost an important folder once, very cautious now"),
            "workflow_note": V("prefers to review changes before they are applied"),
        },
        "hobbies": {"main_hobby": V("builds and tunes a voice assistant in the evenings")},
        "communication_habits": dict(_OWNER_RULES),
    }


def _dropped(text):
    m = re.search(r"\((\d+) more saved facts", text)
    return int(m.group(1)) if m else 0


def _rules_present(text):
    return sum(1 for k in _OWNER_RULES if k.replace("_", " ").title() in text)


def _plain_facts(text):
    return text.count("  - ") - _rules_present(text)


# ── 1. Тот самый случай владельца ────────────────────────────────────────

def test_the_owner_loses_nothing_anymore():
    """Главный тест шага: его 20 фактов доходят все до единого."""
    text = mm.format_memory_for_prompt(_owner_memory())
    assert _dropped(text) == 0, (
        f"владелец снова теряет факты: выпало {_dropped(text)}")


def test_the_notes_and_hobbies_that_vanished_are_back():
    """Именно эти две секции выпадали в живом запуске."""
    text = mm.format_memory_for_prompt(_owner_memory())
    assert "Other notes:" in text, "секция заметок снова выпала"
    assert "Hobbies:" in text, "секция хобби снова выпала"


def test_every_behaviour_rule_still_arrives():
    """Расширение бюджета не должно было ничего вытеснить."""
    text = mm.format_memory_for_prompt(_owner_memory())
    assert _rules_present(text) == 8, f"дошло правил: {_rules_present(text)}/8"


def test_the_file_promise_is_never_the_thing_that_goes():
    """Забытый любимый цвет - огорчение. Забытое "предупреждай перед
    удалением файлов" - нарушенное обещание, и владелец узнает о потере
    только по факту потери файлов."""
    text = mm.format_memory_for_prompt(_owner_memory())
    assert "will be affected" in text


# ── 2. Потолок снят ──────────────────────────────────────────────────────

def _plain_reaching(extra):
    mem = _owner_memory()
    for i in range(extra):
        mem["preferences"][f"x{i:04d}"] = V(f"remembered detail number {i}")
    return _plain_facts(mm.format_memory_for_prompt(mem))


def test_more_talking_means_more_remembering():
    """Раньше доходило одно и то же число фактов при ЛЮБОМ их количестве.

    ВАЖНО про формулировку. Сначала я написал этот тест как
    `assert дошло > 9` - и он ПРОШЁЛ на старом, сломанном коде: там доходило
    11, то есть порог был подобран так, что дефект в него не попадал. Тест,
    зелёный на баге, хуже отсутствующего теста: он создаёт ложную уверенность.

    Настоящая подпись потолка - не конкретное число, а то, что число ПЕРЕСТАЁТ
    расти, когда владелец рассказывает больше. Это и проверяем.
    """
    few = _plain_reaching(10)
    many = _plain_reaching(150)
    assert many > few, (
        f"потолок вернулся: +10 фактов -> дошло {few}, "
        f"+150 фактов -> дошло {many} (не выросло)")


def test_the_ceiling_grew_with_the_budget_not_by_luck():
    """Проверяем связь с бюджетом, а не запоминаем магическое число."""
    mem = _owner_memory()
    for i in range(400):
        mem["preferences"][f"x{i:04d}"] = V(f"remembered detail number {i}")

    original = mm.PROMPT_CHAR_BUDGET
    try:
        mm.PROMPT_CHAR_BUDGET = 1200
        small = _plain_facts(mm.format_memory_for_prompt(mem))
        mm.PROMPT_CHAR_BUDGET = 4000
        large = _plain_facts(mm.format_memory_for_prompt(mem))
    finally:
        mm.PROMPT_CHAR_BUDGET = original

    assert large > small * 2, (
        f"бюджет вырос втрое, а фактов дошло {small} -> {large}")


# ── 3. Правила не могут снова захватить бюджет ───────────────────────────

def test_rules_cannot_eat_more_than_their_share():
    """Сердце шага 6. 300 правил не должны выдавить рассказ о владельце.

    Именно в эту сторону система ломалась раньше: закрепление правил работало
    как захват, а не как приоритет.
    """
    mem = {
        "identity": {"city": V("Moscow")},
        "preferences": {f"p{i:03d}": V(f"a real fact about the owner number {i}")
                        for i in range(40)},
        "communication_habits": {
            f"rule_{i:03d}": V(f"a standing rule about how to behave, number {i}")
            for i in range(300)},
    }
    text = mm.format_memory_for_prompt(mem)
    plain = text.count("  - ") - sum(
        1 for i in range(300) if f"Rule {i:03d}" in text)
    assert plain >= 10, (
        f"правила снова съели бюджет: обычных фактов дошло {plain}")


def test_at_least_one_rule_always_survives():
    """Обратный перегиб: делёж бюджета не должен стереть правила целиком."""
    mem = {
        "preferences": {f"p{i:03d}": V(f"a fact about the owner number {i}")
                        for i in range(400)},
        "communication_habits": {
            "warn_before_file_action": V("Warn me before touching my files")},
    }
    text = mm.format_memory_for_prompt(mem)
    assert "before touching my files" in text, "последнее правило пропало"


def test_the_budget_still_wins_in_the_end():
    """Бюджет остаётся жёстким: неограниченный блок - это тот самый дефект,
    ради которого бюджет и вводили."""
    mem = {
        "preferences": {f"p{i:04d}": V(f"a fact about the owner number {i}")
                        for i in range(2000)},
        "communication_habits": {
            f"rule_{i:03d}": V(f"a standing rule number {i}") for i in range(200)},
    }
    text = mm.format_memory_for_prompt(mem)
    assert len(text) <= mm.PROMPT_CHAR_BUDGET + 300, (
        f"блок вышел из бюджета: {len(text)} знаков")


# ── 4. Старые гарантии целы ──────────────────────────────────────────────

def test_no_fact_is_ever_cut_in_half():
    """Обрезка посередине факта - исходный дефект, который бюджет лечил."""
    mem = {
        "preferences": {f"p{i:04d}": V(f"a fact about the owner number {i}")
                        for i in range(500)},
    }
    text = mm.format_memory_for_prompt(mem)
    assert "…" not in text


def test_a_small_memory_is_still_left_completely_alone():
    text = mm.format_memory_for_prompt({
        "identity": {"name": V("Rustam")},
        "preferences": {"favorite_color": V("blue")},
    })
    assert "Rustam" in text and "blue" in text
    assert "did not fit" not in text


def test_the_dropped_count_is_still_honest():
    """Врущий счётчик хуже отсутствующего: Джарвис не сможет отличить
    "не сохранено" от "не влезло" и уверенно скажет "я не знаю"."""
    total = 2000
    mem = {"notes": {f"f{i:04d}": V(f"a saved fact about work and tools, number {i}")
                     for i in range(total)}}
    text = mm.format_memory_for_prompt(mem)
    shown = sum(1 for line in text.splitlines() if line.startswith("  - "))
    assert _dropped(text) == total - shown, (
        f"заявлено выпавших {_dropped(text)}, реально {total - shown}")


def test_rendering_never_mutates_the_owners_memory():
    """Делёж бюджета режет СПИСКИ строк, а не данные владельца."""
    import json
    mem = _owner_memory()
    for i in range(200):
        mem["preferences"][f"x{i:03d}"] = V(f"detail number {i}")
    before = json.dumps(mem, sort_keys=True, ensure_ascii=False)
    mm.format_memory_for_prompt(mem)
    assert json.dumps(mem, sort_keys=True, ensure_ascii=False) == before


def test_dropping_facts_leaves_no_empty_headings():
    mem = {
        "preferences": {f"p{i:04d}": V(f"a fact about the owner number {i}")
                        for i in range(600)},
        "communication_habits": {"never_suggest": V("Never suggest reinstalling Windows")},
    }
    lines = mm.format_memory_for_prompt(mem).splitlines()
    for i, line in enumerate(lines):
        if line.endswith(":") and not line.startswith("  - ") \
                and "[WHAT YOU KNOW" not in line:
            following = lines[i + 1] if i + 1 < len(lines) else ""
            assert following.startswith("  - "), \
                f"заголовок без содержимого: {line!r}"


# ── 5. Цена изменения названа вслух ──────────────────────────────────────

def test_the_budget_stays_a_small_part_of_the_prompt():
    """Системный промпт - 29064 знака и уходит каждую сессию. Память при
    бюджете 4000 - около 14% от него. Если однажды кто-то поставит 40000,
    этот тест обязан упасть: расход на память нельзя менять молча.
    """
    assert mm.PROMPT_CHAR_BUDGET <= 8000, (
        "бюджет памяти вырос до размеров, которые надо обсуждать с владельцем, "
        f"а не менять тихо: {mm.PROMPT_CHAR_BUDGET}")


def test_the_rules_share_is_a_share_not_everything():
    assert 0.0 < mm._RULES_BUDGET_SHARE < 1.0, (
        "доля правил должна оставлять место фактам о владельце")
