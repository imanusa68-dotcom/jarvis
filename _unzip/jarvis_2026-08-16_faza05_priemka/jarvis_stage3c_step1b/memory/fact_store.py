# memory/fact_store.py
"""
Stage 3B.1 - memory v2 storage layer: schema access, normalisation, import.

WHY THIS EXISTS
---------------
Memory v1 (memory/long_term.json) is durable (Stage 3.0) and reaches the
prompt (Stage 3.1), but it is *dumb*:

  - it stores LABELS, not meaning ('soon', 'schedule updated, disregard
    previous'), so nothing downstream can ever sound intelligent;
  - it has no provenance - you cannot tell what the user actually SAID from
    what the model GUESSED;
  - the whole file is dumped into the prompt and cut at 1997 chars, which
    silently drops the tail once memory grows.

This module is the storage half of the fix. It is ADDITIVE: nothing in the
live request pipeline imports it yet (see Stage 3B.3), so 3B.1 cannot change
Jarvis's behaviour. Tests are the only consumer today.

DESIGN NOTES (hard-won, do not "simplify" away)
-----------------------------------------------
1. NORMALISATION HAPPENS IN PYTHON, NEVER IN SQL.
   SQLite's built-in lower() is ASCII-only: it does NOT lowercase Cyrillic
   without the ICU extension. Any attempt to fold case inside a trigger would
   silently do nothing for Russian. So we compute `search_text` in Python and
   store it as a real column; the triggers merely copy it.

2. TWO FTS INDEXES, NOT ONE. Measured, not assumed:
     - trigram   handles Russian morphology ('ноч' finds 'по ночам') but is
                 BLIND to queries shorter than 3 chars - a query for 'AI'
                 returns nothing, and 'AI automation' is a real stored fact.
     - unicode61 handles short tokens and prefix search, but cannot match
                 inside a word.
   Neither alone is sufficient. We index both and merge the scores.

3. 'ё' IS NORMALISED TO 'е' ON BOTH WRITE AND QUERY. Measured: without this,
   a search for 'зелен' does not find 'зелёный'.

4. JUNK IS HIDDEN, NEVER DELETED. Legacy garbage gets a low confidence so it
   stops reaching the prompt, but the row survives. Automation does not get to
   decide which of the user's data is worthless.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.store import config_get, config_set

# One-shot marker so the JSON import can never run twice and double facts.
IMPORT_MARKER = "memory_v2.legacy_import"

# Confidence ladder. Anything below PROMPT_MIN_CONFIDENCE is stored but never
# offered to the prompt.
CONFIDENCE_EXPLICIT = 1.0   # the user said it in so many words
CONFIDENCE_INFERRED = 0.7   # the model worked it out from context
CONFIDENCE_LEGACY = 0.6   # imported from memory v1, provenance unknown
CONFIDENCE_JUNK = 0.2   # recognised garbage: kept on disk, hidden from view
PROMPT_MIN_CONFIDENCE = 0.3

SOURCE_EXPLICIT = "explicit"
SOURCE_INFERRED = "inferred"
SOURCE_LEGACY = "legacy"

# Values that say nothing about a person. Matched against the NORMALISED value,
# deliberately short and conservative - when in doubt we keep the fact visible.
_JUNK_EXACT = {
    "soon", "tbd", "n/a", "na", "none", "null", "unknown", "updated",
    "changed", "ok", "yes", "no", "true", "false", "-", "?",
    "скоро", "обновлено", "неизвестно", "нет данных",
}
_JUNK_SUBSTRINGS = (
    "disregard previous",
    "disregard the previous",
    "ignore previous",
    "schedule updated",
    "не учитывать предыдущ",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- Normalisation ------------------------------------------------------------

def normalize(text) -> str:
    """Fold text into its searchable form: lowercase, ё->е, single spaces.

    Python's str.lower() is Unicode-aware (unlike SQLite's), which is exactly
    why this lives here and not in a trigger.
    """
    if text is None:
        return ""
    folded = str(text).lower().replace("ё", "е")
    return re.sub(r"\s+", " ", folded).strip()


def build_search_text(key: str, category: str, value: str,
                      verbatim: str | None = None) -> str:
    """Everything a future query might plausibly aim at, in one folded blob.

    The key is de-slugged ('work_schedule' -> 'work schedule') so a natural
    question can match it. Both languages live in the same blob on purpose:
    the value is often English while the user's own words are Russian.
    """
    parts = [
        str(key or "").replace("_", " "),
        str(category or "").replace("_", " "),
        str(value or ""),
        str(verbatim or ""),
    ]
    return normalize(" ".join(p for p in parts if p))


def looks_like_junk(value) -> bool:
    """True for values that carry no information about the person.

    Kept deliberately narrow. A false positive hides a real fact, which is a
    worse failure than leaving one piece of garbage visible.
    """
    folded = normalize(value)
    if not folded or len(folded) < 2:
        return True
    if folded in _JUNK_EXACT:
        return True
    return any(marker in folded for marker in _JUNK_SUBSTRINGS)


# -- Writes -------------------------------------------------------------------

def upsert_fact(conn: sqlite3.Connection, *, key: str, category: str,
                value: str, verbatim: str | None = None,
                source: str = SOURCE_EXPLICIT,
                confidence: float | None = None,
                pinned: bool = False, lang: str | None = None,
                updated_at: str | None = None) -> int:
    """Insert or update one fact, returning its row id.

    A repeat of the same (category, key) UPDATES the live row rather than
    piling up duplicates. History of superseded facts is a 3B.2 concern.
    """
    key = (str(key or "").strip() or "fact").lower()
    category = (str(category or "").strip() or "notes").lower()
    value = str(value if value is not None else "").strip()

    if confidence is None:
        confidence = {
            SOURCE_EXPLICIT: CONFIDENCE_EXPLICIT,
            SOURCE_INFERRED: CONFIDENCE_INFERRED,
            SOURCE_LEGACY: CONFIDENCE_LEGACY,
        }.get(source, CONFIDENCE_INFERRED)
    if looks_like_junk(value):
        confidence = min(float(confidence), CONFIDENCE_JUNK)

    search_text = build_search_text(key, category, value, verbatim)
    stamp = updated_at or _now()

    row = conn.execute(
        "SELECT id, created_at FROM memory_fact "
        "WHERE category=? AND key=? AND superseded_by IS NULL",
        (category, key),
    ).fetchone()

    if row is not None:
        conn.execute(
            "UPDATE memory_fact SET value=?, verbatim=?, search_text=?, "
            "lang=?, source=?, confidence=?, pinned=?, updated_at=? "
            "WHERE id=?",
            (value, verbatim, search_text, lang, source, float(confidence),
             1 if pinned else 0, stamp, row["id"]),
        )
        return int(row["id"])

    cur = conn.execute(
        "INSERT INTO memory_fact (key, category, value, verbatim, search_text, "
        "lang, source, confidence, pinned, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, category, value, verbatim, search_text, lang, source,
         float(confidence), 1 if pinned else 0, stamp, stamp),
    )
    return int(cur.lastrowid)


def forget_fact(conn: sqlite3.Connection, *, key: str,
                category: str | None = None) -> int:
    """Really delete matching facts. Returns how many rows went away.

    Stage 3.1 lesson: a write path without a matching un-write path forces the
    model to confabulate. Deletion here is genuine, not a tombstone.
    """
    key = normalize(key)
    if category:
        cur = conn.execute(
            "DELETE FROM memory_fact WHERE lower(key)=? AND lower(category)=?",
            (key, normalize(category)),
        )
    else:
        cur = conn.execute("DELETE FROM memory_fact WHERE lower(key)=?", (key,))
    return int(cur.rowcount or 0)


def list_facts(conn: sqlite3.Connection, *, include_hidden: bool = False) -> list:
    """All live facts, newest first. Hidden (low-confidence) rows on request."""
    sql = ("SELECT * FROM memory_fact WHERE superseded_by IS NULL"
           + ("" if include_hidden else " AND confidence >= ?")
           + " ORDER BY pinned DESC, updated_at DESC, id DESC")
    params = () if include_hidden else (PROMPT_MIN_CONFIDENCE,)
    return [dict(r) for r in conn.execute(sql, params)]


# -- Search -------------------------------------------------------------------

# Words that carry no retrieval signal. Without this list the prefix backoff
# below turns "какой у меня график" into a search for "как", which matches
# half the database.
_STOPWORDS = {
    "и", "в", "во", "не", "что", "на", "я", "с", "со", "как", "а", "то", "все",
    "он", "она", "они", "так", "но", "да", "ты", "��", "��", "же", "вы", "за",
    "бы", "по", "мне", "вот", "от", "меня", "нет", "о", "из", "когда", "ли",
    "если", "уже", "или", "ни", "до", "вас", "вам", "там", "где", "для", "мы",
    "тебя", "их", "чем", "без", "чего", "под", "кто", "это", "этот", "того",
    "этого", "какой", "какая", "чтобы", "про", "при", "об", "над", "тот",
    "эти", "нас", "тем", "том", "есть", "был", "была", "было", "быть",
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "of",
    "to", "in", "on", "for", "my", "me", "i", "you", "your", "what", "about",
    "do", "does", "did", "it", "that", "this", "with",
}


def _terms(folded_query: str) -> list:
    words = [t for t in re.split(r"[^0-9A-Za-z\u0400-\u04ff]+", folded_query) if t]
    meaningful = [t for t in words if t not in _STOPWORDS]
    # If the question was nothing BUT stopwords, searching them is still less
    # wrong than pretending the user said nothing.
    return meaningful or words


def search_facts(conn: sqlite3.Connection, query: str, *, limit: int = 8,
                 min_confidence: float = PROMPT_MIN_CONFIDENCE) -> list:
    """Find facts by meaning-ish relevance. Free, offline, no model involved.

    Two indexes are consulted and their scores added:
      - unicode61 with prefix, which is the ONLY one that can answer a
        two-letter query such as 'AI';
      - trigram, which is the only one that survives Russian inflection.
    """
    folded = normalize(query)
    terms = _terms(folded)
    if not terms:
        return []

    scores: dict = {}
    fetch = max(limit * 5, 20)

    def collect(table: str, expression: str, weight: float) -> bool:
        """Add one index's opinion to the running score. True if it matched."""
        try:
            rows = conn.execute(
                f"SELECT rowid AS rid, bm25({table}) AS score "
                f"FROM {table} WHERE {table} MATCH ? "
                f"ORDER BY score LIMIT ?",
                (expression, fetch),
            ).fetchall()
        except sqlite3.OperationalError:
            return False  # a malformed MATCH must never take the assistant down
        for row in rows:
            # bm25 returns negative numbers, better matches being more negative.
            rid = int(row["rid"])
            scores[rid] = scores.get(rid, 0.0) + weight * (-float(row["score"]))
        return bool(rows)

    for term in terms:
        matched_word = collect("memory_fact_word", f'"{term}"*', 1.0)
        matched_tri = collect("memory_fact_tri", f'"{term}"', 1.0) \
            if len(term) >= 3 else False
        if matched_word or matched_tri:
            continue

        # PREFIX BACKOFF - this is what makes Russian actually work.
        # Both indexes match literally, so a question asked in one grammatical
        # form cannot find a fact stored in another: 'ночной' is nowhere inside
        # 'работает по ночам', and 'автоматизация' is nowhere inside
        # 'автоматизацию'. Measured: only a 3-character stem bridges the gap.
        # We shorten the word step by step and stop at the FIRST length that
        # finds anything, with a decaying weight so a loose stem can never
        # outrank a real match. Only used when the exact term found nothing,
        # so precision is preserved whenever precision is available.
        for length, weight in ((5, 0.50), (4, 0.35), (3, 0.25)):
            if len(term) <= length:
                continue
            if collect("memory_fact_tri", f'"{term[:length]}"', weight):
                break

    if not scores:
        return []

    placeholders = ",".join("?" for _ in scores)
    rows = conn.execute(
        f"SELECT * FROM memory_fact WHERE id IN ({placeholders}) "
        f"AND superseded_by IS NULL AND confidence >= ?",
        (*scores.keys(), float(min_confidence)),
    ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        item["score"] = scores.get(int(row["id"]), 0.0) \
            + (0.5 if row["pinned"] else 0.0) \
            + float(row["confidence"])
        results.append(item)
    results.sort(key=lambda r: (-r["score"], r["id"]))
    return results[:limit]


# -- Index health -------------------------------------------------------------

def fts_out_of_sync(conn: sqlite3.Connection) -> bool:
    """True when an index has drifted from the table.

    A drifted index fails SILENTLY - search simply returns nothing while the
    data is right there. That is the same class of bug as losing the data.
    """
    # Two traps here, both measured on this exact SQLite build:
    #   - count(*) on the FTS table LIES. An external-content table reads its
    #     rows back out of memory_fact, so the count still matches after the
    #     index has been wiped.
    #   - plain 'integrity-check' ALSO passes on a wiped index. Only the
    #     variant that compares the index against the content table fails.
    # The shadow docsize table is the cheap direct answer, so it goes first.
    facts = int(conn.execute("SELECT count(*) FROM memory_fact").fetchone()[0])
    for table in ("memory_fact_word", "memory_fact_tri"):
        try:
            indexed = int(conn.execute(
                f"SELECT count(*) FROM {table}_docsize").fetchone()[0])
        except sqlite3.DatabaseError:
            return True
        if indexed != facts:
            return True
        try:
            conn.execute(
                f"INSERT INTO {table}({table}, rank) "
                f"VALUES('integrity-check', 1)")
        except sqlite3.DatabaseError:
            return True
    return False


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild both indexes from the table. Cheap: ~20 ms for 5000 facts."""
    for table in ("memory_fact_word", "memory_fact_tri"):
        conn.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")


# -- Legacy import ------------------------------------------------------------

MIRROR_FINGERPRINT = "memory_v2.mirror_fingerprint"


def _memory_fingerprint(path=None) -> str:
    """Cheap identity of memory v1 on disk: modification time and size.

    Two stat() calls instead of re-reading and re-indexing thousands of rows.
    """
    try:
        if path is None:
            from memory.memory_manager import _memory_path
            path = _memory_path()
        st = Path(path).stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except Exception:
        return ""


def sync_if_stale(conn: sqlite3.Connection, *, memory=None, path=None,
                  force: bool = False) -> dict:
    """Re-mirror memory v1 only when it has actually changed since last time.

    Measured before this existed: a full mirror costs 2 ms at 10 facts, 216 ms
    at 1000 and 2.5 SECONDS at 10 000 - and recall paid it on every single
    lookup, mid-conversation, out loud. A voice assistant that goes silent for
    two and a half seconds to re-read something it already read is broken, even
    though every individual step is correct.

    So the mirror now runs when the file changed, and writes record their own
    fact directly (see note_fact), which keeps the file and the index in step
    without a full pass. A fingerprint mismatch is still authoritative: if
    anything edits the JSON behind our back, the next lookup catches up.
    """
    # A caller that hands us a memory dict is telling us what the truth is -
    # a test, the report tool, a repair path. The fingerprint stored in config
    # describes a DIFFERENT file, so trusting it here would skip the sync and
    # answer out of stale rows. Correctness wins on that path: mirror what we
    # were handed, and never adopt a fingerprint for a file we did not read.
    given_memory = memory is not None and path is None
    current = "" if given_memory else _memory_fingerprint(path)
    if not force and current and config_get(conn, MIRROR_FINGERPRINT) == current:
        return {"skipped": True, "imported": 0, "hidden": 0, "removed": 0,
                "first_run": False}

    if memory is None:
        from memory.memory_manager import load_memory
        memory = load_memory()
    result = import_legacy_memory(conn, memory)
    if current:
        config_set(conn, MIRROR_FINGERPRINT, current)
    return result


def note_fact(category: str, key: str, value: str, *, verbatim: str | None = None,
              deleted: bool = False, conn=None, path=None) -> None:
    """Record one fact in the index at the moment it is saved or forgotten.

    This is what lets the mirror stay lazy: the write path updates both copies,
    so a fact said one second ago is searchable one second later without any
    full re-index. It is also the only way provenance can ever be honest -
    facts that arrive through here are marked as explicitly said, instead of
    every row claiming source=legacy, confidence 0.60 like it used to.

    Never raises: memory v1 on disk is still the durable copy, so failing to
    update the index must not fail the save the user asked for.
    """
    owns_conn = conn is None
    try:
        from core import writer

        def job(c):
            if deleted:
                forget_fact(c, key=key, category=category)
            else:
                upsert_fact(c, key=key, category=category, value=str(value),
                            verbatim=verbatim, source=SOURCE_EXPLICIT,
                            confidence=CONFIDENCE_EXPLICIT)
            # The JSON has just been rewritten, and this row already reflects
            # it - so adopt the new fingerprint instead of forcing a full
            # re-mirror on the next lookup.
            fingerprint = _memory_fingerprint(path)
            if fingerprint:
                config_set(c, MIRROR_FINGERPRINT, fingerprint)

        # БЛОК 7. Раньше здесь открывалось СВОЁ соединение — и не одно на
        # сохранение, а буквально на каждый факт: главный цикл зовёт эту
        # функцию в цикле по фактам, из фонового потока. Теперь запись идёт
        # через кассу, то есть в общей очереди и на одном соединении.
        #
        # Заодно появилась транзакция, которой здесь не было вовсе: правка
        # факта и отметка об отпечатке файла памяти — это ОДНО событие. Порознь
        # они однажды разойдутся, и тогда поиск по памяти начнёт молча
        # отставать от самого файла памяти.
        if owns_conn:
            writer.write(job, label="fact_store.note_fact")
        else:
            writer.write_on(conn, job)
    except Exception as exc:
        print(f"[Memory] index update skipped (non-fatal): {exc}")
        # Р6, шаг 33.2: здесь НЕ бросаем дальше. JSON — главная копия,
        # факт уже сохранён, и падение превратило бы честное «запомнил»
        # в неправду. Но если причина в том, что хранилище новее кода,
        # владелец обязан услышать это словами: иначе поиск по памяти
        # будет отвечать «не знаю» о факте, сказанном минуту назад.
        try:
            from core.store import StoreError as _StoreError
        except Exception:
            _StoreError = ()
        if isinstance(exc, _StoreError):
            print("[Memory] Указатель памяти новее программы: свежие факты "
                  "не попадут в поиск. Верните новую сборку — сам файл "
                  "памяти цел.")


def recall(query: str, *, limit: int = 5, conn=None, memory=None) -> str:
    """Search memory mid-conversation and answer in plain text.

    Until now this index existed only for the report tool: the user could search
    their own memory from a terminal, and Jarvis could not. Everything it knew
    had to be in the prompt block at connect time, which is both finite and
    frozen for the whole session. So the honest failure mode was Jarvis saying
    "I don't know" about a fact sitting three centimetres away on disk.

    memory v1 (the JSON file) is still the master, so we mirror it first. It is
    a handful of rows; correctness is worth far more here than the microseconds.

    The returned text is deliberately blunt about finding nothing, because the
    one thing worse than forgetting is inventing a memory to fill the silence.
    """
    owns_conn = conn is None
    if owns_conn:
        # БЛОК 7. Это ЧТЕНИЕ, которое иногда пишет: `sync_if_stale` может
        # долить в указатель факты из файла памяти. Поэтому две половины
        # разведены — правка идёт через кассу, поиск по своему соединению.
        #
        # Почему это важно именно здесь: по замеру в шапке `sync_if_stale`
        # полное зеркалирование стоит до 2,5 с на десяти тысячах фактов.
        # Такое чтение под замком записи остановило бы журнал и талоны на
        # те же 2,5 с посреди разговора.
        from core import writer
        try:
            writer.write(lambda c: sync_if_stale(c, memory=memory),
                         label="fact_store.recall.sync")
        except Exception as exc:                     # never break the answer
            print(f"[Memory] recall sync failed (non-fatal): {exc}")
        try:
            hits = search_facts(writer.reader(), query, limit=limit)
        except Exception as exc:
            print(f"[Memory] recall failed (non-fatal): {exc}")
            hits = []
    else:
        try:
            # Do NOT load memory here just to hand it over: passing a dict tells
            # sync_if_stale that we know better than the file, which forces a full
            # re-mirror every lookup - the 2.5 second pause we just removed. When
            # nobody hands us one, let the fingerprint decide whether to read at all.
            try:
                sync_if_stale(conn, memory=memory)
            except Exception as exc:                 # never break the answer
                print(f"[Memory] recall sync failed (non-fatal): {exc}")
            hits = search_facts(conn, query, limit=limit)
        except Exception as exc:
            print(f"[Memory] recall failed (non-fatal): {exc}")
            hits = []

    if not hits:
        return (f"Nothing saved about '{query}'. Say so plainly - "
                "do not invent a memory.")

    lines = [f"Found {len(hits)} in long-term memory:"]
    for hit in hits[:limit]:
        line = f"- [{hit['category']}/{hit['key']}] {hit['value']}"
        if hit.get("verbatim"):
            line += f" (their exact words: \u00ab{hit['verbatim']}\u00bb)"
        line += provenance(hit)
        lines.append(line)
    return "\n".join(lines)


# -- Provenance ---------------------------------------------------------------
#
# ФАЗА 1Д. «Откуда ты это знаешь?»
#
# Владелец отказался от отдельного инструмента под этот вопрос:
#     «а разве он не сможет этого делать? ведь он же должен быть
#      универсальным... ясно же что он должен уметь делать.»
# Он прав. Обе нужные величины УЖЕ лежали в строке факта, просто не
# доезжали до модели, и на прямой вопрос отвечать было нечем.
#
# Что теперь доезжает и почему именно это:
#
#   source — КТО автор факта. Три значения, и разница между ними не
#     косметическая. `explicit` — владелец сказал сам. `inferred` —
#     Джарвис вывел из разговора, владелец такого не говорил.
#     `legacy` — приехало из старого файла памяти, авторство утеряно.
#     Пока эти три выглядели одинаково, на вопрос «я тебе это говорил?»
#     модель отвечала наугад и выдавала свою догадку за слова владельца.
#     Это не неудобство, это ложь, и она копится молча.
#
#   created_at — КОГДА. Заполняется при каждой записи, но в текст не
#     попадало, поэтому «когда я это сказал?» оставалось без ответа.
#     Отдаём ТОЛЬКО календарный день: строку вида 2026-07-14T09:12:00+00:00
#     произнести вслух невозможно, а владельцу нужен день, не секунда.
#
# ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕ ДЕЛАЕТСЯ:
#   - Не трогаем бюджет блока памяти. Замерено: `recall` вызывается ровно
#     из одного места (обработчик recall_memory в main.py) и в блок промпта
#     не попадает. Удлинение строки не отнимает места у фактов.
#   - Не переводим на местный часовой пояс. Сдвиг дня на границе суток
#     ради этого не стоит риска; день записи и так точнее, чем нужно.
#   - Ничего не поднимаем на голос сами. Здесь только данные; говорить
#     или молчать решает модель по правилам поведения.
#
# Формат сознательно английский и в скобках — как соседняя пометка про
# дословные слова: это служебная подсказка модели, а не текст для чтения
# вслух. Модель переведёт сама, на языке владельца.

_ORIGIN_WORDS = {
    SOURCE_EXPLICIT: "from them",
    SOURCE_INFERRED: "inferred by you, not stated",
    SOURCE_LEGACY: "from older memory, author unknown",
}


def _calendar_day(stamp) -> str:
    """Календарный день из отметки времени, или пусто, если не разобрать.

    Схема требует created_at, но факты приезжают и из старого JSON, где
    даты может не быть вовсе или лежать что угодно. Это ЧТЕНИЕ: оно
    обязано ответить фактом даже без даты, а не упасть. Поэтому любая
    неожиданность здесь — просто отсутствие даты, а не исключение.
    """
    text = str(stamp or "").strip()
    if not text:
        return ""
    head = text[:10]
    try:
        datetime.strptime(head, "%Y-%m-%d")
    except ValueError:
        return ""
    return head


def provenance(hit) -> str:
    """Служебная пометка «откуда и когда» для одной находки.

    Отдельная функция, а не строчка внутри цикла: так её видно стражам
    и так её можно позвать из другого места, не переписывая формат.
    """
    parts = []
    origin = _ORIGIN_WORDS.get(str(hit.get("source") or "").strip())
    if origin:
        parts.append(origin)
    day = _calendar_day(hit.get("created_at"))
    if day:
        parts.append(f"saved {day}")
    if not parts:
        return ""
    return " [" + ", ".join(parts) + "]"


def import_legacy_memory(conn: sqlite3.Connection, memory: dict,
                         *, force: bool = False) -> dict:
    """Mirror memory v1 (the nested JSON dict) into memory_fact.

    This is a SYNC, not a one-time import, and it is meant to be run again and
    again. It first shipped guarded by a run-once marker, and that was wrong:
    the live assistant keeps writing to memory v1, so every fact said after
    the first run stayed invisible here while the marker cheerfully reported
    'already done'. A mirror must be allowed to catch up.

    Facts removed from v1 are removed here too. Without that, a re-sync would
    resurrect anything the user asked to forget, and Stage 3.1's rule that
    forgetting must be real would quietly stop holding. Only rows this
    importer owns (source=legacy) can be deleted; anything the user said in
    their own words is never touched.

    The source JSON is never modified or deleted - Invariant 9. Recognised
    junk is imported too, just with a confidence that keeps it out of sight.
    """
    imported = 0
    hidden = 0
    seen = set()
    for category, entries in (memory or {}).items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if isinstance(entry, dict):
                value = entry.get("value")
                stamp = entry.get("updated")
                # The user's own sentence, if v1 kept one. Facts saved before this
                # existed simply have none, and stay English-only until re-said.
                said = entry.get("said")
                said = str(said).strip() if said is not None and str(said).strip() else None
            else:
                value = entry
                stamp = None
                said = None
            if value is None or not str(value).strip():
                continue
            confidence = CONFIDENCE_JUNK if looks_like_junk(value) \
                else CONFIDENCE_LEGACY
            upsert_fact(
                conn, key=key, category=category, value=str(value),
                verbatim=said,            # None for anything saved before v2
                source=SOURCE_LEGACY, confidence=confidence,
                updated_at=stamp or None,
            )
            seen.add((normalize(category), normalize(key)))
            imported += 1
            if confidence < PROMPT_MIN_CONFIDENCE:
                hidden += 1

    removed = 0
    for row in conn.execute(
        "SELECT id, category, key FROM memory_fact WHERE source=?",
        (SOURCE_LEGACY,),
    ).fetchall():
        if (normalize(row["category"]), normalize(row["key"])) not in seen:
            conn.execute("DELETE FROM memory_fact WHERE id=?", (row["id"],))
            removed += 1

    first_run = not config_get(conn, IMPORT_MARKER)
    config_set(conn, IMPORT_MARKER, _now())
    return {"imported": imported, "hidden": hidden, "removed": removed,
            "first_run": first_run, "skipped": False}
