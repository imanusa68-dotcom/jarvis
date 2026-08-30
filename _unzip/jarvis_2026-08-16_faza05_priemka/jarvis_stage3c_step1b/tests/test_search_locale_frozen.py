"""Замораживающие сторожа таблицы локалей (шаг 3-тер фазы 0.5).

Зачем этот файл появился
────────────────────────
detect_language() стала обёрткой над core/lang.py. Её зовут поиск по сети,
глубокое исследование и исполнитель. Самая опасная поломка здесь — тихая:
поиск уйдёт с чужой локалью, найдёт не те новости, а никто не упадёт и ничто
не краснеет. Поэтому поведение, снятое ЗАМЕРОМ ДО правки 7 августа 2026,
зафиксировано здесь буквально.

Два изменения сделаны ОСОЗНАННО и названы владельцу вслух:
  • японский текст был "zh", стал "ja" (был дефект: кана не считалась);
  • латинские языки были все "en", теперь различаются.
Всё остальное обязано совпадать со вчерашним днём знак в знак.

Запуск: python -m pytest -q  или  python tests/test_search_locale_frozen.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.search_locale as sl  # noqa: E402


# ═════════════════════════════════════════════════════════════════════
# ПОВЕДЕНИЕ, СНЯТОЕ ДО ПРАВКИ
# ═════════════════════════════════════════════════════════════════════

def test_non_latin_scripts_answer_exactly_as_before():
    frozen = {
        "привет как дела": "ru",
        "найди новости про RTX 5060 и сохрани в файл": "ru",
        "открой Google Chrome": "ru",
        "привіт як справи єдиний": "uk",
        "今天天气很好": "zh",
        "안녕하세요": "ko",
        "مرحبا": "ar",
        "नमस्ते": "hi",
        "γεια σου": "el",
        "สวัสดี": "th",
        "שלום": "he",
    }
    for text, want in frozen.items():
        got = sl.detect_language(text)
        assert got == want, f"локаль поиска уехала: {text!r} было {want!r}, стало {got!r}"


def test_unrecognized_input_answers_russian_but_latin_scraps_stay_english():
    # Изменено сознательно 7 августа по решению владельца: раньше любой
    # непонятный ввод отвечал английским. Теперь — русским, кроме голой
    # латиницы: её ответ уходит в локаль поиска.
    for scrap in ("", "   ", "42", "!!!"):
        got = sl.detect_language(scrap)
        assert got == "ru", f"{scrap!r} должен быть 'ru', стал {got!r}"
    for scrap in ("RTX 5060 review", "hi", "ok", "the weather is very nice today"):
        got = sl.detect_language(scrap)
        assert got == "en", f"{scrap!r} должен быть 'en', стал {got!r}"


def test_the_deliberate_changes_are_exactly_these_and_no_others():
    # Дефект исправлен: японский больше не ищется с китайской локалью.
    assert sl.detect_language("こんにちは世界") == "ja"
    # Латиница теперь различается.
    assert sl.detect_language("bugun hava cok guzel ve ben eve gidiyorum") == "tr"
    assert sl.detect_language("das wetter ist heute sehr warm und gut") == "de"
    # Третья сознательная правка (7 августа, вечер): умолчание стало русским,
    # но латинский обрывок остался английским ради поиска.
    assert sl.detect_language("") == "ru"
    assert sl.detect_language("RTX 5060 review") == "en"


def test_none_and_junk_do_not_raise():
    # Определитель зовётся из горячих мест; он не имеет права бросаться.
    assert sl.detect_language(None) == "ru"
    assert sl.detect_language("\n\t  ") == "ru"
    assert sl.detect_language("🚀🔥✨") == "ru"


# ═════════════════════════════════════════════════════════════════════
# ТАБЛИЦА ЛОКАЛЕЙ — её никто не трогал и трогать не должен
# ═════════════════════════════════════════════════════════════════════

def test_the_locale_table_still_holds_eighteen_languages():
    assert len(sl._LOCALE_MAP) == 18, f"в таблице стало {len(sl._LOCALE_MAP)} языков вместо 18"
    for code in ("ru", "uk", "en", "de", "fr", "es", "it", "pl", "pt", "tr",
                 "zh", "ja", "ko", "ar", "hi", "el", "th", "he"):
        assert code in sl._LOCALE_MAP, f"язык {code!r} пропал из таблицы"


def test_the_odd_provider_codes_are_untouched():
    # Две строки, где поставщик требует не то, что кажется очевидным.
    # Их легко «починить» по незнанию и тихо сломать поиск.
    assert sl.get_serpapi_hl("zh") == "zh-cn", "китайский у поставщика именно 'zh-cn'"
    assert sl.get_serpapi_hl("he") == "iw", "иврит у поставщика до сих пор 'iw'"
    assert sl.get_cse_lr("he") == "lang_iw"
    assert sl.get_cse_lr("zh") == "lang_zh-CN"


def test_case_and_region_suffix_do_not_change_the_answer():
    expected = ("ru", "lang_ru", "ru-RU,ru;q=0.9,en;q=0.5", "Russian")
    assert sl.get_locale("ru") == expected
    assert sl.get_locale("RU") == expected
    assert sl.get_locale("ru-RU") == expected
    assert sl.get_locale("ru_RU") == expected


def test_an_unknown_code_falls_back_instead_of_exploding():
    fallback = sl.get_locale("qq")
    assert isinstance(fallback, tuple) and len(fallback) == 4
    assert sl.get_locale("xx") == fallback
    assert sl.get_label("qq") == fallback[3]


def test_the_four_getters_agree_with_the_table():
    for code, row in sl._LOCALE_MAP.items():
        hl, lr, accept, label = row
        assert sl.get_serpapi_hl(code) == hl
        assert sl.get_cse_lr(code) == lr
        assert sl.get_accept_language(code) == accept
        assert sl.get_label(code) == label


def test_merge_and_dedup_survived_the_edit():
    # Функция живёт в том же файле и к языкам отношения не имеет —
    # именно поэтому её легко задеть плечом при правке соседнего кода.
    assert callable(sl.merge_and_dedup)


def test_the_detector_name_and_shape_are_unchanged():
    # Пять потребителей зовут эту функцию по имени и ждут строку.
    assert callable(sl.detect_language)
    got = sl.detect_language("привет")
    assert isinstance(got, str) and len(got) == 2, f"ответ изменил форму: {got!r}"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} зелёных")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
