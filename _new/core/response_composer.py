# core/response_composer.py
# MARK XXXV — Response Composer
# ─────────────────────────────────────────────────────────────────────────────
# Single, centralised layer that decides HOW to present a tool result.
# Used primarily by agent/executor.py (agent_task path).
#
# The live Gemini session uses its own voice (controlled by the system prompt),
# so this composer handles the multi-step agent_task summarisation path.
#
# Design goals:
#   - Response length matches task complexity, not a fixed sentence limit
#   - Tone adapts to personality profile
#   - Proactive but restrained: offer useful next step hints where natural
#   - No theatrical "sir" in every sentence — use it sparingly and only when
#     it feels natural (Jarvis does say it, but not robotically after every word)
#   - Neutral tool results get a human voice here, not at the action layer
#
# Public API:
#   compose(result, goal, tool_used, personality, dialogue_state, language) → str
#   compose_error(goal, error, tool_used, personality, language) → str
#   compose_proactive_hint(tool_used, result, dialogue_state) → str | None
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Tone helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_brief_profile(personality: dict | None) -> bool:
    if not personality:
        return False
    return personality.get("response_length") == "brief"


def _is_detailed_profile(personality: dict | None) -> bool:
    if not personality:
        return False
    return personality.get("response_length") == "detailed"


def _autonomy_prefers_direct(personality: dict | None) -> bool:
    """User wants the result, not an explanation."""
    if not personality:
        return False
    return personality.get("autonomy_preference") in ("do_it",)


def _low_question_tolerance(personality: dict | None) -> bool:
    if not personality:
        return False
    return personality.get("question_tolerance") == "low"


# ─────────────────────────────────────────────────────────────────────────────
# Goal type detection
# ─────────────────────────────────────────────────────────────────────────────

_RESEARCH_TOOLS = {"web_search", "deep_research"}
_ACTION_TOOLS   = {"open_app", "browser_control",
                   "cmd_control", "reminder", "youtube_video",
                   "file_controller", "game_updater", "computer_control",
                   "desktop_control"}
_CODE_TOOLS     = {"code_helper"}


def _goal_is_action(goal: str, tool_used: str) -> bool:
    if tool_used in _ACTION_TOOLS:
        return True
    action_verbs = re.compile(
        r"^(открой|запусти|закрой|включи|выключи|отправь|поставь|удали|"
        r"нажми|перейди|скачай|установи|обнови|установить|запустить)",
        re.IGNORECASE,
    )
    return bool(action_verbs.match(goal.strip()))


def _goal_is_research(goal: str, tool_used: str) -> bool:
    return tool_used in _RESEARCH_TOOLS


def _goal_is_code(tool_used: str) -> bool:
    return tool_used in _CODE_TOOLS


# ─────────────────────────────────────────────────────────────────────────────
# Sentence count adaptor
# ─────────────────────────────────────────────────────────────────────────────

def _adapt_length(text: str, personality: dict | None, tool_used: str, goal: str) -> str:
    """
    Trim or expand the generated summary to match the user's preferred length
    and the nature of the task.
    Action confirmations → 1 sentence.
    Research → 2-4 sentences.
    Code builds → 1-2 sentences + path if available.
    """
    if not text:
        return text

    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    # Determine target sentence count
    if _is_brief_profile(personality) or _goal_is_action(goal, tool_used):
        target = 1
    elif _is_detailed_profile(personality) or _goal_is_research(goal, tool_used):
        target = 4
    elif _goal_is_code(tool_used):
        target = 2
    else:
        target = 2

    if len(sentences) > target:
        return " ".join(sentences[:target])

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Proactive hint generator
# ─────────────────────────────────────────────────────────────────────────────

def compose_proactive_hint(
    tool_used:      str,
    result:         str,
    dialogue_state: dict | None = None,
    personality:    dict | None = None,
) -> Optional[str]:
    """
    Return a short, useful follow-up suggestion after certain tool results.
    Returns None if there's nothing natural to suggest.
    Stays restrained — no suggestion is better than an annoying one.
    """
    if not dialogue_state:
        return None

    # After web_search: offer to open the top source
    if tool_used == "web_search":
        sources = dialogue_state.get("last_sources", [])
        if sources:
            return "Могу открыть первый источник, если нужно."

    # After deep research: offer to save
    if tool_used == "deep_research":
        return None  # already usually offers a save in its own result

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main composer — via Gemini (used in executor._summarize)
# ─────────────────────────────────────────────────────────────────────────────

def _language_code(language: str | None) -> str:
    """Код языка из чего угодно: "ru", "RU", "ru-RU", "Russian" → "ru".

    Подпись compose() ждёт код, а agent/executor.py::_detect_language отдаёт
    человеческое имя. Сегодня в compose() приходит жёстко вписанное "ru" и
    беды нет; в первый же день, когда язык начнут передавать по-настоящему,
    четыре проверки `language == "ru"` ниже провалились бы молча.
    """
    try:
        from core.search_locale import normalize_lang
    except ImportError:
        print("[ResponseComposer] ⚠ ТАБЛИЦА ЯЗЫКОВ НЕ ЗАГРУЗИЛАСЬ — отвечаю по-русски")
        return "ru"
    return normalize_lang(language, fallback="ru")


def _language_name(language: str | None) -> str:
    """Имя языка для промпта ("Russian") — из общей таблицы на 18 языков.

    До 7 августа 2026 здесь лежал собственный список из пяти языков с
    умолчанием "Russian". Он знал впятеро меньше общей таблицы и был мёртв:
    единственный вызывающий передаёт "ru" константой. Две правды об одном
    и том же — это то, что расходится молча.
    """
    try:
        from core.search_locale import get_label
    except ImportError:
        print("[ResponseComposer] ⚠ ТАБЛИЦА ЯЗЫКОВ НЕ ЗАГРУЗИЛАСЬ — отвечаю по-русски")
        return "Russian"
    return get_label(language, fallback="ru")


def compose(
    result:         str,
    goal:           str,
    tool_used:      str,
    personality:    dict | None = None,
    dialogue_state: dict | None = None,
    language:       str = "ru",
    api_key:        str | None = None,
) -> str:
    """
    Compose a final user-facing response from a raw tool result.
    Calls Gemini only if available; otherwise falls back to a heuristic summary.

    Args:
        result:         Raw tool result string
        goal:           Original user goal
        tool_used:      Which tool produced this result
        personality:    User personality profile dict
        dialogue_state: Current dialogue state dict
        language:       Response language code (default: "ru")
        api_key:        Gemini API key for LLM-based composition

    Returns:
        A natural, style-adapted response string.
    """
    language = _language_code(language)

    if not result or result in ("Done.", "Completed."):
        return _compose_action_confirmation(goal, tool_used, language)

    # Build personality guidance for the prompt
    length_guidance = (
        "one concise sentence" if _is_brief_profile(personality) else
        "2-3 sentences"        if _is_detailed_profile(personality) else
        "1-2 sentences"
    )
    tone = "natural, calm, direct"
    if personality:
        t = personality.get("tone", "balanced")
        tone = {
            "formal":   "professional and precise",
            "balanced": "natural, calm, direct",
            "friendly": "warm and conversational",
        }.get(t, "natural, calm, direct")

    lang_name = _language_name(language)

    if api_key:
        try:
            # Дверь к модели во всём проекте одна: core/aux_model.aux_call.
            # Она знает остывание после 429, повтор на временном отказе и
            # никогда не молчит при неудаче. Своей двери у композера больше нет.
            from core.aux_model import aux_call
            from config.loader import get_model as _get_model
            # Роль строкой-литералом нарочно: так сторож реестра видит её грепом.
            _model_name = _get_model("aux_light")

            proactive = compose_proactive_hint(tool_used, result, dialogue_state, personality)
            proactive_note = (
                f"\nOptionally add this brief follow-up at the end (only if natural): {proactive}"
                if proactive else ""
            )

            prompt = (
                f"You are summarising a completed assistant action for the user.\n"
                f"Goal: {goal}\n"
                f"Tool used: {tool_used}\n"
                f"Result (raw): {result[:600]}\n\n"
                f"Write {length_guidance} in {lang_name}. Tone: {tone}.\n"
                f"Be direct — report what was done and the key outcome.\n"
                f"Do NOT repeat the entire result. Do NOT say 'I have' — just state what happened.\n"
                f"Use 'sir' at most once and only if it sounds natural.\n"
                f"Return ONLY the response text, nothing else.{proactive_note}"
            )

            ok, answer = aux_call(prompt, api_key,
                                  model=_model_name,
                                  caller="ResponseComposer")
            if ok and answer.strip():
                return _adapt_length(answer.strip(), personality, tool_used, goal)

            # Отказ уже назван вслух внутри aux_call; здесь — чем ответим.
            print("[ResponseComposer] 🔁 фраза собрана без модели"
                  + (f" ({answer[:48]})" if answer else ""))

        except Exception as e:
            if "429" not in str(e):
                print(f"[ResponseComposer] ⚠️ Compose failed: {e}")
            # Fall through to heuristic

    return _heuristic_compose(result, goal, tool_used, personality, language)


def compose_error(
    goal:        str,
    error:       str,
    tool_used:   str,
    personality: dict | None = None,
    language:    str = "ru",
) -> str:
    """Compose a natural error message."""
    language = _language_code(language)
    brief = _is_brief_profile(personality)

    if language == "ru":
        if brief:
            return f"Не получилось выполнить: {goal[:60]}."
        return (
            f"К сожалению, не удалось завершить задачу. "
            f"Ошибка: {error[:120]}."
        )
    else:
        if brief:
            return f"Couldn't complete: {goal[:60]}."
        return f"I wasn't able to complete this. Error: {error[:120]}."


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic fallbacks
# ─────────────────────────────────────────────────────────────────────────────

def _compose_action_confirmation(goal: str, tool_used: str, language: str) -> str:
    """Return a brief confirmation for action tools that returned 'Done.'"""
    goal_short = goal[:50].rstrip(".,!?")
    if language == "ru":
        return f"Готово — {goal_short}."
    return f"Done — {goal_short}."


def _heuristic_compose(
    result:      str,
    goal:        str,
    tool_used:   str,
    personality: dict | None,
    language:    str,
) -> str:
    """
    Pure-Python fallback when LLM is unavailable.
    Returns a short, natural-sounding confirmation.
    """
    result_preview = result.strip()[:200]
    goal_short     = goal[:60]

    if _goal_is_action(goal, tool_used):
        if language == "ru":
            return f"Выполнено: {goal_short}."
        return f"Completed: {goal_short}."

    if language == "ru":
        return f"Готово. {result_preview}"
    return f"Done. {result_preview}"
