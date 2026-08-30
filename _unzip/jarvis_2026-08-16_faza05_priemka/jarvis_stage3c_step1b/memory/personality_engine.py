# memory/personality_engine.py
# MARK XXXV - Personality Engine (v2 — Behavioural)
# ─────────────────────────────────────────────────────────────────────────────
# Learns the user's communication style and behavioural preferences over time.
# Extends beyond surface style (length/tone) to track:
#   - autonomy preference: how much the user wants the assistant to just do it
#   - question tolerance: whether the user hates being asked clarifying questions
#   - phrasing style: telegraphic vs verbose communication patterns
#   - proactive preference: whether to offer next-step hints
#   - implicit defaults: apps, browsers, levels of detail the user always wants
# ─────────────────────────────────────────────────────────────────────────────

import json
import re
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys
import time

# Stage 3.0 - durability floor (see core/safe_json.py). Same fix as memory:
# durable location, atomic writes, no silent loss on a corrupt file.
from core.safe_json import (
    atomic_write_json,
    import_legacy_once,
    load_json_safe,
    state_dir,
    state_path,
    update as safe_update,
)


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR     = get_base_dir()

# ── Stage 3.0 ─────────────────────────────────────────────────────
# The learned profile lived in the build folder and was therefore thrown away
# on every update, restarting personality learning from zero each time.
_LEGACY_PROFILE_PATH = BASE_DIR / "memory" / "personality.json"

_migrated_for: str | None = None
_lock        = Lock()
MIN_TURNS_FOR_PROMPT = 3

# ── Stage1 call throttle ────────────────────────────────────────────────────
_PERSONALITY_STAGE1_MIN_INTERVAL: float = 90.0   # seconds between Stage1 checks
_last_personality_stage1:         float = 0.0    # time.monotonic() of last check


def _profile_path() -> Path:
    """Resolved at call time so tests can redirect state via JARVIS_STATE_DIR."""
    return state_path("personality.json")


def _ensure_migrated() -> None:
    """Idempotent one-time lift of the build-folder profile into ~/.jarvis."""
    global _migrated_for
    key = str(state_dir())
    if _migrated_for == key:
        return
    try:
        import_legacy_once(_LEGACY_PROFILE_PATH, _profile_path(),
                           label="Personality")
        _migrated_for = key
    except Exception as exc:
        print("[Personality] Import of legacy profile failed:", exc)


def _default_profile():
    return {
        # Surface style
        "response_length":       "moderate",    # brief | moderate | detailed
        "tone":                  "balanced",    # formal | balanced | friendly
        "explanation_depth":     "moderate",    # simple | moderate | deep
        "humor_level":           "low",         # none | low | moderate | high
        "prefers_confirmations": True,

        # Behavioural preferences (new)
        "autonomy_preference":   "explain_then_do",  # do_it | explain_then_do | always_ask
        "question_tolerance":    "medium",           # low | medium | high
        "phrasing_style":        "normal",           # telegraphic | normal | verbose
        "prefers_proactive":     True,               # offer next-step hints?
        "implicit_defaults":     {},                 # {preferred_browser: "yandex", ...}

        # Meta
        "interaction_count":     0,
        "confidence":            0.0,
        "last_updated":          datetime.now().strftime("%Y-%m-%d"),
        "observations":          [],
    }


def load_profile():
    """Load the learned profile. A corrupt file is quarantined and recovered
    from snapshots rather than silently replaced by defaults."""
    _ensure_migrated()
    with _lock:
        data = load_json_safe(_profile_path(), _default_profile,
                              label="Personality")
        base = _default_profile()
        for key in base:
            if key not in data:
                data[key] = base[key]
        return data


def _save_profile(profile):
    """Durably replace the profile (temp file + fsync + atomic rename)."""
    _ensure_migrated()
    with _lock:
        try:
            atomic_write_json(_profile_path(), profile)
        except Exception as exc:
            print("[Personality] Save error — disk copy left unchanged:", exc)


def _merge_profile(current, updates):
    updatable = {
        "response_length", "tone", "explanation_depth",
        "humor_level", "prefers_confirmations",
        "autonomy_preference", "question_tolerance",
        "phrasing_style", "prefers_proactive",
    }
    for key, value in updates.items():
        if key in updatable and value is not None:
            current[key] = value

    # Merge implicit_defaults dict
    new_defaults = updates.get("implicit_defaults")
    if isinstance(new_defaults, dict):
        existing = current.get("implicit_defaults", {})
        existing.update(new_defaults)
        current["implicit_defaults"] = existing

    current["interaction_count"] = current.get("interaction_count", 0) + 1
    count = current["interaction_count"]
    current["confidence"]   = min(1.0, count / 20.0)
    current["last_updated"] = datetime.now().strftime("%Y-%m-%d")

    note = updates.get("observation_text", "").strip()
    if note:
        obs = current.get("observations", [])
        obs.append(note)
        current["observations"] = obs[-5:]
    return current


def should_analyze_personality(user_text, jarvis_text, api_key):
    """
    Stage 1: Quick YES/NO gate.

    Check order (cheapest → most expensive):
      1. Time throttle
      2. Guard cooldown  — push timestamp forward on cooldown to prevent immediate retry
      3. Make Stage1 call via aux_call (new SDK, guard-protected)

    Note: суточный потолок считает core/metering внутри aux_call (блок 5);
    свой счётчик здесь не нужен и никогда не был нужен.
    Personality shares the same Gemini quota as memory, so the guard cooldown
    already provides the necessary protection.  Double-counting the daily
    counter would burn quota budget for no benefit.
    """
    global _last_personality_stage1

    now = time.monotonic()

    # 1. Time throttle — cheapest check, no side effects
    if now - _last_personality_stage1 < _PERSONALITY_STAGE1_MIN_INTERVAL:
        return False

    # 2. Guard cooldown — defer next check past the cooldown window
    try:
        from core.aux_model import default_model
        from core.model_guard import get_guard
        guard = get_guard()
        aux_m = default_model()
        if not guard.is_available(aux_m):
            rem = guard.cooldown_remaining(aux_m)
            _last_personality_stage1 = now + rem  # retry only after cooldown ends
            print(f"[Personality] ⏳ Stage1 skipped — quota cooldown {rem:.0f}s remaining")
            return False
    except Exception:
        pass

    # Commit to Stage1 call — update throttle timestamp
    _last_personality_stage1 = now

    combined = "User: " + user_text[:250] + "\nJarvis: " + jarvis_text[:180]
    prompt = (
        "Does this exchange reveal ANYTHING about how the user prefers to communicate "
        "or what defaults they expect (e.g. they hate questions, they're very brief, "
        "they always want a specific browser, they expect the assistant to just act)?\n"
        "Reply ONLY: YES or NO\n\nConversation:\n" + combined
    )

    from core.aux_model import aux_call, aux_is_quota_error, aux_cooldown_seconds
    ok, text = aux_call(prompt, api_key, caller="Personality-Stage1")

    if not ok:
        if aux_is_quota_error(text):
            secs = aux_cooldown_seconds(text)
            _last_personality_stage1 = now + secs  # defer past cooldown end
            print(f"[Personality] 🚫 Stage1 hit quota — next check deferred {secs:.0f}s")
        else:
            print(f"[Personality] ⚠️ Stage1 failed: {text}")
        return False

    return "YES" in text.upper()


def analyze_personality(user_text, jarvis_text, api_key):
    """
    Stage 2: Full behavioural profile extraction.
    Guard-protected via aux_call.
    """
    from core.aux_model import aux_call, aux_is_quota_error

    combined = "User: " + user_text[:400] + "\nJarvis: " + jarvis_text[:280]
    prompt = (
        "Analyze the user's communication STYLE and behavioural preferences.\n"
        "Return ONLY valid JSON. Use null for fields you cannot determine.\n\n"
        "Fields:\n"
        '  response_length: "brief"|"moderate"|"detailed"\n'
        '  tone: "formal"|"balanced"|"friendly"\n'
        '  explanation_depth: "simple"|"moderate"|"deep"\n'
        '  humor_level: "none"|"low"|"moderate"|"high"\n'
        "  prefers_confirmations: true|false\n"
        '  autonomy_preference: "do_it"|"explain_then_do"|"always_ask"\n'
        '  question_tolerance: "low"|"medium"|"high"\n'
        '  phrasing_style: "telegraphic"|"normal"|"verbose"\n'
        "  prefers_proactive: true|false\n"
        "  implicit_defaults: object (e.g. {\"preferred_browser\": \"yandex\"})\n"
        "  observation_text: one sentence <=80 chars\n\n"
        "Conversation:\n" + combined + "\n\nJSON:"
    )

    ok, text = aux_call(prompt, api_key, caller="Personality-Stage2")
    if not ok:
        if not aux_is_quota_error(text):
            print(f"[Personality] ⚠️ Stage2 analyze failed: {text}")
        return {}

    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    if not text or text == "{}":
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def update_personality(updates):
    """Слить новые наблюдения в профиль, не потеряв чужие.

    Блок 9: то же лечение, что у памяти. Раньше было три отдельных действия
    (прочитать, слить, записать) без замка между ними, и счётчик реплик мог
    откатиться назад: два потока читали одно и то же число и оба записали
    «на единицу больше».

    Отдельно стоит сказать: в main.py объявлен `_personality_lock`, но он НЕ
    ИСПОЛЬЗУЕТСЯ НИГДЕ (проверено грепом 21.08.2026) — то есть выглядел
    защитой, ею не будучи. Настоящая защита теперь здесь, в кассе состояния.
    """
    if not updates:
        return
    _ensure_migrated()
    result = {}

    def change(profile: dict) -> dict:
        merged = _merge_profile(profile, updates)
        result.update(merged)
        return merged

    try:
        safe_update(_profile_path(), change, _default_profile,
                    label="Personality")
    except Exception as exc:
        # Прежний файл цел. Но об удаче не сообщаем: раньше здесь печаталось
        # «Updated» даже после провала записи.
        print(f"[Personality] ⚠️ Не сохранил, прежняя копия цела: {exc}")
        return
    print(
        "[Personality] Updated -",
        "turns:", result.get("interaction_count"),
        "confidence:", str(round(result.get("confidence", 0) * 100)) + "%",
        "autonomy:", result.get("autonomy_preference", "?"),
        "q_tolerance:", result.get("question_tolerance", "?"),
    )


def format_personality_for_prompt(profile=None):
    if profile is None:
        profile = load_profile()

    count      = profile.get("interaction_count", 0)
    confidence = profile.get("confidence", 0.0)
    if count < MIN_TURNS_FOR_PROMPT or confidence < 0.15:
        return ""

    length   = profile.get("response_length",       "moderate")
    tone     = profile.get("tone",                  "balanced")
    depth    = profile.get("explanation_depth",     "moderate")
    humor    = profile.get("humor_level",           "low")
    confirms = profile.get("prefers_confirmations", True)
    autonomy = profile.get("autonomy_preference",   "explain_then_do")
    q_tol    = profile.get("question_tolerance",    "medium")
    phrasing = profile.get("phrasing_style",        "normal")
    proactive = profile.get("prefers_proactive",    True)
    defaults  = profile.get("implicit_defaults",    {})

    length_map = {
        "brief":    "Keep responses VERY short — one sentence unless asked for more.",
        "moderate": "Concise but complete — don't over-explain.",
        "detailed": "This user appreciates thorough, complete answers.",
    }
    tone_map = {
        "formal":   "Professional, precise tone.",
        "balanced": "Natural, calm, direct tone.",
        "friendly": "Warm, conversational — this user prefers a relaxed style.",
    }
    depth_map = {
        "simple":   "Plain language — avoid technical jargon.",
        "moderate": "Normal language; add brief technical detail when relevant.",
        "deep":     "This user is technical — use precise terminology.",
    }
    humor_map = {
        "none":     "Keep responses professional — no jokes.",
        "low":      "Occasional dry wit is fine, but rare.",
        "moderate": "Include wit and personality where natural.",
        "high":     "Be playful and witty — this user enjoys banter.",
    }
    autonomy_map = {
        "do_it":           "JUST DO IT — act immediately, minimal explanation. Skip preamble.",
        "explain_then_do": "Brief explanation, then act. Don't over-explain.",
        "always_ask":      "Confirm intent before acting on ambiguous requests.",
    }
    q_tol_map = {
        "low":    "NEVER ask unnecessary clarifying questions — make a sensible assumption and proceed.",
        "medium": "Clarify only when genuinely needed.",
        "high":   "It is fine to ask for more details when uncertain.",
    }
    phrasing_map = {
        "telegraphic": "User speaks in short, incomplete phrases — infer context, don't ask for full sentences.",
        "normal":      "",
        "verbose":     "User gives lots of detail — read it all before responding.",
    }

    out = ["[USER BEHAVIOUR PROFILE — adapt your behaviour accordingly]"]
    out.append("Length   : " + length_map.get(length, ""))
    out.append("Tone     : " + tone_map.get(tone, ""))
    out.append("Depth    : " + depth_map.get(depth, ""))
    out.append("Humor    : " + humor_map.get(humor, ""))
    out.append("Autonomy : " + autonomy_map.get(autonomy, ""))
    out.append("Questions: " + q_tol_map.get(q_tol, ""))

    phrasing_note = phrasing_map.get(phrasing, "")
    if phrasing_note:
        out.append("Phrasing : " + phrasing_note)

    if not confirms:
        out.append("Confirmations: Skip 'Understood, sir' style phrases — just act.")

    if not proactive:
        out.append("Proactive: Do not offer unsolicited next-step suggestions.")

    if defaults:
        default_items = "; ".join(f"{k}={v}" for k, v in list(defaults.items())[:5])
        out.append("Defaults : " + default_items)

    obs = profile.get("observations", [])
    if obs and confidence >= 0.4:
        out.append("Observed : " + obs[-1])

    return "\n".join(out) + "\n"
