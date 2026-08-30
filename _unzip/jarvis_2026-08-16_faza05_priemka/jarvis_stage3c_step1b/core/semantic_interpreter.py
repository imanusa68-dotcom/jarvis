# core/semantic_interpreter.py
# MARK XXXV — Semantic Intent Interpreter
# ─────────────────────────────────────────────────────────────────────────────
# Pure heuristic module — NO API calls, NO blocking I/O.
# Maps colloquial, vague, or short user phrases to structured semantic intent,
# using the current dialogue state for referent resolution.
#
# This runs BEFORE the planner and BEFORE tool routing so that even broken,
# incomplete, or highly informal inputs can be handled gracefully.
#
# Public API:
#   interpret(user_text, dialogue_state) → SemanticIntent
#   enrich_goal(user_text, dialogue_state) → str   (reformulated, cleaner goal)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SemanticIntent:
    action:          str            # primary semantic action class
    referent:        Optional[str]  # what the vague reference points to
    topic:           Optional[str]  # resolved topic entity if any
    goal_enriched:   str            # cleaner rephrasing of the goal
    confidence:      float          # 0.0–1.0 how sure we are
    needs_clarify:   bool           # should we ask for more info?
    meta:            dict = field(default_factory=dict)  # extra hints for planner


# ─────────────────────────────────────────────────────────────────────────────
# Action classes
# ─────────────────────────────────────────────────────────────────────────────

OPEN_SOURCE     = "open_source"      # "открой тот источник", "открой первый"
COMPARE         = "compare"          # "сравни с прошлым", "а теперь сравни"
SEARCH          = "search"           # "найди X"
EXPLAIN         = "explain"          # "объясни по-людски", "что это значит"
NAVIGATE        = "navigate"         # "открой в яндексе", "перейди на сайт"
CONFIRM_DO      = "confirm_do"       # "ну сделай сам", "давай"
REFINE_SEARCH   = "refine_search"    # "не то, другое", "нормальный источник"
WHAT_IS_NEW     = "what_is_new"      # "что нового", "что там нового"
REPEAT_LAST     = "repeat_last"      # "повтори", "ещё раз"
CONVERSATIONAL  = "conversational"   # pure chat, no tool needed
GENERIC         = "generic"          # fallback — let LLM decide
SCREEN_VIEW     = "screen_view"      # questions about current screen content


# ─────────────────────────────────────────────────────────────────────────────
# Keyword patterns
# ─────────────────────────────────────────────────────────────────────────────

_OPEN_SOURCE_PAT = re.compile(
    r"\b(открой|открыть|зайди|перейди|покажи)\b.*\b(источник|ссылку|статью|сайт|страницу|первый|второй|третий|тот|этот|его|её)\b"
    r"|\b(источник|ссылку)\b.*\b(открой|открыть|зайди)\b"
    r"|\bну открой\b"
    r"|\bоткрой\s+(это|то|его|её|тот|этот)\b",
    re.IGNORECASE,
)

_COMPARE_PAT = re.compile(
    r"\b(сравни|сравнить|сравнение|compare|versus|vs\.?)\b"
    r"|\bа\s+теперь\s+сравни\b"
    r"|\bс\s+(прошлым|предыдущим|первым|старым)\b",
    re.IGNORECASE,
)

_EXPLAIN_PAT = re.compile(
    r"\b(объясни|объяснить|расскажи\s+по[- ]людски|по[- ]человечески|по[- ]простому|понятным\s+языком|попроще|простыми\s+словами)\b"
    r"|\bчто\s+это\s+значит\b"
    r"|\bкак\s+это\s+понять\b",
    re.IGNORECASE,
)

_NAVIGATE_PAT = re.compile(
    r"\b(открой|открыть|зайди|перейди|go\s+to)\b.*(яндекс|yandex|chrome|хром|браузер|browser|firefox|edge|сайт|в\s+том\s+же)\b"
    r"|\b(в\s+том\s+же\s+(окне|браузере|вкладке))\b"
    r"|\bв\s+(яндексе|хроме|chrome|yandex)\b",
    re.IGNORECASE,
)

_CONFIRM_DO_PAT = re.compile(
    r"\bну\s+сделай\s+сам\b"
    r"|\bдавай\b"
    r"|\bну\s+ты\s+понял\b"
    r"|\bты\s+знаешь\s+что\s+делать\b"
    r"|\bсделай\s+(сам|без\s+меня)\b"
    r"|\bдавай\s+уже\b",
    re.IGNORECASE,
)

_REFINE_PAT = re.compile(
    r"\bне\s+(это|то|так|тот)\b"
    r"|\bдругой?\b.*\b(источник|результат|сайт|ссылка)\b"
    r"|\bнормальн(ый|ую|ое)\b"
    r"|\bофициальн(ый|ую|ое|ую)\b"
    r"|\bнайди\s+нормальный\b"
    r"|\bкороче\s+найди\b"
    r"|\bлучший?\b.*\bисточник\b",
    re.IGNORECASE,
)

_WHAT_NEW_PAT = re.compile(
    r"\bчто\s+(там\s+)?нов(ого|ости|ую)\b"
    r"|\bчто\s+нового\b"
    r"|\bчто\s+произошло\b"
    r"|\bчто\s+случилось\b"
    r"|\bновости\b"
    r"|\blatest\s+news\b"
    r"|\bwhat.s\s+new\b",
    re.IGNORECASE,
)

_REPEAT_PAT = re.compile(
    r"\bповтори\b|\bещё\s+раз\b|\brepeat\b|\bsnooze\b",
    re.IGNORECASE,
)

_SCREEN_VIEW_PAT = re.compile(
    r"\b(что|чего|какой|какая|какое|what|which|explain|объясни|опиши)\b.*"
    r"\b(экран|экране|скрин|окне|видео|video|сервис|сайт|интерфейс|screen|window)\b"
    r"|\b(что\s+(у меня|тут|там|здесь|происходит|видно|показано|открыто|идёт))\b"
    r"|\b(что\s+мне\s+нажать)\b"
    r"|\b(что\s+ты\s+видишь)\b"
    r"|\b(что\s+это\s+за\s+(сервис|сайт|приложение|программа))\b"
    r"|\b(what.s\s+on\s+(my\s+)?screen)\b"
    r"|\b(что\s+делать\s+дальше)\b"
    r"|\b(объясни\s+(интерфейс|этот\s+экран|эту\s+страницу))\b",
    re.IGNORECASE,
)

_CONVERSATIONAL_PAT = re.compile(
    r"^(привет|пока|спасибо|окей|ок|да|нет|ладно|хорошо|понял|ясно|отлично|супер|круто)\.?$",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Referent resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

_SOURCE_INDEX_PAT = re.compile(r"\b(первый|второй|третий|1|2|3|4|5)\b", re.IGNORECASE)
_INDEX_MAP = {
    "первый": 1, "первую": 1, "первое": 1, "1": 1,
    "второй": 2, "вторую": 2, "второе": 2, "2": 2,
    "третий": 3, "третью": 3, "третье": 3, "3": 3,
    "четвёртый": 4, "4": 4,
    "пятый": 5, "5": 5,
}


def _resolve_source_index(text: str) -> Optional[int]:
    for word in _INDEX_MAP:
        if re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE):
            return _INDEX_MAP[word]
    return None


def _extract_topic_from_text(text: str, state: dict) -> Optional[str]:
    """Try to extract a topic from the text; fall back to last_topic_entity."""
    # Use existing state topic if the text is vague (short / many pronouns)
    pronoun_heavy = bool(re.search(r"\b(его|её|им|этот|этого|тот|того|они|он|она)\b", text, re.IGNORECASE))
    is_short = len(text.split()) <= 5

    if (pronoun_heavy or is_short) and state.get("last_topic_entity"):
        return state["last_topic_entity"]

    return state.get("last_topic_entity")


# ─────────────────────────────────────────────────────────────────────────────
# Goal enrichment
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_goal(
    user_text: str,
    action:    str,
    state:     dict,
    referent:  Optional[str],
    topic:     Optional[str],
) -> str:
    """
    Return a cleaner, more specific version of the goal for the planner.
    Fills in missing referents using dialogue state.
    """
    text = user_text.strip()

    if action == OPEN_SOURCE:
        idx = _resolve_source_index(text)
        if idx:
            return f"Open search source number {idx} in the browser"
        if topic:
            return f"Open the main search result about {topic} in the browser"
        if state.get("last_url"):
            return f"Open {state['last_url']} in the browser"
        return "Open the most relevant search result in the browser"

    if action == COMPARE and topic:
        prior = state.get("prior_topic") or "the previous result"
        return f"Compare {topic} with {prior}"

    if action == EXPLAIN and topic:
        return f"Explain {topic} in simple terms"

    if action == NAVIGATE:
        browser = state.get("last_browser", "the browser")
        if topic:
            return f"Open {topic} in {browser}"
        if state.get("last_url"):
            return f"Open {state['last_url']} in {browser}"
        return text

    if action == REFINE_SEARCH and topic:
        return f"Find the official or most authoritative source about {topic}"

    if action == WHAT_IS_NEW and topic:
        return f"Find latest news and updates about {topic}"

    if action == CONFIRM_DO and state.get("last_result_summary"):
        return f"Execute the previously described task: {state['last_result_summary'][:100]}"

    if action == SCREEN_VIEW:
        sv_active = state.get("screen_share_active", False)
        if sv_active:
            src_type  = state.get("screen_share_source_type", "full")
            win_title = state.get("screen_share_window_title")
            if src_type == "window" and win_title:
                return f"Analyze the current screen frame via Screen View live path (window: '{win_title}') and answer: {text}"
            return f"Analyze the current screen frame via Screen View live path (entire screen) and answer: {text}"
        return f"Screen View is OFF. The user is asking: {text}. Inform them Screen View is not active and offer to enable it."

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Main interpreter
# ─────────────────────────────────────────────────────────────────────────────

def interpret(user_text: str, dialogue_state: dict | None = None) -> SemanticIntent:
    """
    Map a raw user utterance to a SemanticIntent.
    All heuristic, zero latency — no API calls.

    Args:
        user_text:      Raw transcribed user input
        dialogue_state: Current state dict from dialogue_state.get()

    Returns:
        SemanticIntent with resolved action, referent, topic, enriched goal.
    """
    if dialogue_state is None:
        dialogue_state = {}

    text   = (user_text or "").strip()
    lower  = text.lower()
    words  = lower.split()
    n      = len(words)

    # ── Conversational (pure chat, no tool needed) ──────────────────────────
    if _CONVERSATIONAL_PAT.match(text):
        return SemanticIntent(
            action=CONVERSATIONAL, referent=None, topic=None,
            goal_enriched=text, confidence=0.95, needs_clarify=False,
        )

    # ── Detect primary action ───────────────────────────────────────────────
    action     = GENERIC
    confidence = 0.6

    if _OPEN_SOURCE_PAT.search(text):
        action, confidence = OPEN_SOURCE, 0.85
    elif _COMPARE_PAT.search(text):
        action, confidence = COMPARE, 0.80
    elif _EXPLAIN_PAT.search(text):
        action, confidence = EXPLAIN, 0.85
    elif _NAVIGATE_PAT.search(text):
        action, confidence = NAVIGATE, 0.80
    elif _CONFIRM_DO_PAT.search(text):
        action, confidence = CONFIRM_DO, 0.90
    elif _REFINE_PAT.search(text):
        action, confidence = REFINE_SEARCH, 0.80
    elif _WHAT_NEW_PAT.search(text):
        action, confidence = WHAT_IS_NEW, 0.85
    elif _REPEAT_PAT.search(text):
        action, confidence = REPEAT_LAST, 0.90
    elif _SCREEN_VIEW_PAT.search(text):
        # Screen-view question — route to SCREEN_VIEW for prompt enrichment
        action, confidence = SCREEN_VIEW, 0.85
    elif n <= 4 and dialogue_state.get("last_tool"):
        # Very short follow-up → likely references prior context
        action, confidence = GENERIC, 0.50

    # ── Resolve topic and referent ──────────────────────────────────────────
    topic    = _extract_topic_from_text(text, dialogue_state)
    referent = dialogue_state.get("last_url") or dialogue_state.get("last_query")

    # ── Build source_index hint ─────────────────────────────────────────────
    meta: dict = {}
    if action == OPEN_SOURCE:
        idx = _resolve_source_index(text)
        if idx:
            meta["source_index"] = idx
        if dialogue_state.get("last_browser"):
            meta["browser"] = dialogue_state["last_browser"]

    if action == NAVIGATE:
        browser_match = re.search(r"\b(яндекс|yandex|chrome|хром|firefox|edge)\b", lower)
        if browser_match:
            raw = browser_match.group(1).lower()
            meta["browser"] = "yandex" if raw in ("яндекс", "yandex") else (
                "chrome" if raw in ("chrome", "хром") else raw
            )
        elif dialogue_state.get("last_browser"):
            meta["browser"] = dialogue_state["last_browser"]

    # ── Decide if we need to clarify ────────────────────────────────────────
    # Use uncertainty_policy for this — low-confidence + no context = clarify
    needs_clarify = (
        confidence < 0.40
        and not dialogue_state.get("last_tool")
        and n <= 2
    )

    # ── Enrich goal ─────────────────────────────────────────────────────────
    goal_enriched = _enrich_goal(text, action, dialogue_state, referent, topic)

    return SemanticIntent(
        action=action,
        referent=referent,
        topic=topic,
        goal_enriched=goal_enriched,
        confidence=confidence,
        needs_clarify=needs_clarify,
        meta=meta,
    )


def enrich_goal(user_text: str, dialogue_state: dict | None = None) -> str:
    """
    Convenience wrapper: return just the enriched goal string.
    Used by planner to get a cleaner goal when context is vague.
    """
    result = interpret(user_text, dialogue_state)
    return result.goal_enriched
