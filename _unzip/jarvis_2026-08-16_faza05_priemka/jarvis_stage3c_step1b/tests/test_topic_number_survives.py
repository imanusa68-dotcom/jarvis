# -*- coding: utf-8 -*-
"""
Номер модели не теряется по дороге в поиск, а пустота называется честно.

Живой случай, с которого начался шаг: владелец спросил про "RTX 5060",
а в поиск ушло "RTX" — и ответ был про всю линейку. Потом спросил про
"Ryzen 9800X3D", в поиск ушло "Ryzen", а сводка сочинила "возможно,
ещё не анонсирован" — неправда, сказанная уверенным голосом.

Почему тела тестов такие подробные: этот файл один раз уже потерял часть
своей строгости при пересборке дерева, и потеря прошла незамеченной,
потому что ослабленные проверки тоже были зелёными. Сообщения об ошибке
здесь обязаны называть фразу и полученное значение — иначе красный тест
не объясняет, что именно сломалось.

Run standalone: python tests/test_topic_number_survives.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.query_rewriter import (  # noqa: E402
    classify_intent,
    detect_topic_entity,
    make_topic_news_queries,
    rewrite,
)

DOOR = "actions/web_search.py"
NO_GUESS = "Never guess whether something exists, was announced, released or cancelled"
NOT_COVERED = "If the results do not cover the exact subject of the question"

PREPOSITIONS = {
    "про", "о", "об", "для", "в", "на", "по", "от", "за", "из",
    "с", "со", "у", "к", "при", "до", "после",
}


# ────────── номер модели остаётся с именем ──────────

def test_the_video_card_keeps_its_number():
    """"новости RTX 5060" — искать надо конкретную карту, а не всю линейку."""
    phrase = "новости RTX 5060"
    got = detect_topic_entity(phrase)
    assert got == "RTX 5060", "номер потерян: %r -> %r" % (phrase, got)


def test_the_processor_keeps_its_number():
    """Живой случай из прогона: Ryzen 9800X3D, а в поиск уходило "Ryzen"."""
    phrase = "новости про Ryzen 9800X3D"
    got = detect_topic_entity(phrase)
    assert got == "Ryzen 9800X3D", "номер потерян: %r -> %r" % (phrase, got)


def test_the_phone_keeps_its_number():
    """Номер поколения телефона — часть предмета поиска."""
    phrase = "новости про iPhone 17"
    got = detect_topic_entity(phrase)
    assert got == "iPhone 17", "номер потерян: %r -> %r" % (phrase, got)


def test_a_single_digit_number_survives():
    """Однозначный номер раньше выбрасывался фильтром длины."""
    phrase = "что нового в GTA 6"
    got = detect_topic_entity(phrase)
    assert got == "GTA 6", "однозначный номер потерян: %r -> %r" % (phrase, got)


def test_the_suffix_after_the_number_survives():
    """Хвост модели (Ti, Pro, XT) отличает одну железку от другой."""
    phrase = "RTX 5060 Ti обзор"
    got = detect_topic_entity(phrase)
    assert got == "RTX 5060 Ti", "хвост модели потерян: %r -> %r" % (phrase, got)


def test_two_numbers_in_a_row_survive():
    """Цепочка из двух чисел — тоже одно имя товара."""
    phrase = "Ryzen 9 9950X3D новости"
    got = detect_topic_entity(phrase)
    assert got == "Ryzen 9 9950X3D", "цепочка номеров потеряна: %r -> %r" % (phrase, got)


def test_the_operating_system_keeps_its_number():
    """Номер версии системы менять предмет поиска не должен."""
    phrase = "новости Windows 11"
    got = detect_topic_entity(phrase)
    assert got == "Windows 11", "номер версии потерян: %r -> %r" % (phrase, got)


def test_the_console_keeps_number_and_suffix():
    """И номер, и хвост сразу: PlayStation 5 Pro."""
    phrase = "новости про PlayStation 5 Pro"
    got = detect_topic_entity(phrase)
    assert got == "PlayStation 5 Pro", "номер или хвост потерян: %r -> %r" % (phrase, got)


def test_an_english_question_keeps_the_number_too():
    """Разбор фразы не должен зависеть от языка вопроса."""
    phrase = "news about RTX 5060"
    got = detect_topic_entity(phrase)
    assert got == "RTX 5060", "в английской фразе номер потерян: %r -> %r" % (phrase, got)


# ────────── что работало — работает ──────────

def test_names_without_numbers_are_untouched():
    """Правка не должна была тронуть имена без цифр."""
    pairs = (
        ("Claude Code новости", "Claude Code"),
        ("возможности OpenAI API", "OpenAI API"),
        ("новости GPT-5", "GPT-5"),
        ("новости про Galaxy S26 Ultra", "Galaxy S26 Ultra"),
    )
    for phrase, want in pairs:
        got = detect_topic_entity(phrase)
        assert got == want, "имя без номера сломалось: %r -> %r" % (phrase, got)


def test_a_general_question_still_has_no_topic():
    """Общий вопрос должен уйти в сводку дня, а не в поиск по теме."""
    phrase = "что нового в мире"
    got = detect_topic_entity(phrase)
    assert got is None, "общий вопрос получил тему: %r -> %r" % (phrase, got)


def test_a_lonely_number_is_not_a_topic():
    """Голый номер без имени — не предмет поиска: "news" и "новости" шум."""
    for phrase in ("новости 5060", "news 5060"):
        got = detect_topic_entity(phrase)
        assert got is None, "голый номер стал темой: %r -> %r" % (phrase, got)


# ────────── предлог не часть темы ──────────

def test_the_preposition_is_not_part_of_the_topic():
    """Предлог, прилипший к теме, травит все запросы к поиску."""
    phrase = "новости про Сбер"
    got = detect_topic_entity(phrase)
    assert got == "Сбер", "предлог прилип к теме: %r -> %r" % (phrase, got)


def test_a_cyrillic_name_with_a_number_loses_the_preposition_only():
    """Кириллическое имя с номером: снимается только предлог."""
    phrase = "новости про Ми-8"
    got = detect_topic_entity(phrase)
    assert got == "Ми-8", "кириллическое имя с номером сломалось: %r -> %r" % (phrase, got)


# ────────── номер доезжает до самих запросов ──────────

def test_every_generated_query_carries_the_full_number():
    """Все варианты запроса обязаны нести номер, иначе поиск про линейку."""
    cases = (
        ("RTX 5060", "новости RTX 5060"),
        ("Ryzen 9800X3D", "новости про Ryzen 9800X3D"),
    )
    for topic, phrase in cases:
        queries = make_topic_news_queries(topic, phrase, "ru")
        assert queries, "запросы по теме не собрались: %r" % topic
        dirty = [q for q in queries if topic not in q]
        assert not dirty, "номер выпал из запросов (%r): %r" % (topic, dirty[:3])
        empty = [q for q in queries if not q.strip()]
        assert not empty, "среди запросов есть пустые: %r" % topic


def test_the_intent_is_still_topic_news():
    """Правка не должна была сбить маршрут вопроса."""
    for phrase in ("новости RTX 5060", "новости про Ryzen 9800X3D"):
        got = classify_intent(phrase)
        assert got == "topic_news", "намерение съехало: %r -> %r" % (phrase, got)


def test_the_rewrite_hands_the_full_number_to_the_search():
    """Публичный путь целиком: rewrite отдаёт и тему, и очередь запросов."""
    phrase = "новости про Ryzen 9800X3D"
    variants = rewrite(phrase, language="ru")
    topic = variants.get("topic")
    assert topic == "Ryzen 9800X3D", "в поиск ушла обрезанная тема: %r" % topic
    head = topic.split()[0].lower()
    assert head not in PREPOSITIONS, "тема начинается с предлога: %r" % topic
    topic_queries = variants.get("topic_queries", [])
    assert topic_queries, "вариантов запроса нет: %r" % phrase
    dirty = [q for q in topic_queries if "9800X3D" not in q]
    assert not dirty, "номер выпал из запросов: %r" % dirty[:3]


# ────────── пустота называется честно ──────────

def test_the_answer_is_forbidden_to_guess_about_a_release():
    """Оба промпта обязаны запрещать догадки про выход товара."""
    src = (ROOT / DOOR).read_text(encoding="utf-8")
    seen = src.count(NO_GUESS)
    assert seen == 2, "запрет догадок стоит не в обоих ответах: %d" % seen
    assert NOT_COVERED in src, "нет правила честно называть несовпадение темы"


def test_the_rewriter_never_opens_a_door_to_the_model():
    """Разбор запроса — чистые правила, без модели и без сети."""
    src = (ROOT / "core/query_rewriter.py").read_text(encoding="utf-8")
    for needle in ("aux_call", "genai." + "Client(", "import google", "api_key"):
        assert needle not in src, "переписчик открыл дверь к модели: %s" % needle


def test_a_time_span_is_not_a_topic():
    """"новости за неделю" — это срок, а не предмет поиска."""
    for phrase in (
        "главные новости за вчера",
        "новости за неделю",
        "новости за сегодня",
        "новости за прошлую неделю",
        "новости за месяц",
    ):
        got = detect_topic_entity(phrase)
        assert got is None, "срок стал темой: %r -> %r" % (phrase, got)


def test_a_digest_question_still_reaches_the_digest():
    """Сводка дня не должна угоняться в поиск по предлогу."""
    for phrase in (
        "главные новости за вчера",
        "новости за сегодня",
    ):
        got = classify_intent(phrase)
        assert got == "headline_digest", \
            "сводка ушла не туда: %r -> %r" % (phrase, got)


def test_no_topic_ever_starts_with_a_preposition():
    """Ни одна тема не начинается с предлога — он травит все запросы."""
    phrases = (
        "новости про Сбер", "новости из Китая", "новости про Ми-8",
        "новости про Ryzen 9800X3D", "что нового в GTA 6",
    )
    for phrase in phrases:
        got = detect_topic_entity(phrase)
        assert got, "тема пропала совсем: %r" % phrase
        head = got.split()[0].lower()
        assert head not in PREPOSITIONS, \
            "тема начинается с предлога: %r -> %r" % (phrase, got)


if __name__ == "__main__":
    names = sorted(n for n in globals() if n.startswith("test_"))
    passed = 0
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print("  FAIL  %s -- %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  ERROR %s -- %s: %s" % (name, type(exc).__name__, exc))
        else:
            passed += 1
            print("  PASS  %s" % name)
    total = passed + failed
    print("RESULT: %d/%d %s" % (passed, total, "ALL PASS" if failed == 0 else "SOME FAILED"))
    sys.exit(0 if failed == 0 else 1)
