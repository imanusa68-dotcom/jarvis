# core/search_source_registry.py
# MARK XXXV — Session-Scoped Search Source Registry
#
# Lightweight in-memory registry that stores the sources from the most recent
# web_search or deep_research response shown to the user.
#
# RULES:
#   - Runtime/session only: nothing is written to disk
#   - No connection to long-term memory / personality layer
#   - One set of sources at a time (last response wins)
#   - Thread-safe via a simple Lock

from __future__ import annotations
import threading
from datetime import datetime, timezone
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

_lock    = threading.Lock()
_state: Optional[dict] = None   # None means "no sources stored yet"


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def set_last_sources(
    query: str,
    mode: str,
    sources: list[dict],
) -> None:
    """
    Store the sources from the most recent search/research response.

    Args:
        query:   The original user query string.
        mode:    One of "search", "research", "verify", "compare".
        sources: List of source dicts, each with keys:
                     index (int), title (str), url (str), domain (str)
                 Index values should match what the user sees (1-based).
    """
    global _state
    with _lock:
        _state = {
            "query":     query,
            "mode":      mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources":   list(sources),
        }
    print(f"[SourceRegistry] Stored {len(sources)} source(s) for query: {query!r:.50}")


def get_last_sources() -> Optional[dict]:
    """
    Return the full source-set dict from the most recent response, or None.

    Returned dict structure::

        {
            "query":     str,
            "mode":      str,
            "timestamp": str (ISO-8601),
            "sources": [
                {"index": 1, "title": "...", "url": "...", "domain": "..."},
                ...
            ]
        }
    """
    with _lock:
        return dict(_state) if _state is not None else None


def get_source_by_index(index: int) -> Optional[dict]:
    """
    Return the source at position *index* (1-based) from the last source set.
    Returns None if no sources are stored or the index is out of range.
    """
    with _lock:
        if _state is None:
            return None
        for src in _state["sources"]:
            if src.get("index") == index:
                return dict(src)
    return None


def find_source(match: str) -> Optional[dict]:
    """
    Case-insensitive search for a source by title, domain, or URL substring.

    Returns the first matching source dict, or None if nothing matches.
    """
    if not match:
        return None
    needle = match.lower().strip()
    with _lock:
        if _state is None:
            return None
        for src in _state["sources"]:
            if (
                needle in src.get("title",  "").lower()
                or needle in src.get("domain", "").lower()
                or needle in src.get("url",    "").lower()
            ):
                return dict(src)
    return None


def clear() -> None:
    """Clear the stored sources (useful for testing)."""
    global _state
    with _lock:
        _state = None
