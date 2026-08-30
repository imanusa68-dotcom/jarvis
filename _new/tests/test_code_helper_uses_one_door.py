"""У разборщика кода больше нет своей двери к модели.

Что проверяем:
  1. в actions/code_helper.py не осталось старого SDK;
  2. общая дверь открывается лениво — только внутри функции, не при старте;
  3. 'explain' идёт ровно один раз в aux_call и берёт роль aux_heavy (умная модель);
  4. удачное объяснение доходит до владельца целиком;
  5. отказ вида [quota-cooldown:65s] никогда не утекает во фразу владельцу;
  6. пустой ответ модели тоже превращается в человеческую фразу, а не в пустоту;
  7. отсутствие ключа не роняет инструмент исключением;
  8. опасные действия (run/write/edit/auto) по-прежнему закрыты и к модели не ходят;
  9. число старых дверей по проекту не растёт (сегодня их не больше шести).

Запуск: python -m pytest -q  или  python tests/test_code_helper_uses_one_door.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.aux_model as aux_model
import actions.code_helper as code_helper_module
from actions.code_helper import code_helper

# Имя старого SDK собирается из кусков нарочно: иначе сам этот
# файл попадёт в свой же греп и проверка станет ложью.
OLD_SDK = "google." + "generativeai"
SKIP_DIRS = {"tests", "__pycache__", ".pytest_cache", "logs", "docs"}

SOURCE = (ROOT / "actions" / "code_helper.py").read_text(encoding="utf-8")


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
        self.calls.append({"prompt": prompt, "model": model, "caller": caller, "key": api_key})
        return self.reply


def _explain_with(reply, parameters=None, key_getter=None):
    """Зовём code_helper с подменённой дверью и подменённым ключом.

    Ключ подменяется нарочно: проверка не должна зависеть от того,
    введён ли на этой машине настоящий ключ.
    """
    door = _Door(reply)
    saved_door = aux_model.aux_call
    saved_key = code_helper_module._get_api_key
    aux_model.aux_call = door
    code_helper_module._get_api_key = key_getter or (lambda: "ключ-для-теста")
    try:
        text = code_helper(parameters or {"action": "explain", "code": "print(1 + 1)"})
    finally:
        aux_model.aux_call = saved_door
        code_helper_module._get_api_key = saved_key
    return text, door


def test_the_explainer_has_no_door_of_its_own():
    assert OLD_SDK not in SOURCE, "разборщик кода снова лезет в старый SDK своей дверью"
    assert "aux_call" in SOURCE, "разборщик кода не зовёт общую дверь"
    assert "_get_gemini" not in SOURCE, "старая дверь _get_gemini всё ещё в файле"


def test_the_door_opens_lazily():
    """Импорт двери должен быть только внутри функции — иначе SDK грузится при старте."""
    imports = [
        line for line in SOURCE.splitlines()
        if "core.aux_model" in line and "import" in line and not line.strip().startswith("#")
    ]
    assert imports, "импорт общей двери пропал совсем"
    for line in imports:
        assert line.startswith(" ") or line.startswith("\t"), (
            "импорт двери вылез на уровень модуля — SDK начнёт грузиться при старте: "
            + line.strip()
        )


def test_the_explainer_walks_through_the_shared_door():
    from config.loader import get_model

    text, door = _explain_with((True, "Этот код складывает единицу с единицей, сэр."))
    assert len(door.calls) == 1, f"заходов в дверь {len(door.calls)}, а должен быть один"
    call = door.calls[0]
    assert call["model"] == get_model("aux_heavy"), (
        f"модель подменилась: {call['model']} вместо роли aux_heavy — разбор кода оглупел"
    )
    assert "CodeHelper" in call["caller"], f"дверь не знает, кто стучит: {call['caller']}"
    assert "print(1 + 1)" in call["prompt"], "сам код до модели не доехал"
    assert text.strip(), "разборщик вернул пустоту"


def test_a_good_explanation_reaches_the_owner():
    reply = "Этот код складывает единицу с единицей и печатает двойку, сэр."
    text, _ = _explain_with((True, "  " + reply + "  "))
    assert text == reply, f"объяснение дошло искажённым: {text!r}"


def test_a_refusal_never_leaks_into_the_owners_words():
    for refusal in ("[quota-cooldown:65s]", "[quota-429:cooldown 30s]", "[error:503 UNAVAILABLE]"):
        text, _ = _explain_with((False, refusal))
        assert text.strip(), f"при отказе {refusal} разборщик промолчал"
        assert "[quota" not in text, f"служебный текст утёк владельцу: {text!r}"
        assert "[error:" not in text, f"служебный текст утёк владельцу: {text!r}"


def test_an_empty_explanation_falls_back_to_words():
    text, _ = _explain_with((True, "   "))
    assert text.strip(), "пустой ответ модели превратился в пустой ответ владельцу"
    assert "[" not in text, f"служебный текст утёк владельцу: {text!r}"


def test_a_missing_key_never_explodes():
    """Без ключа инструмент обязан сказать фразу, а не упасть исключением."""

    def _no_key():
        raise RuntimeError("gemini_api_key не найден")

    text, door = _explain_with((True, "сюда дойти не должно"), key_getter=_no_key)
    assert isinstance(text, str) and text.strip(), "без ключа разборщик промолчал"
    assert "gemini_api_key" not in text, f"служебный текст утёк владельцу: {text!r}"
    assert not door.calls, "без ключа всё равно пошли к модели"


def test_dangerous_actions_are_still_blocked():
    """Переезд двери не должен был приоткрыть запрещённые действия."""
    for action in ("run", "write", "edit", "build", "auto", "screen_debug"):
        text, door = _explain_with(
            (True, "модель не должна была сюда попасть"),
            {"action": action, "code": "print(1 + 1)", "description": "x"},
        )
        assert "SECURITY" in text, f"действие '{action}' больше не закрыто: {text!r}"
        assert not door.calls, f"действие '{action}' сходило к модели, а не должно было"


def test_the_number_of_old_doors_never_grows():
    points = _live_old_sdk_points()
    files = {name for name, _ in points}
    assert len(points) <= 6, (
        "старых дверей стало больше, а не меньше: "
        + ", ".join(f"{n}:{ln}" for n, ln in points)
    )
    assert "actions/code_helper.py" not in files, "разборщик кода вернулся в старый SDK"


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
