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
    """Не на каждую фразу нужен ответ — иначе Джарвис болтает из вежливости."""
    desc = _save_memory_description().lower()
    assert "say nothing and just save" in desc


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


# -- цена: описание отправляется каждую сессию ---------------------------

def test_the_description_stays_within_a_sane_size():
    """Описание уходит в каждую сессию, поэтому у него есть цена в знаках.

    Потолок нарочно жёсткий: если следующее правило его пробьёт, пусть
    падает тест, а не молча растёт промпт.
    """
    desc = _save_memory_description()
    assert len(desc) < 4500, f"описание save_memory выросло до {len(desc)} знаков"


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
