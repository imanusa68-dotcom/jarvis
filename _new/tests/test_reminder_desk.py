# tests/test_reminder_desk.py
"""
Сторожа напоминаний (фаза 1, блок 10, шаг 20).

Правило этих тестов: каждый закрепляет ЗАМЕРЕННЫЙ дефект, а не вообразимый.
Числа в шапках получены прогонами 22.08.2026 на песочнице, до переезда:

    владелец ставит + проверялка вычёркивает -> НОВОЕ ПОТЕРЯНО 30 РАЗ ИЗ 40
    опоздание 31 минута и больше             -> МОЛЧА НЕ СКАЖЕТ НИКОГДА
    два напоминания в одно окно              -> сказал первое, УДАЛИЛ ОБА
    39 просроченных                          -> перечитываются ВЕЧНО
    битый файл                               -> молча пусто, потом ЗАТИРАЕТСЯ
    отмена по части слова                    -> убила два, сказала про одно
"""
from __future__ import annotations

import io
import json
import re
import threading
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import pytest

from core import scheduler as S
from core import writer

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def clean():
    """Свой дом даёт conftest; здесь только сброс защёлок на процесс."""
    from actions import reminder as R
    S.reset_for_tests()
    R.reset_for_tests()
    return R


# -- Главное: правка не теряется -------------------------------------------

def test_setting_a_reminder_while_the_tick_fires_loses_nothing(clean):
    """ГЛАВНЫЙ СТОРОЖ БЛОКА.

    Замер до переезда: владелец ставит напоминание, пока проверялка вычёркивает
    сработавшее, -> НОВОЕ ПОТЕРЯНО В 30 СЛУЧАЯХ ИЗ 40 (75%). Это хуже, чем было
    у памяти в блоке 9 (50%), и по той же причине: писателей двое, и один из них
    дёргается каждые 30 секунд, то есть окно наложения открыто постоянно.

    Здесь тот же сценарий на настоящих потоках: у каждого круга есть готовое к
    выдаче напоминание (его берёт тик) и новое от владельца.
    """
    rounds = 30
    lost = []
    errors = []

    for i in range(rounds):
        S.arm("сработавшее-%d" % i, _now() - timedelta(seconds=40))
        gate = threading.Barrier(2, timeout=60)
        mine = "новое-%d" % i

        def owner():
            try:
                gate.wait()
                S.arm(mine, _now() + timedelta(days=1))
            except Exception as exc:                          # noqa: BLE001
                errors.append("владелец: %r" % exc)

        def ticker():
            try:
                gate.wait()
                S.tick()
            except Exception as exc:                          # noqa: BLE001
                errors.append("тик: %r" % exc)

        threads = [threading.Thread(target=owner),
                   threading.Thread(target=ticker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not [t for t in threads if t.is_alive()], "постановка встала"

        if not any(r["text"] == mine for r in S.alive(limit=500)):
            lost.append(mine)

    assert not errors, errors[:3]
    assert not lost, (
        "потеряно %d из %d новых напоминаний: %s"
        % (len(lost), rounds, lost[:5]))


def test_two_reminders_in_one_window_are_both_accounted_for(clean):
    """Замер до переезда: два напоминания в одно окно -> сказал первое,
    УДАЛИЛ ОБА. Второе исчезало молча.

    Теперь оба обязаны быть отмечены, и владелец обязан узнать, что их два.
    """
    S.arm("позвонить в банк", _now() - timedelta(minutes=3))
    S.arm("выпить лекарство", _now() - timedelta(minutes=2))

    items = S.tick()
    assert len(items) == 2, "тик отдал %d из 2" % len(items)

    said = clean._phrase(items)
    assert "2" in said, "во фразе не сказано, что напоминаний два: %r" % said

    for row in (S.get(i["row"]["rem_id"]) for i in items):
        assert int(row["main_done"]) == 1, "напоминание осталось неотмеченным"


def test_the_salvo_is_one_phrase_not_eight(clean):
    """ПРАВИЛО ЗАЛПА (Д43) ДОСЛОВНО: «всё просроченное сжимается в одну
    реплику... Никогда не восемь реплик подряд».

    И это же — единственная форма, которая влезает в замороженную подпись
    check_and_fire() -> одна строка.
    """
    for i in range(8):
        S.arm("дело-%d" % i, _now() - timedelta(hours=i + 1))

    said = clean.check_and_fire()
    assert isinstance(said, str) and said, "залп не произнесён вовсе"
    assert said.count("[НАПОМИНАНИЕ]") == 1, (
        "во фразе несколько приказов модели — это восемь реплик в одной строке")
    assert "8" in said, "число накопившихся не названо: %r" % said

    # И ни одно не осталось висеть.
    left = [r for r in S.alive(limit=100) if not int(r["main_done"])]
    assert not left, "после залпа осталось необработанным: %d" % len(left)


# -- Просроченное не теряется ----------------------------------------------

def test_a_reminder_missed_while_off_still_speaks_and_names_its_hour(clean):
    """ЖИВОЙ СЦЕНАРИЙ ВЛАДЕЛЬЦА, слово в слово его вопрос 22.08.2026:
    «сказал напомнить завтра в 12:00, включил в 19:00 — напомнит?»

    Замер до переезда: опоздание 31 минута и больше -> МОЛЧА НЕ СКАЖЕТ НИКОГДА,
    но и из файла не уйдёт.

    И второе, не менее важное: опоздавшее ОБЯЗАНО называть свой срок. Если в
    19:00 просто произнести «работать с проектом», владелец услышит «пора
    сейчас» — то есть Джарвис его дезинформирует.
    """
    S.arm("работать с проектом", _now() - timedelta(hours=7))
    said = clean.check_and_fire()
    assert said, "напоминание с опозданием 7 часов потеряно молча"
    assert "опоздал" in said, (
        "срок не назван — владелец поймёт «пора сейчас»: %r" % said)
    assert re.search(r"\d{1,2}:\d{2}", said), (
        "не сказано, на какой час оно было: %r" % said)


@pytest.mark.parametrize("hours,speaks", [
    (2, True),                       # включил через 2 часа
    (24 * 3, True),                  # через три дня — ещё живое
    (24 * (S.STALE_DAYS + 1), False),  # старше выбранного владельцем срока
])
def test_the_staleness_edge_is_the_one_the_owner_chose(clean, hours, speaks):
    """Порог выбран ВЛАДЕЛЬЦЕМ 22.08.2026: 7 суток. В плане этого числа нет —
    решение принято здесь и записано вслух.

    Число берётся из S.STALE_DAYS, но проверяются ОБЕ стороны границы: тест,
    который смотрит только на «скажет», остался бы зелёным и если бы порог стал
    вечностью.
    """
    r = S.arm("дело", _now() - timedelta(hours=hours))
    said = clean.check_and_fire()
    if speaks:
        assert said, "опоздание %d ч должно звучать" % hours
    else:
        assert not said, "опоздание %d ч не должно звучать: %r" % (hours, said)
    # И в любом случае строка НЕ ПОТЕРЯНА: старый код терял её навсегда.
    assert S.get(r["rem_id"]) is not None, "строка исчезла"


def test_nothing_is_ever_deleted_only_marked(clean):
    """Замер до переезда: сработавшее УДАЛЯЛОСЬ из файла ДО произнесения, и
    падение в этот миг уносило напоминание навсегда.

    Здесь: сработало -> 'done', отменено -> 'cancelled', строка ОСТАЁТСЯ.
    """
    fired = S.arm("сработает", _now() - timedelta(minutes=1))
    killed = S.arm("отменю", _now() + timedelta(days=1))

    clean.check_and_fire()
    clean.reminder({"action": "cancel", "message": "отменю"})

    got_fired = S.get(fired["rem_id"])
    got_killed = S.get(killed["rem_id"])
    assert got_fired is not None and int(got_fired["main_done"]) == 1
    assert got_killed is not None, "отменённое напоминание УДАЛЕНО, а не помечено"
    assert got_killed["state"] == S.STATE_CANCELLED


def test_no_row_stays_armed_forever(clean, monkeypatch):
    """Замер до переезда: 39 просроченных перечитывались каждые 30 секунд ВЕЧНО,
    и никто их не убирал.

    Этот сторож ловит и МОЮ СОБСТВЕННУЮ ошибку, найденную в дымовом прогоне:
    строка с уже сказанным главным, чей повтор не нужен, оставалась `armed`
    навсегда — говорить о ней нечего, а уборка её не берёт, потому что трогает
    только 'done' и 'cancelled'. Тот же дефект в новой одежде, созданный моими
    руками. Лечение — исход 'close'.

    ЭТОТ ТЕСТ БЫЛ МИГАЮЩИМ: 5 падений из 8. Я вызывал note_owner_spoke() ДО
    того, как Джарвис сказал, и на живых часах порядок двух отметок в одну и ту
    же микросекунду становился лотереей — иногда «ответ» оказывался раньше
    нашей фразы и не считался ответом.

    Мигающий сторож хуже отсутствующего: отсутствующий молчит, а мигающий
    выдаёт чужие заслуги за свои (я уже наступил на это в блоке 9). Поэтому
    часы под контролем, и порядок событий задан явно: сначала сказали, потом
    владелец ответил.
    """
    clock = _clock(monkeypatch, 600_000.0)
    S.reset_for_tests()
    for i in range(6):
        S.arm("старое-%d" % i, _now() - timedelta(hours=i + 1))

    clean.check_and_fire()                   # Джарвис сказал (залпом)
    clock[0] += 20
    S.note_owner_spoke()                     # и ТОЛЬКО ПОТОМ владелец ответил

    for _ in range(4):                       # ещё четыре тика подряд
        clock[0] += 30
        clean.check_and_fire()

    stuck = [r for r in S.alive(limit=100)
             if S._to_utc(r["due_utc"]) < _now()]
    assert not stuck, (
        "просроченных, всё ещё живых: %d — они будут перечитываться вечно"
        % len(stuck))


def test_the_purge_keeps_living_reminders(clean, monkeypatch):
    """Уборка отработанных не имеет права тронуть живое.

    ТРИ МОИ ОШИБКИ В ЭТОМ ТЕСТЕ, ВСЕ ПОЙМАНЫ ИМ ЖЕ.
    Первая: я звал check_and_fire(), а он сам убирается раз в сутки — к моменту
    проверки убирать было нечего, и purge() честно вернул 0.
    Вторая: я считал, что одного тика хватит, чтобы строка стала 'done'. Нет:
    вовремя сработавшее НАРОЧНО остаётся 'armed'.
    Третья, после правки 22.08.2026: я думал, что закрывает ПОВТОР. Больше нет —
    повтор оставляет строку ждать владельца. Закрывает её ОТВЕТ владельца.
    """
    import time as _time
    clock = [200_000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])

    alive = S.arm("живое", _now() + timedelta(days=2))
    old = S.arm("древнее", _now() - timedelta(seconds=5))
    S.tick()                                          # главное
    clock[0] += 20
    S.note_owner_spoke()                              # владелец услышал
    clock[0] += 30
    S.tick(_now() + timedelta(seconds=50))            # закрытие
    assert S.get(old["rem_id"])["state"] == S.STATE_DONE, (
        "строка не отработана после ответа владельца")

    writer.write(lambda c: c.execute(
        "UPDATE mx_reminder SET created_utc=? WHERE rem_id=?",
        (S._iso(_now() - timedelta(days=S.KEEP_DAYS + 10)), old["rem_id"])))

    gone = S.purge()
    assert gone == 1, "убрано %d вместо одного" % gone
    assert S.get(old["rem_id"]) is None, "отработанная строка осталась"
    assert S.get(alive["rem_id"]) is not None, "уборка забрала ЖИВОЕ напоминание"


def test_a_reminder_waiting_for_the_owner_is_not_purged(clean, monkeypatch):
    """Строка, ждущая владельца, — ЖИВАЯ. Если уборка начнёт её брать, владелец
    никогда не узнает о том, что пропустил, пока отходил.

    Это не мелочь: именно на такой строке держится всё, о чём владелец просил
    22.08.2026 («отошёл больше трёх минут — напомни, когда вернусь»).
    """
    import time as _time
    clock = [300_000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])

    r = S.arm("позвонить клиенту", _now() - timedelta(seconds=5))
    S.tick()
    clock[0] += S.RETRY_MINUTES * 60 + 5
    assert [i["kind"] for i in S.tick(
        _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5))] == ["retry"]

    # Состарим строку так, будто она лежит месяц: уборка НЕ ИМЕЕТ права её взять,
    # потому что она всё ещё ждёт владельца.
    writer.write(lambda c: c.execute(
        "UPDATE mx_reminder SET created_utc=? WHERE rem_id=?",
        (S._iso(_now() - timedelta(days=S.KEEP_DAYS + 10)), r["rem_id"])))
    assert S.purge() == 0, "уборка забрала напоминание, ждущее владельца"

    # И когда он вернётся, оно всё ещё на месте и прозвучит.
    clock[0] += 1800
    S.note_owner_spoke()
    items = S.tick(_now() + timedelta(minutes=35))
    assert [i["kind"] for i in items] == ["again"], "ждавшее напоминание пропало"


# -- Повтор ----------------------------------------------------------------

def _clock(monkeypatch, start=100_000.0):
    """Взять монотонные часы под контроль и вернуть рычаг к ним.

    Нужно всем сторожам повтора, и вот почему. Повтор и различение
    «услышал / вернулся» отсчитываются от МОНОТОННЫХ часов (секундомера), а срок
    напоминания — от календарных. Тест, который подделывает только календарные,
    проверяет не то, что живёт: он уже дал ложную зелень 22.08.2026, когда я
    вернул неверный вопрос про ответ владельца и ни один сторож не покраснел.
    """
    import time as _time
    holder = [float(start)]
    monkeypatch.setattr(_time, "monotonic", lambda: holder[0])
    return holder


def test_the_retry_speaks_once_when_the_owner_stays_silent(clean, monkeypatch):
    """Д30: один повтор через 3 минуты. ВЫБОР ВЛАДЕЛЬЦА: только при тишине.

    ВАЖНОЕ ИЗМЕНЕНИЕ 22.08.2026, по прямой просьбе владельца: повтор БОЛЬШЕ НЕ
    ЗАКРЫВАЕТ напоминание. Раньше закрывал — и фраза, прозвучавшая в пустую
    комнату, считалась доставленной. Теперь строка ждёт признака, что владелец
    был рядом; закрывает её либо его ответ, либо просрочка в 7 суток.
    """
    clock = _clock(monkeypatch)
    S.reset_for_tests()
    r = S.arm("позвонить клиенту", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]
    assert S.tick() == [], "сказал дважды в одну минуту"

    # Отсчёт повтора идёт от НАШЕЙ фразы, а не от срока напоминания.
    clock[0] += S.RETRY_MINUTES * 60 + 5
    later = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5)
    assert [i["kind"] for i in S.tick(later)] == ["retry"]

    clock[0] += 60
    assert S.tick(later + timedelta(minutes=1)) == [], "повтор не один"
    assert S.get(r["rem_id"])["state"] == S.STATE_ARMED, (
        "повтор закрыл напоминание — значит фраза в пустую комнату снова "
        "считается доставленной")


def test_a_reminder_waits_for_the_owner_to_come_back(clean, monkeypatch):
    """ГЛАВНОЕ, ЧТО ПОПРОСИЛ ВЛАДЕЛЕЦ 22.08.2026, его словами:

        «бывает такое что я могу вообще отойти куда-то больше этих 3 минут и
        когда вернусь(после 3 минут) и начну общаться то он мне не напомнит то
        что должен был. Крч нужно чтобы когда я вернулся и ответил тогда снова
        мне напомнил»

    До этой правки повтор звучал по таймеру в пустую комнату и ЗАКРЫВАЛ
    напоминание: Джарвис считал дело сделанным, а владелец не слышал ни слова.
    """
    clock = _clock(monkeypatch)
    S.reset_for_tests()
    r = S.arm("позвонить в банк", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]

    clock[0] += S.RETRY_MINUTES * 60 + 5
    t2 = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5)
    assert [i["kind"] for i in S.tick(t2)] == ["retry"]

    # Владельца нет ПОЛЧАСА. Ничего не говорим и НЕ закрываем.
    clock[0] += 1800
    t3 = _now() + timedelta(minutes=33)
    assert S.tick(t3) == [], "говорил в пустую комнату"
    assert S.get(r["rem_id"])["state"] == S.STATE_ARMED, (
        "закрылось, пока владельца не было — он его так и не услышит")

    # Вернулся и заговорил.
    clock[0] += 60
    S.note_owner_spoke()
    t4 = _now() + timedelta(minutes=34)
    items = S.tick(t4)
    assert [i["kind"] for i in items] == ["again"], (
        "владелец вернулся, а напоминание молчит: %r" % items)

    said = clean._phrase(items)
    assert "пока вас не было" in said, (
        "не сказано, что напоминание его ждало: %r" % said)
    assert "позвонить в банк" in said

    assert S.get(r["rem_id"])["state"] == S.STATE_DONE
    clock[0] += 600
    assert S.tick(t4 + timedelta(minutes=10)) == [], "говорит третий раз"


def test_an_answer_right_after_the_reminder_closes_it_without_a_repeat(
        clean, monkeypatch):
    """Обратная сторона: владелец БЫЛ рядом и ответил сразу. Ни повтора, ни
    «пока вас не было» — он был."""
    clock = _clock(monkeypatch)
    S.reset_for_tests()
    r = S.arm("выпить воды", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]

    clock[0] += 15
    S.note_owner_spoke()                      # «хорошо, спасибо»

    for extra in (S.RETRY_MINUTES, S.RETRY_MINUTES + 10, S.RETRY_MINUTES + 40):
        clock[0] += 600
        assert S.tick(_now() + timedelta(minutes=extra)) == [], (
            "потревожил того, кто уже ответил, через %d мин" % extra)
    assert S.get(r["rem_id"])["state"] == S.STATE_DONE


def test_a_waiting_reminder_does_not_wait_past_the_owners_limit(clean,
                                                               monkeypatch):
    """Ждать — не значит ждать вечно. Иначе возвращается дефект №7 из десяти
    замеренных: строки копятся и перечитываются бесконечно.

    Граница — те же 7 суток, что владелец выбрал для просрочки. Старше —
    закрываем МОЛЧА, но строку сохраняем.
    """
    clock = _clock(monkeypatch)
    S.reset_for_tests()
    r = S.arm("древнее дело", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]

    clock[0] += S.RETRY_MINUTES * 60 + 5
    assert [i["kind"] for i in S.tick(
        _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5))] == ["retry"]

    clock[0] += 86400 * (S.STALE_DAYS + 2)
    assert S.tick(_now() + timedelta(days=S.STALE_DAYS + 2)) == [], (
        "заговорил о деле недельной давности")
    assert S.get(r["rem_id"])["state"] == S.STATE_DONE, (
        "строка ждёт дольше выбранного владельцем срока — она будет "
        "перечитываться вечно")



def test_the_retry_is_silent_when_the_owner_answered(clean, monkeypatch):
    """ЖИВАЯ ПРОБА ВЛАДЕЛЬЦА 22.08.2026 НАШЛА ЗДЕСЬ ДЕФЕКТ, и он был мой.

    Он ответил «хорошо, спасибо» — и повтор всё равно прозвучал. Причина: гашение
    задавало НЕВЕРНЫЙ вопрос. Оно спрашивало «говорил ли владелец за последние
    три минуты», а надо «говорил ли владелец С ТЕХ ПОР, как я сказал».

    Разница выходит наружу ВСЕГДА, а не в редком случае:

        напоминание на 16:00:00
        главное прозвучало  ~16:00:20
        владелец ответил    ~16:00:30   <- человек отвечает сразу
        проверка повтора    ~16:03:50   <- тик ходит раз в 30 секунд
        говорил 200 секунд назад, а смотрели на 180 -> НЕ ПОГАСИЛО

    Поэтому повтор звучал почти всегда. Владелец услышал это дважды за одну
    пробу и написал «работает ужасно» — он был прав.

    ПЕРВАЯ ВЕРСИЯ ЭТОГО СТОРОЖА БЫЛА БЕССИЛЬНА, и это показала порча кода: я
    вернул старый неверный вопрос, и тест остался зелёным. Причина — он подделывал
    только КАЛЕНДАРНЫЕ часы (срок напоминания), а гашение смотрит на ВНУТРЕННИЙ
    СЕКУНДОМЕР (time.monotonic). В тесте, который длится миллисекунды, секундомер
    почти не двигается, поэтому «говорил ли за последние три минуты» отвечало
    «да» — и старый код проходил.

    Теперь под контролем ОБА времени. Иначе сторож проверяет не то, что живёт.
    """
    import time as _time

    clock = [10_000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])

    S.reset_for_tests()
    due = _now() - timedelta(seconds=20)
    r = S.arm("вынести мусор", due)

    # 16:00:20 — главное прозвучало.
    assert [i["kind"] for i in S.tick()] == ["main"]

    # 16:00:30 — владелец ответил «хорошо, спасибо», сразу после напоминания.
    clock[0] += 10
    S.note_owner_spoke()

    # 16:03:50 — проверка повтора. Секундомер ушёл на 200 секунд ВПЕРЁД от
    # ответа: старый вопрос («за последние 180 секунд») здесь отвечает «нет»,
    # и порча сразу становится видна.
    clock[0] += 200
    later = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=30)
    assert S.tick(later) == [], (
        "повторил тому, кто уже отозвался: вопрос задан неверно — "
        "надо «говорил ли ПОСЛЕ того, как я сказал», а не «за последние N секунд»")

    # И позже тоже молчит, а строка закрыта, а не висит вечно.
    clock[0] += 600
    assert S.tick(later + timedelta(minutes=10)) == []
    assert S.get(r["rem_id"])["state"] == S.STATE_DONE, (
        "повтор не нужен, а строка осталась живой навсегда")


def test_the_retry_still_speaks_when_the_owner_spoke_only_before(clean,
                                                                 monkeypatch):
    """Обратная сторона того же вопроса, и без неё сторож выше половинчатый.

    Владелец говорил ДО напоминания и замолчал после — это и есть тишина, ради
    которой повтор существует. Если гашение начнёт считать любой прошлый разговор
    за ответ, повтор не прозвучит никогда, и выбор владельца («повторять при
    тишине») тихо перестанет работать.
    """
    import time as _time

    clock = [20_000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])

    S.reset_for_tests()
    S.note_owner_spoke()                      # говорил ДО напоминания
    clock[0] += 60

    S.arm("позвонить клиенту", _now() - timedelta(seconds=10))
    assert [i["kind"] for i in S.tick()] == ["main"]

    clock[0] += S.RETRY_MINUTES * 60 + 20
    later = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=20)
    assert [i["kind"] for i in S.tick(later)] == ["retry"], (
        "владелец молчал после напоминания, а повтора не было")


def test_the_retry_does_not_call_itself_a_missed_reminder(clean, monkeypatch):
    """ВТОРОЙ ДЕФЕКТ ИЗ ЖИВОЙ ПРОБЫ. Владелец услышал дословно:

        «Напоминание было на 16:00, вы опоздали на 3 минуты: выпить воды»

    Это был ПОВТОР. Он обязан звучать повтором: иначе владелец думает, что
    что-то пропустил, хотя ему это сказали три минуты назад и он всё слышал.
    """
    clock = _clock(monkeypatch, 400_000.0)
    S.reset_for_tests()
    S.arm("выпить воды", _now() - timedelta(seconds=5))
    S.tick()

    # Повтор отсчитывается от НАШЕЙ фразы, значит двигать надо секундомер.
    clock[0] += S.RETRY_MINUTES * 60 + 5
    later = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5)
    items = S.tick(later)
    assert [i["kind"] for i in items] == ["retry"]

    said = clean._phrase(items)
    assert "опозда" not in said, (
        "повтор объявил себя опозданием: %r" % said)
    assert "повторяю" in said, "повтор не назван повтором: %r" % said
    assert "выпить воды" in said


def test_a_tick_delay_is_never_announced_as_being_late(clean):
    """ТРЕТИЙ ДЕФЕКТ ИЗ ЖИВОЙ ПРОБЫ. Владелец услышал дословно:

        «Напоминание было на 16:02, вы опоздали на 1 минуту: закрыть окно»

    Напоминание вышло ВОВРЕМЯ. Проверялка в замороженном main.py спит 30 секунд
    перед каждой проверкой, поэтому опоздание в полминуты-минуту — это норма
    работы, а не событие. Слово «опоздал» пугало на ровном месте.

    Слово существует ровно для одного: не дать услышать «пора сейчас» там, где
    пора было давно. Проверяются ОБЕ стороны порога — иначе сторож остался бы
    зелёным, даже если порог станет вечностью.
    """
    edge = S.LATE_WORDS_FROM_S

    # Ниже порога — про опоздание молчим.
    S.reset_for_tests()
    S.arm("закрыть окно", _now() - timedelta(seconds=edge - 60))
    quiet = clean._phrase(S.tick())
    assert "опозда" not in quiet, (
        "задержка тика объявлена опозданием: %r" % quiet)
    assert "закрыть окно" in quiet

    # Выше порога — говорим, иначе владелец услышит «пора сейчас».
    S.reset_for_tests()
    S.arm("позвонить в банк", _now() - timedelta(seconds=edge + 120))
    loud = clean._phrase(S.tick())
    assert "опозда" in loud, (
        "настоящее опоздание не названо — владелец поймёт «пора сейчас»: %r"
        % loud)
    assert re.search(r"\d{1,2}:\d{2}", loud), "не сказано, на какой час было"


def test_a_late_reminder_does_not_turn_into_two_phrases(clean):
    """ДЕФЕКТ, НАЙДЕННЫЙ В МОЁМ ЖЕ ДЫМОВОМ ПРОГОНЕ 22.08.2026.

    Повтор отсчитывался ОТ СРОКА, поэтому у напоминания, опоздавшего на трое
    суток, условие «прошло три минуты после срока» выполнялось сразу — и
    следующий тик через 30 секунд говорил то же самое ВТОРОЙ раз. То есть догон
    пропущенного превращался в двойную реплику, ровно в то «никогда не восемь
    реплик подряд», что запрещает правило залпа.
    """
    S.reset_for_tests()
    S.arm("давнее дело", _now() - timedelta(days=3))
    first = S.tick()
    assert [i["kind"] for i in first] == ["main"]
    assert S.tick(_now() + timedelta(seconds=30)) == [], (
        "догон сказал второй раз через 30 секунд")
    assert S.tick(_now() + timedelta(minutes=5)) == [], (
        "догон сказал второй раз через 5 минут")


def test_a_short_notice_reminder_gets_no_advance_warning(clean):
    """«Напомни через 5 минут»: предупреждение за 15 минут прозвучало бы почти
    сразу после постановки, то есть дважды об одном за пять минут."""
    S.arm("выключить чайник", _now() + timedelta(minutes=5))
    assert S.tick() == [], "предупредил о том, что и так через 5 минут"
    assert [i["kind"] for i in S.tick(_now() + timedelta(minutes=5, seconds=5))] \
        == ["main"]


def test_the_advance_warning_fires_for_a_distant_reminder(clean):
    """Д30: за 15 минут. Путь, который МОЙ ПЕРВЫЙ ДЫМОВОЙ ПРОГОН НЕ ПРОВЕРИЛ:
    я смотрел на «через 10 минут» и получал [], но это была отсечка короткого
    срока, а не предупреждение. Зелено ни о чём."""
    due = _now() + timedelta(hours=2)
    r = S.arm("встреча с клиентом", due)
    assert S.tick() == [], "предупредил за два часа"

    moment = due - timedelta(minutes=10)
    items = S.tick(moment)
    assert [i["kind"] for i in items] == ["pre"], (
        "предупреждения за 15 минут нет: %r" % items)
    assert S.tick(moment) == [], "предупредил дважды"
    assert int(S.get(r["rem_id"])["pre_done"]) == 1
    assert S.get(r["rem_id"])["state"] == S.STATE_ARMED, (
        "предупреждение закрыло напоминание — главное не прозвучит")

    said = clean._phrase(items)
    assert str(S.PRE_MINUTES) in said, "во фразе нет «через 15 минут»: %r" % said


# -- Отмена ----------------------------------------------------------------

def test_cancelling_an_ambiguous_keyword_asks_instead_of_guessing(clean):
    """Замер до переезда: «отмени про позвонить» убило И «позвонить маме», И
    «позвонить в банк», а ответило в единственном числе — второе исчезало молча.

    ВЫБОР ВЛАДЕЛЬЦА 22.08.2026: при нескольких совпадениях спросить.
    """
    S.arm("позвонить маме", _now() + timedelta(days=1))
    S.arm("позвонить в банк", _now() + timedelta(days=1))
    S.arm("купить хлеб", _now() + timedelta(days=1))

    answer = clean.reminder({"action": "cancel", "message": "позвонить"})
    assert "Which one" in answer, "не спросил, а решил сам: %r" % answer
    assert len(S.alive(limit=50)) == 3, "отменил, хотя должен был спросить"

    exact = clean.reminder({"action": "cancel", "message": "позвонить маме"})
    assert "cancelled" in exact.lower(), exact
    left = sorted(r["text"] for r in S.alive(limit=50))
    assert left == ["купить хлеб", "позвонить в банк"], left


# -- Ноль вызовов модели (Д4) ----------------------------------------------

def test_firing_a_reminder_never_calls_a_model(clean):
    """Д4 дословно: «напоминания работают без единого вызова модели».

    Обещание словами не держится ничем, поэтому здесь ДВЕ проверки: провайдер
    подменён на падающий (любое обращение к облаку = ошибка) и сверяется тетрадь
    расхода до и после.
    """
    from core import provider as provider_pkg
    from core import metering

    def _spent():
        row = writer.reader().execute(
            "SELECT count(*) AS n FROM mx_meter_call").fetchone()
        return int(row["n"])

    S.arm("позвонить в банк", _now() - timedelta(minutes=1))
    S.arm("встреча", _now() + timedelta(hours=2))

    class Forbidden:
        def generate(self, *a, **k):
            raise AssertionError("напоминание полезло в облако")

        def __getattr__(self, name):
            raise AssertionError("напоминание полезло в облако через " + name)

    before = _spent()
    saved = provider_pkg.set_provider(Forbidden())
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            said = clean.check_and_fire()
            clean.reminder({"action": "list"})
            clean.reminder({"action": "set", "date": "2027-01-01",
                            "time": "09:00", "message": "дело"})
    finally:
        provider_pkg.set_provider(saved)

    assert said, "тик ничего не сказал — проверять было бы нечего"
    assert _spent() == before, "напоминание записало вызов модели в учёт"


# -- Перенос старого файла -------------------------------------------------

def test_the_old_json_is_imported_once_and_never_modified(clean, tmp_path,
                                                          monkeypatch):
    """Р-6 дословно: «миграция данных из JSON в mx_reminder С СОХРАНЕНИЕМ
    СТАРОГО ФАЙЛА».

    Проверено 22.08.2026: у владельца этого файла нет ни в проекте, ни в доме,
    ни в старых сборках. Значит либо напоминаниями не пользовались, либо они УЖЕ
    потерялись при переезде на новую сборку — узнать теперь нельзя.
    """
    legacy = tmp_path / "reminders.json"
    rows = [
        {"datetime_iso": "2027-04-10T15:00:00+02:00", "message": "позвонить маме"},
        {"datetime_msk": "2027-04-11 09:30", "message": "старый формат"},
        {"message": "без срока — не переносим"},
        "мусор вместо словаря",
    ]
    legacy.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    before = legacy.read_bytes()

    monkeypatch.setattr(clean, "LEGACY_PATH", legacy)
    monkeypatch.setattr(clean, "LEGACY_MARK",
                        legacy.with_name(legacy.name + ".imported"))
    clean.reset_for_tests()

    moved = clean.import_legacy_once()
    assert moved == 2, "перенесено %d из 2 годных" % moved
    assert legacy.read_bytes() == before, "СТАРЫЙ ФАЙЛ ИЗМЕНЁН — Р-6 нарушен"
    assert legacy.exists(), "старый файл удалён — Р-6 нарушен"

    texts = sorted(r["text"] for r in S.alive(limit=50))
    assert texts == ["позвонить маме", "старый формат"], texts

    clean.reset_for_tests()
    assert clean.import_legacy_once() == 0, "перенос побежал второй раз"
    assert len(S.alive(limit=50)) == 2, "напоминания удвоились"


def test_the_local_hour_is_not_shifted_by_the_import(clean, tmp_path,
                                                     monkeypatch):
    """Схема прямо предупреждает: старый код кладёт в JSON строку С МЕСТНЫМ
    СДВИГОМ, и запись её прямо в колонку due_utc сдвинула бы все напоминания на
    часы. Заметили бы это в день перевода часов.

    Поэтому исходная строка обязана лечь в due_raw НЕТРОНУТОЙ, а в due_utc —
    честный UTC, и они не равны.
    """
    raw = "2027-04-10T15:00:00+02:00"
    legacy = tmp_path / "reminders.json"
    legacy.write_text(json.dumps([{"datetime_iso": raw, "message": "встреча"}],
                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(clean, "LEGACY_PATH", legacy)
    monkeypatch.setattr(clean, "LEGACY_MARK",
                        legacy.with_name(legacy.name + ".imported"))
    clean.reset_for_tests()
    clean.import_legacy_once()

    row = S.alive()[0]
    assert row["due_raw"] == raw, "исходная строка потеряна: %r" % row["due_raw"]
    assert row["due_utc"] == "2027-04-10T13:00:00+00:00", (
        "сдвиг не пересчитан: %r" % row["due_utc"])


def test_a_time_without_an_offset_is_read_as_the_owners_local_time(clean,
                                                                  monkeypatch):
    """ПРОПУСК ПОРЧИ 22.08.2026, РАЗОБРАННЫЙ ДО КОНЦА — и это самый поучительный
    случай в блоке.

    Порча: снести ветвь «у строки нет сдвига — считать её местной». Первая версия
    сторожа её не поймала, потому что смотрела только на строку СО сдвигом. Я
    добавил проверку строки БЕЗ сдвига — И ОНА ТОЖЕ НЕ ПОЙМАЛА. Разобрался
    замером, а не догадкой:

        пояс из настроек: None -> действующий пояс = системный
        как делает код (replace tzinfo): 2027-03-01 09:00:00+00:00
        как выйдет без ветви (astimezone): 2027-03-01 09:00:00+00:00   <- ТО ЖЕ

    То есть на ЭТОЙ машине оба пути дают одно число, потому что запасной путь
    берёт тот же системный пояс. Мой тест сравнивал ответ кода с величиной,
    вычисленной ТЕМ ЖЕ СПОСОБОМ, — классическое «мерить постоянную самой собой»,
    которое уже попадалось в блоке 6.

    Вывод честный: пока пояс в настройках не задан, порча действительно НИЧЕГО НЕ
    МЕНЯЕТ — это безвредная порча, а не пропущенная дыра. Но она перестанет быть
    безвредной в тот день, когда владелец задаст пояс вручную: тогда «в 12:00»
    начнёт считаться по системному поясу вместо выбранного. Поэтому сторож
    РАЗВОДИТ два пояса нарочно и требует, чтобы код слушался настройки.
    """
    from datetime import timedelta as _td
    from core import time_utils

    naive = "2027-03-01T12:00:00"

    # Пояс настройки нарочно НЕ равен системному: иначе оба пути совпадут и
    # проверять будет нечего.
    picked = timezone(_td(hours=9), "ТЕСТ+9")
    monkeypatch.setattr(time_utils, "get_effective_timezone", lambda: picked)

    got = S._to_utc(naive)
    want = datetime.fromisoformat(naive).replace(
        tzinfo=picked).astimezone(timezone.utc)
    assert got == want, (
        "время без сдвига прочитано не по ВЫБРАННОМУ поясу: %s вместо %s"
        % (got, want))
    assert got != datetime.fromisoformat(naive).astimezone(timezone.utc), (
        "код взял системный пояс вместо настройки владельца")

    # И тот же путь через настоящую постановку, а не только через перевод.
    r = S.arm("работать с проектом", naive)
    stored = S.get(r["rem_id"])
    assert stored["due_utc"] == S._iso(want), (
        "напоминание уехало по часовому поясу: %r" % stored["due_utc"])
    assert stored["due_raw"] == naive, "исходная строка потеряна"


@pytest.mark.parametrize("return_after_s,branch", [
    (200, "короткая"),     # вернулся быстро: опоздание меньше LATE_WORDS_FROM_S
    (2400, "длинная"),     # вернулся через 40 минут: опоздание названо вслух
])
def test_the_return_phrase_always_says_the_reminder_was_waiting(
        clean, monkeypatch, return_after_s, branch):
    """У фразы возвращения ДВЕ ветки: со сроком (когда опоздание большое) и без.

    ПЕРВАЯ ВЕРСИЯ ЭТОГО СТОРОЖА ПРОВЕРЯЛА ТОЛЬКО ОДНУ, и порча кода это показала:
    я убрал слова из короткой ветки — тест остался зелёным. Причина в том, что оба
    моих случая уходили в ДЛИННУЮ ветку: пока владелец «отсутствовал» 35 минут,
    опоздание всегда переваливало порог LATE_WORDS_FROM_S. Короткая ветка живёт в
    узком окне — вернулся позже трёх минут (иначе «услышал»), но раньше десяти
    (иначе опоздание называют вслух), — и я его просто не задел.

    Теперь параметр — КОГДА владелец вернулся, и оба окна покрыты. При любом
    опоздании он должен понять: это напоминание его ЖДАЛО, а не появилось сейчас.
    """
    clock = _clock(monkeypatch, 500_000.0 + return_after_s)
    S.reset_for_tests()
    S.arm("позвонить в банк", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]

    clock[0] += S.RETRY_MINUTES * 60 + 5
    at_retry = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5)
    assert [i["kind"] for i in S.tick(at_retry)] == ["retry"]

    clock[0] += return_after_s
    S.note_owner_spoke()
    at_return = at_retry + timedelta(seconds=return_after_s)
    items = S.tick(at_return)
    assert [i["kind"] for i in items] == ["again"], items

    late_s = float(items[0]["late_s"])
    if branch == "короткая":
        assert late_s < S.LATE_WORDS_FROM_S, (
            "случай ушёл не в ту ветку: опоздание %.0f с" % late_s)
    else:
        assert late_s >= S.LATE_WORDS_FROM_S, (
            "случай ушёл не в ту ветку: опоздание %.0f с" % late_s)

    said = clean._phrase(items)
    assert "пока вас не было" in said, (
        "владелец не поймёт, что напоминание его ждало (%s ветка): %r"
        % (branch, said))
    assert "позвонить в банк" in said


# -- Часы модели отстают ----------------------------------------------------

def test_a_relative_reminder_ignores_the_models_frozen_clock(clean):
    """ЖИВАЯ ПРОБА ВЛАДЕЛЬЦА 22.08.2026, третий дефект и самый обидный:

        17:19  «напомни через 2 минуты выпить воды»  -> на 17:19, верно
        17:21  «напомни через 2 минуты закрыть окно» -> НА 17:21, то есть на
               УЖЕ НАСТУПИВШЕЕ время, и прозвучало мгновенно

    Его слова: «у тебя проблемы с временем».

    Причина не в напоминаниях. Модель узнаёт время РОВНО ОДИН РАЗ, при
    подключении: format_time_context() зовётся только внутри _build_config
    (main.py:1189, файл заморожен). Дальше её часы стоят, и чем дольше разговор,
    тем сильнее ошибка.

    Лечение: модель называет ДЛИТЕЛЬНОСТЬ, а момент считает код по настоящим
    часам. Здесь проверяется именно это — что присланное моделью «сейчас»
    вообще не участвует.
    """
    from core.time_utils import get_now, get_effective_timezone

    tz = get_effective_timezone()
    before = get_now(tz)
    answer = clean.reminder({"time": "+2", "message": "выпить воды"})
    assert "Reminder set" in answer, answer

    rows = S.alive()
    assert len(rows) == 1, rows
    due = S._to_utc(rows[0]["due_raw"] or rows[0]["due_utc"])
    delta = (due - before).total_seconds() / 60.0
    assert 1.0 <= delta <= 3.0, (
        "«через 2 минуты» дало срок через %.1f мин" % delta)
    assert due > before, "срок уехал в прошлое — вернулся дефект владельца"


def test_a_stale_absolute_time_from_the_model_does_not_beat_the_duration(clean):
    """Если модель пришлёт И длительность, И своё (отставшее) время —
    верим длительности. Иначе починка не работает ровно в том случае, для
    которого сделана."""
    from core.time_utils import get_now, get_effective_timezone

    tz = get_effective_timezone()
    stale = get_now(tz) - timedelta(minutes=30)
    clean.reminder({"date": stale.strftime("%Y-%m-%d"),
                    "time": "+5",
                    "message": "позвонить в банк"})
    rows = S.alive()
    due = S._to_utc(rows[0]["due_raw"] or rows[0]["due_utc"])
    assert due > get_now(tz), (
        "победило отставшее время модели, а не длительность: %s" % due)


@pytest.mark.parametrize("params,want", [
    ({"time": "+2"}, 2),
    ({"time": "+30"}, 30),
    ({"time": "+ 15 мин"}, 15),
    ({"time": "in 5 minutes"}, 5),
    ({"time": "через 45 минут"}, 45),
    ({"in_minutes": 7}, 7),
    # А это НЕ длительность, а обычные часы. Спутать нельзя ни в одну сторону:
    # принять «17:19» за длительность значит поставить напоминание через 17 часов.
    ({"time": "17:19"}, None),
    ({"time": "09:00"}, None),
    ({"time": "15"}, None),
    ({"time": ""}, None),
    ({"time": "+0"}, None),
    ({"time": "+5000"}, None),
    ({"time": "+abc"}, None),
])
def test_a_duration_is_never_confused_with_a_clock_reading(clean, params, want):
    assert clean._minutes_param(params) == want, params


def test_an_absolute_time_still_works(clean):
    """Починка относительных сроков не имеет права сломать обычные: «напомни
    завтра в 10:00» модель по-прежнему присылает датой и временем."""
    from core.time_utils import get_now, get_effective_timezone

    tz = get_effective_timezone()
    tomorrow = (get_now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    answer = clean.reminder({"date": tomorrow, "time": "10:00",
                             "message": "позвонить маме"})
    assert "10:00" in answer, answer
    rows = S.alive()
    got = S._to_utc(rows[0]["due_raw"] or rows[0]["due_utc"])
    assert got.astimezone(tz).strftime("%H:%M") == "10:00", got


def test_asking_for_a_reminder_costs_no_network(clean):
    """Владелец спросил прямо: «я надеюсь определение времени будет занимать
    миллисекунды?». Замер 22.08.2026: чтение часов 0,0002 мс, разбор «+2»
    0,0007 мс, сети нет.

    Сторож закрепляет именно ОТСУТСТВИЕ СЕТИ, а не сами миллисекунды: время
    прогона зависит от машины, а вот обращение к облаку — это то, чего здесь
    быть не должно никогда (Д4).
    """
    from core import provider as provider_pkg

    class Forbidden:
        def generate(self, *a, **k):
            raise AssertionError("постановка напоминания полезла в облако")

        def __getattr__(self, name):
            raise AssertionError("постановка полезла в облако через " + name)

    saved = provider_pkg.set_provider(Forbidden())
    try:
        clean.reminder({"time": "+2", "message": "выпить воды"})
        clean.reminder({"time": "+60", "message": "позвонить"})
    finally:
        provider_pkg.set_provider(saved)
    assert len(S.alive()) == 2


# -- Присутствие владельца: голос И печать ----------------------------------

def _bare_log():
    """Настоящий ui.write_log без окна: несвязанный метод на пустышке.

    Окно на tkinter в тесте не поднять, но проверять надо ТОТ САМЫЙ код, а не
    его копию: копия разошлась бы с оригиналом молча.
    """
    import ui as UI

    class Bare:
        def __init__(self):
            self._ui_queue = type("Q", (), {"put": lambda s, x: None})()

        def _is_ui_thread(self):
            return False

    bare = Bare()
    return UI.JarvisUI.write_log.__get__(bare, Bare)


@pytest.mark.parametrize("line,counts", [
    ("You: здаров", True),               # напечатал — ЭТО и был дефект владельца
    ("You: ок", True),                   # короче шести символов
    ("You: да", True),
    ("you: привет", True),               # регистр не должен решать
    ("Jarvis: выпить воды", False),      # свои фразы отметкой быть не могут
    ("SYS: JARVIS online.", False),
    ("[Reminder] Firing: main", False),
])
def test_only_the_owners_own_lines_count_as_presence(clean, line, counts):
    """ЖИВАЯ ПРОБА ВЛАДЕЛЬЦА 22.08.2026: он поставил напоминание, отошёл на шесть
    минут, вернулся, НАПЕЧАТАЛ «здаров» — и Джарвис промолчал.

    Замер показал, что голос и печать идут РАЗНЫМИ дорогами: микрофон попадает в
    разбор памяти (main.py:1749), а печать уходит прямо в модель через
    _on_text_command (main.py:941), МИМО него. То есть напечатанное не считалось
    ответом ВООБЩЕ, ни при какой длине, и напоминание не догоняло владельца
    никогда. Обхода не было — только «говорите голосом».

    Обратная сторона не менее важна: фразы САМОГО Джарвиса отметкой быть не
    могут. Иначе он гасил бы напоминания собственным голосом, и повтор не
    прозвучал бы никогда.
    """
    write_log = _bare_log()
    S.reset_for_tests()
    assert S._last_owner_word == 0.0

    write_log(line)

    if counts:
        assert S._last_owner_word > 0.0, (
            "реплика владельца не отметила его присутствие: %r" % line)
    else:
        assert S._last_owner_word == 0.0, (
            "чужая строка выдана за ответ владельца: %r" % line)


def test_a_typed_answer_brings_the_waiting_reminder_back(clean, monkeypatch):
    """Тот же сценарий владельца целиком, но ответ ПЕЧАТЬЮ, и через настоящий
    ui.write_log — то есть по той дороге, которая молчала."""
    clock = _clock(monkeypatch, 700_000.0)
    write_log = _bare_log()
    S.reset_for_tests()

    S.arm("выпить воды", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]

    clock[0] += S.RETRY_MINUTES * 60 + 5
    at_retry = _now() + timedelta(minutes=S.RETRY_MINUTES, seconds=5)
    assert [i["kind"] for i in S.tick(at_retry)] == ["retry"]

    # Владельца нет шесть минут — ровно как в его пробе.
    clock[0] += 360
    assert S.tick(at_retry + timedelta(minutes=6)) == []

    write_log("You: здаров")              # напечатал
    items = S.tick(at_retry + timedelta(minutes=6, seconds=30))
    assert [i["kind"] for i in items] == ["again"], (
        "напечатанный ответ снова не считается возвращением: %r" % items)
    assert "пока вас не было" in clean._phrase(items)


def test_the_presence_mark_cannot_break_the_window(clean, monkeypatch):
    """Правка живёт в файле окна, поэтому обязана быть безвредной: если отметка
    падает, окно должно работать как раньше. Иначе цена правки — не контрольная
    сумма, а неработающий Джарвис."""
    import core.scheduler as sched
    write_log = _bare_log()

    def boom(*a, **k):
        raise RuntimeError("отметка сломалась")

    monkeypatch.setattr(sched, "note_owner_spoke", boom)
    write_log("You: здаров")              # не должно бросить наружу


# -- Петля: модель слышит саму себя -----------------------------------------

def test_the_model_cannot_schedule_the_reminder_it_just_announced(clean,
                                                                 monkeypatch):
    """ЖИВАЯ ПРОБА ВЛАДЕЛЬЦА 23.08.2026 — БЕСКОНЕЧНАЯ ПЕТЛЯ, и создал её я.

    Из его терминала, дословно:

        [Reminder] Firing: main выпить воды
        [JARVIS] reminder {time:+1, action:set, message:выпить воды}
        [Reminder] Firing: main выпить воды
        [JARVIS] reminder {time:+1, action:set, message:выпить воды}
        ... каждую минуту, БЕЗ ЕДИНОЙ реплики владельца

    Механизм: когда напоминание срабатывает, модели уходит приказ «Немедленно
    скажи мне вслух следующее напоминание: выпить воды». Модель прочитала его как
    ПРОСЬБУ поставить напоминание. Подтолкнула её к этому МОЯ ЖЕ правка подсказки
    от 22.08.2026 («Call the tool immediately»).

    Подсказку я поправил, но сторож стоит на КОДЕ: инструкция модели — это
    вероятность, а не логика, и проверить её тестом нельзя.
    """
    clock = _clock(monkeypatch, 800_000.0)
    S.reset_for_tests()

    S.arm("выпить воды", _now() - timedelta(seconds=5))
    assert [i["kind"] for i in S.tick()] == ["main"]

    before = len(S.alive(limit=100))
    said = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: said.append(" ".join(str(x) for x in a)))
    for _ in range(4):
        clock[0] += 60
        answer = clean.reminder({"action": "set", "time": "+1",
                                 "message": "выпить воды"})
        assert "just announced" in answer, (
            "эхо приняли за просьбу — петля вернулась: %r" % answer)

    assert len(S.alive(limit=100)) == before, (
        "петля наплодила напоминаний: было %d, стало %d"
        % (before, len(S.alive(limit=100))))
    assert any("эхо" in s for s in said), "отказ прошёл молча"


def test_the_owner_can_still_ask_for_the_same_text_again(clean, monkeypatch):
    """Обратная сторона, и без неё защёлка была бы вредна: настоящая просьба
    владельца обязана проходить, даже если текст ровно тот же.

    Отличие одно и надёжное: владелец физически не мог попросить, не заговорив.
    """
    clock = _clock(monkeypatch, 810_000.0)
    S.reset_for_tests()

    S.arm("выпить воды", _now() - timedelta(seconds=5))
    S.tick()

    clock[0] += 30
    S.note_owner_spoke()                      # владелец заговорил
    answer = clean.reminder({"action": "set", "time": "+1",
                             "message": "выпить воды"})
    assert "Reminder set" in answer, (
        "просьбу владельца приняли за эхо: %r" % answer)
    fresh = [r for r in S.alive(limit=100) if not int(r["main_done"])]
    assert len(fresh) == 1, "новое напоминание не поставлено: %r" % fresh


def test_a_different_text_is_never_treated_as_an_echo(clean, monkeypatch):
    """Защёлка обязана быть узкой. Если она начнёт глотать ЛЮБУЮ постановку
    после срабатывания, владелец потеряет напоминания на ровном месте."""
    clock = _clock(monkeypatch, 820_000.0)
    S.reset_for_tests()

    S.arm("выпить воды", _now() - timedelta(seconds=5))
    S.tick()

    clock[0] += 30                            # владелец МОЛЧИТ
    answer = clean.reminder({"action": "set", "time": "+5",
                             "message": "закрыть окно"})
    assert "Reminder set" in answer, (
        "другой текст приняли за эхо: %r" % answer)


def test_the_echo_latch_forgets_after_its_window(clean, monkeypatch):
    """Через час та же просьба — уже не эхо: столько петля не живёт, а владелец
    вполне может попросить то же самое снова."""
    clock = _clock(monkeypatch, 830_000.0)
    S.reset_for_tests()

    S.arm("выпить воды", _now() - timedelta(seconds=5))
    S.tick()

    clock[0] += S.ECHO_WINDOW_S + 60          # окно вышло, владелец так и молчал
    answer = clean.reminder({"action": "set", "time": "+1",
                             "message": "выпить воды"})
    assert "Reminder set" in answer, (
        "защёлка не отпускает после своего окна: %r" % answer)


# -- «О чём ты мне напоминал?» ----------------------------------------------

def test_the_list_tells_what_was_already_announced(clean, monkeypatch):
    """ЖИВАЯ ПРОБА ВЛАДЕЛЬЦА 23.08.2026:

        You:    ты случайно не должен был мне напомнить кое что?
        Jarvis: Нет активных напоминаний, сэр.

    А напоминал — за пять минут до этого, про воду. Ответ был формально верен и
    по сути бесполезен: владелец спрашивал не «что ждёт», а «о чём ты говорил».

    Данные при этом лежали целыми: блок 10 нарочно ничего не удаляет. Не хватало
    не данных, а ДВЕРИ к ним.
    """
    clock = _clock(monkeypatch, 840_000.0)
    S.reset_for_tests()

    S.arm("выпить воды", _now() - timedelta(seconds=5))
    S.tick()                                  # прозвучало
    clock[0] += 20
    S.note_owner_spoke()
    clock[0] += 30
    S.tick(_now() + timedelta(seconds=50))    # закрылось

    tomorrow = (_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    clean.reminder({"action": "set", "date": tomorrow, "time": "10:00",
                    "message": "позвонить маме"})

    answer = clean.reminder({"action": "list"})
    assert "позвонить маме" in answer, "ждущее напоминание пропало: %r" % answer
    assert "выпить воды" in answer, (
        "про уже сказанное молчит — владелец опять услышит «нет активных»: %r"
        % answer)
    assert "announced" in answer.lower(), (
        "сказанное не отделено от ждущего, их будет не различить: %r" % answer)


def test_the_list_is_still_honest_when_there_is_nothing(clean):
    """Пустой список обязан остаться пустым: сторожа оффлайн-ядра проверяют эту
    строку дословно, и владельцу нельзя показывать вчерашнее как сегодняшнее."""
    S.reset_for_tests()
    assert clean.reminder({"action": "list"}) == "No active reminders, sir."


def test_the_announced_list_never_shows_what_was_not_said(clean, monkeypatch):
    """ПРОПУСК ПОРЧИ 23.08.2026: я убрал из выборки условие `main_done=1`, и ни
    один сторож не покраснел. То есть в «уже напоминал» могло попасть то, чего
    Джарвис НЕ ГОВОРИЛ.

    ПЕРВАЯ ПОПЫТКА ЭТОГО СТОРОЖА ТОЖЕ НЕ ПОЙМАЛА, и разобрался я замером, а не
    рассуждением: я брал просроченное на восемь суток, а оно ВЫПАДАЕТ из
    двенадцатичасового окна выборки — значит проверить фильтр этот случай не мог
    в принципе. Нужен случай ВНУТРИ окна, и он самый обычный: напоминание,
    которое ещё не наступило.

    Чем это плохо живьём: владелец спрашивает «ты мне напоминал?», слышит «да,
    про воду» — и считает вопрос закрытым. А Джарвис ещё ничего не говорил. Это
    ровно то враньё, ради борьбы с которым блок 10 и делался: пара (`state`,
    `main_done`) нарочно хранит разницу между «сказано» и «не сказано».
    """
    clock = _clock(monkeypatch, 860_000.0)
    S.reset_for_tests()

    # Срок ещё НЕ НАСТУПИЛ: main_done=0, но в окно выборки строка попадает.
    S.arm("ещё не звучало", _now() + timedelta(hours=2))
    row = S.alive()[0]
    assert int(row["main_done"]) == 0, row

    answer = clean.reminder({"action": "list"})
    assert "Active reminders" in answer, answer
    assert "ещё не звучало" in answer, "ждущее напоминание пропало: %r" % answer
    announced = answer.split("Already announced")[1] if "Already announced" in answer else ""
    assert "ещё не звучало" not in announced, (
        "в «уже напоминал» попало то, чего Джарвис НЕ ГОВОРИЛ: %r" % answer)


def test_the_announced_list_does_not_reach_back_forever(clean, monkeypatch):
    """Сказанное неделю назад — не ответ на «о чём ты мне напоминал». Иначе через
    месяц список станет свалкой, и владелец перестанет его слушать."""
    clock = _clock(monkeypatch, 850_000.0)
    S.reset_for_tests()

    old = S.arm("древнее дело", _now() - timedelta(days=3))
    writer.write(lambda c: c.execute(
        "UPDATE mx_reminder SET main_done=1, state=? WHERE rem_id=?",
        (S.STATE_DONE, old["rem_id"])))

    assert clean.reminder({"action": "list"}) == "No active reminders, sir.", (
        "трёхдневной давности попало в «о чём напоминал»")


# -- Форма и правила -------------------------------------------------------

def test_the_frozen_signature_is_untouched():
    """main.py заморожен: он зовёт check_and_fire() без аргументов и ждёт ОДНУ
    строку или None, а результат отдаёт в _deliver_reminder."""
    import inspect
    from actions import reminder as R

    sig = inspect.signature(R.check_and_fire)
    assert not sig.parameters, (
        "у check_and_fire появились аргументы — main.py её так не зовёт")

    src = (ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
    assert "msg = check_and_fire()" in src, (
        "main.py зовёт напоминания иначе — сторож смотрит не туда")

    sig2 = inspect.signature(R.reminder)
    assert list(sig2.parameters) == ["parameters", "response", "player",
                                     "session_memory"], (
        "поверхность инструмента изменилась: её зовут оффлайн-ядро и агент")


def test_the_reminder_module_never_writes_json_state():
    """Напоминания больше не живут файлом. Если запись файла вернётся, вернутся
    и все шесть замеренных дефектов сразу.

    ПЕРВАЯ ВЕРСИЯ ЭТОГО СТОРОЖА ЗАПРЕЩАЛА СЛОВО `write_text` ЦЕЛИКОМ и покраснела
    на законной записи — отметке о переносе (`reminders.json.imported`, один
    байт даты, пишется один раз за жизнь сборки). Запрет по слову не различает
    «пишет напоминания» и «пишет отметку», поэтому проверяем СТРУКТУРУ: ни одна
    запись в файл не имеет права идти в путь самих напоминаний.
    """
    import ast
    src = (ROOT / "actions" / "reminder.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    assert "def _save_reminders" not in src, (
        "вернулась старая запись файла напоминаний")
    assert "json.dumps" not in src, (
        "напоминания снова превращают себя в JSON для записи")

    allowed = {"LEGACY_MARK"}          # только отметка о переносе
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        if fn.attr not in ("write_text", "write_bytes", "open"):
            continue
        target = fn.value
        name = target.id if isinstance(target, ast.Name) else ast.dump(target)
        assert name in allowed, (
            "запись в файл через %s.%s — напоминания снова хранятся файлом"
            % (name, fn.attr))


def test_reminders_go_through_the_one_desk():
    """Д41: все записи через одну кассу. Своё соединение здесь означало бы
    второго писателя и потерю правки — тот дефект, который блок и лечит."""
    src = (ROOT / "core" / "scheduler.py").read_text(encoding="utf-8")
    assert "writer.write" in src, "планировщик пишет не через кассу"
    assert "sqlite3.connect" not in src, (
        "планировщик открывает своё соединение — это второй писатель")
    assert "writer.reader()" in src, (
        "чтение пошло через кассу — холостой тик начнёт занимать замок")


def test_the_idle_tick_never_takes_the_write_lock(clean):
    """ПРОПУСК ПОРЧИ 22.08.2026: я перевёл холостой тик на кассу записи, и ни
    один сторож не покраснел — прежний смотрел на ТЕКСТ файла, а текст я как раз
    и менял. Сторож по тексту не видит поведения.

    Почему это важно живьём: тик ходит раз в 30 секунд, круглые сутки. Если он
    берёт замок записи, то раз в полминуты встаёт в очередь с речью владельца и
    с фоновым извлекателем памяти. Замер холостого тика — 0,0055 мс, и он
    держится ровно потому, что чтение идёт МИМО кассы.

    Проверяем поведение: пока идёт тик, замок кассы обязан быть свободен.
    """
    S.arm("будущее", _now() + timedelta(days=1))

    seen = []
    real = writer.transaction

    def watched(*a, **k):
        seen.append(True)
        return real(*a, **k)

    # 1. Холостой тик: ничего не пора -> касса не открывается ни разу.
    writer.transaction = watched
    try:
        assert S.tick() == [], "тик что-то отдал, хотя ничего не пора"
    finally:
        writer.transaction = real
    assert not seen, "ХОЛОСТОЙ тик взял замок записи %d раз" % len(seen)

    # 2. И сама выборка не держит замок в момент чтения.
    assert not writer._LOCK.locked(), "после тика замок остался занятым"
    S.due_now()
    assert not writer._LOCK.locked(), "чтение сроков держит замок записи"


def test_the_whole_salvo_is_marked_in_one_transaction(clean):
    """ПРОПУСК ПОРЧИ 22.08.2026: я разбил отметку залпа на сделку-на-строку, и
    сторожа промолчали — они проверяли РЕЗУЛЬТАТ («все отмечены»), а он одинаков
    при любом числе сделок.

    Почему число сделок важно: старый код на два просроченных говорил первое и
    УДАЛЯЛ ОБА. Если отмечать по сделке на строку, падение посреди залпа отметит
    часть — и часть напоминаний исчезнет так же молча, только реже. Одна сделка
    на весь залп значит: либо все отмечены, либо ни одно, и тогда следующий тик
    через 30 секунд повторит попытку целиком.
    """
    for i in range(5):
        S.arm("залп-%d" % i, _now() - timedelta(minutes=i + 1))

    before = writer.stats().get("writes", 0)
    items = S.tick()
    after = writer.stats().get("writes", 0)

    assert len(items) == 5, "тик отдал %d из 5" % len(items)
    assert after - before == 1, (
        "залп из 5 напоминаний отмечен %d сделками вместо одной — падение "
        "посреди залпа потеряет часть" % (after - before))


def test_the_owner_spoke_mark_is_actually_wired_to_the_dialogue(clean,
                                                               monkeypatch):
    """ПРОПУСК ПОРЧИ 22.08.2026, и это была САМАЯ ОПАСНАЯ из четырёх.

    Я выкинул вызов `scheduler.note_owner_spoke()` из memory_manager — и все
    сторожа остались зелёными, потому что каждый ставил отметку САМ, вручную.
    То есть проверялось «повтор слушается отметки», но НЕ проверялось, что
    отметку вообще кто-то ставит. Живьём это значило бы: повтор звучит всегда, а
    выбор владельца («только при тишине») тихо не работает.

    Здесь проверяется ПРОВОД: ту же функцию, что зовёт main.py на каждой реплике,
    и после неё отметка обязана появиться.
    """
    from core import aux_model
    from memory import memory_manager as mm

    S.reset_for_tests()
    assert not S.owner_spoke_since(60), "отметка стоит до начала разговора"

    # Дверь к облаку затыкаем НАСТОЯЩУЮ. Первая версия этого теста подменяла не
    # тот шов, и он ушёл в сеть по-настоящему — в выводе прогона был живой ответ
    # Google «API key not valid». Тест, который звонит наружу, недопустим: он
    # медленный, зависит от чужого сервера и тратит квоту владельца.
    called = []

    def no_cloud(*a, **k):
        called.append(True)
        return False, "офлайн"

    monkeypatch.setattr(aux_model, "aux_call", no_cloud)
    monkeypatch.setattr(mm, "aux_call", no_cloud, raising=False)

    # Ровно то, что зовёт main.py на каждой реплике владельца.
    mm.should_extract_memory("напомни мне позвонить", "хорошо", "no-key")

    assert S.owner_spoke_since(60), (
        "после реплики владельца отметки нет — повтор перестанет её слушаться, "
        "и выбор «повторять только при тишине» не работает")
