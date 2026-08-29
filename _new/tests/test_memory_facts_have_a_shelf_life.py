# tests/test_memory_facts_have_a_shelf_life.py
"""
Срок годности фактов о событиях + обновление даты при повторе.

ЧТО ЛЕЧИЛИ
----------
Владелец сказал «у меня болит голова». Факт лёг в память и ехал в промпт
КАЖДУЮ сессию — вечно, потому что блок собирался из JSON, а на поле `updated`
не смотрел никто. Через две недели Джарвис всё ещё спрашивал «как голова?» —
не из заботы, а потому что для него это по-прежнему СЕГОДНЯ. Замер на
настоящей памяти владельца: 4 просроченных факта из 18 — 22% памяти.

ПОЧЕМУ ЗДЕСЬ ДВЕ ПРАВКИ, А НЕ ОДНА
Вторая правка (`_recursive_update` обновляет дату при повторе) не косметика,
а условие работоспособности первой. Раньше при повторе того же значения запись
не трогали вовсе, и `updated` навсегда оставалось датой ПЕРВОГО раза. Пока
дата ни на что не влияла, это было незаметно. Как только дата решает, что
показывать, тот же дефект становится потерей: однажды скрытый факт не
вернулся бы НИКОГДА, сколько бы владелец ни повторял.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ
Проверки, что факт исчез с диска. Он и НЕ ДОЛЖЕН исчезать: инвариант этого
дома — «скрыто, но не удалено». Сторож на это есть ниже, и он один из самых
важных в файле.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import memory_manager as mm  # noqa: E402


def day(n: int) -> str:
    """Дата n дней назад в том формате, в котором её пишет сама память."""
    return (datetime.now().date() - timedelta(days=n)).strftime("%Y-%m-%d")


def V(value, updated=None):
    e = {"value": value}
    if updated is not None:
        e["updated"] = updated
    return e


# Значение должно быть НЕ мусорным, иначе его скроет _without_junk, и сторож
# станет зелёным по чужой причине. Замерено: looks_like_junk("x") -> True.
ROT = "someone visited on that occasion"
LIVE = "owner works on serious matters"


# ── 1. Собственно болезнь: старое событие не едет в промпт ────────────────

def test_a_month_old_headache_does_not_reach_the_prompt():
    """Тот самый случай владельца, дословно."""
    mem = {
        "identity": {"name": V("Rustam", day(400))},
        "notes": {"headache_today": V("has a headache", day(30))},
    }
    text = mm.format_memory_for_prompt(mem)
    assert "has a headache" not in text, "просроченное событие уехало в промпт"
    assert "Rustam" in text, "вместе с событием потеряли постоянный факт"


def test_the_prompt_says_the_fact_is_hidden_and_not_deleted():
    """Без этой фразы Джарвис уверенно скажет «не знаю» вместо «посмотрю».

    Именно за такую ложь в этом файле уже переписывался счётчик «не влезло».
    """
    mem = {"notes": {"headache_today": V("has a headache", day(30))}}
    text = mm.format_memory_for_prompt(mem)
    assert "time-expired" in text
    assert "NOT deleted" in text
    assert "recall_memory" in text


def test_the_expiry_note_is_not_the_did_not_fit_note():
    """Причины разные, и поведение модели обязано отличаться.

    «Не влезло» значит «спроси, если нужно». «Срок годности вышел» значит
    «не начинай разговор сам» — ровно это и было болью.
    """
    mem = {"notes": {"headache_today": V("has a headache", day(30))}}
    text = mm.format_memory_for_prompt(mem)
    assert "did not fit" not in text, \
        "срок годности выдал себя за переполнение бюджета"


def test_the_hidden_fact_is_still_on_disk():
    """ИНВАРИАНТ ДОМА: скрыто, но НЕ удалено.

    Дословно из шапки fact_store: «Automation does not get to decide which of
    the user's data is worthless». Срок годности его продолжает, а не отменяет.
    """
    mem = {"notes": {"headache_today": V("has a headache", day(30))}}
    before = json.dumps(mem, sort_keys=True, ensure_ascii=False)
    mm.format_memory_for_prompt(mem)
    assert json.dumps(mem, sort_keys=True, ensure_ascii=False) == before, \
        "сборка промпта тронула данные владельца"
    assert mem["notes"]["headache_today"]["value"] == "has a headache"


# ── 2. Три предохранителя против ложного скрытия ──────────────────────────

def test_a_permanent_property_never_expires_however_old():
    """«Работает удалённо» не портится от времени, сколько бы лет ни прошло."""
    mem = {"identity": {"works_remotely": V("works remotely", day(3000))}}
    text = mm.format_memory_for_prompt(mem)
    assert "works remotely" in text


@pytest.mark.parametrize("category", sorted(mm._EXPIRY_NEVER_CATEGORIES))
def test_protected_categories_are_never_touched(category):
    """Кто есть человек, что он любит, чем занят и как с ним говорить."""
    mem = {category: {"headache_today": V(ROT, day(900))}}
    _, hidden = mm._expire_stale(mem)
    assert hidden == 0, f"срок годности залез в {category}"


@pytest.mark.parametrize("key", ["job_today", "todays_job", "plan_for_tomorrow",
                                 "last_name", "career_plan", "work_habit"])
def test_a_role_word_outranks_a_time_word(key):
    """«Работа на сегодня» — это работа, а не событие.

    Слово-роль говорит, ЧЕМ факт является; слово-времени — лишь когда о нём
    говорили. Роль сильнее.
    """
    assert mm._is_event_key("notes", key) is False


def test_words_are_matched_whole_not_as_substrings():
    """«Sunday» содержит «sun», «visitor» содержит «visit».

    Поиск подстрокой скрывал бы факты, не имеющие к событиям отношения.
    """
    assert mm._is_event_key("notes", "birthday_is_sunday") is False
    assert mm._is_event_key("notes", "headache_today") is True


# ── 3. Границы порога названы вслух ──────────────────────────────────────

@pytest.mark.parametrize("age,hidden", [(0, 0), (13, 0), (14, 0), (15, 1), (99, 1)])
def test_the_threshold_is_exactly_two_weeks(age, hidden):
    """14 дней ещё видно, 15 — уже нет. Число обсуждаемое, но не тихое."""
    mem = {"notes": {"visit_today": V(ROT, day(age))}}
    _, got = mm._expire_stale(mem)
    assert got == hidden, f"возраст {age} дн.: скрыто {got}, ожидалось {hidden}"


def test_a_fact_between_three_and_fourteen_days_carries_its_age():
    """Модель должна ЗНАТЬ, что деталь не свежая, а не угадывать."""
    mem = {"notes": {"headache_today": V("has a headache", day(5))}}
    visible, hidden = mm._visible_memory(mem)
    assert hidden == 0
    assert visible["notes"]["headache_today"]["value"] == "has a headache [5 дн. назад]"


def test_a_fact_from_yesterday_carries_no_age_label():
    """Вчера сказал про голову, сегодня спрашивает про таблетку — оговорка
    «1 дн. назад» тут была бы неуместной."""
    mem = {"notes": {"headache_today": V("has a headache", day(1))}}
    visible, _ = mm._visible_memory(mem)
    assert visible["notes"]["headache_today"]["value"] == "has a headache"


def test_the_age_label_is_never_applied_twice():
    """Замерено (probe64): повторный проход давал «[5 дн. назад][5 дн. назад]».

    Так и случится, если однажды кто-то позовёт срок годности дважды.
    """
    once, _ = mm._expire_stale({"notes": {"headache_today": V("has a headache", day(5))}})
    twice, _ = mm._expire_stale(once)
    assert twice["notes"]["headache_today"]["value"].count("дн. назад") == 1


# ── 4. Кривая дата НИКОГДА не прячет факт (fail-open) ────────────────────

@pytest.mark.parametrize("stamp", [
    None, "", "   ", "not-a-date", "2026-13-45", "0000-00-00",
    12345, [], {}, b"2026-08-29", "2026-08-29T12:00:00",
])
def test_an_unreadable_date_keeps_the_fact_visible(stamp):
    """Ошибиться в сторону «показать лишнее» дешевле, чем «спрятать нужное».

    Тесты этого дома хранят факты вообще без дат — это ровно тот случай.
    """
    mem = {"notes": {"visit_today": V(ROT, stamp)}}
    visible, hidden = mm._expire_stale(mem)
    assert hidden == 0
    assert "visit_today" in visible["notes"]


def test_a_date_in_the_future_does_not_look_fresh_it_looks_broken():
    """«Минус три дня» означает сбитые часы, а не свежесть."""
    mem = {"notes": {"visit_today": V(ROT, day(-5))}}
    _, hidden = mm._expire_stale(mem)
    assert hidden == 0


def test_a_fact_without_updated_survives_a_thousand_calls():
    """Отсутствие даты — норма, а не ошибка. Так пишет половина этого дома."""
    mem = {"notes": {"visit_today": {"value": ROT}}}
    for _ in range(3):
        _, hidden = mm._expire_stale(mem)
        assert hidden == 0


# ── 5. Повтор возвращает факт (вторая правка) ────────────────────────────

def test_repeating_the_same_thing_refreshes_the_date():
    """Сердце второй правки: раз владелец сказал заново — факт снова свежий."""
    target = {"headache_today": {"value": "has a headache", "updated": day(30)}}
    changed = mm._recursive_update(target, {"headache_today": "has a headache"})
    assert changed is False, "повтор того же — не новость, «Saved» печатать нельзя"
    assert target["headache_today"]["updated"] == day(0), "дата не обновилась"


def test_the_returned_fact_reaches_the_prompt_again():
    """Полный круг: спрятали -> владелец повторил -> снова видно.

    Без этого сторожа скрытие было бы односторонним, то есть тихой потерей.
    """
    mem = {"notes": {"headache_today": V("has a headache", day(30))}}
    assert "has a headache" not in mm.format_memory_for_prompt(mem)
    mm._recursive_update(mem["notes"], {"headache_today": "has a headache"})
    assert "has a headache" in mm.format_memory_for_prompt(mem)


def test_refreshing_the_date_never_loses_the_owners_own_wording():
    """`said` — дословная фраза владельца. Повтор без неё не имеет права её
    затирать: именно по ней работает поиск на его языке."""
    target = {"k": {"value": "has a headache", "said": "у меня болит голова",
                    "updated": day(30)}}
    mm._recursive_update(target, {"k": "has a headache"})
    assert target["k"]["said"] == "у меня болит голова"
    assert target["k"]["value"] == "has a headache"


def test_a_changed_value_is_still_real_news():
    """Обратный перегиб: правка не должна заглушить настоящую новость."""
    target = {"k": {"value": "old", "updated": day(30)}}
    assert mm._recursive_update(target, {"k": "new"}) is True
    assert target["k"]["value"] == "new"


def test_the_seen_list_is_optional():
    """Вызовы с двумя аргументами — их в этом доме много — работают как прежде."""
    target = {"k": {"value": "same", "updated": day(5)}}
    assert mm._recursive_update(target, {"k": "same"}) is False


# ── 6. Путь отступления существует и работает ────────────────────────────

def test_the_flag_switches_the_whole_thing_off(monkeypatch):
    """Список слов-признаков конечен. Один выключатель возвращает прежнее
    поведение целиком — без него правка была бы необратимой."""
    from core import feature_flags
    monkeypatch.setattr(feature_flags, "memory_expiry_enabled", lambda: False)
    mem = {"notes": {"headache_today": V("has a headache", day(30))}}
    visible, hidden = mm._visible_memory(mem)
    assert hidden == 0
    assert "headache_today" in visible["notes"]
    assert "time-expired" not in mm.format_memory_for_prompt(mem)


def test_the_flag_defaults_to_on():
    """Выключенный по умолчанию флаг не лечит ничего: владелец просил, чтобы
    проблема исчезла, а не чтобы появилась настройка."""
    from core import feature_flags
    assert feature_flags.EXPIRY_DEFAULT is True


def test_a_missing_settings_file_does_not_break_the_prompt(monkeypatch):
    """Промпт обязан собираться, даже если настройки не читаются вовсе."""
    from core import feature_flags

    def boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(feature_flags, "memory_expiry_enabled", boom)
    mem = {"notes": {"headache_today": V("has a headache", day(30))}}
    text = mm.format_memory_for_prompt(mem)     # не должно упасть
    assert isinstance(text, str)


# ── 7. Цена: бюджет и одна дверь ─────────────────────────────────────────

def test_the_expiry_note_pays_out_of_the_budget_not_on_top_of_it():
    """Дефект, найденный замером УЖЕ ПОСЛЕ правки (probe73/probe76).

    Заметка приписывалась к готовому блоку, то есть после делёжки бюджета, и
    блок вышел на 4343 знака против 4000. Новая возможность не имеет права
    молча расширять расход на промпт.
    """
    mem = {"relationships": {}, "notes": {}}
    for i in range(60):
        mem["relationships"][f"visit_today_{i}"] = V(f"{ROT} {i}", day(90))
    for i in range(120):
        mem["notes"][f"real_fact_{i}"] = V(f"{LIVE} number {i}", day(1))
    text = mm.format_memory_for_prompt(mem)
    assert len(text) <= mm.PROMPT_CHAR_BUDGET + 300, \
        f"блок вышел из бюджета: {len(text)} знаков"


def test_expiry_gives_the_owners_live_facts_their_places_back():
    """Второе, независимое основание правки: гниль вытесняла живое.

    Замер: 7 живых фактов доезжало против 59 после правки.
    """
    mem = {"relationships": {}, "notes": {}}
    for i in range(60):
        mem["relationships"][f"visit_today_{i}"] = V(f"{ROT} {i}", day(90))
    for i in range(60):
        mem["notes"][f"real_fact_{i}"] = V(f"{LIVE} number {i}", day(1))
    live = sum(1 for L in mm.format_memory_for_prompt(mem).splitlines()
               if LIVE in L)
    assert live >= 40, f"живых фактов владельца доехало всего {live}"


def test_the_prompt_and_the_diagnostic_use_the_same_door():
    """Диагностика, расходящаяся с реальностью, хуже отсутствующей.

    Раньше main.py повторял фильтр своими руками. Теперь оба зовут
    `_visible_memory`, и расхождение невозможно ПО ПОСТРОЕНИЮ, а не по
    договорённости. Сторож смотрит на исходник: договорённости тихо гниют.
    """
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8", errors="replace")
    assert "_visible_memory" in src, \
        "диагностика памяти больше не спрашивает единую дверь"
    assert "import _without_junk" not in src, \
        "в main.py вернулся собственный фильтр — счётчик снова будет врать"


def test_the_number_in_the_note_agrees_with_itself():
    """Этот текст читает МОДЕЛЬ. «1 facts are hidden» она может понять как
    «фактов несколько» и сошлётся на то, чего нет."""
    one = mm.format_memory_for_prompt(
        {"notes": {"visit_today": V(ROT, day(90))}})
    assert "1 older saved fact about a passing event is" in one
    assert "It is NOT deleted" in one

    many = {"notes": {f"visit_today_{i}": V(f"{ROT} {i}", day(90))
                      for i in range(3)}}
    text = mm.format_memory_for_prompt(many)
    assert "3 older saved facts about passing events are" in text
    assert "They are NOT deleted" in text


def test_a_memory_with_nothing_expired_pays_nothing():
    """Скорость: пока просроченного нет, ни настройка, ни заметка не стоят
    ничего. Замер (probe62): чтение флага дороже всей сборки блока."""
    mem = {"identity": {"name": V("Rustam", day(1))}}
    visible, hidden = mm._visible_memory(mem)
    assert hidden == 0
    assert "time-expired" not in mm.format_memory_for_prompt(mem)
