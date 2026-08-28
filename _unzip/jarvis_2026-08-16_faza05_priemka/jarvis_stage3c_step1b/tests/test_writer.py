# tests/test_writer.py
"""
Сторожа одной кассы записи (фаза 1, блок 7).

Правило этих тестов: проверяем ПОВЕДЕНИЕ на настоящих потоках и настоящем
втором процессе, а не наличие слов в исходнике. Мёртвую хватку нельзя поймать
чтением кода — только запуском с таймаутом.
"""
from __future__ import annotations

import io
import sqlite3
import subprocess
import sys
import threading
import time
import tokenize
from pathlib import Path

import pytest

from core import store, writer

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db():
    """База дома этого теста. Касса откроет её сама, мы только смотрим."""
    conn = store.open_store()
    yield conn
    conn.close()


def _put(name, n):
    def fn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO mx_counter (quota_day, name, n)"
            " VALUES ('2026-08-19', ?, ?)", (name, n))
        return n
    return fn


# -- Основное ------------------------------------------------------------

def test_a_write_lands_and_returns_what_the_work_returned(db):
    assert writer.write(_put("проба", 7)) == 7
    row = db.execute("SELECT n FROM mx_counter WHERE name='проба'").fetchone()
    assert row is not None and row[0] == 7


def test_a_failed_write_leaves_nothing_behind(db):
    """Откат обязателен: полузаписанная правда хуже отсутствующей."""
    def broken(conn):
        conn.execute("INSERT OR REPLACE INTO mx_counter (quota_day, name, n)"
                     " VALUES ('2026-08-19','должна-исчезнуть',1)")
        raise RuntimeError("нарочно")

    with pytest.raises(RuntimeError):
        writer.write(broken)
    left = db.execute(
        "SELECT count(*) FROM mx_counter WHERE name='должна-исчезнуть'"
    ).fetchone()[0]
    assert left == 0, "упавшая запись оставила след"


def test_a_nested_write_joins_the_open_transaction(db):
    """САМАЯ ВАЖНАЯ ЛОВУШКА БЛОКА.

    `consent_store.consume(on_authorized=...)` зовёт чужую функцию изнутри
    записи, и та сама пишет в базу. Обычный замок встал бы намертво, RLock
    закрыл бы транзакцию раньше внешней. Присоединение — единственный верный
    ответ, и вот его проверка.
    """
    def outer(conn):
        conn.execute("INSERT OR REPLACE INTO mx_counter (quota_day, name, n)"
                     " VALUES ('2026-08-19','внешняя',1)")
        writer.write(_put("вложенная", 2))
        assert writer.in_write(), "внутри записи, а касса думает иначе"
        return "дошли"

    before = writer.stats()["nested"]
    assert writer.write(outer) == "дошли"
    assert writer.stats()["nested"] == before + 1, "вложенность не замечена"
    got = {r[0] for r in db.execute(
        "SELECT name FROM mx_counter WHERE name IN ('внешняя','вложенная')")}
    assert got == {"внешняя", "вложенная"}


def test_a_nested_write_that_fails_rolls_back_the_outer_one_too(db):
    """Присоединился — значит и падаешь вместе. Иначе внешняя работа осталась
    бы записанной наполовину, а это ровно то, от чего касса и ставится."""
    def outer(conn):
        conn.execute("INSERT OR REPLACE INTO mx_counter (quota_day, name, n)"
                     " VALUES ('2026-08-19','внешняя-2',1)")

        def inner(c2):
            raise RuntimeError("вложенная упала")
        writer.write(inner)

    with pytest.raises(RuntimeError):
        writer.write(outer)
    left = db.execute(
        "SELECT count(*) FROM mx_counter WHERE name='внешняя-2'").fetchone()[0]
    assert left == 0, "внешняя запись выжила после падения вложенной"


def test_the_lock_is_not_reentrant(db):
    """RLock был бы лечением симптома: он спрятал бы вложенность вместо того,
    чтобы её обработать. Тот же сторож стоит у пропуска с блока 3."""
    assert not isinstance(writer._LOCK, type(threading.RLock())), (
        "замок кассы стал повторным — вложенность перестанет ловиться")


def test_the_connection_is_never_created_under_the_write_lock(db, monkeypatch):
    """Открытие базы умеет запустить миграции и снять копию всего дома.
    Под замком записи это был бы самозахват и остановка всех писателей.

    Первая версия этого сторожа читала ИСХОДНИК и искала в нём порядок строк.
    Она покраснела от простой перестановки кода, хотя свойство сохранилось —
    то есть проверяла текст, а не работу. Теперь проверяем работу: в момент
    открытия базы замок записи обязан быть свободен.
    """
    real = store.open_store
    seen = []

    def checking(*a, **k):
        seen.append(writer._LOCK.locked())
        return real(*a, **k)

    monkeypatch.setattr(store, "open_store", checking)
    writer.reset_for_tests()
    writer.write(_put("после-сброса", 1))
    assert seen, "база так и не открылась — тест ничего не проверил"
    assert not any(seen), "соединение создавалось ПОД замком записи"


# -- Одновременность ------------------------------------------------------

def test_four_threads_writing_at_once_never_hang_and_never_lose(db):
    """Мёртвую хватку нельзя поймать чтением кода — только запуском.

    Таймаут обязателен: без него зависание выглядит как «прогон подвис», и
    искать будут где угодно, кроме замка.
    """
    rounds = 40
    names = ("ящик", "учёт", "журнал", "талоны")
    errors = []

    def worker(name):
        for i in range(rounds):
            try:
                writer.write(_put(f"{name}-{i}", i))
            except Exception as exc:      # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in names]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    spent = time.perf_counter() - t0

    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"МЁРТВАЯ ХВАТКА: потоки не вернулись за 30 с: {alive}"
    assert not errors, f"потери при одновременной записи: {errors[:3]}"
    landed = db.execute(
        "SELECT count(*) FROM mx_counter WHERE quota_day='2026-08-19'"
    ).fetchone()[0]
    assert landed >= rounds * len(names), (
        f"легло {landed}, ждали не меньше {rounds * len(names)}")
    assert spent < 30


def test_a_reader_does_not_wait_behind_a_writer(db):
    """Чтение — своё соединение (Д41). Тяжёлое чтение не имеет права стоять
    за записью, иначе поиск по памяти заморозит журнал."""
    seen = []
    stop = threading.Event()

    def reading():
        conn = writer.reader()
        while not stop.is_set():
            conn.execute("SELECT count(*) FROM mx_counter").fetchone()
            seen.append(1)
            time.sleep(0.001)

    th = threading.Thread(target=reading)
    th.start()
    try:
        for i in range(50):
            writer.write(_put(f"под-чтением-{i}", i))
    finally:
        stop.set()
        th.join(timeout=10)
    assert not th.is_alive(), "читатель завис за писателем"
    assert seen, "читатель не сделал ни одного чтения"


def test_the_reader_connection_refuses_to_write(db):
    """Соединение чтения открыто только на чтение НАРОЧНО: случайная запись
    отсюда обязана падать вслух, а не проходить мимо кассы."""
    conn = writer.reader()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT OR REPLACE INTO mx_counter (quota_day, name, n)"
                     " VALUES ('2026-08-19','мимо-кассы',1)")


def test_each_thread_gets_its_own_reader(db):
    """Одно соединение чтения на два потока — это гонка курсоров."""
    got = {}

    def grab(name):
        got[name] = id(writer.reader())

    ths = [threading.Thread(target=grab, args=(n,)) for n in ("а", "б")]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=10)
    assert len(set(got.values())) == 2, "потоки поделили одно соединение чтения"


# -- Чужой процесс: то, ради чего касса и нужна --------------------------

HOLDER = r'''
import sqlite3, sys, time
db, hold = sys.argv[1], float(sys.argv[2])
c = sqlite3.connect(db, isolation_level=None)
c.execute("PRAGMA busy_timeout=5000")
c.execute("BEGIN IMMEDIATE")
c.execute("INSERT OR REPLACE INTO mx_counter (quota_day, name, n)"
          " VALUES ('2026-08-19','чужой',1)")
print("HELD", flush=True)
time.sleep(hold)
c.execute("COMMIT")
c.close()
'''


def test_a_foreign_process_holding_the_db_costs_a_retry_not_a_loss(db):
    """ЗАМЕРЕНО 19.08.2026: чужой процесс, державший запись 7 секунд, ронял
    нашу запись с «database is locked» через 5,68 с. Внутренний замок от этого
    не спасает вообще — спасает повтор, и вот его проверка.

    Держим 6 секунд: дольше терпения SQLite (5 с), значит первая попытка
    обязана провалиться, а вторая — пройти.
    """
    path = str(store.db_path())
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, path, "6"],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "HELD"
        before = writer.stats()["retries"]
        t0 = time.perf_counter()
        writer.write(_put("после-чужого", 1))
        spent = time.perf_counter() - t0
        assert writer.stats()["retries"] > before, (
            "повтора не было — значит чужой процесс нас не задержал, "
            "и тест ничего не проверил")
        assert spent >= 4.0, f"запись прошла слишком быстро ({spent:.1f} с)"
    finally:
        proc.wait(timeout=30)
    row = db.execute(
        "SELECT n FROM mx_counter WHERE name='после-чужого'").fetchone()
    assert row is not None, "запись потеряна, хотя должна была дождаться"


def test_a_db_held_forever_is_a_loud_refusal_not_a_silent_loss(db, monkeypatch):
    """Молчаливая потеря хуже отказа: талон согласия терять нельзя, и
    звонящий обязан узнать, что записи не было."""
    monkeypatch.setattr(writer, "BEGIN_ATTEMPTS", 2)
    monkeypatch.setattr(writer, "BEGIN_PAUSE_S", 0.01)

    class _AlwaysBusy:
        def execute(self, sql, *a):
            if sql.startswith("BEGIN"):
                raise sqlite3.OperationalError("database is locked")
            return None

    monkeypatch.setattr(writer, "_connection", lambda: _AlwaysBusy())
    with pytest.raises(writer.WriteBusy):
        writer.write(_put("никогда", 1))
    assert writer.stats()["failures"] >= 1, "отказ не посчитан"


def test_the_transaction_is_immediate_not_deferred():
    """Немедленная транзакция берёт замок базы СРАЗУ. Только поэтому повтор
    безопасен без условий: если начало прошло, до конца никто не вмешается.
    С отложенной замок брался бы посреди работы, и повторять пришлось бы
    с половины — а половина работы могла быть уже необратима."""
    import inspect
    src = inspect.getsource(writer._begin)
    assert "BEGIN IMMEDIATE" in src
    assert "DEFERRED" not in src


def test_two_threads_racing_for_the_last_quota_slot_cannot_both_win(db):
    """ДЫРА, НАЙДЕННАЯ ПОРЧЕЙ КОДА 19.08.2026.

    Я перенёс проверку суточного потолка ВНУТРЬ транзакции кассы и написал,
    что теперь «два потока не могут оба увидеть остался один и оба пойти».
    Порча вынесла проверку обратно наружу — и ни один тест не покраснел. То
    есть свойство я объявил, а стеречь его было нечем.

    Здесь восемь потоков одновременно рвутся за последние два места. Пройти
    обязаны РОВНО два: столько и осталось. Если проверка окажется вне
    транзакции, пройдут все восемь — и владелец узнает об исчерпании квоты
    не от Джарвиса, а от Google.
    """
    from core import metering
    cap = 2
    monkey = {"paid": cap}

    saved = metering.caps
    metering.caps = lambda: {metering.PAID_BUCKET: cap,
                             metering.CHEAP_BUCKET: cap}
    try:
        allowed, refused = [], []
        errors = []

        def racer():
            try:
                got = metering.reserve("aux_light")
                (allowed if got.get("allowed") else refused).append(got)
            except Exception as exc:               # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=racer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not [t for t in threads if t.is_alive()], "гонка за квоту зависла"
        assert not errors, errors[:3]

        rows = db.execute("SELECT count(*) FROM mx_meter_call").fetchone()[0]
        assert len(allowed) == cap, (
            f"потолок {cap}, а разрешено {len(allowed)} — проверка потолка "
            f"вышла из транзакции")
        assert rows == cap, f"в базе {rows} резервов вместо {cap}"
        assert len(refused) == 8 - cap
        assert all(r.get("why") == "daily_cap" for r in refused), (
            "отказ без причины — исчерпание обязано быть названным (I19)")
    finally:
        metering.caps = saved
        assert monkey  # держим ссылку, чтобы линтер не съел


def test_each_thread_gets_its_own_reader_even_under_load(db):
    """Проверка того же свойства, что и выше, но настоящей нагрузкой.

    Первая порча этого свойства оказалась НЕ ПОРЧЕЙ: она подменяла поиск на
    общую переменную, но саму переменную не задавала, поэтому код падал в
    обычную ветку и снова делал своё соединение. Порча, которая ничего не
    меняет, выглядит как «сторож пропустил» — и это опаснее настоящей дыры,
    потому что успокаивает. Здесь свойство проверяется прямо: у восьми
    потоков восемь разных соединений, и все они читают одновременно.
    """
    seen = {}
    errors = []

    def grab(name):
        try:
            conn = writer.reader()
            conn.execute("SELECT count(*) FROM mx_counter").fetchone()
            seen[name] = id(conn)
        except Exception as exc:                   # noqa: BLE001
            errors.append(f"{name}: {exc}")

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors[:3]
    assert len(seen) == 8, f"вернулось {len(seen)} потоков из 8"
    assert len(set(seen.values())) == 8, (
        "потоки поделили соединения чтения: "
        f"{len(set(seen.values()))} штук на 8 потоков")


# -- Касса единственная: сторожа против возвращения второго писателя ------

LIVE_DIRS = ("core", "agent", "actions", "memory")

# Кому позволено открывать базу рабочим путём, и почему именно им.
MAY_OPEN = {
    # Сам фундамент. Обратного пути нет и быть не может: касса ВВОЗИТ store,
    # поэтому store не может ввозить кассу — это был бы круг. Здесь живут
    # миграции, и они идут до того, как касса вообще способна работать.
    "core/store.py": "фундамент: миграции и открытие, касса стоит на нём",
    # Собственно касса.
    "core/writer.py": "это и есть касса",
}


def _live_files():
    out = []
    for folder in LIVE_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            out.append(path)
    for name in ("main.py", "ui.py", "consent_mode.py"):
        p = ROOT / name
        if p.exists():
            out.append(p)
    return out


def _code_only(path):
    """Код без комментариев и строк. Сторож, который ищет слово в тексте,
    находит сам себя в объяснении, почему это слово запрещено — в проекте так
    случалось шесть раз."""
    with io.open(path, "rb") as fh:
        try:
            return " ".join(
                t.string for t in tokenize.tokenize(fh.readline)
                if t.type not in (tokenize.COMMENT, tokenize.STRING))
        except (tokenize.TokenError, SyntaxError):
            return ""


def test_nobody_but_the_desk_opens_the_database_for_writing():
    """Один писатель — это не обещание в комментарии, а проверяемое свойство.

    Решение про одну кассу говорит дословно, чего мы боимся: альтернатива
    «каждый пишет сам, но аккуратно» требует, чтобы ни один из десятков
    БУДУЩИХ модулей не забыл. Сторож и есть та память, которая не забудет.
    """
    guilty = []
    for path in _live_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in MAY_OPEN:
            continue
        if "open_store" in _code_only(path):
            guilty.append(rel)
    assert not guilty, (
        "эти файлы открывают базу мимо кассы: " + ", ".join(guilty) +
        " — возьмите соединение у core/writer вместо своего")


def test_nobody_but_the_desk_starts_a_transaction():
    """Своя транзакция — это свой писатель. Их снова стало бы столько же,
    сколько было до блока 7, только теперь молча."""
    guilty = []
    for path in _live_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in MAY_OPEN:
            continue
        code = _code_only(path)
        if "BEGIN" in code:
            guilty.append(rel)
    assert not guilty, (
        "эти файлы начинают транзакцию сами: " + ", ".join(guilty))


def test_the_modules_that_gave_up_their_locks_did_not_take_them_back():
    """Замки, снятые в блоке 7, обязаны остаться снятыми.

    Два замка вокруг одной базы — это РАЗНЫЙ ПОРЯДОК захвата, то есть мёртвая
    хватка. Она не проявляется на одном потоке и ждёт блока 8.
    """
    from core import blackbox, metering
    assert not hasattr(blackbox, "_LOCK"), (
        "у чёрного ящика снова свой замок вокруг записи")
    assert not hasattr(metering, "_LOCK"), (
        "у учёта снова свой замок вокруг записи")


def test_the_only_lock_around_the_database_is_the_desk_one():
    """Замок талонов остался, но базы под ним быть не должно: он стережёт
    номер сессии в памяти. Проверяем это по коду, а не по обещанию."""
    from core import consent_runtime
    assert hasattr(consent_runtime, "_LOCK"), "замок талонов исчез целиком"
    code = _code_only(ROOT / "core" / "consent_runtime.py")
    # Под замком не должно быть открытия базы: раньше было ровно оно.
    after_lock = code.split("with _LOCK")[1] if "with _LOCK" in code else ""
    assert "open_store" not in after_lock, (
        "открытие базы вернулось под замок талонов")


# -- Мёртвая хватка на настоящей смеси писателей -------------------------

def test_all_the_real_writers_at_once_never_deadlock(db):
    """ГЛАВНЫЙ СТОРОЖ БЛОКА, и его нельзя заменить чтением кода.

    Мёртвая хватка не видна в исходнике: она возникает от ПОРЯДКА захвата в
    двух потоках. Поэтому здесь работают настоящие писатели — чёрный ящик,
    учёт, журнал действий и талоны — одновременно и с таймаутом.

    Без таймаута зависание выглядело бы как «прогон подвис», и искать стали бы
    где угодно, кроме замка. Живьём это «Джарвис завис при запуске».
    """
    from core import action_log, blackbox, metering
    errors = []
    rounds = 12

    def via_blackbox():
        for i in range(rounds):
            rec = blackbox.open_rec(day="2026-08-19")
            blackbox.write(rec, "prompt", {"t": f"вопрос {i}"})
            blackbox.write(rec, "model_out", {"ok": True, "t": "ответ"})

    def via_metering():
        for i in range(rounds):
            got = metering.reserve("aux_light")
            if got.get("call_id"):
                metering.commit(got["call_id"], in_tokens=10, out_tokens=5)

    def via_journal():
        for i in range(rounds):
            action_log.note("проба", action="шаг", summary=f"строка {i}")

    def via_purge():
        for _ in range(rounds):
            blackbox.purge(days=9999)

    def guard(fn, name):
        def run():
            try:
                fn()
            except Exception as exc:                  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        return run

    jobs = [("ящик", via_blackbox), ("учёт", via_metering),
            ("журнал", via_journal), ("уборка", via_purge)]
    threads = [threading.Thread(target=guard(fn, name), name=name)
               for name, fn in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"МЁРТВАЯ ХВАТКА: не вернулись за 60 с: {alive}"
    assert not errors, f"писатели поссорились: {errors[:4]}"
    # И работа действительно сделана, а не пропущена молча.
    assert db.execute("SELECT count(*) FROM mx_bb_body").fetchone()[0] > 0
    assert db.execute("SELECT count(*) FROM mx_meter_call").fetchone()[0] > 0
    assert db.execute("SELECT count(*) FROM action_journal").fetchone()[0] > 0


def test_a_ticket_and_its_saga_are_born_together_or_not_at_all(db):
    """ДЕФЕКТ, НАЙДЕННЫЙ РАЗБОРОМ 19.08.2026, и его сторож.

    Шапка функции траты талона обещала: «on_authorized выполняется В ТОЙ ЖЕ
    транзакции, что и отметка талона». Слова `BEGIN` в файле не было ни разу —
    обещание держалось только на том, что соединение одно.

    Цена названа в той же шапке: сбой посередине оставляет талон, потраченный
    НИ НА ЧТО, и владелец отвечает на вопрос второй раз за работу, которая
    может быть уже сделана наполовину. Здесь мы этот сбой устраиваем нарочно.
    """
    from core import consent_store as cs

    minted = cs.mint(db, tool="file_controller", action="delete",
                     parameters={"path": "C:/tmp/файл.txt"},
                     preview="Удалить файл?", session_id="s-1")
    ticket = minted["ticket"]

    def explodes(conn, row):
        # Работа, которую талон оплачивает, падает на середине.
        conn.execute(
            "INSERT INTO action_journal (ts, tool, action, summary, ok) "
            "VALUES ('2026-08-19T00:00:00','проба','x','должна исчезнуть',1)")
        raise RuntimeError("работа сорвалась")

    before = db.execute(
        "SELECT count(*) FROM action_journal WHERE summary='должна исчезнуть'"
    ).fetchone()[0]

    with pytest.raises(RuntimeError):
        cs.consume(db, ticket=ticket, tool="file_controller", action="delete",
                   parameters={"path": "C:/tmp/файл.txt"}, session_id="s-1",
                   on_authorized=explodes)

    row = db.execute("SELECT status FROM consent_ticket WHERE ticket=?",
                     (ticket,)).fetchone()
    assert row["status"] == "pending", (
        "талон потрачен, а работа не сделана — владельца спросят второй раз "
        "за работу, которая могла быть сделана наполовину")
    after = db.execute(
        "SELECT count(*) FROM action_journal WHERE summary='должна исчезнуть'"
    ).fetchone()[0]
    assert after == before, "полработы осталась записанной"
