"""
Stage 3B.1 - memory v2 storage: schema, index and legacy import.

WHAT THIS PINS DOWN
-------------------
3B.1 is deliberately INERT: it adds a table and a module, and changes nothing
about how Jarvis behaves. So these tests prove two different things:

  1. the new storage layer genuinely works (including the two search blind
     spots that were MEASURED before any of this was written, and which would
     otherwise have shipped as production bugs);
  2. the live pipeline is still untouched, so this build cannot regress
     anything the user already relies on.

The two measured blind spots, kept as permanent regression tests:
  - trigram search cannot see queries shorter than 3 characters, so a search
    for 'AI' found NOTHING while 'AI automation' sat in memory;
  - 'зелен' did not match 'зелёный', because ё and е are different
    characters as far as SQLite is concerned.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]

from core import store  # noqa: E402
from memory import fact_store as fs  # noqa: E402


def _fresh_db():
    """A brand-new migrated jarvis.db in a throwaway directory."""
    d = Path(tempfile.mkdtemp(prefix="jarvis-stage3b-"))
    return store.open_store(d / "jarvis.db")


def _legacy_memory() -> dict:
    """Memory v1 exactly as it looks on the user's machine today, junk included."""
    return {
        "identity": {"favourite_colour": {"value": "любимый цвет зелёный",
                                          "updated": "2026-07-20"}},
        "projects": {"main_project": {"value": "AI automation",
                                      "updated": "2026-07-21"}},
        "habits": {"work_schedule": {"value": "schedule updated, disregard previous",
                                     "updated": "2026-07-25"}},
        "wishes": {"someday": {"value": "soon", "updated": "2026-07-01"}},
        "notes": "not a dict - must be skipped, not crash",
    }


# ---------------------------------------------------------------- schema


def test_migration_reaches_v5_and_builds_the_schema():
    conn = _fresh_db()
    # ">= 5", not "== 5": what this test cares about is that the memory schema
    # is there. Pinning the exact number made every LATER migration (3A's
    # consent_ticket was the first) look like a memory regression.
    assert store._user_version(conn) >= 5
    for table in ("memory_fact", "memory_fact_word", "memory_fact_tri"):
        assert store._table_exists(conn, table), f"missing {table}"


def test_an_existing_v4_database_upgrades_in_place():
    """The user's real DB is at v4 with data in it. Upgrading must not lose it."""
    d = Path(tempfile.mkdtemp(prefix="jarvis-stage3b-up-"))
    path = d / "jarvis.db"

    conn = store.connect(path)
    store.migrate(conn, [m for m in store.JARVIS_MIGRATIONS if m[0] <= 4])
    store.config_set(conn, "pre_existing", "keep me")
    assert store._user_version(conn) == 4
    conn.close()

    conn = store.open_store(path)
    assert store._user_version(conn) >= 5
    assert store.config_get(conn, "pre_existing") == "keep me"
    assert store._table_exists(conn, "memory_fact")


def test_running_migrations_twice_is_a_no_op():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="colour", category="identity", value="green")
    store.migrate(conn, store.JARVIS_MIGRATIONS)
    assert len(fs.list_facts(conn)) == 1


# ---------------------------------------------------------------- search


def test_a_saved_fact_can_be_found_again():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="work_schedule", category="habits",
                   value="works at night", verbatim="я работаю по ночам")
    hits = fs.search_facts(conn, "ночной график")
    assert hits, "stored fact was not findable"
    assert hits[0]["key"] == "work_schedule"


def test_short_query_ai_is_found_despite_trigram_blindness():
    """MEASURED BUG: trigram returns nothing for a 2-character query.

    'AI automation' is a real fact in the user's memory, and 'AI' is the
    obvious thing to ask for. The word index exists precisely for this.
    """
    conn = _fresh_db()
    fs.upsert_fact(conn, key="main_project", category="projects",
                   value="AI automation")
    hits = fs.search_facts(conn, "AI")
    assert hits, "two-letter query found nothing - trigram blind spot is back"
    assert hits[0]["value"] == "AI automation"


def test_yo_and_ye_are_interchangeable():
    """MEASURED BUG: 'зелен' did not match 'зелёный'."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="favourite_colour", category="identity",
                   value="любимый цвет зелёный")
    assert fs.search_facts(conn, "зеленый"), "ё/е normalisation is broken"
    assert fs.search_facts(conn, "зелёный")


def test_russian_inflection_still_matches():
    """'ночь' vs 'по ночам': different endings, same meaning."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="work_schedule", category="habits",
                   value="работает по ночам")
    assert fs.search_facts(conn, "ноч"), "trigram substring search is broken"


def test_the_users_own_words_are_searchable():
    """Answer 1 of the five: verbatim is stored, and it is not decoration."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="work_schedule", category="habits",
                   value="works at night", verbatim="я работаю по ночам")
    hits = fs.search_facts(conn, "работаю")
    assert hits, "the user's original wording is not searchable"
    assert hits[0]["verbatim"] == "я работаю по ночам"


def test_an_unknown_term_returns_nothing_rather_than_noise():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="colour", category="identity", value="green")
    assert fs.search_facts(conn, "квантовая телепортация") == []


def test_a_malformed_query_cannot_crash_search():
    """FTS5 has its own query syntax; user speech is not it."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="colour", category="identity", value="green")
    for nasty in ['"', "AND OR NOT", "*", "^", "", "   ", "a" * 500]:
        fs.search_facts(conn, nasty)  # must not raise


# ---------------------------------------------------------------- writes


def test_saving_the_same_key_twice_updates_instead_of_duplicating():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="city", category="identity", value="Moscow")
    fs.upsert_fact(conn, key="city", category="identity", value="Kazan")
    facts = fs.list_facts(conn)
    assert len(facts) == 1
    assert facts[0]["value"] == "Kazan"


def test_an_updated_fact_leaves_no_stale_index_entry():
    """The old value must stop being findable, or search will contradict itself."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="city", category="identity", value="Moscow")
    fs.upsert_fact(conn, key="city", category="identity", value="Kazan")
    assert fs.search_facts(conn, "Kazan")
    assert not fs.search_facts(conn, "Moscow"), "stale index entry survived"


def test_forgetting_removes_the_fact_from_the_index_too():
    conn = _fresh_db()
    fs.upsert_fact(conn, key="work_schedule", category="habits",
                   value="works at night")
    assert fs.forget_fact(conn, key="work_schedule", category="habits") == 1
    assert fs.search_facts(conn, "night") == []
    assert fs.list_facts(conn, include_hidden=True) == []


def test_a_guess_is_recorded_as_a_guess():
    """Answer 5 of the five: an inference must never look like a stated fact."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="mood", category="notes", value="probably tired",
                   source=fs.SOURCE_INFERRED)
    fact = fs.list_facts(conn)[0]
    assert fact["source"] == fs.SOURCE_INFERRED
    assert fact["confidence"] < fs.CONFIDENCE_EXPLICIT


# ---------------------------------------------------------------- junk


def test_junk_is_hidden_but_never_deleted():
    """Answer 2 of the five. The row stays on disk; it just stops being quoted.

    'schedule updated, disregard previous' is the actual garbage sitting in
    the user's memory right now, recited back as if it meant something.
    """
    conn = _fresh_db()
    fs.upsert_fact(conn, key="work_schedule", category="habits",
                   value="schedule updated, disregard previous")
    assert fs.list_facts(conn) == [], "junk still reaches the prompt"
    hidden = fs.list_facts(conn, include_hidden=True)
    assert len(hidden) == 1, "junk was destroyed instead of hidden"
    assert hidden[0]["confidence"] <= fs.CONFIDENCE_JUNK


def test_real_facts_are_not_mistaken_for_junk():
    """A false positive here silently erases something the user cares about."""
    conn = _fresh_db()
    for value in ("любимый цвет зелёный", "AI automation", "работает по ночам",
                  "жена Анна", "Python"):
        assert not fs.looks_like_junk(value), value


# ---------------------------------------------------------------- import


def test_legacy_memory_is_imported():
    conn = _fresh_db()
    report = fs.import_legacy_memory(conn, _legacy_memory())
    assert report["imported"] == 4      # the non-dict 'notes' entry is skipped
    assert report["hidden"] == 2        # 'disregard previous' + 'soon'

    visible = {f["key"] for f in fs.list_facts(conn)}
    assert visible == {"favourite_colour", "main_project"}
    assert len(fs.list_facts(conn, include_hidden=True)) == 4


def test_importing_twice_does_not_double_the_facts():
    conn = _fresh_db()
    first = fs.import_legacy_memory(conn, _legacy_memory())
    second = fs.import_legacy_memory(conn, _legacy_memory())
    assert second["imported"] == first["imported"]
    assert second["removed"] == 0
    assert len(fs.list_facts(conn, include_hidden=True)) == 4


def test_a_later_sync_picks_up_facts_said_after_the_first_one():
    """The bug that shipped in 3B.1 and was caught on the user's machine.

    The importer stopped after its first run, but the live assistant keeps
    writing to memory v1. Everything said in the next conversation stayed
    invisible, and searching for it honestly returned nothing.
    """
    conn = _fresh_db()
    memory = {"preferences": {"drink": {"value": "матча"}}}
    fs.import_legacy_memory(conn, memory)

    memory["notes"] = {"productivity": {"value": "соображает ночью"}}
    report = fs.import_legacy_memory(conn, memory)

    assert report["skipped"] is False
    assert report["first_run"] is False
    keys = {f["key"] for f in fs.list_facts(conn)}
    assert keys == {"drink", "productivity"}
    assert fs.search_facts(conn, "ночью"), "new fact must be searchable"


def test_a_fact_forgotten_in_v1_does_not_come_back_on_the_next_sync():
    """Re-syncing must not resurrect what the user asked to forget."""
    conn = _fresh_db()
    memory = {"preferences": {"drink": {"value": "матча"},
                              "colour": {"value": "зелёный"}}}
    fs.import_legacy_memory(conn, memory)
    assert len(fs.list_facts(conn)) == 2

    memory["preferences"].pop("drink")          # the user said: forget it
    report = fs.import_legacy_memory(conn, memory)

    assert report["removed"] == 1
    assert {f["key"] for f in fs.list_facts(conn, include_hidden=True)} == \
        {"colour"}


def test_a_sync_never_deletes_what_the_user_said_in_their_own_words():
    """The mirror owns only what it imported. Direct facts are untouchable."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="drink", category="preferences",
                   value="кофе", verbatim="я вернулся к кофе")
    fs.import_legacy_memory(conn, {"notes": {"x": {"value": "старое"}}})
    fs.import_legacy_memory(conn, {})           # v1 emptied out entirely

    survivors = fs.list_facts(conn, include_hidden=True)
    assert {f["key"] for f in survivors} == {"drink"}
    assert survivors[0]["verbatim"] == "я вернулся к кофе"


def test_imported_facts_are_marked_as_legacy_not_as_the_users_words():
    """Memory v1 never kept the original phrasing, so we must not invent one."""
    conn = _fresh_db()
    fs.import_legacy_memory(conn, _legacy_memory())
    for fact in fs.list_facts(conn, include_hidden=True):
        assert fact["source"] == fs.SOURCE_LEGACY
        assert fact["verbatim"] is None


def test_import_never_modifies_the_json_file():
    """Invariant 9: the old file is a fallback, not a casualty."""
    conn = _fresh_db()
    path = Path(tempfile.mkdtemp(prefix="jarvis-stage3b-json-")) / "long_term.json"
    payload = _legacy_memory()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    fs.import_legacy_memory(conn, json.loads(path.read_text(encoding="utf-8")))

    assert path.exists()
    assert path.read_bytes() == before


def test_import_survives_a_malformed_memory_file():
    conn = _fresh_db()
    for junk in ({}, {"identity": None}, {"identity": {"k": None}},
                 {"identity": {"k": {"value": ""}}}):
        fs.import_legacy_memory(conn, junk, force=True)  # must not raise


# ---------------------------------------------------------------- health


def test_index_drift_is_detected_and_repairable():
    """A drifted index fails SILENTLY: the data is there, search says nothing."""
    conn = _fresh_db()
    fs.upsert_fact(conn, key="main_project", category="projects",
                   value="AI automation")
    assert not fs.fts_out_of_sync(conn)

    # Wreck the index behind the triggers' back, the way a crash or a manual
    # edit would.
    conn.execute("INSERT INTO memory_fact_word(memory_fact_word) VALUES('delete-all')")
    assert fs.fts_out_of_sync(conn), "drift went undetected"

    fs.rebuild_fts(conn)
    assert not fs.fts_out_of_sync(conn)
    assert fs.search_facts(conn, "AI"), "rebuild did not restore search"


def test_search_stays_fast_with_five_hundred_facts():
    conn = _fresh_db()
    for i in range(500):
        fs.upsert_fact(conn, key=f"fact_{i}", category="notes",
                       value=f"заметка номер {i} про проекты и автоматизацию")
    started = time.monotonic()
    hits = fs.search_facts(conn, "автоматизация", limit=8)
    elapsed = time.monotonic() - started
    assert hits
    assert len(hits) <= 8
    assert elapsed < 1.0, f"search took {elapsed:.3f}s"


# ---------------------------------------------------------------- inertness


def test_the_search_index_is_wired_in_through_recall_only():
    """Superseded tripwire, kept as a boundary.

    Through 3B.1 and 3B.2 this test demanded that nothing live touch
    fact_store: the index was a mirror, and an unwired mirror cannot corrupt
    anything. 3B.4 wires it in deliberately, with its own tests
    (test_stage3b34_budget_recall.py) - so the assertion flips rather than
    disappears. What must stay true is the shape of the wiring: v1 JSON is
    still the master, the prompt is still built from it, and the index is
    reached only through recall_memory.
    """
    main_src = (REPO / "main.py").read_text(encoding="utf-8", errors="ignore")
    assert "recall_memory" in main_src
    assert main_src.count("fact_store") == 1, \
        "main.py should reach the index only through recall"

    # 3B.5 opens a second, deliberate seam: the save path now writes through to
    # the index instead of letting every lookup rebuild it. What must stay true
    # is that a failure to index can never break a save - so every use here is
    # a lazy import inside a try, never a module-level dependency.
    mm_src = (REPO / "memory" / "memory_manager.py").read_text(
        encoding="utf-8", errors="ignore")
    for line in mm_src.splitlines():
        if "import" in line and "fact_store" in line:
            assert line.startswith("        ") or line.startswith("    "), \
                f"fact_store must be imported lazily, not at module level: {line!r}"
