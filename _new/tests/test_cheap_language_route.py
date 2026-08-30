"""Сторожа дешёвого маршрута определения языка (шаг 3 фазы 0.5).

Что здесь охраняется:
  1. Кириллица решается буквами — модель не зовётся вообще (ни запроса,
     ни квоты, ни ожидания сети).
  2. Латиница тоже решается без модели: с 7 августа 2026 года этим занят
     core/lang.py — по частым словам и своеобразным буквам.
  3. Ответ всегда имя из таблицы локалей, а не чужая фраза.
  4. Полностью мёртвая модель больше не влияет на язык вовсе.
  5. Число предела живёт в конфиге, а не в коде: сама дверь Gemma с предобрезкой
     осталась в проекте для будущих задач, просто язык её больше не зовёт.
  6. У языкового маршрута нет ни одной двери к модели — ни старой, ни дешёвой.

Запуск: python -m pytest -q  или  python tests/test_cheap_language_route.py
"""
import inspect
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.executor as executor          # noqa: E402
import config.loader as loader             # noqa: E402
import core.aux_model as aux_model         # noqa: E402


def _registry() -> dict:
    text = (ROOT / "config" / "registry.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _cheap_limit() -> int:
    return int(((_registry().get("limits") or {}).get("aux_cheap") or {})["max_input_chars"])


class _Door:
    """Подмена двери к модели: запоминает каждый заход и отвечает заготовкой."""

    def __init__(self, answer=(True, "Turkish"), boom: bool = False):
        self.answer = answer
        self.boom = boom
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"prompt": prompt, "model": model, "caller": caller})
        if self.boom:
            raise RuntimeError("сеть отвалилась")
        return self.answer


def _ask(text: str, door: _Door) -> str:
    """Спросить язык, подсунув поддельную дверь и ключ."""
    saved_call = aux_model.cheap_call
    saved_key = executor._get_api_key
    aux_model.cheap_call = door
    executor._get_api_key = lambda: "test-key"
    try:
        return executor._detect_language(text)
    finally:
        aux_model.cheap_call = saved_call
        executor._get_api_key = saved_key


def test_cyrillic_is_decided_by_letters_without_the_model():
    door = _Door()
    assert _ask("найди новости про RTX 5060 и сохрани в файл", door) == "Russian"
    assert _ask("Привет, сэр", door) == "Russian"
    assert _ask("", door) == "Russian"
    assert _ask("   ", door) == "Russian"
    assert door.calls == [], f"модель звали без надобности: {door.calls}"


def test_latin_goes_to_the_cheap_role_and_the_input_is_trimmed():
    limit = _cheap_limit()
    expected_model = (_registry().get("roles") or {})["aux_cheap"]

    door = _Door()
    saved = aux_model.aux_call
    aux_model.aux_call = door
    try:
        ok, answer = aux_model.cheap_call("x" * (limit + 5000), "test-key", caller="test")
    finally:
        aux_model.aux_call = saved

    assert ok and answer == "Turkish"
    assert len(door.calls) == 1, "дешёвый вход не дошёл до двери"
    sent = door.calls[0]
    assert len(sent["prompt"]) == limit, (
        f"обрезка до отправки не сработала: ушло {len(sent['prompt'])} знаков вместо {limit}"
    )
    assert sent["model"] == expected_model, (
        f"ушло к модели {sent['model']!r}, а должно к роли aux_cheap ({expected_model!r})"
    )

    short = _Door()
    saved = aux_model.aux_call
    aux_model.aux_call = short
    try:
        aux_model.cheap_call("hangi dil bu", "test-key", caller="test")
    finally:
        aux_model.aux_call = saved
    assert short.calls[0]["prompt"] == "hangi dil bu", "короткий вход трогать нельзя"


def test_a_chatty_answer_can_no_longer_reach_the_translator():
    # Раньше язык спрашивали у модели, и она отвечала то именем языка, то
    # целой фразой. Фразу приходилось ловить сетью сторожей. Теперь ответ
    # берётся из таблицы локалей, и чужой фразе просто неоткуда взяться.
    from core.search_locale import _LOCALE_MAP

    allowed = {row[3] for row in _LOCALE_MAP.values()}
    door = _Door()
    for text in (
        "hangi dil bu kardesim",
        "welche sprache ist das",
        "que idioma es este",
        "найди новости про RTX 5060",
        "今天天气很好",
        "42",
        "",
    ):
        got = _ask(text, door)
        assert got in allowed, f"{text!r} дал не имя языка, а {got!r}"
    assert door.calls == [], f"модель звали за языком: {door.calls}"


def test_the_language_survives_a_completely_dead_model():
    # Дверь взрывается от любого касания, а язык всё равно определяется.
    # Именно этого не хватало ночью, когда чужой 503 решал, на каком языке
    # говорит владелец.
    boom = _Door(boom=True)
    assert _ask("привет, сэр", boom) == "Russian"
    assert _ask("bugun hava cok guzel ve ben eve gidiyorum", boom) == "Turkish"
    assert _ask("das wetter ist heute sehr warm und gut", boom) == "German"
    assert _ask("hello world how are you", boom) == "English"
    assert _ask("", boom) == "Russian"


def test_the_limit_lives_in_the_config_not_in_the_code():
    limit = _cheap_limit()
    assert limit > 0
    assert loader.get_limit("aux_cheap", "max_input_chars") == limit
    assert loader.get_limit("aux_cheap", "no_such_limit", 7) == 7
    assert loader.get_limit("no_such_role", "max_input_chars", 7) == 7

    needle = str(limit)
    offenders = []
    for folder in ("core", "agent"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            if needle in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"число {needle} зашито в код: {offenders}"


def test_the_language_route_has_no_second_door_to_the_model():
    source = inspect.getsource(executor._detect_language)
    for forbidden in ("generativeai", "genai", "generate_content", "GenerativeModel"):
        assert forbidden not in source, f"в определении языка осталась вторая дверь: {forbidden}"
    assert "search_locale" in source, "бесплатный определитель по буквам потерян"
    assert "core.lang" in source, "быстрый определитель языка больше не зовётся"
    assert "cheap_call" not in source, "в языковом пути снова появилась дверь к модели"


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
