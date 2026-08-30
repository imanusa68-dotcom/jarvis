# core/dialogue_state.py
# MARK XXXV — Dialogue State Tracker
# ─────────────────────────────────────────────────────────────────────────────
# Thread-safe singleton. Tracks conversational context across turns so that
# vague follow-up requests ("ну открой то", "а теперь сравни") can be resolved
# without asking the user to repeat themselves.
#
# Integration points:
#   main.py._execute_tool()  → call update() after each tool completes
#   main.py._build_config()  → call format_for_prompt() to inject into system prompt
#   agent/planner.py         → create_plan() receives context via format_for_prompt()
#   core/semantic_interpreter.py → reads get() to resolve referents
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import threading
from typing import Any

_lock  = threading.Lock()
_state: dict[str, Any] = {
    "last_tool":            None,   # str  — name of last tool called
    "last_params":          {},     # dict — params used in that tool call
    "last_result_summary":  None,   # str  — first 300 chars of result
    "last_query":           None,   # str  — last search/browse query
    "last_topic_entity":    None,   # str  — named entity from query_rewriter
    "last_sources":         [],     # list[str] — URLs/titles from last search
    "last_url":             None,   # str  — last browser URL navigated to
    "last_browser":         None,   # str  — "yandex" | "chrome" | etc.
    "last_app_opened":      None,   # str  — last app name opened
    "turn_count":           0,      # int  — number of turns this session
    "prior_topic":          None,   # str  — topic before the current one
      # Screen vision fields (updated by _poll_sv_state in ui.py)
      "screen_share_active":       False,  # bool
      "screen_share_source_type":  None,   # "full" | "window"
      "screen_share_window_title": None,   # str
      "last_screen_analysis":      None,   # str (first 400 chars of last analysis)
      "last_screen_frame_ts":      None,   # float (monotonic ts of analyzed frame)
      # Screen CONTROL (clicking) toggle — distinct from Screen VIEW above.
      # Reflects the live SCREEN button so the model never has to guess whether
      # interactive computer_control actions are currently permitted.
      "screen_control_active":     False,  # bool — mirrors ui.screen_control
      # Action journal (Slice A) — a SHORT rolling list of the last few actions
      # Jarvis performed this session, kept structurally (not one overwritten
      # slot). Lets follow-ups like "change that file" resolve to a concrete
      # target. Injected into context at connect/reconnect; survives reset.
      "action_journal":            [],     # list[dict]: {summary, ok}
}

# How many recent actions to remember. Small on purpose — keeps the context
# block tiny and cheap (free-tier token budget).
_JOURNAL_MAX = 8


def update(
    tool:           str | None    = None,
    params:         dict | None   = None,
    result:         str | None    = None,
    query:          str | None    = None,
    topic_entity:   str | None    = None,
    sources:        list | None   = None,
    url:            str | None    = None,
    browser:        str | None    = None,
    app_opened:     str | None    = None,
) -> None:
    """Update dialogue state after a tool call or turn."""
    with _lock:
        _state["turn_count"] += 1

        if tool is not None:
            _state["last_tool"] = tool
        if params is not None:
            _state["last_params"] = dict(params)
        if result is not None:
            _state["last_result_summary"] = str(result)[:400]
        if query is not None:
            _state["last_query"] = query
        if topic_entity is not None:
            if _state["last_topic_entity"] and _state["last_topic_entity"] != topic_entity:
                _state["prior_topic"] = _state["last_topic_entity"]
            _state["last_topic_entity"] = topic_entity
        if sources is not None:
            _state["last_sources"] = list(sources)[:10]
        if url is not None:
            _state["last_url"] = url
        if browser is not None:
            _state["last_browser"] = browser
        if app_opened is not None:
            _state["last_app_opened"] = app_opened


def get() -> dict:
    """Return a snapshot of current dialogue state."""
    with _lock:
        return dict(_state)


def reset() -> None:
    """Reset state at session reconnect (keeps turn_count)."""
    with _lock:
        turns = _state["turn_count"]
        # UI-owned toggles must survive a reconnect — the physical button does
        # not un-press itself. Preserve it across the reset.
        screen_ctrl = _state.get("screen_control_active", False)
        # The action journal must ALSO survive reconnect — that is exactly when
        # the live conversation history is lost, so the journal is what lets the
        # model still know what it just did.
        journal = list(_state.get("action_journal") or [])
        for key in list(_state):
            if key != "turn_count":
                _state[key] = [] if key == "last_sources" else ({} if key == "last_params" else None)
        _state["turn_count"] = turns
        _state["last_sources"] = []
        _state["last_params"] = {}
        _state["screen_share_active"] = False
        _state["screen_share_source_type"] = None
        _state["screen_share_window_title"] = None
        _state["last_screen_analysis"] = None
        _state["last_screen_frame_ts"] = None
        _state["screen_control_active"] = screen_ctrl
        _state["action_journal"] = journal


def set_screen_control(active: bool) -> None:
    """
    Mirror the live SCREEN button (Screen Control / clicking) into dialogue
    state so it is injected into the model's context every turn. Called from
    ui.py when the toggle changes and on the periodic UI poll.
    """
    with _lock:
        _state["screen_control_active"] = bool(active)


def record_action(tool: str, action: str | None, summary: str, ok: bool = True) -> None:
    """
    Append one action to the rolling journal (Slice A). Keeps only the last
    _JOURNAL_MAX entries. `summary` should be a short human-readable line
    describing what happened (ideally with a concrete target, e.g. a full path)
    so follow-ups like "change that file" can resolve a concrete target.
    """
    if not tool:
        return
    label = f"{tool}/{action}" if action else str(tool)
    short = str(summary).strip().replace("\n", " ")[:160]
    entry = {"summary": f"{label}: {short}", "ok": bool(ok)}
    with _lock:
        journal = list(_state.get("action_journal") or [])
        journal.append(entry)
        _state["action_journal"] = journal[-_JOURNAL_MAX:]  # ring buffer


def update_screen_share(
    active:       bool | None   = None,
    source_type:  str  | None   = None,
    window_title: str  | None   = None,
    analysis:     str  | None   = None,
    frame_ts:     float| None   = None,
) -> None:
    """Update screen-vision fields in dialogue state."""
    with _lock:
        if active is not None:
            _state["screen_share_active"] = bool(active)
        if source_type is not None:
            _state["screen_share_source_type"] = source_type
        if window_title is not None:
            _state["screen_share_window_title"] = window_title
        if analysis is not None:
            _state["last_screen_analysis"] = str(analysis)[:400]
        if frame_ts is not None:
            _state["last_screen_frame_ts"] = frame_ts


def format_for_prompt() -> str:
    """
    Produce a concise prompt block describing recent context.
    Injected into system prompt at reconnect, and into planner context.
    Returns empty string if no meaningful state exists yet.
    """
    with _lock:
        s = dict(_state)

    lines: list[str] = []

    if s["last_tool"]:
        lines.append(f"Last action: {s['last_tool']}")

    if s["last_query"]:
        lines.append(f"Last query: {s['last_query']}")

    if s["last_topic_entity"]:
        lines.append(f"Current topic: {s['last_topic_entity']}")

    if s["prior_topic"]:
        lines.append(f"Previous topic: {s['prior_topic']}")

    if s["last_url"]:
        lines.append(f"Last URL: {s['last_url']}")

    if s["last_browser"]:
        lines.append(f"Active browser: {s['last_browser']}")

    if s["last_app_opened"]:
        lines.append(f"Last app: {s['last_app_opened']}")

    if s["last_sources"]:
        sources_preview = s["last_sources"][:3]
        lines.append("Recent sources: " + "; ".join(str(x) for x in sources_preview))

    if s.get("screen_share_active"):
        src_type = s.get("screen_share_source_type", "full")
        win_title = s.get("screen_share_window_title")
        if src_type == "window" and win_title:
            lines.append(f"Screen View: ON — window '{win_title}'")
        else:
            lines.append("Screen View: ON — entire screen")
        if s.get("last_screen_analysis"):
            lines.append(f"Last screen analysis: {s['last_screen_analysis'][:120]}")

    # Action journal (Slice A) — what Jarvis recently DID, with concrete
    # targets, so "change that file / open it again" resolves without asking.
    journal = s.get("action_journal") or []
    if journal:
        lines.append("Recent actions you performed (most recent last):")
        for entry in journal[-_JOURNAL_MAX:]:
            mark = "✓" if entry.get("ok", True) else "✗"
            lines.append(f"  {mark} {entry.get('summary', '')}")

    # Live SCREEN-control (clicking) state — always reported so the model never
    # guesses. When ON, it must NOT tell the user to enable it.
    if s.get("screen_control_active"):
        lines.append(
            "Screen control (clicking/typing on screen): ON — "
            "interactive computer_control actions are permitted right now. "
            "Do NOT ask the user to enable the SCREEN button; it is already on."
        )
    else:
        lines.append(
            "Screen control (clicking/typing on screen): OFF — "
            "if the user asks to click/type on screen, ask them to press the "
            "SCREEN button first."
        )

    if not lines:
        return ""

    return (
        "[RECENT SESSION CONTEXT — use to resolve vague references like 'то', 'тот источник', 'это']\n"
        + "\n".join(lines)
        + "\n"
    )
