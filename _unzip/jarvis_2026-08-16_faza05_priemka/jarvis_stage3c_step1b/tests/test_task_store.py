# tests/test_task_store.py
"""
Сторожа состояний задачи и работы с таблицей (фаза 1, блок 8, шаг 16).

Правило этих тестов: проверяем СВОЙСТВО на настоящей базе и настоящих потоках.
Правило рестарта нельзя проверить внутри одного процесса — для него отдельный
тест с двумя процессами (в конце файла).
"""
from __future__ import annotations

import io
import subprocess
import sys
import threading
import tokenize
from pathlib import Path

import pytest

from agent import task_store as tstore
from core import store
from core import task_state as ts

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db():
    """База ДОМА этого теста: касса открывает дом, и мы смотрим туда же."""
    conn = store.open_store()
    yield conn
    conn.close()


def _states(conn):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT task_id, state FROM mx_task")}


# -- Словарь состояний ----------------------------------------------------

def test_the_three_lookalike_lists_stay_apart():
    """Состояние ЗАДАЧИ, статус ОТЧЁТА и исход ЗАПИСИ — три разных списка с
    тремя разными обязанностями. Кто-нибудь обязательно решит, что это один."""
    from agent.contracts import REPORT_STATUS
    from core.blackbox import OUTCOMES
    assert set(ts.STATES) != set(REPORT_STATUS)
    assert set(ts.STATES) != set(OUTCOMES)
    # У задачи состояния ПРОПИСНЫЕ, у отчёта и записи — строчные. Это не
    # косметика: так их невозможно перепутать глазами в базе.
    assert all(s.isupper() for s in ts.STATES)
    assert all(s.islower() for s in REPORT_STATUS)
    assert all(s.islower() for s in OUTCOMES)


def test_every_state_has_a_transition_rule():
    """Состояние без правила — это состояние, из которого код не знает выхода.
    Пустой кортеж законен (конечное), отсутствие ключа — нет."""
    for state in ts.STATES:
        assert state in ts.LEGAL, f"у состояния {state} нет правила переходов"
    for src, dsts in ts.LEGAL.items():
        assert src in ts.STATES, f"правило для незнакомого состояния {src}"
        for dst in dsts:
            assert dst in ts.STATES, f"переход в незнакомое состояние {dst}"


def test_final_states_are_exactly_the_ones_with_no_way_out():
    """«Конечное» и «переходов нет» — два способа сказать одно. Если они
    разойдутся, уборка начнёт трогать живые задачи или пропускать мёртвые."""
    no_exit = {s for s in ts.STATES if not ts.LEGAL[s]}
    # PARTIAL — конечный ИСХОД, но из него разрешён возврат в очередь
    # (13.4 п.12), поэтому он в FINAL, но выход у него есть.
    assert no_exit <= set(ts.FINAL)
    assert set(ts.FINAL) - no_exit == {ts.PARTIAL}


def test_an_illegal_transition_is_refused_out_loud():
    """Тихо записанное неверное состояние не падает — оно ломает подсчёты и
    приёмку через несколько недель, и найти концы будет негде."""
    with pytest.raises(ts.StateError):
        ts.check(ts.DONE, ts.RUNNING)
    with pytest.raises(ts.StateError):
        ts.check(ts.CANCELLED, ts.VERIFYING)
    with pytest.raises(ts.StateError):
        ts.check("ВЫДУМАННОЕ", ts.QUEUED)
    # А законные проходят молча.
    ts.check(ts.QUEUED, ts.RUNNING)
    ts.check(ts.RUNNING, ts.FAILED)
    ts.check(ts.FROZEN, ts.QUEUED)


def test_a_cancel_reason_is_a_code_not_a_story():
    """Свободный текст в причине означает, что через полгода на вопрос
    «почему задача умерла» будет двести ответов вместо пяти."""
    for good in ts.CANCEL_REASONS:
        ts.check_reason(good)
    ts.check_reason(None)
    with pytest.raises(ts.StateError):
        ts.check_reason("владелец передумал, потому что вспомнил про встречу")


# -- Номер дела ----------------------------------------------------------

def test_the_number_is_issued_in_order_and_matches_the_agreed_shape(db):
    """Формат решён в блоке 3 и стал первичным ключом. Выдача — здесь, потому
    что выдавать должен тот, кто вставляет строку."""
    from core.task_context import today_stamp
    day = today_stamp()
    first = tstore.create("разбери загрузки", supersede=False)
    second = tstore.create("найди отчёт", supersede=False)
    assert first == f"T-{day}-001"
    assert second == f"T-{day}-002"


def test_a_thousandth_number_does_not_wrap_around(db):
    """Соблазн — взять максимум ПО СТРОКЕ одним запросом. Но строки
    сравниваются по знакам: '999' больше, чем '1000'.

    ДЫРА В ЭТОМ ЖЕ СТОРОЖЕ, НАЙДЕННАЯ ПОРЧЕЙ 20.08.2026. Первая версия
    подкладывала только 999 и ждала 1000 — и это проходило ОБА способа, потому
    что при сортировке по строке '999' и есть максимум. Ловушка открывается
    ровно на следующем шаге: когда 1000 уже существует, строковый максимум
    по-прежнему '999', и код выдал бы 1000 второй раз — прямо в первичный ключ.
    """
    from core.task_context import today_stamp
    from core import writer
    from agent.contracts import insert_task
    day = today_stamp()

    def put(seq):
        doc = tstore.build_doc(f"задача {seq}", task_id=f"T-{day}-{seq}")
        writer.write(lambda c: insert_task(
            c, doc, state=ts.QUEUED, priority=2, run_id="r",
            now_utc="2026-08-20T00:00:00+00:00"))

    put("001")
    put("999")
    put("1000")
    nxt = tstore.create("тысяча первая", supersede=False)
    assert nxt == f"T-{day}-1001", (
        f"номер пошёл по кругу: выдан {nxt}, а 1000 уже занят")


def test_recovery_never_touches_a_task_of_the_current_run(db):
    """ДЫРА, НАЙДЕННАЯ ПОРЧЕЙ 20.08.2026.

    Уборка обязана трогать ТОЛЬКО задачи прошлых запусков. Порча убрала
    сравнение с номером запуска — и ни один тест не покраснел: тесты на два
    процесса проверяли чужую задачу, а своей живой у второго процесса не было.

    Цена ошибки живьём: Джарвис объявляет проваленной задачу, которая работает
    у него прямо сейчас, а поток продолжает её делать. Владелец услышит
    «сорвалось» о том, что через минуту будет сделано.
    """
    task_id = tstore.create("моя живая задача", supersede=False)
    taken = tstore.claim()
    assert taken and taken["task_id"] == task_id

    fixed = tstore.recover_after_restart()

    assert fixed == 0, f"уборка тронула {fixed} задач этого запуска"
    row = tstore.get(task_id)
    assert row["state"] == ts.RUNNING, (
        f"свою живую задачу пометили как {row['state']}")
    assert row["cancel_reason"] is None


def test_two_threads_never_get_the_same_number(db):
    """Номер — первичный ключ. Посмотреть «какой последний» и вставить
    следующий двумя действиями значит однажды получить два одинаковых."""
    got, errors = [], []

    def maker(i):
        try:
            got.append(tstore.create(f"цель номер {i}", supersede=False))
        except Exception as exc:                       # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=maker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not [t for t in threads if t.is_alive()], "выдача номеров зависла"
    assert not errors, errors[:3]
    assert len(got) == 8
    assert len(set(got)) == 8, f"номера столкнулись: {sorted(got)}"


# -- Создание и форма ----------------------------------------------------

def test_a_created_task_goes_through_the_contract(db):
    """Вставка живёт только в контракте, и это держит сторож грепом. Здесь
    проверяем следствие: документ в базе прошёл проверку и лежит дословно."""
    import json
    task_id = tstore.create("разбери загрузки по папкам", supersede=False)
    row = db.execute("SELECT * FROM mx_task WHERE task_id=?",
                     (task_id,)).fetchone()
    assert row["state"] == ts.QUEUED
    assert row["attempts"] == 0
    assert row["type"] == tstore.FREE_GOAL
    assert row["run_id"], "номер запуска не записан — правило рестарта нечем проверить"
    doc = json.loads(row["payload_json"])
    assert doc["goal"] == "разбери загрузки по папкам"
    assert doc["schema_ver"] == 1 and doc["depth"] == 0


def test_a_long_goal_is_trimmed_instead_of_refused(db):
    """Не принять задачу из-за длинной фразы значит наказать владельца за то,
    что он подробно объяснил. Но и потерять хвост молча нельзя."""
    import json
    task_id = tstore.create("я" * 900, supersede=False)
    doc = json.loads(db.execute(
        "SELECT payload_json FROM mx_task WHERE task_id=?",
        (task_id,)).fetchone()[0])
    assert len(doc["goal"]) <= 500
    assert doc["goal"].endswith("..."), "обрезка не видна"
    assert len(doc["title"]) <= 80


def test_the_form_key_holds_a_fingerprint_and_never_the_speech(db):
    """У колонки формы другой срок жизни, чем у речи владельца. Речи в ней
    быть не должно вовсе."""
    task_id = tstore.create("разбери мои личные загрузки", supersede=False)
    form = db.execute("SELECT form_key FROM mx_task WHERE task_id=?",
                      (task_id,)).fetchone()[0]
    assert "загрузки" not in form and "личные" not in form
    assert len(form) == 16


# -- Вытеснение той же формы (Д18) ---------------------------------------

def test_the_same_form_asked_twice_supersedes_the_one_still_waiting(db):
    """Д18. Обе задачи живы в момент перехода — именно поэтому в схеме нет
    уникального индекса по форме (решение блока 2)."""
    first = tstore.create("разбери загрузки")
    second = tstore.create("Разбери   ЗАГРУЗКИ")      # та же форма
    states = _states(db)
    assert states[first] == ts.SUPERSEDED
    assert states[second] == ts.QUEUED
    reason = db.execute("SELECT cancel_reason FROM mx_task WHERE task_id=?",
                        (first,)).fetchone()[0]
    assert reason == "superseded"


def test_a_task_already_running_is_never_superseded(db):
    """Задача в работе уже что-то сделала на диске. Молча забыть об этом
    значит потерять след — она доработает, а вытеснение достанется очереди."""
    first = tstore.create("разбери загрузки")
    claimed = tstore.claim()
    assert claimed and claimed["task_id"] == first
    second = tstore.create("разбери загрузки")        # та же форма
    states = _states(db)
    assert states[first] == ts.RUNNING, "вытеснили задачу, которая работает"
    assert states[second] == ts.QUEUED


def test_a_different_goal_does_not_supersede(db):
    first = tstore.create("разбери загрузки")
    second = tstore.create("найди отчёт за июль")
    states = _states(db)
    assert states[first] == ts.QUEUED and states[second] == ts.QUEUED


# -- Взятие в работу -----------------------------------------------------

def test_only_one_thread_can_claim_the_same_task(db):
    """Второй поток обязан получить ноль изменённых строк, а не вторую копию
    той же работы. Тот же приём, что у талонов согласия.

    ДЫРА В ЭТОМ СТОРОЖЕ, НАЙДЕННАЯ ПОРЧЕЙ 20.08.2026. Порча убрала из правки
    условие «...И состояние всё ещё то, которое мы прочитали» — и тест остался
    зелёным. Причина: касса записи (блок 7) выстраивает писателей в очередь,
    поэтому шести потокам просто не удаётся столкнуться, и сторожить условие
    было нечем.

    Теперь условие проверяется НАПРЯМУЮ: два взятия одной и той же задачи из
    одного состояния, второе обязано не сработать. Это ровно то, что защищает
    от двойной работы, когда очередь кассы однажды пропадёт (например, если
    задачи начнут брать из двух процессов).
    """
    task_id = tstore.create("одна задача", supersede=False)

    # Прямая проверка правила: из одного и того же состояния взять дважды
    # нельзя. Первое взятие проходит, второе обязано отказать.
    from core import writer
    from agent import task_store as tsx
    assert writer.write(
        lambda c: tsx._move(c, task_id, ts.QUEUED, ts.RUNNING) or True)
    with pytest.raises(tsx.TaskStoreError):
        writer.write(lambda c: tsx._move(c, task_id, ts.QUEUED, ts.RUNNING))

    # И то же самое на потоках: победитель ровно один.
    second = tstore.create("вторая задача", supersede=False)
    assert second
    got, errors = [], []

    def claimer():
        try:
            got.append(tstore.claim())
        except Exception as exc:                       # noqa: BLE001
            errors.append(str(exc))

    threads = [threading.Thread(target=claimer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors[:3]
    winners = [g for g in got if g]
    assert len(winners) == 1, f"задачу взяли {len(winners)} раз"
    assert winners[0]["task_id"] == second


def test_the_cap_on_simultaneous_tasks_is_enforced_inside_the_transaction(db,
                                                                         monkeypatch):
    """Потолок считается ЗДЕСЬ ЖЕ, иначе два потока оба увидят «место есть»."""
    monkeypatch.setattr(tstore, "max_alive", lambda: 2)
    for i in range(5):
        tstore.create(f"цель {i}", supersede=False)
    taken = [tstore.claim() for _ in range(5)]
    running = [t for t in taken if t]
    assert len(running) == 2, f"взято {len(running)} задач при потолке 2"
    assert db.execute("SELECT count(*) FROM mx_task WHERE state=?",
                      (ts.RUNNING,)).fetchone()[0] == 2


def test_claiming_picks_the_highest_priority_first(db):
    """Индекс по состоянию и приоритету заведён в блоке 2 ровно под этот
    запрос: «дай следующую»."""
    low = tstore.create("неважная", priority=3, supersede=False)
    high = tstore.create("важная", priority=1, supersede=False)
    got = tstore.claim()
    assert got and got["task_id"] == high, "взяли не самую важную"
    assert low


def test_a_task_that_used_up_its_attempts_fails_instead_of_looping(db):
    """Потолок повторов держится колонкой, потому что сама задача неизменяема.
    Провал честнее вечного кружения."""
    task_id = tstore.create("упрямая", supersede=False)
    from core import writer
    writer.write(lambda c: c.execute(
        "UPDATE mx_task SET attempts=? WHERE task_id=?",
        (tstore.MAX_ATTEMPTS, task_id)))
    assert tstore.claim() is None
    row = db.execute("SELECT state, cancel_reason FROM mx_task WHERE task_id=?",
                     (task_id,)).fetchone()
    assert row["state"] == ts.FAILED and row["cancel_reason"] == "error"


# -- Закрытие ------------------------------------------------------------

def test_finishing_writes_the_time_and_keeps_the_owner_decision(db):
    task_id = tstore.create("цель", supersede=False)
    tstore.claim()
    assert tstore.finish(task_id, ts.DONE) is True
    row = db.execute("SELECT state, finished_utc FROM mx_task WHERE task_id=?",
                     (task_id,)).fetchone()
    assert row["state"] == ts.DONE and row["finished_utc"]
    # Второй раз — не ошибка, но и не переписывание: задача уже закрыта.
    assert tstore.finish(task_id, ts.FAILED) is False
    assert db.execute("SELECT state FROM mx_task WHERE task_id=?",
                      (task_id,)).fetchone()[0] == ts.DONE


def test_an_illegal_finish_changes_nothing(db):
    task_id = tstore.create("цель", supersede=False)
    # Из очереди нельзя сразу в «принимается»: там ещё нечего принимать.
    assert tstore.move(task_id, ts.QUEUED, ts.VERIFYING) is False
    assert db.execute("SELECT state FROM mx_task WHERE task_id=?",
                      (task_id,)).fetchone()[0] == ts.QUEUED


# -- Чтение --------------------------------------------------------------

def test_the_owner_can_be_told_what_is_alive(db):
    """Заголовок задачи описан в схеме как «то, чем её назовёт голос при
    перечислении». Значит перечисление обязано существовать.

    Первая версия этого теста пыталась закрыть как «выполнено» задачу, которая
    ни разу не работала, — и автомат состояний ОТКАЗАЛ, по делу: из очереди
    нельзя попасть в «выполнено», там ещё нечего выполнять. Тест был неверен,
    автомат прав. Теперь закрываем ту задачу, которую действительно взяли.
    """
    tstore.create("первая цель", supersede=False)
    tstore.create("вторая цель", supersede=False)
    taken = tstore.claim()
    assert taken, "задачу не взяли — тест проверял бы пустоту"
    assert tstore.finish(taken["task_id"], ts.DONE) is True

    names = [t["title"] for t in tstore.alive()]
    assert taken["title"] not in names, "закрытая задача попала в живые"
    assert len(names) == 1, f"в живых осталось не то: {names}"
    assert len(tstore.recent()) >= 2


def test_the_call_count_comes_from_the_meter_not_from_a_second_column(db):
    """Две копии одного числа рано или поздно разойдутся — это решение блока 1,
    записанное в комментарии миграции 7."""
    task_id = tstore.create("цель", supersede=False)
    assert tstore.calls_spent(task_id) == 0
    from core import writer
    writer.write(lambda c: c.execute(
        "INSERT INTO mx_meter_call (call_id, quota_day, role, model_name, "
        "bucket, ok, started_utc, task_id) VALUES "
        "('c-1','2026-08-19','aux_light','м','task',1,'2026-08-19T00:00:00Z',?)",
        (task_id,)))
    assert tstore.calls_spent(task_id) == 1
    cols = [r[1] for r in db.execute("PRAGMA table_info(mx_task)")]
    assert not any("call" in c for c in cols), "в задаче завелась своя копия"


# -- Структурные сторожа -------------------------------------------------

def test_the_store_writes_tasks_only_through_the_contract():
    """Дверь одна. Проверяем КОД токенизатором: слово «вставка» в объяснении,
    почему её здесь нет, красить сторожа не должно."""
    path = ROOT / "agent" / "task_store.py"
    with io.open(path, "rb") as fh:
        code = " ".join(t.string for t in tokenize.tokenize(fh.readline)
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "insert_task" in code, "запись задач идёт не через контракт"
    assert "BEGIN" not in code, "у работы с задачами завелась своя транзакция"
    assert "open_store" not in code, "база открывается мимо кассы"


def test_the_store_keeps_no_lock_of_its_own():
    """Два замка вокруг одной базы — разный порядок захвата, то есть мёртвая
    хватка. Очередь работает под замком кассы и своего не имеет."""
    assert not hasattr(tstore, "_LOCK")
    assert not hasattr(tstore, "_lock")


# -- Правило рестарта: ДВА НАСТОЯЩИХ ПРОЦЕССА ----------------------------

WORKER = r'''
import os, sys
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
from agent import task_store as tstore
from core import task_context, task_state as ts
{body}
'''


def _run(body, home):
    import os
    code = WORKER.format(root=str(ROOT), body=body)
    env = dict(os.environ, JARVIS_STATE_DIR=str(home),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, proc.stderr[-1200:]
    return proc.stdout


def test_a_task_running_in_a_previous_run_becomes_failed_and_never_resumes(db,
                                                                          tmp_path):
    """I15, и проверить это внутри одного процесса НЕВОЗМОЖНО: «прошлая жизнь»
    — это буквально другой процесс.

    Возобновления нет нарочно: задача могла успеть сделать половину работы на
    диске, и начать заново значит сделать половину дважды.
    """
    home = tmp_path / "дом"
    home.mkdir()

    out = _run("""
tid = tstore.create("долгая работа", supersede=False)
got = tstore.claim()
print("RUN1", tid, got["task_id"], task_context.run_id())
""", home)
    assert "RUN1" in out
    tid = out.split("RUN1")[1].split()[0]

    out2 = _run(f"""
fixed = tstore.recover_after_restart()
row = tstore.get({tid!r})
print("RUN2", fixed, row["state"], row["cancel_reason"])
after = tstore.claim()
print("CLAIM", after)
""", home)
    line = [l for l in out2.splitlines() if l.startswith("RUN2")][0]
    _, fixed, state, reason = line.split()
    assert fixed == "1", f"уборка не тронула брошенную задачу: {line}"
    assert state == ts.FAILED, f"задача осталась в {state}"
    assert reason == "restart"
    claim_line = [l for l in out2.splitlines() if l.startswith("CLAIM")][0]
    assert claim_line.strip() == "CLAIM None", (
        f"задача возобновилась сама, а это запрещено: {claim_line}")


def test_a_foreign_running_task_can_never_be_claimed(db, tmp_path):
    """Безопасность даёт НЕ уборка, а то, что в работу берут только из очереди.
    Проверяем без уборки вовсе: задача чужого запуска недоступна."""
    home = tmp_path / "дом2"
    home.mkdir()
    _run("""
tid = tstore.create("чужая", supersede=False)
tstore.claim()
print("READY", tid)
""", home)
    out = _run("""
got = tstore.claim()
print("CLAIM", got)
""", home)
    assert "CLAIM None" in out, "задачу прошлого запуска удалось взять в работу"
