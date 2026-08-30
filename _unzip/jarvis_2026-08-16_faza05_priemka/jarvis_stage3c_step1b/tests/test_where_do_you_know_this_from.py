"""Фаза 1д — «откуда ты это знаешь?» через УЖЕ СУЩЕСТВУЮЩИЙ инструмент.

ЧТО СКАЗАЛ ВЛАДЕЛЕЦ
-------------------
Я предложил сделать ОТДЕЛЬНЫЙ голосовой инструмент «откуда ты это знаешь».
Владелец отказал, и был прав:

    «а разве он не сможет этого делать? ведь он же должен быть универсальным,
     не думал что будешь спрашивать об этом ведь ясно же что он должен
     уметь делать.»

То есть: не наращивать по кнопке на каждый вопрос, а чтобы уже имеющийся
`recall_memory` отдавал достаточно, и вопрос «откуда» отвечался сам собой.
Новой политики, новой двери, новых прав — НЕТ. Только богаче ответ.

ЗАМЕР ДО ПРАВКИ (живой вызов fact_store.recall, 28.08.2026)
-----------------------------------------------------------
    Found 1 in long-term memory:
    - [preferences/кофе] любит кофе без сахара (their exact words: «я пью кофе без сахара»)

    в базе лежало: source='explicit', created_at='2026-07-14T09:12:00+00:00'

Читаем внимательно. Дословная фраза владельца УЖЕ отдавалась — значит на
«с моих ли слов?» Джарвис ответить мог и до этой правки. А вот две вещи
лежали в базе и НЕ доезжали до модели:

  1. КОГДА — поле `created_at` заполняется при каждой записи, но в текст
     не выкладывалось. На «когда я это сказал?» ответа не было.
  2. ОТКУДА — поле `source`: explicit (владелец сказал сам), inferred
     (Джарвис вывел из разговора), legacy (приехало из старого файла
     памяти). Разница принципиальная: «вы мне это сказали» и «я это сам
     решил» — не одно и то же, а модель их не различала и вынуждена была
     говорить про оба одинаково.

Второй пункт — не про удобство, а про ложь. Не отличая своей догадки от
слов владельца, модель на вопрос «я тебе это говорил?» отвечала наугад.

ПОЧЕМУ ЭТО НЕ ЛОМАЕТ БЮДЖЕТ ПАМЯТИ
----------------------------------
Замерено: `recall` вызывается ровно из одного места — обработчика
recall_memory в main.py. В блок промпта (тот, что режется по бюджету
в 3B.3) его текст НЕ попадает. Значит удлинение строки не отнимает место
у фактов и не может обрезать их посередине.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ
------------------------
  - Нового инструмента. Прямое требование владельца.
  - Смены прав: `recall_memory` остаётся risk=low и по-прежнему разрешён
    под-агенту на чтение (решение владельца фазы 1г).
  - Точного времени с секундами. Владельцу нужно «когда», а не отметка
    в микросекундах; произносить вслух ISO-строку — издевательство.
"""

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MAIN = (REPO / "main.py").read_text(encoding="utf-8")
BEHAVIOR = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
    encoding="utf-8")

from core import store            # noqa: E402
from memory import fact_store as fs   # noqa: E402


def _fresh_db():
    """Пустая база с настоящей схемой — без сети и без файлов."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.migrate(conn, store.JARVIS_MIGRATIONS)
    return conn


# ── КОГДА: дата доезжает до модели ──────────────────────────────────────

def test_recall_says_when_the_fact_was_saved():
    """Без даты вопрос «когда я это сказал?» остаётся без ответа."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="любит кофе без сахара",
                   verbatim="я пью кофе без сахара",
                   source=fs.SOURCE_EXPLICIT,
                   updated_at="2026-07-14T09:12:00+00:00")
    out = fs.recall("кофе", conn=conn, memory={})
    assert "2026-07-14" in out, (
        "дата записи лежит в created_at, но не доехала до модели:\n" + out)


def test_the_date_is_readable_not_a_machine_stamp():
    """ISO с секундами и часовым поясом вслух не произнести."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="любит кофе без сахара",
                   source=fs.SOURCE_EXPLICIT,
                   updated_at="2026-07-14T09:12:00+00:00")
    out = fs.recall("кофе", conn=conn, memory={})
    assert "T09:12:00" not in out, (
        "сырая ISO-отметка в тексте для голоса:\n" + out)
    assert "+00:00" not in out, (
        "часовой пояс в тексте для голоса:\n" + out)


# ── ОТКУДА: слова владельца против собственной догадки ──────────────────

def test_a_fact_the_owner_said_is_marked_as_his_words():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="любит кофе без сахара",
                   source=fs.SOURCE_EXPLICIT,
                   updated_at="2026-07-14T09:12:00+00:00")
    out = fs.recall("кофе", conn=conn, memory={})
    assert "from them" in out, (
        "не видно, что факт со слов владельца:\n" + out)


def test_a_fact_jarvis_guessed_is_marked_as_a_guess():
    """Самое важное здесь. Смешав догадку со словами владельца, модель
    отвечает «вы говорили» на то, чего он не говорил."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="сон", category="habits",
                   value="ложится поздно",
                   source=fs.SOURCE_INFERRED,
                   updated_at="2026-07-14T09:12:00+00:00")
    out = fs.recall("сон", conn=conn, memory={})
    assert "inferred" in out, (
        "догадка Джарвиса выглядит как слова владельца:\n" + out)
    assert "from them" not in out, (
        "догадка помечена как сказанное владельцем — это ложь:\n" + out)


def test_the_owners_words_and_a_guess_never_look_the_same():
    """Прямое сравнение: два факта, разные источники, разные тексты."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="без сахара", source=fs.SOURCE_EXPLICIT,
                   updated_at="2026-07-14T09:12:00+00:00")
    fs.upsert_fact(conn, key="какао", category="preferences",
                   value="без сахара", source=fs.SOURCE_INFERRED,
                   updated_at="2026-07-14T09:12:00+00:00")
    said = fs.recall("кофе", conn=conn, memory={})
    guessed = fs.recall("какао", conn=conn, memory={})
    mark_said = said.split("кофе]")[-1]
    mark_guessed = guessed.split("какао]")[-1]
    assert mark_said != mark_guessed, (
        "слова владельца и догадка неотличимы:\n"
        f"сказано: {mark_said}\nдогадка: {mark_guessed}")


def test_a_fact_from_the_old_memory_file_admits_it():
    """legacy — приехало из старого JSON, кто это сказал, уже неизвестно.

    ОШИБКА В ЭТОМ СТРАЖЕ, найденная при прогоне (28.08.2026). Сначала я
    положил legacy-факт напрямую через upsert_fact и позвал recall с
    memory={}. Страж покраснел, и я почти пошёл править код — а виноват
    был страж.

    Причина: recall перед поиском зеркалит файл памяти v1, и это зеркало
    УДАЛЯЕТ строки source=legacy, которых в файле больше нет. Иначе
    забытый факт воскресал бы при каждой синхронизации — ровно то, что
    запрещает страж test_forgotten_facts_do_not_come_back_through_recall.
    Пустой memory={} для legacy означает «в файле этого нет», и факт
    честно исчезал.

    Поэтому идём настоящим путём: факт живёт в файле памяти v1, а
    зеркало само проставит ему source=legacy.
    """
    conn = _fresh_db()
    memory = {"profile": {"город": {
        "value": "Москва", "updated": "2026-07-14T09:12:00+00:00"}}}
    out = fs.recall("Москва", conn=conn, memory=memory)
    assert "older memory" in out, (
        "старый факт выдаётся за свежие слова владельца:\n" + out)
    assert "saved 2026-07-14" in out, (
        "дата старого факта потерялась:\n" + out)


# ── ничего из ранее работавшего не потеряно ─────────────────────────────

def test_the_exact_words_are_still_handed_back():
    """Это работало до правки. Дословная фраза — главный ответ на
    «откуда», и потерять её было бы хуже, чем не добавить дату."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="ночь", category="notes",
                   value="работает по ночам",
                   verbatim="Я вечно работаю по ночам, днём толку мало.",
                   source=fs.SOURCE_EXPLICIT)
    out = fs.recall("по ночам", conn=conn, memory={})
    assert "днём толку мало" in out, (
        "дословная фраза владельца пропала:\n" + out)


def test_finding_nothing_still_refuses_to_invent():
    conn = _fresh_db()
    out = fs.recall("акваланг", conn=conn, memory={})
    assert "Nothing saved" in out
    assert "do not invent" in out
    assert "2026" not in out, "дата приклеилась к пустому ответу:\n" + out


def test_the_category_and_key_are_still_there():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="без сахара", source=fs.SOURCE_EXPLICIT)
    out = fs.recall("кофе", conn=conn, memory={})
    assert "[preferences/кофе]" in out, (
        "разметка категории/ключа сломана:\n" + out)


def test_a_fact_with_no_date_does_not_crash_recall():
    """Схема требует created_at, но факты приезжают и из старого файла.
    Пустая дата не должна валить поиск — это чтение, оно обязано отвечать."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="без сахара", source=fs.SOURCE_EXPLICIT)
    conn.execute("UPDATE memory_fact SET created_at=''")
    out = fs.recall("кофе", conn=conn, memory={})
    assert "без сахара" in out, (
        "факт потерялся из-за пустой даты:\n" + out)


def test_a_broken_date_does_not_crash_recall():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="кофе", category="preferences",
                   value="без сахара", source=fs.SOURCE_EXPLICIT)
    conn.execute("UPDATE memory_fact SET created_at='вчера примерно'")
    out = fs.recall("кофе", conn=conn, memory={})
    assert "без сахара" in out, (
        "кривая дата уронила поиск:\n" + out)


# ── модель должна ЗНАТЬ, что теперь может ответить «откуда» ─────────────

def test_the_prompt_tells_jarvis_he_can_answer_where_from():
    """Данные без разрешения бесполезны: модель по правилам не обсуждает
    внутренности, и на «откуда знаешь» отвечала бы отказом."""
    low = BEHAVIOR.lower()
    assert "where" in low and "know" in low, (
        "промпт не разрешает отвечать, откуда взялся факт")


def test_the_prompt_forbids_passing_a_guess_off_as_the_owners_words():
    low = BEHAVIOR.lower()
    assert "inferred" in low, (
        "промпт не объясняет разницу между догадкой и словами владельца")


# ── права не поехали (решение владельца фазы 1г) ────────────────────────

def test_recalling_is_still_free_for_the_owner():
    from core import security
    pol = security.SECURITY_POLICY["recall_memory"]
    assert pol.risk == "low", (
        "risk вырос — владельца начнут спрашивать подтверждение")
    assert pol.status == "allowed"


def test_a_subagent_may_still_read_the_memory():
    """Решение владельца: под-агент читать может, писать и стирать — нет."""
    from core import fences, task_context
    root = task_context.TaskCtx(run_id="R-test", task_id="T-1")
    ctx = root.child(agent_role="pc_operator", task_id="T-2")
    verdict = fences.check("recall_memory", ctx=ctx)
    assert not verdict.blocked, (
        "под-агенту закрыли чтение памяти — это не то, что решил владелец")


def test_recalling_is_still_not_silent():
    """save/forget молчат, recall — нет: найденное надо произнести."""
    block = MAIN.split('if name == "recall_memory":')[1][:1200]
    assert '"silent": True' not in block, (
        "recall стал молчаливым — владелец не услышит найденное")
