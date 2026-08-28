"""Stage 3B.2b - remember the user's own words, not just an English label.

MEASURED ON 2026-07-25 (live, on the user's machine)
----------------------------------------------------
3B.2a worked: three standing instructions were saved. But look at what landed
in storage:

    [communication_habits/explanation_style]
        "Technical explanations without condescension or jargon"

The user never said any of those words. He said, in Russian, that he is not a
programmer by training and wants technical things explained without
condescension and without jargon. What was stored is a translation - a decent
summary, and completely unsearchable by the person who said it.

The measurement that proves the damage, from the same machine:

    --search "late night"  -> 5.76  hit
    --search "coffee"      -> 3.31  hit
    --search "ночной график" -> nothing
    --search "кофе"          -> nothing

So Jarvis could find its own translations and could not find the user's speech.
Recall in the language the user actually speaks was impossible BY CONSTRUCTION,
not by accident of ranking.

The fix keeps the English label (it is what the model reasons with, and the
prompt stays compact) and stores the original sentence beside it in `said`,
which flows into the searchable `verbatim` column. Both spellings now hit the
same fact.
"""

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MAIN = (REPO / "main.py").read_text(encoding="utf-8")

from core import store  # noqa: E402
from memory import fact_store as fs  # noqa: E402


def _memory_module():
    """A memory_manager bound to a throwaway state directory."""
    tmp = tempfile.mkdtemp(prefix="jarvis-stage3b2b-")
    os.environ["JARVIS_STATE_DIR"] = tmp
    import memory.memory_manager as mm
    importlib.reload(mm)
    return mm, Path(tmp)


def _fresh_db():
    tmp = tempfile.mkdtemp(prefix="jarvis-stage3b2b-db-")
    return store.open_store(Path(tmp) / "jarvis.db")


# ── the contract the model is given ──────────────────────────────────────────

def test_the_tool_asks_for_the_users_original_sentence():
    assert '"said"' in MAIN, "save_memory must accept the original wording"


def test_the_tool_forbids_translating_the_original():
    assert "Never translate it" in MAIN


def test_the_tool_explains_why_the_original_matters():
    # Without a reason the model treats optional fields as decoration.
    assert "words are how they will ask for this again later" in MAIN


def test_the_handler_actually_stores_what_was_said():
    assert 'entry["said"] = said.strip()' in MAIN


# ── storage in memory v1 (still the master file) ──────────────────────────────

def test_the_label_and_the_original_are_kept_side_by_side():
    mm, state = _memory_module()
    mm.update_memory({"communication_habits": {"explanation_style": {
        "value": "Technical explanations without jargon",
        "said": "Объясняй техническое без снисходительности, но и без жаргона",
    }}})
    entry = mm.load_memory()["communication_habits"]["explanation_style"]
    assert entry["value"] == "Technical explanations without jargon"
    assert entry["said"].startswith("Объясняй техническое")


def test_a_save_without_the_original_leaves_no_empty_field():
    mm, _ = _memory_module()
    mm.update_memory({"notes": {"k": {"value": "v"}}})
    assert "said" not in mm.load_memory()["notes"]["k"]


def test_a_later_sloppy_save_does_not_erase_the_original():
    # The model omits optional fields all the time. Losing the wording because
    # of one careless call would be a silent regression to the old behaviour.
    mm, _ = _memory_module()
    mm.update_memory({"preferences": {"drink": {
        "value": "coffee", "said": "я пью кофе литрами"}}})
    mm.update_memory({"preferences": {"drink": {"value": "black coffee"}}})
    entry = mm.load_memory()["preferences"]["drink"]
    assert entry["value"] == "black coffee"
    assert entry["said"] == "я пью кофе литрами"


def test_correcting_the_original_wording_is_recorded():
    mm, _ = _memory_module()
    mm.update_memory({"preferences": {"drink": {
        "value": "coffee", "said": "я пью кофе"}}})
    mm.update_memory({"preferences": {"drink": {
        "value": "coffee", "said": "я пью только чёрный кофе"}}})
    assert mm.load_memory()["preferences"]["drink"]["said"] == \
        "я пью только чёрный кофе"


# ── the point of the whole exercise: recall in the user's language ───────────

def test_the_original_wording_reaches_the_search_index():
    conn = _fresh_db()
    fs.import_legacy_memory(conn, {"preferences": {"favorite_drink": {
        "value": "coffee", "said": "я пью кофе литрами", "updated": "2026-07-25"}}})
    facts = fs.list_facts(conn)
    assert facts[0]["verbatim"] == "я пью кофе литрами"


def test_a_fact_is_findable_by_the_words_the_user_actually_said():
    conn = _fresh_db()
    fs.import_legacy_memory(conn, {"preferences": {"favorite_drink": {
        "value": "coffee", "said": "я пью кофе литрами", "updated": "2026-07-25"}}})
    hits = fs.search_facts(conn, "кофе")
    assert hits, "the exact miss measured on 2026-07-25 must not come back"
    assert hits[0]["key"] == "favorite_drink"


def test_the_english_label_still_finds_it_too():
    conn = _fresh_db()
    fs.import_legacy_memory(conn, {"preferences": {"favorite_drink": {
        "value": "coffee", "said": "я пью кофе литрами", "updated": "2026-07-25"}}})
    assert fs.search_facts(conn, "coffee")


def test_a_standing_instruction_is_findable_in_russian():
    conn = _fresh_db()
    fs.import_legacy_memory(conn, {"communication_habits": {"explanation_style": {
        "value": "Technical explanations without condescension or jargon",
        "said": "Объясняй техническое без снисходительности, но и без жаргона",
        "updated": "2026-07-25"}}})
    assert fs.search_facts(conn, "жаргон")


def test_facts_saved_before_this_change_still_import():
    # Everything already on the user's disk has no `said`. It must keep working,
    # just without Russian recall until the fact is said again.
    conn = _fresh_db()
    report = fs.import_legacy_memory(conn, {"notes": {"productivity_time": {
        "value": "late night", "updated": "2026-07-25"}}})
    assert report["imported"] == 1
    facts = fs.list_facts(conn)
    assert facts[0]["verbatim"] is None
    assert fs.search_facts(conn, "late night")


def test_the_report_shows_the_users_own_words():
    text = (REPO / "tools" / "memory_report.py").read_text(encoding="utf-8")
    assert "твоими словами" in text
