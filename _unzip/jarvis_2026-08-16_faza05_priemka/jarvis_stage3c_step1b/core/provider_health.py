# core/provider_health.py
# MARK XXXV — Provider Health / Circuit Breaker
#
# Tracks the health of each search provider.
# If a provider generates too many consecutive errors, it enters a cooldown
# period and is skipped until it recovers — preventing hammering dead endpoints.
#
# Thread-safe. Stateless across restarts (in-memory only).

import threading
import time
from typing import Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

ERROR_THRESHOLD  = 3       # errors before cooldown
COOLDOWN_SECONDS = 300     # 5 minutes cooldown
HALF_OPEN_AFTER  = 60      # after 1 min, allow one probe request

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

_state: Dict[str, dict] = {}
_lock  = threading.Lock()


def _get(provider: str) -> dict:
    return _state.setdefault(provider, {
        "errors":          0,
        "successes":       0,
        "cooldown_until":  0.0,
        "last_error_time": 0.0,
        "probe_allowed":   False,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def report_success(provider: str) -> None:
    """Call after a provider returns usable results."""
    with _lock:
        s = _get(provider)
        s["errors"]        = 0
        s["successes"]    += 1
        s["cooldown_until"] = 0.0
        s["probe_allowed"]  = False
    print(f"[ProviderHealth] ✅ {provider} healthy")


def report_error(provider: str, reason: str = "") -> None:
    """Call after a provider throws an exception or returns empty results."""
    with _lock:
        s = _get(provider)
        s["errors"]         += 1
        s["last_error_time"]  = time.time()
        s["probe_allowed"]    = False

        if s["errors"] >= ERROR_THRESHOLD:
            s["cooldown_until"] = time.time() + COOLDOWN_SECONDS
            print(
                f"[ProviderHealth] ⛔ {provider} → COOLDOWN {COOLDOWN_SECONDS}s"
                + (f" ({reason})" if reason else "")
            )
        else:
            print(
                f"[ProviderHealth] ⚠️  {provider} error {s['errors']}/{ERROR_THRESHOLD}"
                + (f" ({reason})" if reason else "")
            )


def is_healthy(provider: str) -> bool:
    """
    Returns True if the provider should be tried.
    
    A provider in cooldown is still allowed ONE probe after HALF_OPEN_AFTER
    seconds so we can detect recovery without waiting the full cooldown.
    """
    with _lock:
        s = _get(provider)
        now = time.time()

        if s["cooldown_until"] == 0.0:
            return True  # never had an error

        if now >= s["cooldown_until"]:
            # Cooldown expired — reset
            s["errors"]         = 0
            s["cooldown_until"] = 0.0
            s["probe_allowed"]  = False
            print(f"[ProviderHealth] 🔄 {provider} cooldown expired → healthy")
            return True

        # Still in cooldown — allow one probe after HALF_OPEN_AFTER
        half_open_at = s["last_error_time"] + HALF_OPEN_AFTER
        if now >= half_open_at and not s["probe_allowed"]:
            s["probe_allowed"] = True
            print(f"[ProviderHealth] 🔬 {provider} half-open probe allowed")
            return True

        return False


def get_status() -> dict:
    """Return a snapshot of all provider states (for logging/debug)."""
    now = time.time()
    with _lock:
        return {
            p: {
                "errors":       s["errors"],
                "in_cooldown":  s["cooldown_until"] > now,
                "cooldown_sec": max(0, int(s["cooldown_until"] - now)),
            }
            for p, s in _state.items()
        }


def reset(provider: Optional[str] = None) -> None:
    """Reset health state. Pass None to reset all providers."""
    with _lock:
        if provider:
            _state.pop(provider, None)
        else:
            _state.clear()
