"""ОГОВОРКА — ЭТО СТИРАНИЕ, А НЕ ВТОРАЯ ЗАПИСЬ.

ЖИВОЙ ЗАМЕР, 30.08.2026. Владелец сказал одной фразой:
    «я в америке живу… ой тоесть Химках»
и в его журнале два вызова подряд:
    save_memory {'key':'country','value':'United States'}
    save_memory {'key':'city',   'value':'Khimki'}
Поправка легла в ДРУГОЙ ключ, ошибочный `country` остался цел, и строка
«Country: United States» уезжала в промпт КАЖДУЮ сессию — 35 фактов,
1854 знака вместо 34 и 1831 — пока владелец сам не сказал «забудь мою
страну» и в логе не появилось «Forgotten: identity/country».

МОДЕЛЬ ДЕЙСТВОВАЛА ПО ИНСТРУКЦИИ. В описании save_memory прямо сказано:
не уверена, что свойство то же — заводи НОВЫЙ ключ, потому что перезапись
необратима. Правило верное, и эти тесты его НЕ отменяют (см. секцию 3).
Дыра была ровно в одном: случай «владелец отменяет только что сказанное»
не был назван нигде, а для него нужен третий путь — стирание.

ЧЕГО ЭТИ ТЕСТЫ НЕ ДОКАЗЫВАЮТ. Что модель ПОСЛУШАЕТСЯ. Инструкция — просьба,
не запрет: судить будет живой прогон на машине владельца. Здесь проверяется
только то, что проверяемо в песочнице: правило существует, стоит в нужном
инструменте, не спорит с соседними правилами и не может пропасть молча.
Ровно та же честная граница, что у правила про второй ответ.

ПОЧЕМУ ТЕКСТ В forget_memory, А НЕ В save_memory. Замер: описание
save_memory — 4498 знаков при потолке 4500, и мой же сторож в
test_step1_save_criterion говорит, что четвёртый подъём потолка означает
«текст исчерпан, задачу надо решать не текстом». Секция 4 сторожит это
устройство: правило обязано жить там, куда модель смотрит, решая ЧТО
удалить, и не имеет права переехать обратно и пробить потолок.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "main.py"


def _main_source() -> str:
    return MAIN.read_text(encoding="utf-8")


def _tool_description(tool: str) -> str:
    """Собрать описание инструмента из склеенных литералов исходника.

    Читаем так же, как читает Python: берём только строки, ЗАКАНЧИВАЮЩИЕ
    строку файла. Иначе в выборку попадут ключи словаря `parameters` и
    любое совпадение станет случайным. Способ повторяет
    tests/test_step2_key_reuse_and_tone.py намеренно: два разных чтения
    одного текста разошлись бы, и тогда один из сторожей начал бы врать.
    """
    src = _main_source()
    start = src.index(f'"name": "{tool}"')
    end = src.index('"parameters"', start)
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"\s*(?:\n|$)', src[start:end])
    return "".join(parts)


FORGET = _tool_description("forget_memory")
SAVE = _tool_description("save_memory")


# ── 1. ПРАВИЛО СУЩЕСТВУЕТ И НАЗЫВАЕТ СЛУЧАЙ ВЛАДЕЛЬЦА ────────────────────

def test_the_self_correction_case_is_named_out_loud():
    """Без имени случая правило не сработает: модель не догадается сама."""
    low = FORGET.lower()
    assert "self-correction" in low
    assert "also a removal" in low


def test_the_owners_actual_sentence_is_the_example():
    """Пример взят из ЕГО лога, а не выдуман.

    Выдуманный пример («I like tea, no, coffee») звучит убедительно и не
    покрывает разбор: у владельца отменялась СТРАНА, а поправка называла
    ГОРОД — то есть два разных ключа, и именно поэтому дубликат выжил.
    """
    low = FORGET.lower()
    assert "america" in low
    assert "khimki" in low
    assert "oh i mean" in low


def test_the_russian_trigger_words_are_there():
    """Владелец говорит по-русски. «ой тоесть» — его дословная формулировка.

    Английские маркеры оставлены рядом: разговор двуязычный, и обрезать
    одну сторону значит слепнуть на половине реплик.
    """
    assert "ой тоесть" in FORGET
    assert "нет, я имел в виду" in FORGET
    assert "no wait, i meant" in FORGET.lower()


def test_the_consequence_is_stated_not_implied():
    """Сказано, ЧЕМ плох новый ключ, а не только «так не делай».

    Замер владельца: оба факта остались, и неверный ехал в промпт каждую
    сессию. Правило без последствия читается как вкусовое и проигрывает
    соседнему правилу, у которого последствие названо.
    """
    low = FORGET.lower()
    assert "leaves both facts in memory" in low
    assert "every session" in low


def test_the_order_of_the_two_calls_is_explicit():
    """Сначала стереть, потом записать — порядок обязан быть назван.

    Обратный порядок (сначала save, потом forget) в живом ходе стирает
    ТОЛЬКО ЧТО записанное, если модель промахнётся ключом. Здесь цена
    ошибки выше, чем польза от свободы.
    """
    low = FORGET.lower()
    assert "call forget_memory on the key you just" in low
    assert "only then save the corrected fact" in low


# ── 2. ПРАВИЛО НЕ ПРЕВРАЩАЕТ ПОПРАВКУ В ДОКЛАД ───────────────────────────

def test_a_correction_still_gets_no_receipt():
    """Два вызова — это ДВА повода отчитаться, и оба запрещены.

    Дефект «Отмечено, сэр» лечился в шаге 2′ для save_memory. Правка,
    добавляющая второй вызов и молчащая про тон, воскресила бы расписку
    ровно там, где её уже вылечили: «Забыл страну. Записал город».
    """
    low = FORGET.lower()
    assert "needs no receipt" in low
    assert "do not report either call" in low


def test_the_model_is_told_what_to_say_instead():
    """Запрета мало: пустоту снова займёт отчёт.

    Тот же вывод, что в шаге 2′ (probe34): модель соблюдала запрет на
    слово «сохраняю» и всё равно пересказывала результат, потому что
    замены ей не дали.
    """
    low = FORGET.lower()
    assert "answer what they actually said" in low


# ── 3. СОСЕДНИЕ ПРАВИЛА НЕ СЛОМАНЫ ───────────────────────────────────────

def test_the_new_key_rule_in_save_memory_survives():
    """Правило «сомневаешься — новый ключ» ОСТАЁТСЯ на месте.

    Это не формальность. Оно защищает от перезаписи, которая необратима
    (probe26: upsert_fact делает UPDATE, прежнее значение исчезает без
    следа). Оговорка — узкое исключение: владелец САМ отменил сказанное.
    Стереть общее правило заодно с добавлением исключения значило бы
    вылечить один случай и открыть худший.
    """
    low = SAVE.lower()
    assert "use a new key" in low
    assert "an overwrite destroys something they told you" in low


def test_the_ban_on_fake_forgetting_survives():
    """Стирание по-прежнему нельзя подделывать записью «disregard»."""
    low = FORGET.lower()
    assert "never fake forgetting" in low
    assert "disregard previous" in low


def test_the_honest_report_of_the_result_survives():
    """«Забыл» говорится только после 'Forgotten:' — это не тронуто.

    Иначе Джарвис будет уверенно сообщать об удалении, которого не было:
    ровно тот дефект, из-за которого forget_memory и появился.
    """
    assert "AFTER this returns 'Forgotten:'" in FORGET
    assert "'Not found'" in FORGET


def test_the_two_rules_do_not_contradict_each_other():
    """Внутренняя непротиворечивость: у исключения ЕСТЬ признак.

    Без признака модель получает два взаимоисключающих указания («новый
    ключ» против «сначала стереть») и выберет любое. Признак — отмена
    только что сказанного, и он назван словами-маркерами.
    """
    low = FORGET.lower()
    assert "when they retract what they have just said" in low
    # и это НЕ разрешение стирать по своему усмотрению:
    assert "the key you just" in low


# ── 4. УСТРОЙСТВО: ПРАВИЛО СТОИТ ТАМ, ГДЕ ПОМЕЩАЕТСЯ ─────────────────────

def test_the_rule_lives_in_the_forgetting_tool():
    """Правило про стирание — в описании стирания, и это замер, не вкус.

    Описание save_memory занимает 4498 знаков при потолке 4500. Тот же
    абзац там пробил бы потолок и потребовал ЧЕТВЁРТОГО подъёма, который
    мой же сторож объявил признаком исчерпанности текста.
    """
    assert "self-correction" in FORGET.lower()
    assert "self-correction" not in SAVE.lower()


def test_save_memory_did_not_grow_by_this_change():
    """Цена правки для save_memory — ровно ноль знаков.

    Число вписано намеренно: если однажды кто-то перенесёт абзац сюда,
    тест упадёт с точной цифрой, а не с рассуждением.
    """
    assert len(SAVE) == 4498, (
        f"описание save_memory изменилось: {len(SAVE)} знаков вместо 4498 — "
        "проверьте потолки в test_step1/test_step2")


def test_the_price_of_the_new_paragraph_is_named():
    """Описание forget_memory выросло, и рост назван вслух.

    Оно уходит в промпт каждую сессию, как и save_memory. Потолок
    поставлен близко к текущему размеру: расход не должен расти молча.
    """
    assert len(FORGET) < 1500, (
        f"описание forget_memory разрослось до {len(FORGET)} знаков — "
        "это надо обсуждать с владельцем, а не менять тихо")


def test_the_behaviour_prompt_was_not_touched():
    """core/prompts/06_behavior.txt НЕ правился — обещание владельцу.

    Прямое обещание: этот файл больше не редактируется. Вся правка —
    код. Сторож стоит здесь, потому что соблазн дописать абзац в промпт
    возникает ровно в таких задачах.
    """
    text = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
        encoding="utf-8")
    assert "self-correction" not in text.lower()
    assert "ой тоесть" not in text
    # а прежний блок про забывание на месте — его никто не ломал:
    assert "TO FORGET, ACTUALLY FORGET" in text


# ── 5. МЕХАНИЗМ ПОД ПРАВИЛОМ РАБОТАЕТ ────────────────────────────────────

def test_forgetting_actually_removes_the_owners_country(tmp_path, monkeypatch):
    """Сквозная проверка на ЕГО данных: правило зовёт то, что работает.

    Инструкция без работающего инструмента — обещание. Здесь
    воспроизведён его случай целиком: страна записана, город записан,
    страна стёрта — и город при этом цел.
    """
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    import importlib

    import memory.memory_manager as mm
    importlib.reload(mm)

    mm.update_memory({"identity": {"country": {"value": "United States",
                                               "said": "я в америке живу"}}})
    mm.update_memory({"identity": {"city": {"value": "Khimki",
                                            "said": "ой тоесть Химках"}}})
    assert "country" in mm.load_memory()["identity"]

    result = mm.forget("country", "identity")
    assert result.startswith("Forgotten:"), result

    identity = mm.load_memory()["identity"]
    assert "country" not in identity
    assert identity["city"]["value"] == "Khimki", \
        "стирание страны не имеет права задеть город"


def test_the_wrong_fact_leaves_the_prompt_after_forgetting(tmp_path,
                                                           monkeypatch):
    """Главное для владельца: строка исчезает ИЗ ПРОМПТА.

    Он видел «Country: United States» в блоке. Проверяем то, что видит
    он, а не только словарь на диске.
    """
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    import importlib

    import memory.memory_manager as mm
    importlib.reload(mm)

    mm.update_memory({"identity": {"country": {"value": "United States"},
                                   "city": {"value": "Khimki"}}})
    before = mm.format_memory_for_prompt(mm.load_memory())
    assert "Country: United States" in before

    mm.forget("country", "identity")
    after = mm.format_memory_for_prompt(mm.load_memory())
    assert "Country: United States" not in after
    assert "City: Khimki" in after, "город обязан остаться"
