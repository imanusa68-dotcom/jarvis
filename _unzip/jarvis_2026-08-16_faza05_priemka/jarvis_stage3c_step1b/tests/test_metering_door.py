# -*- coding: utf-8 -*-
"""Фаза 1, блок 5, шаг 5.2 — учёт подключён к двери.

Здесь живёт `test_metering_no_bypass` — имя из плана, но в
ПЕРЕФОРМУЛИРОВАННОМ виде. Почему именно так, написано в самом тесте: план
предлагает обернуть одну дверь, а проверка 18.08.2026 нашла ещё пять мест,
и одно из них — живая голосовая сессия, где «один вызов» не определён.
"""
import io
import sys
import tempfile
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import metering as mt
from core import store


@pytest.fixture()
def db():
    """База ДОМА этого теста, а не боковая.

    После блока 7 запись идёт через кассу, а касса открывает дом. Боковая
    база означала бы, что дверь пишет в одно место, а тест смотрит в другое,
    и тест был бы зелёным ни о чём.
    """
    conn = store.open_store()
    yield conn
    conn.close()


def _rows(conn):
    return conn.execute("SELECT * FROM mx_meter_call").fetchall()


def _day(conn):
    return conn.execute("SELECT * FROM mx_meter_day").fetchall()


def _spent_now(conn):
    return conn.execute("SELECT count(*) FROM mx_meter_call").fetchone()[0]


class _Reply:
    def __init__(self, text):
        self.text = text


def _code_only(path: Path) -> str:
    with io.open(path, "rb") as fh:
        try:
            return " ".join(t.string for t in tokenize.tokenize(fh.readline)
                            if t.type not in (tokenize.COMMENT, tokenize.STRING))
        except (tokenize.TokenError, SyntaxError):
            return ""


# -- Инвариант I16 --------------------------------------------------------

# Сессионные и осознанно отложенные пути. Каждый со своей причиной: список
# без причин через месяц превращается в разрешение на что угодно.
ALLOWED_BYPASS = {
    "main.py":
        "живой голос: сессия, а не вызов; по реестру суток без лимита",
    "core/screen_live_session.py":
        "Screen View runtime отключён (close 1007) — учитывать нечего",
    "core/screen_live_runtime.py":
        "то же: runtime отключён до ревизии этапа 5",
    "actions/screen_processor.py":
        "живая сессия зрения; достижима только с агентами (сейчас OFF)",
    "core/provider/gemini.py":
        "слой поставщика: единственный законный импорт SDK",
}

SDK_NEEDLES = ("generate_content", "genai.Client", "models.generate")


def test_metering_no_bypass():
    """Имя из плана (I16), НО В ПЕРЕФОРМУЛИРОВАННОМ ВИДЕ — и это осознанно.

    План говорит «ни один вызов модели не проходит мимо метеринга» и
    предлагает обернуть `core/aux_model`. Проверка 18.08.2026 показала, что
    этого НЕДОСТАТОЧНО: модель зовут ещё из пяти мест. Живой голос — это
    WebSocket-сессия, внутри которой модель отвечает много раз, и «один
    вызов» там не определён: одна сессия это один вызов или сто? Плюс по
    реестру у живого голоса суток без ограничения, то есть дефицитный ресурс
    он не расходует вообще. Считать его наравне с разовыми — значит считать
    не то, а счётчик, который считает не то, вреднее отсутствующего:
    владелец услышит «осталось мало» при полном запасе.

    Поэтому проверяем: НИ ОДИН РАЗОВЫЙ вызов не идёт мимо учёта, а
    сессионные пути перечислены поимённо и с причиной.
    """
    paths = [ROOT / n for n in ("main.py", "ui.py", "consent_mode.py")]
    for folder in ("core", "agent", "actions", "memory"):
        base = ROOT / folder
        if base.exists():
            paths += [p for p in sorted(base.rglob("*.py"))
                      if "__pycache__" not in p.parts]
    guilty = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED_BYPASS:
            continue
        # Смотрим на КОД без комментариев и строк. Сторож, который ищет
        # запретное слово в тексте, находит САМ СЕБЯ — в объяснении, почему
        # это слово запрещено. Четвёртый случай в проекте: доктор с sqlite3,
        # mx_task_check, task_context с quota_day, и здесь aux_model, где
        # generate_content стоит в собственной шапке.
        code = _code_only(path)
        for needle in SDK_NEEDLES:
            if needle in code:
                guilty.append(f"{rel}: {needle}")
    assert not guilty, (
        "разовый вызов модели мимо учёта: " + "; ".join(guilty)
        + " — либо через aux_call, либо в ALLOWED_BYPASS с причиной")


def test_every_allowed_bypass_still_exists():
    """Сторож на сторожа: если файл из списка исключений исчез или перестал
    звать модель, исключение обязано уйти вместе с ним. Иначе список станет
    разрешением на будущее, которого никто не заметит."""
    stale = []
    for rel in ALLOWED_BYPASS:
        path = ROOT / rel
        if not path.exists():
            stale.append(rel + " (нет файла)")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not any(n in text for n in SDK_NEEDLES + ("live.connect",)):
            stale.append(rel + " (больше не зовёт модель)")
    assert not stale, "список исключений устарел: " + "; ".join(stale)


# -- Дверь считает --------------------------------------------------------

def test_the_door_reserves_before_and_commits_after(db, monkeypatch):
    """Резерв ДО обращения к модели: вызов может не вернуться, а квоту
    Google уже списал."""
    from core import aux_model
    seen = []

    def _gen(model, contents, key):
        seen.append(_spent_now(db))
        return _Reply("готово")

    monkeypatch.setattr(aux_model, "_generate", _gen)
    monkeypatch.setattr(aux_model, "_sweep_lost_once", lambda: None)
    ok, text = aux_model.aux_call("вопрос", "key", model="m", caller="Тест")
    assert ok and text == "готово"
    assert seen == [1], "резерв не взят ДО обращения к модели"
    row = _rows(db)[0]
    assert row["ok"] == 1 and row["out_tokens"] == len("готово")
    assert _day(db)[0]["calls_n"] == 1


def test_the_door_records_a_failure_too(db, monkeypatch):
    from core import aux_model

    def _boom(model, contents, key):
        raise RuntimeError("сеть пропала")

    monkeypatch.setattr(aux_model, "_generate", _boom)
    monkeypatch.setattr(aux_model, "_sweep_lost_once", lambda: None)
    ok, _why = aux_model.aux_call("вопрос", "key", model="m", caller="Тест")
    assert ok is False
    assert _rows(db)[0]["ok"] == 0
    assert _day(db)[0]["fail_n"] == 1


def test_the_door_refuses_out_loud_when_the_cap_is_reached(db, monkeypatch):
    """I19: исчерпание никогда не молчаливое — и модель при этом НЕ зовётся.

    Печать ловим подменой `_out`, а не через capsys: в этом проекте слой
    захвата вывода отключён настройкой `-p no:capture` (обход сломанного
    pyreadline на этой машине), поэтому фикстуры capsys просто нет.
    """
    from core import aux_model
    said = []
    monkeypatch.setattr(aux_model, "_out", said.append)
    called = []
    monkeypatch.setattr(aux_model, "_generate",
                        lambda m, c, k: called.append(1) or _Reply("нельзя"))
    monkeypatch.setattr(aux_model, "_sweep_lost_once", lambda: None)
    monkeypatch.setattr(mt, "caps", lambda: {mt.PAID_BUCKET: 0,
                                             mt.CHEAP_BUCKET: 0})
    ok, why = aux_model.aux_call("вопрос", "key", model="m", caller="Тест")
    assert ok is False and why.startswith("[quota-cap:")
    assert not called, "потолок исчерпан, а модель всё равно позвали"
    assert any("исчерпан" in line for line in said), said


def test_a_broken_meter_does_not_stop_the_call(monkeypatch):
    """Учёт не важнее дела: молчаливый отказ работать страшнее неучтённого
    вызова."""
    from core import aux_model
    monkeypatch.setattr(aux_model, "_generate", lambda m, c, k: _Reply("жив"))
    monkeypatch.setattr(aux_model, "_sweep_lost_once", lambda: None)

    from core import writer

    def _no_db(fn, **kw):
        raise RuntimeError("нет базы")

    monkeypatch.setattr(writer, "write", _no_db)
    mt.reset_for_tests()
    ok, text = aux_model.aux_call("вопрос", "key", model="m", caller="Тест")
    assert ok is True and text == "жив"


def test_the_role_decides_which_bucket_pays(db, monkeypatch):
    from core import aux_model
    monkeypatch.setattr(aux_model, "_generate", lambda m, c, k: _Reply("ок"))
    monkeypatch.setattr(aux_model, "_sweep_lost_once", lambda: None)
    aux_model.aux_call("q", "key", model="m", role="aux_cheap")
    assert _rows(db)[0]["bucket"] == mt.CHEAP_BUCKET


def test_the_task_number_reaches_the_meter_through_the_door(db, monkeypatch):
    from core import aux_model
    from core.task_context import TaskCtx
    monkeypatch.setattr(aux_model, "_generate", lambda m, c, k: _Reply("ок"))
    monkeypatch.setattr(aux_model, "_sweep_lost_once", lambda: None)
    aux_model.aux_call("q", "key", model="m",
                       ctx=TaskCtx(run_id="R1", task_id="T-20260818-003",
                                   bucket="task"))
    assert _rows(db)[0]["task_id"] == "T-20260818-003"


def test_the_lost_sweep_happens_once_per_run(monkeypatch):
    """Чаще раза за запуск — это тот самый баг, что убивал живой резерв
    соседнего потока."""
    from core import aux_model
    calls = []
    monkeypatch.setattr(mt, "close_lost", lambda **kw: calls.append(1))
    aux_model.reset_sweep_for_tests()
    for _ in range(4):
        aux_model._sweep_lost_once()
    aux_model.reset_sweep_for_tests()
    assert len(calls) == 1, "чистка потерянных зовётся чаще раза за запуск"


# -- Старое удалено, а не выключено (правило 9) ---------------------------

def test_the_old_memory_counter_is_gone():
    """Он считал по МЕСТНОЙ дате, а квотные сутки у поставщика свои. Два
    счётчика с разными сутками расходятся раз в день на несколько часов, и
    понять, какой прав, невозможно. Плюс он не видел ни один другой вызов в
    проекте, то есть на «сколько я потратил» отвечал неверно всегда."""
    for rel in ("memory/memory_manager.py", "memory/personality_engine.py"):
        code = _code_only(ROOT / rel)
        for needle in ("_api_allowed", "_DAILY_CALL_LIMIT", "_usage_path"):
            assert needle not in code, f"{rel}: старый счётчик жив ({needle})"


def test_the_duplicate_vision_door_is_gone():
    """В screen_share_manager лежала КОПИЯ двери: свой клиент SDK, своя
    проверка остывания, свой разбор 429 — шестьдесят строк. Пока копия жива,
    вызов через неё не попадает в учёт, и I16 — ложь."""
    src = (ROOT / "core" / "screen_share_manager.py").read_text(encoding="utf-8")
    assert "genai" not in src, "SDK вернулся в обход слоя поставщика"
    assert "generate_content" not in src
    assert "aux_call" in src, "файл перестал звать общую дверь"


# -- Правда вслух ---------------------------------------------------------

def test_the_owner_hears_numbers_not_percents(db, monkeypatch):
    from core import offline_core
    for _ in range(3):
        got = mt.reserve("aux_light", conn=db)
        mt.commit(got["call_id"], conn=db)
    reply = offline_core._route_quota("сколько осталось квоты", "", None, 0)
    assert "3 из 120" in reply.text, reply.text
    assert "117" in reply.text
    assert "%" not in reply.text, "проценты вместо чисел"


def test_the_owner_hears_when_the_balance_is_unknown(monkeypatch):
    """Выдуманное число хуже отсутствующего: по нему принимают решение."""
    from core import offline_core
    monkeypatch.setattr(mt, "remaining",
                        lambda *a, **k: {"known": False, "limit": None})
    reply = offline_core._route_quota("квота", "", None, 0)
    assert "не могу" in reply.text.lower(), reply.text


def test_the_old_confession_phrase_is_gone():
    """Две фразы про одно и то же — это вопрос «какая из них сегодня правда».

    Смотрим НЕ на текст файла, а на саму таблицу фраз, которую слышит
    владелец. Пятый раз в проекте сторож нашёл сам себя: фраза стоит в
    объяснении, ПОЧЕМУ её больше нет. Вывод глубже частного случая — искать
    запретную фразу грепом по тексту почти всегда неверно; надо проверять ту
    структуру, которая работает.
    """
    from core import offline_core
    spoken = " ".join(
        part for value in offline_core._PHRASES.values() for part in value)
    assert "счётчика расхода в проекте" not in spoken, "старая фраза жива"
    assert "usage meter yet" not in spoken
    # А новая — на месте, и с числами.
    assert "{spent} из {limit}" in spoken
