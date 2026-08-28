# core/search_cache.py
# MARK XXXV — Search Result Cache
#
# In-memory TTL cache for search results and fetched page content.
# Reduces repeated identical queries to search providers.
#
# TTL strategy:
#   - news / current events: 5 minutes
#   - docs / technical:      30 minutes
#   - general web:           20 minutes
#   - page content:          15 minutes

import hashlib
import threading
import time
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# TTL Constants (seconds)
# ─────────────────────────────────────────────────────────────────────────────

TTL_NEWS    = 300    # 5 min
TTL_DOCS    = 1800   # 30 min
TTL_GENERAL = 1200   # 20 min
TTL_PAGE    = 900    # 15 min

_SEARCH_TYPE_TTL = {
    "news":     TTL_NEWS,
    "docs":     TTL_DOCS,
    "web":      TTL_GENERAL,
    "general":  TTL_GENERAL,
    "compare":  TTL_GENERAL,
    "verify":   TTL_DOCS,
    "research": TTL_GENERAL,
    "page":     TTL_PAGE,
}

# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

_cache: dict = {}
_lock  = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_key(*parts: str) -> str:
    raw = "|".join(str(p).lower().strip() for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _ttl_for(search_type: str) -> int:
    return _SEARCH_TYPE_TTL.get(search_type, TTL_GENERAL)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Search results
# ─────────────────────────────────────────────────────────────────────────────

def get_search(
    query:       str,
    search_type: str = "web",
    language:    str = "en",
) -> Optional[Any]:
    """Return cached search results or None if missing / expired."""
    key = _make_key(query, search_type, language)
    with _lock:
        entry = _cache.get(key)
        if entry and entry["expires"] > time.time():
            print(f"[SearchCache] HIT  search: {query[:60]!r}")
            return entry["data"]
        if entry:
            del _cache[key]  # expired
    return None


def set_search(
    query:       str,
    data:        Any,
    search_type: str = "web",
    language:    str = "en",
) -> None:
    """Store search results with appropriate TTL."""
    key = _make_key(query, search_type, language)
    ttl = _ttl_for(search_type)
    with _lock:
        _cache[key] = {"data": data, "expires": time.time() + ttl}
    print(f"[SearchCache] SET  search ({search_type}, TTL={ttl}s): {query[:60]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Page content
# ─────────────────────────────────────────────────────────────────────────────

def get_page(url: str) -> Optional[Any]:
    """Return cached page content or None."""
    key = _make_key("page", url)
    with _lock:
        entry = _cache.get(key)
        if entry and entry["expires"] > time.time():
            print(f"[SearchCache] HIT  page: {url[:60]}")
            return entry["data"]
        if entry:
            del _cache[key]
    return None


def set_page(url: str, data: Any) -> None:
    """Cache fetched page content."""
    key = _make_key("page", url)
    with _lock:
        _cache[key] = {"data": data, "expires": time.time() + TTL_PAGE}


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance
# ─────────────────────────────────────────────────────────────────────────────

def clear_expired() -> int:
    """Remove expired entries. Returns number of entries removed."""
    now = time.time()
    with _lock:
        expired = [k for k, v in _cache.items() if v["expires"] < now]
        for k in expired:
            del _cache[k]
    return len(expired)


def clear_all() -> None:
    """Clear the entire cache."""
    with _lock:
        _cache.clear()


def stats() -> dict:
    """Return cache statistics."""
    now = time.time()
    with _lock:
        total   = len(_cache)
        active  = sum(1 for v in _cache.values() if v["expires"] > now)
        expired = total - active
    return {"total": total, "active": active, "expired": expired}
