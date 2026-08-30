"""Сторожа определителя языка core/lang.py (шаг 3-тер фазы 0.5).

Что здесь охраняется:
  1. Письменности решаются буквами — точно и мгновенно.
  2. Японский больше не выдаёт себя за китайский.
  3. Латинские языки различаются по частым словам и своеобразным буквам.
  4. Две юникод-ловушки: турецкая заглавная İ и буква ö в двух записях.
  5. Слова и пороги живут в конфиге, а не в коде.
  6. Ни сети, ни модели, ни тяжёлых библиотек — ни одной двери наружу.
  7. Потеря конфига деградирует до письменностей, а не роняет задачу.
  8. Каждый код из конфига имеет человеческое имя в таблице локалей.
  9. Решение всегда сообщает, на каком основании оно принято.

Запуск: python -m pytest -q  или  python tests/test_lang_detector.py
"""
import inspect
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.loader as loader             # noqa: E402
import core.lang as lang                   # noqa: E402
import core.search_locale as search_locale  # noqa: E402


def _languages_config() -> dict:
    text = (ROOT / "config" / "languages.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


# ═════════════════════════════════════════════════════════════════════
# ПИСЬМЕННОСТИ
# ═════════════════════════════════════════════════════════════════════

def test_scripts_are_decided_by_letters():
    cases = {
        "привет как дела сэр": "ru",
        "найди новости про RTX 5060 и сохрани в файл": "ru",
        "привіт як справи єдиний": "uk",
        "今天天气很好": "zh",
        "안녕하세요 세계": "ko",
        "مرحبا كيف حالك": "ar",
        "नमस्ते दुनिया": "hi",
        "γεια σου κόσμε": "el",
        "สวัสดีชาวโลก": "th",
        "שלום עולם": "he",
    }
    for text, want in cases.items():
        code, why = lang.detect_with_reason(text)
        assert code == want, f"{text!r} опознан как {code!r}, а не {want!r}"
        assert why == "буквы", f"{text!r} решён по {why!r}, а должен по буквам"


def test_japanese_no_longer_pretends_to_be_chinese():
    # До 7 августа 2026 японский текст искался с китайской локалью:
    # кана нигде не считалась, а кандзи попадают в китайский диапазон.
    assert lang.detect("こんにちは世界") == "ja", "хирагана не узнана"
    assert lang.detect("コンピューター") == "ja", "катакана не узнана"
    assert lang.detect("日本語を話します") == "ja", "смесь каны и кандзи не узнана"
    # А чистый китайский обязан остаться китайским.
    assert lang.detect("今天天气很好") == "zh", "китайский сломан ради японского"


def test_ukrainian_is_not_swallowed_by_russian():
    assert lang.detect("привіт як справи єдиний") == "uk"
    assert lang.detect("привет как дела") == "ru"


# ═════════════════════════════════════════════════════════════════════
# ЛАТИНИЦА
# ═════════════════════════════════════════════════════════════════════

def test_latin_languages_are_told_apart_by_words():
    cases = {
        "the weather is very nice today": "en",
        "bugun hava cok guzel ve ben eve gidiyorum": "tr",
        "merhaba nasilsin bugun ne yapiyorsun": "tr",
        "das wetter ist heute sehr warm und gut": "de",
        "ich bin nicht sicher aber das ist gut": "de",
        "le temps est tres beau aujourd hui": "fr",
        "je ne sais pas mais c est pour vous": "fr",
        "hola como estas amigo": "es",
        "gracias por todo muy bien hoy": "es",
        "ciao come stai molto bene oggi": "it",
        "dzien dobry jak sie masz bardzo dobrze": "pl",
        "ola bom dia como voce esta hoje": "pt",
    }
    for text, want in cases.items():
        code, why = lang.detect_with_reason(text)
        assert code == want, f"{text!r} опознан как {code!r}, а не {want!r}"
        assert why == "слова", f"{text!r} решён по {why!r}, а должен по словам"


def test_accents_survive_both_ways_of_writing_them():
    # «ö» бывает одним знаком и бывает «o» плюс значок. Глаз разницы не видит.
    assert lang.fold("schön") == lang.fold("scho\u0308n"), "две записи ö разошлись"
    assert lang.detect("das wetter ist heute sehr schön") == "de"
    assert lang.detect("das wetter ist heute sehr scho\u0308n") == "de"
    # Турецкая заглавная İ при понижении регистра даёт два знака вместо одного.
    assert lang.fold("İstanbul") == "istanbul", "турецкая заглавная I не очищена"
    assert lang.detect("çok gu\u0308zel") == "tr", "турецкие слова со значками потеряны"


def test_short_scraps_are_decided_by_rare_letters():
    # Слов мало, но буква выдаёт язык с головой.
    code, why = lang.detect_with_reason("İstanbul")
    assert code == "tr" and why == "значки над буквами", f"получилось {code!r} по {why!r}"
    code, why = lang.detect_with_reason("Gdańsk")
    assert code == "pl" and why == "значки над буквами", f"получилось {code!r} по {why!r}"


def test_nothing_recognizable_falls_back_to_the_config_value():
    cfg = _languages_config()
    for scrap in ("", "   ", "42", "!!!", "\n\t"):
        code, why = lang.detect_with_reason(scrap)
        assert code == cfg["fallback"], f"{scrap!r} дал {code!r}"
        assert why == "по умолчанию"


def test_the_default_is_russian_because_the_owner_speaks_russian():
    """Решение владельца 7 августа 2026, 21:07.

    Сторож нужен, чтобы чей-нибудь «аккуратный порядок» не вернул молча
    английский обратно: умолчание — решение владельца, а не вкус кода.
    """
    assert _languages_config()["fallback"] == "ru"
    for scrap in ("", "   ", "42", "!!!", "🚀🔥"):
        assert lang.detect(scrap) == "ru", f"{scrap!r} перестал быть русским"


def test_latin_scraps_stay_english_so_search_does_not_break():
    """Единственное исключение из русского умолчания.

    Ответ определителя уходит не только в перевод, но и в локаль поиска
    (hl, lr, Accept-Language). Запрос "RTX 5060 review" с русской локалью
    вернёт мусор, поэтому голая латиница остаётся английской.
    """
    assert _languages_config()["fallback_latin"] == "en"
    for scrap in ("RTX 5060 review", "hi", "ok", "asdfgh", "nginx 1.25 changelog"):
        code, why = lang.detect_with_reason(scrap)
        assert code == "en", f"{scrap!r} дал {code!r}"
        assert why == "по умолчанию для латиницы"


# ═════════════════════════════════════════════════════════════════════
# УНИВЕРСАЛЬНОСТЬ И ГРАНИЦЫ
# ═════════════════════════════════════════════════════════════════════

def test_the_words_live_in_the_config_not_in_the_code():
    # Новый язык должен добавляться правкой конфига и нулём правок в core/.
    latin = _languages_config()["latin"]
    assert len(latin) >= 8, "латинских языков стало меньше восьми"

    needles = ["gunaydin", "dziekuje", "obrigado", "aujourd"]
    offenders = []
    for folder in ("core", "agent"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            body = path.read_text(encoding="utf-8", errors="ignore").lower()
            for needle in needles:
                if needle in body:
                    offenders.append(f"{path.relative_to(ROOT)}:{needle}")
    assert not offenders, f"слова языков зашиты в код: {offenders}"


def test_every_config_code_has_a_human_name():
    # Если в конфиг добавят язык, которого нет в таблице локалей, перевод
    # получит чужое имя и поиск уйдёт с чужой локалью. Тихо и обидно.
    for code in _languages_config()["latin"]:
        assert code in search_locale._LOCALE_MAP, (
            f"язык {code!r} есть в languages.yaml, но его нет в таблице локалей"
        )
    assert "ja" in search_locale._LOCALE_MAP, "японский теперь возвращается — имя обязательно"


def test_the_detector_has_no_door_to_the_network_or_a_model():
    source = inspect.getsource(lang)
    for forbidden in ("cheap_call", "aux_call", "genai", "generativeai",
                      "requests", "urllib", "socket", "numpy"):
        assert forbidden not in source, f"в определителе появилась дверь наружу: {forbidden}"


def test_a_missing_config_degrades_instead_of_falling():
    saved = loader.CONFIG_DIR
    loader.CONFIG_DIR = Path("/такой-папки-нет-и-не-будет")
    lang.reset_cache()
    try:
        # Письменности — факты юникода, они работают без конфига.
        assert lang.detect("привет как дела") == "ru"
        assert lang.detect("今天天气很好") == "zh"
        # Слова без конфига неизвестны — честное «не знаю», а не падение.
        assert lang.detect("bugun hava cok guzel") == "en"
        assert lang.detect("") == "ru"
    finally:
        loader.CONFIG_DIR = saved
        lang.reset_cache()
    # После возврата конфига всё как раньше.
    assert lang.detect("bugun hava cok guzel") == "tr"


def test_only_the_head_of_a_long_text_is_read():
    limit = int(_languages_config()["thresholds"]["sample_chars"])
    head = "привет как дела " * 40           # заведомо длиннее предела
    assert len(head) > limit
    tail = "x" * 100000
    t0 = time.perf_counter()
    assert lang.detect(head + tail) == "ru", "хвост перебил голову"
    assert time.perf_counter() - t0 < 0.05, "длинный текст читается целиком"


def test_the_detector_is_fast_enough_to_be_invisible():
    # Грубый сторож против случайного вызова сети или тяжёлой библиотеки
    # внутри: один сетевой вызов стоит больше, чем все эти две тысячи.
    phrases = ["bugun hava cok guzel", "привет как дела", "hello world", "今天天气很好"]
    t0 = time.perf_counter()
    for i in range(2000):
        lang.detect(phrases[i % len(phrases)])
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"2000 вызовов заняли {elapsed:.2f} с — внутри что-то тяжёлое"


def test_the_answer_never_changes_between_calls():
    # Кеш конфига не должен делать ответ плавающим. Именно плавающий
    # ответ модели съел ночь 7 августа 2026 года.
    for text in ("bugun hava cok guzel", "привет", "hola como estas amigo"):
        answers = {lang.detect(text) for _ in range(50)}
        assert len(answers) == 1, f"{text!r} отвечает по-разному: {answers}"


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
