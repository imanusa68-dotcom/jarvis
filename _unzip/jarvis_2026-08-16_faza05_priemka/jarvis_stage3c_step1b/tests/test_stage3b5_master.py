"""Stage 3B.5 - one state dir, write-through index, honest counting.

WHAT WAS WRONG (all three measured, not suspected)
--------------------------------------------------
1. THE 2.5-SECOND SILENCE. recall_memory re-mirrored the whole JSON file into
   the index before every single lookup, so that a fact said seconds earlier
   could be found. Measured cost: 2 ms at 10 facts, 216 ms at 1000,
   2521 ms at 10 000. A voice assistant that stops talking for two and a half
   seconds to re-read what it already read is broken, however correct each
   individual step is.

2. THE LYING COUNTER. The prompt block said "51 more saved facts did not fit"
   when 9 984 were missing, because per-category caps ([:15], [:8]) threw facts
   away before the budget ever saw them. A number that is wrong is worse than
   no number: the model quotes it.

3. JUNK IN THE PROMPT, HIDDEN FROM SEARCH. The index had known since 3B.1 that
   "soon" and "updated, disregard previous" are garbage and kept them out of
   search results. The prompt was built straight from the JSON and included
   them anyway. Two components disagreeing about what is worth knowing.

And underneath all of it: core/safe_json.py honoured JARVIS_STATE_DIR while
core/store.py did not, so JSON state and the database could live in two
different places at once.
"""

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core import store  # noqa: E402
from memory import fact_store as fs  # noqa: E402
from memory import memory_manager as mm  # noqa: E402


class _state:
    """Point ALL durable state - JSON and database - at one throwaway dir."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="jarvis-3b5-"))

    def __enter__(self):
        self.saved = os.environ.get("JARVIS_STATE_DIR")
        os.environ["JARVIS_STATE_DIR"] = str(self.dir)
        mm._migrated_for = None
        return self.dir

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("JARVIS_STATE_DIR", None)
        else:
            os.environ["JARVIS_STATE_DIR"] = self.saved
        mm._migrated_for = None
        return False


def _rows(state_dir, include_hidden=False):
    conn = store.open_store(Path(state_dir) / "jarvis.db")
    try:
        return fs.list_facts(conn, include_hidden=include_hidden)
    finally:
        conn.close()


# ── one state dir ───────────────────────────────────────────────────

def test_the_database_lives_where_the_json_lives():
    with _state() as state_dir:
        assert store.app_dir() == state_dir
        assert store.db_path().parent == state_dir


def test_without_the_variable_nothing_moves():
    saved = os.environ.pop("JARVIS_STATE_DIR", None)
    try:
        assert store.app_dir() == Path.home() / ".jarvis"
    finally:
        if saved is not None:
            os.environ["JARVIS_STATE_DIR"] = saved


# ── saving writes through to the index ────────────────────────────────

def test_a_saved_fact_is_searchable_immediately():
    with _state() as state_dir:
        mm.update_memory({"preferences": {"favorite_drink": {
            "value": "Black coffee, no sugar",
            "said": "Кофе я пью только чёрный, без сахара."}}})
        keys = [r["key"] for r in _rows(state_dir)]
        assert "favorite_drink" in keys


def test_a_fact_the_user_just_said_is_not_labelled_legacy():
    # Every row used to report source=legacy, confidence 0.60 - even a sentence
    # spoken one second earlier - because the importer owned every write.
    with _state() as state_dir:
        mm.update_memory({"notes": {"productivity_time": {
            "value": "late night",
            "said": "Я вечно работаю по ночам."}}})
        row = [r for r in _rows(state_dir) if r["key"] == "productivity_time"][0]
        assert row["source"] == fs.SOURCE_EXPLICIT
        assert row["confidence"] == fs.CONFIDENCE_EXPLICIT


def test_the_write_through_keeps_the_users_own_words():
    with _state() as state_dir:
        mm.update_memory({"communication_habits": {"never_suggest": {
            "value": "Never suggest reinstalling Windows",
            "said": "Не предлагай мне переустановить Windows."}}})
        row = [r for r in _rows(state_dir) if r["key"] == "never_suggest"][0]
        assert "переустановить" in (row["verbatim"] or "")


def test_forgetting_removes_it_from_search_too():
    # Otherwise "done, forgotten" is a lie: the prompt loses the fact and
    # recall_memory keeps handing it straight back.
    with _state() as state_dir:
        mm.update_memory({"preferences": {"favorite_color": {"value": "green"}}})
        assert "favorite_color" in [r["key"] for r in _rows(state_dir)]
        assert "Forgotten" in mm.forget("favorite_color")
        assert "favorite_color" not in [
            r["key"] for r in _rows(state_dir, include_hidden=True)]


# ── the lazy mirror ────────────────────────────────────────────────

def test_a_lookup_after_a_save_does_no_full_re_index():
    with _state() as state_dir:
        mm.update_memory({"notes": {"a": {"value": "one"}}})
        conn = store.open_store(Path(state_dir) / "jarvis.db")
        try:
            # No memory argument on purpose: this is exactly how the live
            # lookup path calls it. Handing over a dict means "trust me, not
            # the file", which must always re-mirror.
            result = fs.sync_if_stale(conn)
            assert result["skipped"] is True, \
                "the file has not changed since the write-through"
            forced = fs.sync_if_stale(conn, memory=mm.load_memory())
            assert not forced.get("skipped"), \
                "a caller-supplied memory must never be answered from stale rows"
        finally:
            conn.close()


def test_an_edit_behind_our_back_is_still_caught():
    # The fingerprint is not a promise that nothing changed - it is a cheap way
    # to notice that something did. Hand-edited JSON must still reach search.
    with _state() as state_dir:
        mm.update_memory({"notes": {"a": {"value": "one"}}})
        path = state_dir / "long_term.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("notes", {})["smuggled"] = {"value": "hand written fact"}
        time.sleep(0.01)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        conn = store.open_store(state_dir / "jarvis.db")
        try:
            result = fs.sync_if_stale(conn)
            conn.commit()
            assert not result.get("skipped")
            assert "smuggled" in [r["key"] for r in fs.list_facts(conn)]
        finally:
            conn.close()


# ── honest counting, no junk ───────────────────────────────────────

def _many(n):
    memory = {"identity": {"name": {"value": "Rustam"}}, "notes": {}}
    for i in range(n):
        memory["notes"][f"fact_{i}"] = {
            "value": f"a saved fact about work and tools, number {i}"}
    return memory


def test_the_dropped_count_matches_what_is_actually_missing():
    total = 200
    text = mm.format_memory_for_prompt(_many(total))
    shown = sum(1 for line in text.splitlines() if line.startswith("  - "))
    footer = [line for line in text.splitlines() if "did not fit" in line]
    assert footer, "the block must say how many facts did not fit"
    reported = int(footer[0].replace("(", " ").split()[0])
    assert reported == total - shown, \
        f"claimed {reported} missing, actually {total - shown}"


def test_more_than_fifteen_preferences_can_reach_the_prompt():
    # The old [:15] cap meant the 16th preference was invisible forever, no
    # matter how much room was left in the budget.
    memory = {"preferences": {f"p{i}": {"value": f"v{i}"} for i in range(30)}}
    shown = sum(1 for line in mm.format_memory_for_prompt(memory).splitlines()
                if line.startswith("  - "))
    assert shown > 15


def test_junk_never_reaches_the_prompt():
    memory = {
        "projects": {"new_product_development": {"value": "soon"}},
        "preferences": {"favorite_color": {"value": "green"}},
    }
    text = mm.format_memory_for_prompt(memory)
    assert "green" in text
    assert "soon" not in text.lower()


def test_hiding_junk_does_not_delete_it():
    memory = {"projects": {"new_product_development": {"value": "soon"}}}
    before = json.dumps(memory, sort_keys=True)
    mm.format_memory_for_prompt(memory)
    assert json.dumps(memory, sort_keys=True) == before, \
        "rendering the prompt must never mutate the user's memory"


def test_pinning_rules_does_not_let_the_block_grow_without_limit():
    """Caught by measurement, not by reasoning.

    3B.3 pinned behaviour rules so they would never be the facts dropped. On a
    1000-fact profile that produced a 9550-character block - eight times the
    budget - because a seventh of those facts were rules and nothing was allowed
    to touch them. Pinning is a preference about ORDER, not an exemption from
    the budget, or the mid-prompt truncation bug simply comes back wearing a
    different hat.
    """
    memory = {"communication_habits": {
        f"rule_{i}": {"value": f"a standing rule about how to behave, number {i}"}
        for i in range(300)}}
    text = mm.format_memory_for_prompt(memory)
    assert len(text) <= mm.PROMPT_CHAR_BUDGET + 300, \
        f"pinned facts blew the budget: {len(text)} chars"
    assert "did not fit" in text, "and it must admit what it dropped"


def test_a_single_rule_still_outranks_ordinary_facts():
    memory = _many(500)
    memory["communication_habits"] = {
        "never_suggest": {"value": "Never suggest reinstalling Windows"}}
    assert "reinstalling Windows" in mm.format_memory_for_prompt(memory)


def test_the_budget_still_holds_and_still_keeps_the_rules():
    memory = _many(500)
    memory["communication_habits"] = {
        "warn_before_file_action": {"value": "Warn me before touching files"}}
    text = mm.format_memory_for_prompt(memory)
    assert len(text) <= mm.PROMPT_CHAR_BUDGET + 300
    assert "before touching files" in text
    assert "…" not in text
