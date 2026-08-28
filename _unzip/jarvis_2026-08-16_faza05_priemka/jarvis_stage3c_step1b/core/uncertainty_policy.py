# core/uncertainty_policy.py
# MARK XXXV — Uncertainty & Clarification Policy
# ─────────────────────────────────────────────────────────────────────────────
# Explicit, centralized policy for deciding when to clarify vs when to assume.
#
# Principle: An intelligent assistant minimises unnecessary questions.
# It clarifies only when the cost of a wrong assumption is genuinely high.
#
# Public API:
#   should_clarify(intent, risk, confidence, has_context, personality) → bool
#   classify_risk(tool_name, params) → RiskLevel
#   explain_decision(intent, risk, confidence, has_context) → str
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from enum import Enum
from typing import Optional


class RiskLevel(Enum):
    """Estimated cost of being wrong about this action."""
    LOW    = "low"     # Easy to undo / no real consequence
    MEDIUM = "medium"  # Some effort to undo, but not dangerous
    HIGH   = "high"    # Hard to undo or embarrassing
    FATAL  = "fatal"   # Cannot be undone / financial / security


# ─────────────────────────────────────────────────────────────────────────────
# Risk source — single source of truth is core/security.py.
# We map its string risk ("low"/"medium"/"high"/"critical") onto this enum.
# "critical" maps to FATAL (the top tier here). This avoids defining risk twice.
# ─────────────────────────────────────────────────────────────────────────────

_RISK_FROM_STR: dict[str, RiskLevel] = {
    "low":      RiskLevel.LOW,
    "medium":   RiskLevel.MEDIUM,
    "high":     RiskLevel.HIGH,
    "critical": RiskLevel.FATAL,
}


def classify_risk(tool_name: str, params: dict | None = None) -> RiskLevel:
    """
    Estimate the risk level for a given tool call.

    Base risk comes from core/security.py (single source of truth, including
    action-level risk like file delete = critical). A few param-based nuances
    are layered on top for tools whose risk depends on free-text input.
    """
    # Base risk from the central policy (handles action-level risk too).
    try:
        from core.security import get_risk as _sec_get_risk
        base = _RISK_FROM_STR.get(_sec_get_risk(tool_name, params), RiskLevel.MEDIUM)
    except Exception:
        base = RiskLevel.MEDIUM

    if params is None:
        return base

    # Phase 0, step 3: the second copy of the danger-word list used to live
    # here, and it had already drifted (it was missing 'del '). It was also
    # dead weight: core/security.get_risk() above already promotes free-text
    # cmd_control tasks, so the promotion arrives through `base`. One list,
    # one place - core/security.py.

    return base


# ─────────────────────────────────────────────────────────────────────────────
# Core policy function
# ─────────────────────────────────────────────────────────────────────────────

def should_clarify(
    intent:          str,
    risk:            RiskLevel,
    confidence:      float,
    has_context:     bool,
    personality:     dict | None = None,
) -> bool:
    """
    Decide whether to ask the user for clarification.

    Rules (in priority order):
    1. FATAL risk → always clarify (no matter what)
    2. HIGH risk + confidence < 0.90 → clarify
    3. HIGH risk + confidence ≥ 0.90 + has_context → proceed
    4. MEDIUM risk + confidence < 0.45 + no context → clarify
    5. LOW risk → never clarify (just proceed with best guess)
    6. User personality: low question_tolerance → raise confidence thresholds

    Args:
        intent:      Semantic action class (from semantic_interpreter)
        risk:        RiskLevel for this action
        confidence:  0.0–1.0 interpreter confidence
        has_context: Whether dialogue state provides relevant prior context
        personality: User personality profile dict (optional)

    Returns:
        True  → ask the user for more info
        False → proceed with reasonable assumption
    """
    # Personality adjustment: low question_tolerance → be more autonomous
    tolerance = "medium"
    if personality:
        tolerance = personality.get("question_tolerance", "medium")

    tolerance_factor = {
        "low":    0.80,   # user hates questions → higher autonomy
        "medium": 1.00,
        "high":   1.20,   # user fine with questions → clarify more
    }.get(tolerance, 1.00)

    # Adjust thresholds by tolerance
    high_threshold   = 0.90 / tolerance_factor
    medium_threshold = 0.45 / tolerance_factor

    # Rule 1: Fatal always clarifies
    if risk == RiskLevel.FATAL:
        return True

    # Rule 2–3: High risk
    if risk == RiskLevel.HIGH:
        if confidence >= high_threshold and has_context:
            return False  # Confident enough + context → proceed
        return True

    # Rule 4: Medium risk, low confidence, no context
    if risk == RiskLevel.MEDIUM:
        if confidence < medium_threshold and not has_context:
            return True
        return False

    # Rule 5: Low risk → never ask
    return False


def explain_decision(
    intent:      str,
    risk:        RiskLevel,
    confidence:  float,
    has_context: bool,
) -> str:
    """
    Return a brief human-readable explanation of the clarification decision.
    Useful for debugging.
    """
    decision = should_clarify(intent, risk, confidence, has_context)
    if decision:
        reasons = []
        if risk == RiskLevel.FATAL:
            reasons.append("action is irreversible/fatal")
        if risk == RiskLevel.HIGH and confidence < 0.90:
            reasons.append(f"high risk + low confidence ({confidence:.0%})")
        if risk == RiskLevel.MEDIUM and confidence < 0.45:
            reasons.append(f"medium risk + low confidence ({confidence:.0%})")
        if not has_context:
            reasons.append("no prior context to resolve ambiguity")
        return f"CLARIFY — {'; '.join(reasons) or 'policy default'}"
    else:
        return f"PROCEED — risk={risk.value}, confidence={confidence:.0%}, context={'yes' if has_context else 'no'}"
