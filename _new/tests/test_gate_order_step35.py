# tests/test_gate_order_step35.py
"""Шаг 35.2 — окно «пачки удалений» не течёт между тестами.

Что вскрылось 15.08. Два сторожа калитки —
test_a_broken_consent_store_does_not_open_the_gate и
test_with_the_flag_off_nothing_changes — покраснели, стоило запустить
тесты нестандартным набором из семнадцати файлов. Причина не в калитке:
решение владельца «первое удаление спрашивает, дальше три минуты подряд
не переспрашиваем» живёт в глобальной переменной модуля
(core/security._delete_burst, окно 180 секунд). Тесты, удалявшие до них,
оставляли окно открытым.

Значение: главный сторож проекта — «модель не может одобрить сама себя» —
был зелёным не потому, что замок держит, а потому что до него случайно
никто не удалял. Этот файл делает утечку видимой сразу.

Порядок тестов внутри файла значим: первый нарочно оставляет ловушку,
второй обязан её не заметить. Имена начинаются с a/b, чтобы порядок
не зависел от способа запуска.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.security as sec  # noqa: E402

DELETE = {"path": r"C:\Users\rdrr\Downloads\report.txt", "action": "delete"}


def _needs(mode="interactive"):
    need, _why = sec.needs_confirmation("file_controller", dict(DELETE), mode=mode)
    return need


def test_a_the_window_works_and_is_left_open_on_purpose():
    # Ловушка для следующего теста: окно остаётся открытым.
    sec.reset_delete_burst()
    assert _needs() is True, "первое удаление обязано спрашивать"
    sec.open_delete_burst()
    assert _needs() is False, "внутри окна повтор не переспрашивает (решение владельца)"


def test_b_the_next_test_starts_with_a_closed_window():
    assert _needs() is True, (
        "окно удалений протекло из соседнего теста: сторожа калитки снова "
        "проверяют не то, что думают")


def test_c_an_autonomous_delete_never_rides_the_window():
    # Фоновая задача не имеет права проехать по окну, открытому владельцем.
    sec.open_delete_burst()
    try:
        assert _needs(mode="autonomous") is True
    finally:
        sec.reset_delete_burst()
