# -*- coding: utf-8 -*-
"""Фаза 1, блок 3 — пропуск: сквозной номер запуска и дела.

Имена `test_ctx_reaches_journal` и `test_no_action_without_correlation` взяты
из плана дословно (Р2): поиск по плану обязан приводить в настоящий файл.

Главный сторож этого файла — `test_run_in_executor_does_not_carry_the_context`.
Он закрепляет ЗАМЕР, а не желание: в этой версии Python `run_in_executor` не
переносит контекст, а `to_thread` переносит. main.py зовёт инструменты
двадцатью `run_in_executor`. Без этого сторожа через полгода кто-то напишет
код внутри рабочего потока, будет ждать номер и молча его не получит.
"""
import asyncio
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import task_context as tc


# -- Номер запуска --------------------------------------------------------

def test_the_run_has_a_number_and_it_does_not_change():
    first, second = tc.run_id(), tc.run_id()
    assert first and first == second, "номер запуска обязан быть один на запуск"


def test_two_runs_in_the_same_second_are_still_different():
    """Бывает при отладке. И бывает прыжок часов назад — уникальность не
    имеет права зависеть от часов (грабля шага 24)."""
    same = datetime(2026, 8, 18, 1, 45, 30, tzinfo=timezone.utc)
    made = {tc.new_run_id(now=same) for _ in range(200)}
    assert len(made) > 190, f"номера повторяются: {200 - len(made)} совпадений"
    assert all(x.startswith("20260818T014530Z-") for x in made)


def test_the_run_number_sorts_by_time():
    early = tc.new_run_id(now=datetime(2026, 8, 18, 1, 0, 0, tzinfo=timezone.utc))
    late = tc.new_run_id(now=datetime(2026, 8, 18, 2, 0, 0, tzinfo=timezone.utc))
    assert early < late, "номера запусков не сортируются по времени"


# -- Номер дела -----------------------------------------------------------

def test_the_task_number_looks_like_the_plan_says():
    assert tc.format_task_id("20260818", 1) == "T-20260818-001"
    assert tc.format_task_id("20260818", 42) == "T-20260818-042"


def test_the_task_number_does_not_break_past_a_thousand():
    """Не паникуем и не обрезаем: 1000 дел в сутки у этого владельца
    невозможны, а терять номер хуже, чем потерять сортировку строкой."""
    assert tc.format_task_id("20260818", 1000) == "T-20260818-1000"


def _code_only(path: Path) -> str:
    """Файл без комментариев и без строковых литералов — только код.

    Так делает сторож доктора (шаг 34.2), и по той же причине: сторож,
    который ищет запретное слово в тексте, находит САМ СЕБЯ — в объяснении,
    почему это слово запрещено. У доктора это было слово `sqlite3` в
    комментарии-запрете, здесь `quota_day` в шапке. Третий случай в проекте,
    поэтому берём не «выкинуть строки на #», а токенизатор.
    """
    import io
    import tokenize
    out = []
    with io.open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING,
                            tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.ENCODING,
                            tokenize.ENDMARKER):
                continue
            out.append(tok.string)
    return " ".join(out)


def test_the_task_date_is_local_and_lives_nowhere_else():
    """Дата в номере дела — ЯРЛЫК, а не расчётная граница. Квотные сутки —
    другое понятие и другое место (metering.quota_day, сброс по Pacific).

    Сторож смотрит, что здесь нет второго вычисления квотных суток: три
    разных «дня» в проекте — мина, которая всплывает раз в полгода.
    """
    body = _code_only(ROOT / "core" / "task_context.py")
    for needle in ("quota_day", "Pacific"):
        assert needle not in body, (
            f"в пропуске появилось вычисление квотных суток ({needle!r}) — "
            "оно обязано жить только в метеринге")
    assert tc.today_stamp(now=datetime(2026, 8, 18, 1, 0)) == "20260818"


# -- Строка для журнала ---------------------------------------------------

def test_the_correlation_string_is_built_and_read_back():
    ctx = tc.TaskCtx(run_id="20260818T014530Z-a3f1", task_id="T-20260818-007")
    line = ctx.correlation(step=3)
    assert line == "run:20260818T014530Z-a3f1/task:T-20260818-007/step:3"
    back = tc.TaskCtx.parse(line)
    assert back == {"run": "20260818T014530Z-a3f1",
                    "task": "T-20260818-007", "step": 3}


def test_a_dialog_without_a_task_writes_a_dash_not_a_hole():
    """Пустое место в строке невозможно отличить от обрезанной строки."""
    ctx = tc.TaskCtx(run_id="R1")
    assert ctx.correlation(step=1) == "run:R1/task:-/step:1"
    assert tc.TaskCtx.parse(ctx.correlation(step=1))["task"] is None


def test_steps_count_up_on_their_own():
    ctx = tc.TaskCtx(run_id="R1")
    assert [tc.TaskCtx.parse(ctx.correlation())["step"] for _ in range(3)] == [1, 2, 3]


# -- Подчинённые и предел рекурсии ---------------------------------------

def test_a_child_carries_roles_not_names():
    """I21: ядро имён агентов не знает. Добавить третьего агента должно быть
    можно правкой одного yaml, без правок в core/."""
    main = tc.dialog_ctx()
    kid = main.child(agent_role="file_clerk", task_id="T-1")
    assert kid.origin_chain == ("owner", "main", "file_clerk")
    assert kid.depth == main.depth + 1
    assert kid.parent_id == main.task_id
    assert kid.run_id == main.run_id, "подчинённый ушёл в другой запуск"
    assert kid.bucket == "task", "работа подчинённого — не разговор"


def test_recursion_stops_at_the_named_limit():
    ctx = tc.dialog_ctx().child(agent_role="pc_operator")
    deep = ctx.child(agent_role="file_clerk")
    assert deep.depth == tc.MAX_DEPTH
    with pytest.raises(ValueError) as caught:
        deep.child(agent_role="ещё_один")
    assert "pc_operator" in str(caught.value), "отказ не показывает цепочку"


def test_an_unknown_bucket_is_refused_loudly():
    """Молча подставить свою корзину нельзя: по ней потом делят расход."""
    with pytest.raises(ValueError):
        tc.TaskCtx(run_id="R1", bucket="какая-нибудь")


def test_the_depth_limit_cannot_be_bypassed_by_hand():
    with pytest.raises(ValueError):
        tc.TaskCtx(run_id="R1", depth=tc.MAX_DEPTH + 1)


# -- Кто сейчас говорит ---------------------------------------------------

def test_there_is_always_a_pass_even_when_nobody_set_one():
    got = tc.current()
    assert got is not None and got.run_id == tc.run_id()
    assert got.bucket == "dialog"


def test_an_explicit_pass_beats_the_one_in_the_context():
    """Правило 1 из шапки. В фазе 1б у гейта появится параметр ctx, и два
    источника правды разошлись бы молча."""
    mine = tc.TaskCtx(run_id="R-мой", task_id="T-мой")
    other = tc.TaskCtx(run_id="R-чужой")
    with tc.bind(other):
        assert tc.current().run_id == "R-чужой"
        assert tc.current(mine).run_id == "R-мой"


def test_leaving_the_with_block_restores_the_previous_pass():
    outer = tc.TaskCtx(run_id="R-внешний")
    with tc.bind(outer):
        with tc.bind(tc.TaskCtx(run_id="R-внутренний")):
            assert tc.current().run_id == "R-внутренний"
        assert tc.current().run_id == "R-внешний", "прежний пропуск не вернулся"
    assert tc.current().run_id == tc.run_id()


def test_a_pooled_thread_never_inherits_a_stranger_pass():
    """Потоки в пуле ПЕРЕИСПОЛЬЗУЮТСЯ. Пропуск, забытый в таком потоке,
    всплыл бы в следующем чужом деле — поэтому только `with`."""
    seen = []

    def work(tag):
        with tc.bind(tc.TaskCtx(run_id=f"R-{tag}")):
            seen.append((tag, tc.current().run_id, threading.current_thread().name))
        seen.append((tag + "-после", tc.current().run_id))

    with ThreadPoolExecutor(max_workers=1) as pool:   # ОДИН поток на оба дела
        pool.submit(work, "первое").result()
        pool.submit(work, "второе").result()

    assert seen[0][1] == "R-первое" and seen[2][1] == "R-второе"
    assert seen[0][2] == seen[2][2], "тест не воспроизвёл переиспользование потока"
    assert seen[1][1] != "R-первое", "пропуск остался жить в потоке пула"


def test_run_in_executor_does_not_carry_the_context():
    """ЗАМЕР, а не желание (18.08.2026).

    `run_in_executor` контекст НЕ переносит, `to_thread` переносит. main.py
    зовёт инструменты двадцатью `run_in_executor`, значит ВНУТРИ инструмента
    пропуска нет. Проект на этом и построен: запись в журнал идёт ПОСЛЕ
    await, уже в цикле, где пропуск виден. Если этот сторож однажды
    покраснеет — значит Python начал переносить контекст, и врезку в журнал
    можно упростить. Пока он зелёный, никакой код внутри рабочего потока не
    имеет права ждать пропуск из контекста.
    """
    async def scenario():
        loop = asyncio.get_running_loop()
        with tc.bind(tc.TaskCtx(run_id="R-цикла")):
            inside = tc.current().run_id
            in_executor = await loop.run_in_executor(
                None, lambda: tc.current().run_id)
            in_to_thread = await asyncio.to_thread(lambda: tc.current().run_id)
        return inside, in_executor, in_to_thread

    inside, in_executor, in_to_thread = asyncio.run(scenario())
    assert inside == "R-цикла"
    assert in_executor != "R-цикла", (
        "run_in_executor начал переносить контекст — перечитай врезку в журнал")
    assert in_executor == tc.run_id(), "в рабочем потоке пропала даже опора"
    assert in_to_thread == "R-цикла", "to_thread перестал переносить контекст"


def test_a_plain_thread_starts_with_no_pass_of_its_own():
    """Агент — это поток (правило 5 запретов). Он НЕ наследует пропуск
    разговора, и это правильно: своя сессия, чужие разрешения не наследуются
    (план Р4)."""
    out = []
    with tc.bind(tc.TaskCtx(run_id="R-разговора")):
        t = threading.Thread(target=lambda: out.append(tc.current().run_id))
        t.start()
        t.join()
    assert out[0] != "R-разговора", "поток унёс пропуск разговора"


def test_forgetting_the_run_number_between_tests_is_possible():
    """Весь прогон — один процесс. Без сброса номер первого теста достался бы
    всем остальным, и сторожа были бы зелёными по неверной причине."""
    was = tc.run_id()
    tc.reset_for_tests()
    assert tc.run_id() != was


def test_nothing_here_takes_the_lock_twice():
    """Мёртвая хватка: `dialog_ctx` брал замок и внутри звал `run_id`, который
    берёт ТОТ ЖЕ замок. Обычный замок не повторный — процесс встаёт молча и
    навсегда. Поймано первым же прогоном 18.08.2026, а живьём это выглядело
    бы как «Джарвис завис при запуске», и искали бы в микрофоне.

    Сторож не про этот случай, а про весь класс: любой вложенный захват в
    этом файле снова повесит запуск. Проверяем на неповторном замке — если
    кто-то однажды подменит его на RLock, тест об этом скажет.
    """
    assert not isinstance(tc._LOCK, type(threading.RLock())), (
        "замок стал повторным — вложенные захваты перестанут ловиться")

    done = []
    t = threading.Thread(target=lambda: (tc.dialog_ctx(), tc.run_id(),
                                         tc.current(), done.append(True)))
    t.start()
    t.join(timeout=5)
    assert done, "вызовы пропуска встали в мёртвую хватку"


# -- Пропуск доезжает до журнала (имена тестов из плана, Р2) --------------

def _journal_rows():
    """Строки журнала действий из базы этого теста."""
    from core import store
    conn = store.open_store()
    try:
        return conn.execute(
            "SELECT summary, correlation_id FROM action_journal ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_ctx_reaches_journal():
    """Имя из плана. Колонка есть с версии схемы 2, и до блока 3 в неё
    никогда ничего не писали — всегда NULL."""
    from core import action_log
    assert action_log.note(tool="notepad", summary="открыл блокнот") is True
    rows = _journal_rows()
    assert len(rows) == 1
    got = tc.TaskCtx.parse(rows[0]["correlation_id"])
    assert got["run"] == tc.run_id(), "номер запуска не доехал до журнала"
    assert got["step"] == 1
    assert got["task"] is None, "у разговора дела нет, и это не ошибка"


def test_no_action_without_correlation():
    """Имя из плана. Ни одной записи без номера: именно это и лечится."""
    from core import action_log
    for i in range(4):
        action_log.note(tool="notepad", summary=f"дело {i}")
    rows = _journal_rows()
    assert len(rows) == 4
    assert all(r["correlation_id"] for r in rows), "есть строка без номера"
    steps = [tc.TaskCtx.parse(r["correlation_id"])["step"] for r in rows]
    assert steps == [1, 2, 3, 4], f"шаги не считаются подряд: {steps}"


def test_an_explicit_pass_wins_in_the_journal_too():
    """Порядок старшинства один на весь проект, включая запись."""
    from core import action_log
    mine = tc.TaskCtx(run_id="R-агента", task_id="T-20260818-005",
                      agent_role="file_clerk", bucket="task")
    action_log.note(tool="file_controller", summary="перенёс 14 файлов",
                    ctx=mine)
    got = tc.TaskCtx.parse(_journal_rows()[0]["correlation_id"])
    assert got["run"] == "R-агента"
    assert got["task"] == "T-20260818-005"


def test_a_broken_pass_never_breaks_the_record():
    """Правило 1 кассы сильнее номера: записка о действии не стоит того,
    чтобы из-за неё упало само действие."""
    from core import action_log

    class Broken:
        def correlation(self, *a, **k):
            raise RuntimeError("пропуск сломан")

    assert action_log.note(tool="notepad", summary="всё равно записано",
                           ctx=Broken()) is True
    rows = _journal_rows()
    # Касса дописывает имя инструмента к сводке — это её обычное поведение,
    # проверяем вхождение, а не полное совпадение.
    assert len(rows) == 1 and "всё равно записано" in rows[0]["summary"]
    assert rows[0]["correlation_id"] is None, "сломанный пропуск подсунул мусор"


def test_the_offline_path_gets_a_number_too():
    """Путей в кассу ровно два (проверено грепом): main.py и оффлайн-ядро.
    Второй не передаёт пропуск вовсе — и обязан получить его сам."""
    from core import offline_core
    offline_core._note("notepad", {"action": "open"}, "открыл блокнот", True)
    rows = _journal_rows()
    assert len(rows) == 1
    assert tc.TaskCtx.parse(rows[0]["correlation_id"])["run"] == tc.run_id()


def test_the_session_number_grows_from_the_run_number():
    """План Р2 просит убрать лишние номерки, и я сначала сделал номер сессии
    РАВНЫМ номеру запуска. Существующий тест отмены покраснел и оказался прав:
    обязанности разные (см. шапку new_session_id). Номер сессии производится
    от номера запуска, но остаётся своим."""
    from actions import fileops_bridge
    fileops_bridge.reset()
    fo = fileops_bridge.get_fileops()
    assert fo is not None, "файловый слой не поднялся"
    sid = getattr(fo.journal, "session_id", None)
    assert sid and sid.startswith(tc.run_id() + "#"), (
        f"номер сессии {sid!r} не привязан к запуску {tc.run_id()!r}")


def test_each_session_start_gets_its_own_number():
    """Иначе «отмени последнее» дотянется до правок прошлой сессии — тот
    самый случай с report.txt, который поймал сторож 18.08.2026."""
    made = [tc.new_session_id() for _ in range(3)]
    assert len(set(made)) == 3, f"номера сессий повторяются: {made}"
    assert all(x.startswith(tc.run_id() + "#") for x in made)


def test_the_run_number_is_readable_unlike_the_old_one():
    """Старый номер сессии был 32 случайных знака и в логах не говорил
    ничего. Новый читается глазами и сортируется по времени."""
    rid = tc.run_id()
    assert len(rid) < 32 and "-" in rid
    stamp, _, tail = rid.partition("-")
    assert stamp.endswith("Z") and len(stamp) == 16, rid
    assert len(tail) == 4, rid
