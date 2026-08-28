# core/feature_flags.py
"""
Stage 2 feature flags - small, central place to gate new subsystems.

Read from ~/.jarvis/settings.json via config.loader. Each flag has a safe
default so a fresh install behaves predictably even before the settings file
mentions it.

Phase 0, step 2 (2026-08-06): the settings file moved OUT of the project folder
and into the home directory, next to jarvis.db, the memory and the personality.
Read the note under CONSENT_SETTING for what living inside the project cost us.
Nothing in this module knows where the file is - config.loader is the one door,
and it is the only place that has to change if the location ever moves again.

Stage-2 decision: fileops is ON by default and can be switched OFF by putting
"fileops_enabled": false in ~/.jarvis/settings.json. There is no toggle in the
UI: this docstring used to claim one, and ui.py has never contained a single
call to get_setting or set_setting (checked 2026-08-06 - zero hits).
"""
from __future__ import annotations

FILEOPS_SETTING = "fileops_enabled"
FILEOPS_DEFAULT = True  # Stage-2 decision: default ON


def _get(name, default):
    try:
        from config.loader import get_setting
        val = get_setting(name, default)
    except Exception:
        return default
    return default if val is None else val


def _as_bool(val, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(val, (int, float)):
        return bool(val)
    return default


def fileops_enabled() -> bool:
    """True if the transactional fileops layer should handle file mutations."""
    return _as_bool(_get(FILEOPS_SETTING, FILEOPS_DEFAULT), FILEOPS_DEFAULT)


def set_fileops_enabled(enabled: bool) -> None:
    """Persist the fileops flag (used by the UI settings toggle)."""
    from config.loader import set_setting
    set_setting(FILEOPS_SETTING, bool(enabled))


# -- Stage 3A: durable consent ------------------------------------------------
# Confirmations become database rows instead of a boolean the model sets on its
# own call. Starts OFF: the two mechanisms coexist for one validation round on
# the real machine, then this flips to ON and the old `confirmed` flag is
# deleted outright. The overlap is deliberately SHORT - a permanent "both ways"
# switch is how a security fix quietly never finishes.
#
# 2026-07-26: flipped to ON after two clean Windows runs (531 passed) plus a
# live delete where the ticket was minted, spent, and correctly refused on
# replay/mismatch. It ALSO had to flip because the setting lives in the project
# folder (config/settings.json), so every fresh unzip silently reverted to the
# legacy path - and a safety mechanism that depends on the user remembering a
# command in the right directory is not a safety mechanism.
#
# 2026-08-06 (phase 0, step 2): that cause is now fixed at the root - the file
# lives in the home directory and survives an unzip. The default stays ON:
# fixing where a setting is stored says nothing about which mechanism is right.
CONSENT_SETTING = "durable_consent_enabled"
CONSENT_DEFAULT = True


def durable_consent_enabled() -> bool:
    """True if confirmations are backed by consent tickets in jarvis.db."""
    return _as_bool(_get(CONSENT_SETTING, CONSENT_DEFAULT), CONSENT_DEFAULT)


def set_durable_consent_enabled(enabled: bool) -> None:
    from config.loader import set_setting
    set_setting(CONSENT_SETTING, bool(enabled))


# -- Phase 0, step 2: the agents switch ---------------------------------------
# The two-level architecture (one main agent that speaks, worker agents that do
# not) arrives in phase 2. The switch is created now, deliberately controlling
# nothing, for two reasons. First, it has to live outside the project folder
# BEFORE any agent code exists, or the first unzip after phase 2 turns agents on
# by accident. Second, it is the rollback plan for every later phase: one line
# in one file, and Jarvis is a monolith again with all the agent code still on
# disk but dead.
#
# The key is spelled JARVIS_AGENTS because that is the name the design document
# and the plan use. One name means the owner can search for it once and find
# both the switch and the reasoning behind it.
AGENTS_SETTING = "JARVIS_AGENTS"
AGENTS_DEFAULT = False   # off until phase 2 says otherwise, on purpose


def agents_enabled() -> bool:
    """True if work may be handed to worker agents instead of the monolith."""
    return _as_bool(_get(AGENTS_SETTING, AGENTS_DEFAULT), AGENTS_DEFAULT)


def set_agents_enabled(enabled: bool) -> None:
    """Persist the agents switch in ~/.jarvis/settings.json."""
    from config.loader import set_setting
    set_setting(AGENTS_SETTING, bool(enabled))
