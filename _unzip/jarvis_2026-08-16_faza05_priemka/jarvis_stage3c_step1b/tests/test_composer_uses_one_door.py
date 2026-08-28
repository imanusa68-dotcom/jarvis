"""У композера больше нет своей двери к модели.

Что проверяем:
  1. в core/response_composer.py не осталось старого SDK;
  2. композер идёт ровно один раз в общую дверь aux_call и берёт роль aux_light;
  3. удачный ответ модели доходит до владельца;
  4. отказ вида [quota-cooldown:65s] никогда не утекает в фразу владельцу;
  5. пустой ответ модели тоже уводит на заготовленные фразы, а не в пустоту;
  6. число старых дверей по проекту не растёт (сегодня их не больше семи).

Запуск: python -m pytest -q  или  python tests/test_composer_uses_one_door.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.aux_model as aux_model
from core.response_composer import compose

# Имя старого SDK собирается из кусков нарочно: иначе сам этот файл
# попадёт в свой же греп и проверка станет ложью.
OLD_SDK = "google." + "generativeai"
SKIP_DIRS = {"tests", "__pycache__", ".pytest_cache", "logs", "docs"}


def _live_old_sdk_points():
    """Живые строки с импортом старого SDK: (файл, номер строки)."""
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue          # комментарий — не дверь
            if OLD_SDK in stripped and "import" in stripped:
                found.append((str(rel).replace("\\", "/"), number))
    return found


class _Door:
    """Подставная общая дверь: запоминает заходы и отдаёт заготовленный ответ."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({"prompt": prompt, "model": model, "caller": caller})
        return self.reply


def _compose_with(reply):
    """Зовём compose с подменённой дверью и возвращаем (текст, дверь)."""
    door = _Door(reply)
    saved = aux_model.aux_call
    aux_model.aux_call = door
    try:
        text = compose(
            result="Найдено 8 источников, файл RTX_5060_news.txt создан.",
            goal="найди новости про RTX 5060 и сохрани в файл",
            tool_used="web_search",
            language="ru",
            api_key="слово-ключ-для-теста",
        )
    finally:
        aux_model.aux_call = saved
    return text, door


def test_the_composer_has_no_door_of_its_own():
    source = (ROOT / "core" / "response_composer.py").read_text(encoding="utf-8")
    assert OLD_SDK not in source, "композер снова лезет в старый SDK своей дверью"
    assert "aux_call" in source, "композер больше не зовёт общую дверь"


def test_the_composer_walks_through_the_shared_door():
    from config.loader import get_model

    text, door = _compose_with((True, "Готово, сэр — новости сохранил в файл."))
    assert len(door.calls) == 1, f"заходов в дверь {len(door.calls)}, а должен быть один"
    call = door.calls[0]
    assert call["model"] == get_model("aux_light"), (
        f"модель подменилась: {call['model']} вместо роли aux_light"
    )
    assert "Composer" in call["caller"], f"дверь не знает, кто стучит: {call['caller']}"
    assert text.strip(), "композер вернул пустоту"


def test_a_good_answer_reaches_the_owner():
    reply = "Готово, сэр — новости сохранил в файл."
    text, _ = _compose_with((True, reply))
    assert reply[:20] in text, f"ответ модели не дошёл до владельца: {text!r}"


def test_a_refusal_never_leaks_into_the_owners_phrase():
    for refusal in ("[quota-cooldown:65s]", "[quota-429:cooldown 30s]", "[error:503 UNAVAILABLE]"):
        text, _ = _compose_with((False, refusal))
        assert text.strip(), f"при отказе {refusal} композер промолчал"
        assert "[quota" not in text, f"служебный текст утёк владельцу: {text!r}"
        assert "[error:" not in text, f"служебный текст утёк владельцу: {text!r}"


def test_an_empty_answer_falls_back_to_the_ready_phrases():
    text, _ = _compose_with((True, "   "))
    assert text.strip(), "пустой ответ модели превратился в пустой ответ владельцу"


def test_the_number_of_old_doors_never_grows():
    points = _live_old_sdk_points()
    files = {name for name, _ in points}
    assert len(points) <= 7, (
        "старых дверей стало больше, а не меньше: "
        + ", ".join(f"{n}:{ln}" for n, ln in points)
    )
    assert "core/response_composer.py" not in files, "композер вернулся в старый SDK"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} зелёных")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
