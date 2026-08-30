"""У вопроса «где эта вещь на экране» теперь одна дверь.

До сегодня ветки screen_find и screen_click звали догадку модели напрямую.
Теперь они спрашивают у лестницы источников. Сегодня в лестнице ровно
одна ступень — та же самая модель, и поведение не меняется ни на букву.

Что проверяем:
  1. ветки действий больше не зовут поиск напрямую;
  2. первый ответивший источник побеждает, остальных не беспокоят;
  3. промолчавший источник уступает очередь следующему;
  4. сорвавшийся источник не роняет поиск и называет причину вслух;
  5. молчание всех источников — это «не нашёл», а не клик наугад;
  6. слова ответов не изменились ни на знак;
  7. нажатие приходится ровно туда, куда указал источник, и ровно один раз;
  8. пауза 0,3 с перед нажатием сохранилась;
  9. поиск без нажатия мышь не трогает;
 10. вместе с координатами едет имя источника;
 11. сегодня ступень ровно одна — старая модель;
 12. старый путь цел: его пара чисел становится целью, а None — молчанием;
 13. пустое описание по-прежнему отклоняется до всякого поиска.

Запуск: python -m pytest -q  или  python tests/test_locator_ladder.py
"""

import io
import sys
import time as _time_mod
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import actions.computer_control as cc

SOURCE = (ROOT / "actions" / "computer_control.py").read_text(encoding="utf-8")


class _Player:
    """Подставной интерфейс: экранный тумблер включён, журнал в корзину."""

    screen_control = True

    def write_log(self, _text):
        pass


def _source(name, answer):
    """Поддельный источник. answer — цель, None или исключение."""

    def finder(description):
        finder.asked.append(description)
        if isinstance(answer, Exception):
            raise answer
        return answer

    finder.asked = []
    finder.source_name = name
    return finder


def _run(action, description, ladder):
    """Зовём боевой диспетчер с подменённой лестницей и мышью.

    Ни сети, ни экрана, ни настоящего нажатия: проверка обязана
    работать на любой машине, включая ту, где Windows нет вовсе.
    """
    clicks = []
    naps = []

    saved_ladder = cc._LOCATORS
    saved_click = cc._click
    saved_sleep = _time_mod.sleep

    cc._LOCATORS = ladder
    cc._click = lambda **kw: clicks.append(kw) or "ok"
    _time_mod.sleep = lambda seconds: naps.append(seconds)

    out = io.StringIO()
    try:
        with redirect_stdout(out):
            answer = cc.computer_control(
                parameters={"action": action, "description": description},
                player=_Player(),
            )
    finally:
        cc._LOCATORS = saved_ladder
        cc._click = saved_click
        _time_mod.sleep = saved_sleep

    return answer, clicks, naps, out.getvalue()


def _branches():
    """Кусок исходника с ветками поиска и нажатия."""
    start = SOURCE.index('elif action == "screen_find":')
    end = SOURCE.index('elif action == "wait_image":')
    return SOURCE[start:end]


# 1 ─────────────────────────────────────────────────────────────────────────
def test_the_branches_no_longer_call_the_search_directly():
    piece = _branches()
    assert "_analyze_screen_for_element" not in piece, (
        "ветка действия снова зовёт модель напрямую, в обход лестницы"
    )
    assert piece.count("_locate(description)") == 2, (
        "обе ветки обязаны спрашивать у лестницы"
    )


# 2 ─────────────────────────────────────────────────────────────────────────
def test_the_first_answer_wins():
    first = _source("windows", cc.Target(10, 20, "windows", 1.0, "ОК"))
    second = _source("model", cc.Target(99, 99, "model", 0.0, "ОК"))

    saved = cc._LOCATORS
    cc._LOCATORS = [first, second]
    try:
        target = cc._locate("кнопка ОК")
    finally:
        cc._LOCATORS = saved

    assert target == (10, 20, "windows", 1.0, "ОК")
    assert first.asked == ["кнопка ОК"]
    assert second.asked == [], "второй источник тревожить было незачем"


# 3 ─────────────────────────────────────────────────────────────────────────
def test_a_silent_source_yields_the_turn():
    first = _source("windows", None)
    second = _source("model", cc.Target(7, 8, "model", 0.0, "ОК"))

    saved = cc._LOCATORS
    cc._LOCATORS = [first, second]
    try:
        target = cc._locate("кнопка ОК")
    finally:
        cc._LOCATORS = saved

    assert target is not None and (target.x, target.y) == (7, 8)
    assert first.asked and second.asked, "очередь не дошла до второго источника"


# 4 ─────────────────────────────────────────────────────────────────────────
def test_a_broken_source_never_kills_the_search():
    first = _source("windows", RuntimeError("дерево окон упало"))
    second = _source("model", cc.Target(3, 4, "model", 0.0, "ОК"))

    saved = cc._LOCATORS
    cc._LOCATORS = [first, second]
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            target = cc._locate("кнопка ОК")
    finally:
        cc._LOCATORS = saved

    assert target is not None and (target.x, target.y) == (3, 4)
    printed = out.getvalue()
    assert "windows" in printed and "дерево окон упало" in printed, (
        "сорвавшийся источник обязан назвать причину вслух: " + printed
    )


# 5 ─────────────────────────────────────────────────────────────────────────
def test_nobody_found_is_not_a_click():
    answer, clicks, _naps, _printed = _run(
        "screen_click", "кнопка ОК", [_source("model", None)]
    )
    assert clicks == [], "при ненайденном элементе мышь двигаться не имеет права"
    assert answer == "Could not find 'кнопка ОК' on screen."


# 6 ─────────────────────────────────────────────────────────────────────────
def test_the_words_of_the_answer_did_not_change():
    found = cc.Target(450, 320, "model", 0.0, "ОК")

    answer, _c, _n, _p = _run("screen_find", "кнопка ОК", [_source("model", found)])
    assert answer == "Found 'кнопка ОК' at (450, 320)."

    answer, _c, _n, _p = _run("screen_find", "кнопка ОК", [_source("model", None)])
    assert answer == "Element 'кнопка ОК' not found on screen."

    answer, _c, _n, _p = _run("screen_click", "кнопка ОК", [_source("model", found)])
    assert answer == "Clicked 'кнопка ОК' at (450, 320)."


# 7 ─────────────────────────────────────────────────────────────────────────
def test_the_click_lands_exactly_where_the_source_pointed():
    found = cc.Target(133, 244, "windows", 1.0, "Файл")
    _a, clicks, _n, _p = _run("screen_click", "меню Файл", [_source("windows", found)])
    assert clicks == [{"x": 133, "y": 244}], f"нажали не туда или не раз: {clicks!r}"


# 8 ─────────────────────────────────────────────────────────────────────────
def test_the_pause_before_the_click_survived():
    found = cc.Target(1, 2, "model", 0.0, "ОК")
    _a, _c, naps, _p = _run("screen_click", "кнопка ОК", [_source("model", found)])
    assert 0.3 in naps, f"пауза на успокоение экрана пропала: {naps!r}"


# 9 ─────────────────────────────────────────────────────────────────────────
def test_the_search_never_touches_the_mouse():
    found = cc.Target(5, 6, "model", 0.0, "ОК")
    _a, clicks, _n, _p = _run("screen_find", "кнопка ОК", [_source("model", found)])
    assert clicks == [], "поиск обязан только смотреть"


# 10 ────────────────────────────────────────────────────────────────────────
def test_the_source_travels_with_the_answer():
    saved = cc._LOCATORS
    cc._LOCATORS = [_source("windows", cc.Target(1, 1, "windows", 0.9, "Пуск"))]
    try:
        target = cc._locate("Пуск")
    finally:
        cc._LOCATORS = saved

    assert target.source == "windows", "ответ обязан помнить, кто его дал"
    assert target.confidence == 0.9 and target.label == "Пуск"


# 11 ────────────────────────────────────────────────────────────────────────
def test_today_the_ladder_has_exactly_one_step():
    assert len(cc._LOCATORS) == 1, (
        "ступеней стало больше одной — это должно быть осознанным шагом с тестами"
    )
    assert cc._LOCATORS[0] is cc._locate_by_model
    assert cc._locate_by_model.source_name == "model"


# 12 ────────────────────────────────────────────────────────────────────────
def test_the_old_path_is_untouched():
    saved = cc._analyze_screen_for_element
    asked = []
    try:
        cc._analyze_screen_for_element = lambda d: asked.append(d) or (77, 88)
        target = cc._locate_by_model("кнопка ОК")
        assert (target.x, target.y) == (77, 88)
        assert target.source == "model" and target.label == "кнопка ОК"
        assert asked == ["кнопка ОК"]

        cc._analyze_screen_for_element = lambda d: None
        assert cc._locate_by_model("кнопка ОК") is None
    finally:
        cc._analyze_screen_for_element = saved


# 13 ────────────────────────────────────────────────────────────────────────
def test_an_empty_description_is_refused_before_any_search():
    never = _source("model", cc.Target(1, 1, "model", 0.0, ""))

    saved_ladder = cc._LOCATORS
    cc._LOCATORS = [never]
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            answer = cc.computer_control(
                parameters={"action": "screen_click", "description": ""},
                player=_Player(),
            )
    finally:
        cc._LOCATORS = saved_ladder

    assert answer == "No element description provided."
    assert never.asked == [], "без описания искать нечего"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713 {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  \u2717 {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} зелёных")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
