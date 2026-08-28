"""Сторожа того, что выбор языка назван вслух (шаг 3-кватер фазы 0.5).

Зачем этот файл появился
───────────────────────
7 августа 2026 владелец запустил живой поиск и не смог по логу понять,
кто выбрал язык. Видно было только lang=ru. Решить могли двое —
голосовая модель или наш core/lang.py, и чинить надо разное.

Что здесь проверяется:
  • модель назвала язык — говорим об этом прямо;
  • модель промолчала — называем причину нашего решения;
  • определитель не загрузился — английский берётся ГРОМКО, а не молча;
  • обе двери к поиску зовут ОДНУ общую функцию, а не свои копии;
  • общая функция не ходит ни в сеть, ни к модели.

Запуск: python -m pytest -q  или  python tests/test_language_source_is_audible.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.search_locale as sl  # noqa: E402


def test_the_model_named_the_language_and_we_say_so():
    code, who = sl.resolve_language("найди новости", "ru")
    assert code == "ru"
    assert "модель" in who, f"автор решения не назван: {who!r}"
    # Региональный хвост и регистр от модели срезаются как и раньше.
    assert sl.resolve_language("x", "RU-ru")[0] == "ru"
    assert sl.resolve_language("x", "EN")[0] == "en"


def test_our_code_decided_and_the_reason_is_named():
    code, who = sl.resolve_language("привет сэр", None)
    assert code == "ru"
    assert who == "решил наш код (буквы)", f"причина потеряна: {who!r}"
    assert sl.resolve_language("bugun hava cok guzel ve ben eve gidiyorum")[1] == "решил наш код (слова)"
    assert sl.resolve_language("RTX 5060 review")[1] == "решил наш код (по умолчанию для латиницы)"


def test_the_pair_never_disagrees_with_the_plain_detector():
    for text in ("привет", "今天天气很好", "こんにちは世界", "hello world how are you today",
                 "das wetter ist heute sehr warm und gut", "", "42", "ok"):
        assert sl.resolve_language(text)[0] == sl.detect_language(text), f"два ответа разошлись на {text!r}"


def test_the_emergency_branch_is_no_longer_silent():
    # Имитируем самый опасный случай: определитель не загрузился вовсе.
    saved = sys.modules.get("core.lang")
    sys.modules["core.lang"] = None
    try:
        code, who = sl.resolve_language("привет сэр")
    finally:
        if saved is None:
            sys.modules.pop("core.lang", None)
        else:
            sys.modules["core.lang"] = saved
    assert code == "en", "в аварии язык поиска обязан остаться английским"
    assert "НЕ ЗАГРУЗИЛСЯ" in who, f"авария обязана быть слышной: {who!r}"
    # И после аварии всё работает как раньше.
    assert sl.resolve_language("привет сэр")[0] == "ru"


def test_both_search_doors_name_the_author_out_loud():
    for rel in ("actions/web_search.py", "actions/deep_research.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "\U0001F524 язык {language}" in src, f"{rel}: нет строки о том, кто решил язык"
        assert "lang_source" in src, f"{rel}: автор решения нигде не берётся"


def test_the_private_copy_in_deep_research_is_gone():
    src = (ROOT / "actions/deep_research.py").read_text(encoding="utf-8")
    assert "from core.search_locale import resolve_language" in src
    assert "import detect_language" not in src, "вернулась собственная копия определения языка"


def test_the_shared_door_never_touches_network_or_model():
    source = inspect.getsource(sl.resolve_language)
    for forbidden in ("genai", "requests", "http", "cheap_call", "aux_call", "api_key"):
        assert forbidden not in source, f"в общей двери появился {forbidden!r}"


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
