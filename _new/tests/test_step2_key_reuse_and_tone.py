# -*- coding: utf-8 -*-
"""ШАГ 2′ — два дефекта, найденные владельцем и замером, а не планом.

ДЕФЕКТ 1 (владелец, живой диалог 29.08.2026). Джарвис отвечал как
делопроизводитель: «Отмечено, сэр. У вас есть друг по имени Лёха»,
«Обновляю информацию: вашего кота зовут Лев». Разбор (probe34) показал, что
старый запрет говорил только «не объявляй ДЕЙСТВИЕ» — и модель его
СОБЛЮДАЛА, слова «сохраняю» нет ни в одной реплике. Запрета на пересказ
РЕЗУЛЬТАТА не было нигде: он существовал только для блока памяти.

ДЕФЕКТ 2 (замер против плана). План предлагал искать кандидатов через
search_facts перед записью. Замер (probe27-29) показал, что на замене поиск
слепой: «Химки»/«BMW» не делят ни одного слова с «Moscow»/«Mercedes-Benz»,
поиск возвращает ПУСТО. Зато блок памяти уже печатает ключи в виде подписей
(probe30-33, 23 из 23 восстанавливаются), и это стоит 0 знаков.

Тесты ниже проверяют ровно то, что изменено, и НЕ проверяют вкус. Часть из
них падает на коде до Ш2′ — это условие приёмки, а не украшение.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "main.py")


def _main_source():
    with open(MAIN, encoding="utf-8") as fh:
        return fh.read()


def _save_memory_description():
    """Собрать описание save_memory из склеенных строк исходника.

    Описание собирается конкатенацией литералов, поэтому читать его надо так
    же, как читает Python: только строки, ЗАКАНЧИВАЮЩИЕ строку файла, иначе в
    выборку попадут ключи словаря parameters.
    """
    src = _main_source()
    start = src.index('"name": "save_memory"')
    end = src.index('"parameters"', start)
    block = src[start:end]
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"\s*(?:\n|$)', block)
    return "".join(parts)


# -- ДЕФЕКТ 1: тон. Запись не должна звучать как расписка -----------------

def test_reading_the_saved_fact_back_is_forbidden():
    """Прямой запрет на пересказ записанного, а не только на «я сохраняю»."""
    desc = _save_memory_description().lower()
    assert "do not read the saved fact back" in desc


def test_the_old_narrower_ban_is_still_there():
    """Старый запрет НЕ удалён: он покрывает другой случай («я сохраняю»)."""
    desc = _save_memory_description().lower()
    assert "do not announce that you are saving" in desc


def test_the_owners_actual_bad_replies_are_named_as_wrong():
    """Обе живые реплики владельца названы в тексте как НЕПРАВИЛЬНЫЕ.

    Общая инструкция «будь естественным» уже была в промпте и не помогла.
    Помогает только пример того, что именно прозвучало плохо.
    """
    desc = _save_memory_description().lower()
    assert "noted, sir, you have a cat" in desc
    assert "updating: your cat is lev" in desc


def test_the_text_says_what_to_do_instead_not_only_what_to_avoid():
    """Запрет без замены оставляет пустоту, и её снова займёт отчёт.

    Модели сказано, на что реагировать: на сказанное про ЖИЗНЬ.
    """
    desc = _save_memory_description().lower()
    assert "answer that" in desc
    assert "react to the cat" in desc


def test_silence_is_an_allowed_answer():
    """Не на каждую фразу нужен ответ — иначе Джарвис болтает из вежливости.

    ШАГ 2'': формулировка изменена. Было «say nothing and just save», и это
    читалось как «промолчи И ЗАПИШИ» — то есть само создавало давление
    писать в базу (probe35, живая запись cat_on_keyboard_event). Теперь
    разрешение молчать голосом отделено от вопроса, писать ли вообще.
    """
    desc = _save_memory_description().lower()
    assert "need no reply at all" in desc
    assert "then say nothing" in desc
    # и ровно та формулировка, что давила, вернуться не должна
    assert "just save" not in desc


def test_a_correction_gets_no_receipt():
    """«не не перепутал, его зовут тигр» получило «Принято. Вношу
    корректировку» — вторую расписку подряд."""
    desc = _save_memory_description().lower()
    assert "needs no receipt" in desc


def test_the_reason_bookkeeping_is_not_conversation_is_stated():
    """Причина названа, а не только правило: правило без причины модель
    обобщает неверно — так и появилась эта дыра."""
    desc = _save_memory_description().lower()
    assert "bookkeeping is not" in desc


# -- ДЕФЕКТ 2: ключ. Читать блок памяти, а не выдумывать синоним ----------

def test_the_model_is_told_to_look_at_the_memory_block_for_keys():
    desc = _save_memory_description().lower()
    assert "memory block" in desc
    assert "reuse that key" in desc


def test_the_label_to_key_mapping_is_shown_not_implied():
    """Замер (probe30-33): блок печатает 'Favorite Cars: ...'. Превращение
    подписи в ключ надо ПОКАЗАТЬ, иначе модель не обязана его угадать."""
    desc = _save_memory_description()
    assert "Favorite Cars" in desc
    assert "favorite_cars" in desc


def test_the_key_must_mean_what_it_holds():
    """Живая кривизна Ш1: key='cat_name', value='has a cat' — ключ обещает
    имя, которого в значении нет."""
    desc = _save_memory_description().lower()
    assert "has_cat" in desc
    assert "cat_name is for when you know" in desc


def test_no_search_call_was_added_before_saving():
    """План предлагал вызывать поиск перед записью. Замер показал, что на
    замене поиск слепой, поэтому кода в горячем пути быть НЕ должно.

    Тест защищает от того, чтобы это «улучшение» вернулось позже без замера.
    """
    assert "search_facts" not in _save_memory_description()
    assert "recall_memory before saving" not in _save_memory_description().lower()


# -- ШАГ 2'': два ПРОТИВОПОЛОЖНЫХ отказа из одного живого журнала --------
#
# 1) записалось лишнее: notes/cat_on_keyboard_event='Cat is on the keyboard
#    again' — ключ буквально _event, а свойство «есть кот» уже лежало;
# 2) пропущено нужное: «бл опять алекс сломал мой телефон» — ни одного
#    вызова save_memory, хотя Алекса в relationships нет.
# Ошибки в разные стороны, поэтому «строже/мягче» не лечит: нужны оба
# примера сразу.

def test_nothing_new_is_allowed_to_mean_no_call_at_all():
    """Главная причина лишней записи (probe35): разрешения НЕ ПИСАТЬ В БАЗУ
    в тексте не было ни одного, а давление писать было трижды."""
    desc = _save_memory_description().lower()
    assert "nothing new is a valid outcome" in desc
    assert "do not call this tool at all" in desc


def test_the_already_known_cat_is_named_as_the_wrong_case():
    """Именно эта живая запись разобрана в тексте, а не абстракция:
    общее правило про события уже было и не помогло."""
    desc = _save_memory_description()
    assert "cat_on_keyboard_event" in desc
    assert "already know the cat" in desc


def test_calling_with_nothing_new_is_called_worse_than_not_calling():
    """Модели нужен ЗНАК, в какую сторону ошибаться при сомнении."""
    desc = _save_memory_description().lower()
    assert "worse than not calling it" in desc


def test_a_new_person_is_a_property_even_in_a_rude_sentence():
    """Пропуск Алекса (probe36): пример был только РАЗРЕШАЮЩИЙ (рыбалка с
    Лёхой). Что новый человек — свойство даже в брани, сказано не было."""
    desc = _save_memory_description().lower()
    assert "a new person is always a property" in desc
    assert "alex" in desc
    assert "again" in desc


def test_rude_tone_is_explicitly_not_a_reason_to_skip_a_fact():
    """Владелец говорит с матом. Джарвис не должен принимать это за
    команду «не запоминай»."""
    desc = _save_memory_description().lower()
    assert "swearing" in desc
    assert "not being asked to approve" in desc


def test_the_two_opposite_mistakes_are_both_covered():
    """Смысловая проверка целиком: в тексте есть И тормоз, И педаль.

    Если однажды останется только одно, маятник качнётся, и падение этого
    теста скажет, какую половину потеряли.
    """
    desc = _save_memory_description().lower()
    brake = "do not call this tool at all" in desc
    gas = "a new person is always a property" in desc
    assert brake and gas, f"тормоз={brake}, педаль={gas}"


def test_the_removed_duplicates_did_not_come_back():
    """Перед поднятием потолка знаков я сократил три повтора. Если они
    вернутся, описание молча распухнет снова."""
    desc = _save_memory_description()
    assert "Those belong to this conversation" not in desc
    assert "Call this silently whenever either kind" not in desc


# -- цена: описание отправляется каждую сессию ---------------------------

def test_the_description_stays_within_a_sane_size():
    """Описание уходит в каждую сессию, поэтому у него есть цена в знаках.

    Потолок нарочно жёсткий: если следующее правило его пробьёт, пусть
    падает тест, а не молча растёт промпт.
    """
    desc = _save_memory_description()
    assert len(desc) < 4500, f"описание save_memory выросло до {len(desc)} знаков"


def test_the_pressure_to_always_write_is_gone():
    """Корень лишней записи — мои же слова, читавшиеся как «всё равно пиши».

    Тест сторожит именно формулировки, а не смысл: если кто-то вернёт
    «just save» или «file it» без оговорки, давление вернётся вместе с ними.
    """
    desc = _save_memory_description().lower()
    assert "just save" not in desc
    # «file it in the background» осталось, но теперь оно про ТОН ответа,
    # а не про обязанность писать — рядом обязана стоять оговорка.
    if "file it in the background" in desc:
        assert "nothing new is a valid outcome" in desc


def test_no_words_got_glued_together_by_a_missing_space():
    """Описание склеено из десятков литералов. Забытый пробел в конце строки
    молча слепляет два слова — смысл рвётся, а ошибки нет.

    Первая версия этого теста содержала `or True` и не могла упасть вообще.
    Здесь проверка настоящая: ищу склейки вида «backSaving» по всему тексту.
    """
    desc = _save_memory_description()
    glued = re.findall(r"[a-z]{2}[A-Z][a-z]{2}", desc)
    allowed = {"forgetMemory"}  # имён инструментов в camelCase тут нет
    real = [g for g in glued if g not in allowed]
    assert not real, f"склеенные слова в описании save_memory: {real}"


# -- то, что НЕЛЬЗЯ было потерять ----------------------------------------

def test_step1_criterion_survived():
    """Ш1 (свойство против события) не должен был пострадать."""
    desc = _save_memory_description().lower()
    assert "property or an" in desc
    assert "reveals that they have a cat" in desc


def test_replacement_warning_survived():
    """Предупреждение про необратимость замены (замер probe26) на месте."""
    desc = _save_memory_description().lower()
    assert "one property, one key" in desc
    assert "old value is gone" in desc


def test_standing_instructions_survived():
    """Существующие тесты требуют этих слов; проверяю здесь же, чтобы
    поломка нашлась в своём файле."""
    desc = _save_memory_description().lower()
    assert "passing moods" in desc
    assert "one-time commands" in desc


def test_the_one_reply_per_turn_rule_survived():
    """Владелец 28.08 слышал ответ дважды; правило трогать нельзя."""
    desc = _save_memory_description().lower()
    assert "one reply per turn" in desc


def test_the_advice_still_matches_the_measured_replacement_behaviour():
    """Если upsert_fact когда-нибудь НАЧНЁТ хранить историю, текст выше
    станет ложью. Тогда этот тест обязан упасть и потребовать переписать."""
    path = os.path.join(ROOT, "memory", "fact_store.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    body = src[src.index("def upsert_fact"):]
    body = body[:body.index("\ndef ", 10)]
    if "superseded_by=" in body.replace(" ", ""):
        pytest.fail(
            "upsert_fact начал писать superseded_by — значит замена больше не "
            "необратима, и предупреждение 'old value is GONE' в описании "
            "save_memory надо переписать по новому замеру."
        )
