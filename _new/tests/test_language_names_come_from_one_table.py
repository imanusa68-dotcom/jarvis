"""
Свод таблиц языков в одну (фаза 0.5, 7 августа 2026).

Проверяется ровно то, что можно проверить без сети и без модели:
  1. имя языка для промпта берётся только из общей таблицы core/search_locale.py;
  2. старое поведение таблицы заморожено: без запасного языка незнакомый
     код по-прежнему даёт English — на этом стоит локаль поиска;
  3. с запасным языком незнакомый код даёт русский — решение владельца
     от 7 августа «умолчание — русский»;
  4. композер понимает и код "ru", и человеческое имя "Russian" — это
     обезвреженная мина: раньше имя молча уводило ответ в английский;
  5. ни в одном из четырёх файлов не осталось собственного списка языков;
  6. называние языка не ходит в сеть.

Запуск: python -m pytest -q  или  python tests/test_language_names_come_from_one_table.py
"""
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.response_composer as rc  # noqa: E402
import core.search_locale as sl  # noqa: E402


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_the_shared_table_answers_exactly_as_before():
    assert len(sl._LOCALE_MAP) == 18
    assert sl.get_label("ru") == "Russian"
    assert sl.get_label("tr") == "Turkish"
    assert sl.get_label("qq") == "English", "старое поведение поиска трогать нельзя"


def test_asking_for_a_fallback_gives_russian_not_english():
    assert sl.get_label("qq", fallback="ru") == "Russian"
    assert sl.get_label(None, fallback="ru") == "Russian"
    assert sl.get_label("", fallback="ru") == "Russian"
    assert sl.get_label("tr", fallback="ru") == "Turkish", "известный язык запасной не подменяет"


def test_a_human_name_is_understood_as_well_as_a_code():
    assert sl.normalize_lang("Russian") == "ru"
    assert sl.normalize_lang("TURKISH") == "tr"
    assert sl.normalize_lang(" German ") == "de"
    assert sl.normalize_lang("ru-RU") == "ru"
    assert sl.normalize_lang("RU") == "ru"
    assert sl.normalize_lang(None) == "ru"
    assert sl.normalize_lang("qq") == "ru"
    assert sl.normalize_lang("qq", fallback="en") == "en"


def test_every_name_leads_back_to_its_own_code():
    for code, row in sl._LOCALE_MAP.items():
        assert sl.normalize_lang(row[3]) == code, f"имя {row[3]!r} должно вести обратно в {code!r}"


def test_the_composer_no_longer_turns_a_name_into_english():
    # Мина: _detect_language отдаёт "Russian", а проверки внутри ждут "ru".
    assert rc._language_code("Russian") == "ru"
    assert rc._language_code("ru") == "ru"
    assert rc._language_code(None) == "ru"
    assert rc._language_code("qq") == "ru"

    russian = rc.compose_error(goal="найти новости", error="timeout",
                               tool_used="web_search", language="Russian")
    assert "удалось" in russian or "получилось" in russian, f"ответил не по-русски: {russian!r}"

    english = rc.compose_error(goal="find news", error="timeout",
                               tool_used="web_search", language="English")
    assert "able" in english or "Couldn" in english, f"ответил не по-английски: {english!r}"


def test_the_composer_takes_names_from_the_shared_table():
    assert rc._language_name("tr") == "Turkish"
    assert rc._language_name("Turkish") == "Turkish"
    assert rc._language_name("qq") == "Russian", "неизвестный язык — русский, не английский"
    for code, row in sl._LOCALE_MAP.items():
        assert rc._language_name(code) == row[3]


def test_no_file_keeps_its_own_list_of_language_names():
    for rel in ("core/response_composer.py", "actions/web_search.py",
                "actions/deep_research.py"):
        src = _text(rel)
        assert '"de": "German"' not in src, f"{rel}: вернулся собственный список языков"
        assert "'Russian' if" not in src, f"{rel}: снова выбор из двух языков"
        assert "with code '" not in src, f"{rel}: модели опять показывают код вместо имени"
        assert "search_locale" in src, f"{rel}: перестал брать имена из общей таблицы"


def test_naming_a_language_never_goes_to_the_network():
    mines = ("socket", "create_connection", "getaddrinfo")
    saved = {name: getattr(socket, name) for name in mines}

    def boom(*a, **k):
        raise AssertionError("называние языка полезло в сеть")

    try:
        for name in mines:
            setattr(socket, name, boom)
        assert sl.get_label("tr", fallback="ru") == "Turkish"
        assert rc._language_name("Turkish") == "Turkish"
        assert rc._language_code("Russian") == "ru"
        assert rc.compose_error(goal="x", error="y", tool_used="t", language="Russian")
    finally:
        for name, value in saved.items():
            setattr(socket, name, value)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  \u2713 {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  \u2717 {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} зелёных")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
