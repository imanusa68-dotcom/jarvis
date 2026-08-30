"""Stage 3B.3 + 3B.4 - a memory that does not fit, and a memory you can search.

TWO DEFECTS, ONE ROOT
---------------------
Everything Jarvis knew had to arrive in one block glued to the front of the
conversation, assembled once at connect and never touched again. That block was
capped by `result[:1997] + "..."` - a cut through the middle of whatever fact
happened to sit at character 1997. The model then read half a sentence as a
whole fact, and nothing anywhere said that anything had been lost.

Measured on the user's machine on 2026-07-25: 9 facts, 676 chars. Not near the
cap yet - which is exactly why this had to be fixed before it started biting,
silently, on a day when memory finally got big enough to matter.

The second defect is the reason the cap hurt at all: the block was the ONLY
memory available during a conversation. The search index built in 3B.1, the
Russian recall won in 3B.2b - all of it existed solely for the report tool in a
terminal. Jarvis itself could not search its own memory, so "I don't know" was
the honest answer for facts lying three centimetres away on disk.

So: cut whole facts and say how many were cut, and give the model recall_memory.
"""

import importlib
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MAIN = (REPO / "main.py").read_text(encoding="utf-8")
BEHAVIOR = (REPO / "core" / "prompts" / "06_behavior.txt").read_text(
    encoding="utf-8")

from core import store  # noqa: E402
from memory import fact_store as fs  # noqa: E402
from memory import memory_manager as mm  # noqa: E402


def _fresh_db():
    tmp = tempfile.mkdtemp(prefix="jarvis-stage3b34-")
    return store.open_store(Path(tmp) / "jarvis.db")


def _big_memory():
    """More facts than any prompt budget can hold.

    ШАГ 6, 29.08.2026: количество считается ОТ бюджета, а не зашито числом 20.
    Раньше здесь было 40 обычных фактов - при бюджете 1200 они гарантированно
    не влезали, и тест честно проверял "блок признаётся, что что-то выкинул".
    Когда бюджет вырос до 4000, те же 40 фактов (3194 знака) стали влезать
    целиком, и тест упал - не потому, что поведение испортилось, а потому что
    фикстура перестала делать то, что обещает в первой строке.

    Считать от PROMPT_CHAR_BUDGET - единственный способ, при котором тест
    остаётся осмысленным после любого следующего изменения бюджета. Если бы я
    просто поднял 20 до 100, следующий человек, меняющий бюджет, наступил бы
    на ту же грабли.
    """
    memory = {
        "identity": {"name": {"value": "Rustam"}, "city": {"value": "Moscow"}},
        "communication_habits": {
            "explanation_style": {
                "value": "Explain technical things without jargon"},
            "warn_before_file_action": {
                "value": "Always say exactly which files will be touched first"},
            "never_suggest": {"value": "Never suggest reinstalling Windows"},
        },
        "preferences": {}, "notes": {}, "projects": {},
    }
    # ~60 знаков на факт, две категории => с двойным запасом перекрываем бюджет
    per_category = max(20, mm.PROMPT_CHAR_BUDGET // 60)
    for i in range(per_category):
        memory["preferences"][f"pref_{i}"] = {
            "value": f"a preference that takes up a fair amount of room number {i}"}
        memory["notes"][f"note_{i}"] = {
            "value": f"a note that also takes up a fair amount of room number {i}"}
    return memory


# ── 3B.3: running out of room honestly ───────────────────────────────────

def test_a_small_memory_is_left_completely_alone():
    text = mm.format_memory_for_prompt({
        "identity": {"name": {"value": "Rustam"}},
        "preferences": {"favorite_color": {"value": "green"}},
    })
    assert "Rustam" in text and "green" in text
    assert "did not fit" not in text


def test_a_large_memory_is_held_to_the_budget():
    text = mm.format_memory_for_prompt(_big_memory())
    assert len(text) <= mm.PROMPT_CHAR_BUDGET + 300, \
        "the budget plus its explanatory footer, nothing more"


def test_no_fact_is_ever_cut_in_half():
    text = mm.format_memory_for_prompt(_big_memory())
    assert "…" not in text, "the old mid-sentence chop must be gone"
    for line in text.splitlines():
        if line.startswith("  - "):
            assert line.strip().endswith(tuple("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.)")), \
                f"truncated fact line: {line!r}"


def test_behaviour_rules_are_never_the_ones_dropped():
    # A forgotten favourite colour is a small disappointment. A forgotten
    # "warn me before touching my files" is a broken promise.
    text = mm.format_memory_for_prompt(_big_memory())
    assert "Never suggest reinstalling Windows" in text
    assert "without jargon" in text
    assert "which files will be touched" in text


def test_the_block_says_how_many_facts_did_not_fit():
    text = mm.format_memory_for_prompt(_big_memory())
    assert "did not fit" in text
    assert "recall_memory" in text, "a dropped fact must be recoverable"


def test_dropping_facts_leaves_no_empty_headings():
    lines = mm.format_memory_for_prompt(_big_memory()).splitlines()
    for i, line in enumerate(lines):
        if line.endswith(":") and not line.startswith("  - ") and "[WHAT YOU KNOW" not in line:
            following = lines[i + 1] if i + 1 < len(lines) else ""
            assert following.startswith("  - "), \
                f"heading with nothing under it: {line!r}"


def test_who_the_person_is_always_survives():
    text = mm.format_memory_for_prompt(_big_memory())
    assert "Rustam" in text and "Moscow" in text


# ── 3B.4: the model can finally search ──────────────────────────────────

def test_recall_finds_a_fact_by_the_users_own_words():
    conn = _fresh_db()
    memory = {"preferences": {"favorite_drink": {
        "value": "Black coffee, no sugar",
        "said": "Кофе я пью только чёрный, без сахара."}}}
    out = fs.recall("чёрный", conn=conn, memory=memory)
    assert "favorite_drink" in out


def test_recall_hands_back_the_users_exact_sentence():
    conn = _fresh_db()
    memory = {"notes": {"productivity_time": {
        "value": "late night",
        "said": "Я вечно работаю по ночам, днём из меня толку мало."}}}
    out = fs.recall("по ночам", conn=conn, memory=memory)
    assert "днём из меня толку мало" in out


def test_recall_sees_facts_saved_since_the_last_sync():
    # The database starts empty; the fact exists only in memory v1. If recall
    # did not mirror first, it would answer "nothing saved" about a fact the
    # user gave it two minutes ago - the exact staleness bug of 3B.1.
    conn = _fresh_db()
    memory = {"projects": {"active_project": {"value": "voice automation"}}}
    assert "active_project" in fs.recall("voice", conn=conn, memory=memory)


def test_recall_says_nothing_rather_than_inventing():
    conn = _fresh_db()
    out = fs.recall("акваланг", conn=conn, memory={})
    assert "Nothing saved" in out
    assert "do not invent" in out


def test_forgotten_facts_do_not_come_back_through_recall():
    conn = _fresh_db()
    memory = {"preferences": {"favorite_color": {"value": "green"}}}
    assert "favorite_color" in fs.recall("green", conn=conn, memory=memory)
    assert "Nothing saved" in fs.recall("green", conn=conn, memory={})


# ── the wiring the model actually sees ───────────────────────────────────

def test_the_tool_is_declared():
    assert '"name": "recall_memory"' in MAIN


def test_the_tool_is_wired_to_a_handler():
    assert 'if name == "recall_memory":' in MAIN


def test_the_tool_must_be_called_before_denying_a_memory():
    assert "CALL THIS BEFORE saying you do not know" in MAIN


def test_the_tool_is_told_to_search_in_the_users_language():
    assert "Search with the words THEY used" in MAIN


def test_the_prompt_admits_the_block_is_only_a_summary():
    assert "THE BLOCK IS A SUMMARY, NOT EVERYTHING" in BEHAVIOR
    assert "recall_memory" in BEHAVIOR


def test_the_prompt_forbids_filling_the_gap_with_invention():
    assert "never invent a memory" in BEHAVIOR.lower()
