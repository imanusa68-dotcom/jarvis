# actions/google_search_api.py
# MARK XXXV — DDGS + Paid Search Providers (REFACTORED v3)
#
# Provides:
#   search_ddgs()       — DuckDuckGo free search (text + news)
#   google_search_api() — Paid providers: Google CSE, SerpAPI (optional)
#
# DDGS fixes in v3:
#   - Imports `ddgs` package first (renamed from `duckduckgo_search`)
#   - Separate health tracking for ddgs_text and ddgs_news
#   - Tries both ddgs.text() AND ddgs.news() for news queries
#   - Proper error messages for import failures
#   - Timeout handling
#
# NOTE: Gemini Grounded Search is NOT present in this module.
#       Gemini is ONLY used for synthesis in web_search.py / deep_research.py.

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = _get_base_dir()


def _load_config() -> dict:
    try:
        from config.loader import get_secret
        return {k: get_secret(k) for k in
                ("google_cse_api_key", "google_cse_cx", "serpapi_api_key")}
    except Exception as e:
        print(f"[GoogleSearchAPI] Config load error: {e}")
    return {}


def _get_google_cse_config() -> tuple[Optional[str], Optional[str]]:
    cfg = _load_config()
    return cfg.get("google_cse_api_key"), cfg.get("google_cse_cx")


def _get_serpapi_key() -> Optional[str]:
    return _load_config().get("serpapi_api_key")


def _extract_domain(url: str) -> str:
    try:
        d = urlparse(url).netloc
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# DDGS import helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_ddgs_class():
    """
    Import DDGS from the current package name.
    The package was renamed from `duckduckgo_search` to `ddgs`.
    We try `ddgs` first (new name) then fall back to `duckduckgo_search` (old).
    """
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass

    try:
        from duckduckgo_search import DDGS
        return DDGS
    except ImportError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DDGS: free DuckDuckGo search (primary free search provider)
# ─────────────────────────────────────────────────────────────────────────────

def search_ddgs(
    query:       str,
    max_results: int           = 8,
    search_type: str           = "web",
    language:    Optional[str] = None,
) -> List[dict]:
    """
    DuckDuckGo search via the `ddgs` package.

    For news/headline_digest search types, uses ddgs.news() first,
    then supplements with ddgs.text() if needed.
    For other types, uses ddgs.text() only.

    Returns normalized result dicts (may be empty on failure).
    Does NOT raise.
    """
    from core.provider_health import is_healthy, report_success, report_error

    provider_key = f"ddgs_{search_type}"
    if not is_healthy(provider_key):
        print(f"[DDGS] Skipping {provider_key} — in cooldown")
        return []

    DDGS = _get_ddgs_class()
    if DDGS is None:
        print("[DDGS] ❌ Neither 'ddgs' nor 'duckduckgo_search' is installed")
        print("[DDGS]    Run: pip install ddgs")
        return []

    results: List[dict] = []

    try:
        with DDGS() as ddgs:
            if search_type in ("news", "headline_digest"):
                # News endpoint
                try:
                    raw_news = list(ddgs.news(query, max_results=max_results))
                    for i, r in enumerate(raw_news, 1):
                        url = r.get("url", "") or r.get("href", "")
                        results.append({
                            "title":          r.get("title", ""),
                            "url":            url,
                            "snippet":        r.get("body", "") or r.get("excerpt", ""),
                            "domain":         _extract_domain(url),
                            "rank":           i,
                            "provider":       "ddgs",
                            "published_date": r.get("date"),
                            "query_used":     query,
                            "language":       language or "en",
                            "score":          0,
                        })
                except Exception as e:
                    print(f"[DDGS] news() failed: {e} — falling back to text()")

                # If news returned too few, supplement with text
                if len(results) < 3:
                    try:
                        raw_text = list(ddgs.text(query, max_results=max_results))
                        for i, r in enumerate(raw_text, 1):
                            url = r.get("href", "") or r.get("url", "")
                            results.append({
                                "title":          r.get("title", ""),
                                "url":            url,
                                "snippet":        r.get("body", "") or r.get("snippet", ""),
                                "domain":         _extract_domain(url),
                                "rank":           len(results) + i,
                                "provider":       "ddgs",
                                "published_date": None,
                                "query_used":     query,
                                "language":       language or "en",
                                "score":          0,
                            })
                    except Exception as e:
                        print(f"[DDGS] text() fallback also failed: {e}")

            else:
                # Standard text search
                raw = list(ddgs.text(query, max_results=max_results))
                for i, r in enumerate(raw, 1):
                    url = r.get("href", "") or r.get("url", "")
                    results.append({
                        "title":          r.get("title", ""),
                        "url":            url,
                        "snippet":        r.get("body", "") or r.get("snippet", ""),
                        "domain":         _extract_domain(url),
                        "rank":           i,
                        "provider":       "ddgs",
                        "published_date": r.get("date"),
                        "query_used":     query,
                        "language":       language or "en",
                        "score":          0,
                    })

        if results:
            report_success(provider_key)
            print(f"[DDGS] ✅ {len(results)} results for {query[:60]!r}")
        else:
            print(f"[DDGS] ⚠️  0 results for {query[:60]!r}")

        return results

    except Exception as e:
        report_error(provider_key, str(e)[:80])
        print(f"[DDGS] ❌ Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Google CSE (paid, optional)
# ─────────────────────────────────────────────────────────────────────────────

def _search_google_cse(
    query:       str,
    max_results: int           = 10,
    search_type: str           = "web",
    language:    Optional[str] = None,
) -> List[dict]:
    import requests as req
    from core.search_locale import get_cse_lr

    api_key, cx = _get_google_cse_config()
    if not api_key or not cx:
        raise ValueError("Google CSE not configured (missing api_key or cx)")

    params = {
        "key": api_key,
        "cx":  cx,
        "q":   query,
        "num": min(max_results, 10),
    }
    if language and language.lower() != "en":
        params["lr"] = get_cse_lr(language)
    if search_type in ("news", "headline_digest"):
        params["sort"] = "date"

    resp = req.get(
        "https://www.googleapis.com/customsearch/v1",
        params=params, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    results: List[dict] = []
    for i, item in enumerate(data.get("items", []), 1):
        results.append({
            "title":          item.get("title", ""),
            "url":            item.get("link", ""),
            "snippet":        item.get("snippet", ""),
            "domain":         _extract_domain(item.get("link", "")),
            "rank":           i,
            "provider":       "google_cse",
            "published_date": (
                item.get("pagemap", {})
                    .get("metatags", [{}])[0]
                    .get("article:published_time")
            ),
            "query_used":  query,
            "language":    language or "en",
            "score":       0,
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# SerpAPI (paid, optional)
# ─────────────────────────────────────────────────────────────────────────────

def _search_serpapi(
    query:       str,
    max_results: int           = 10,
    search_type: str           = "web",
    language:    Optional[str] = None,
) -> List[dict]:
    import requests as req
    from core.search_locale import get_serpapi_hl

    api_key = _get_serpapi_key()
    if not api_key:
        raise ValueError("SerpAPI not configured (missing serpapi_api_key)")

    params = {
        "api_key": api_key,
        "engine":  "google",
        "q":       query,
        "num":     max_results,
        "hl":      get_serpapi_hl(language),
    }
    if search_type in ("news", "headline_digest"):
        params["tbm"] = "nws"

    resp = req.get("https://serpapi.com/search", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results: List[dict] = []
    for i, item in enumerate(data.get("organic_results", [])[:max_results], 1):
        results.append({
            "title":          item.get("title", ""),
            "url":            item.get("link", ""),
            "snippet":        item.get("snippet", ""),
            "domain":         _extract_domain(item.get("link", "")),
            "rank":           i,
            "provider":       "serpapi",
            "published_date": item.get("date"),
            "query_used":     query,
            "language":       language or "en",
            "score":          0,
        })

    if search_type in ("news", "headline_digest"):
        for i, item in enumerate(data.get("news_results", [])[:max_results], 1):
            results.append({
                "title":          item.get("title", ""),
                "url":            item.get("link", ""),
                "snippet":        item.get("snippet", ""),
                "domain":         _extract_domain(item.get("link", "")),
                "rank":           i,
                "provider":       "serpapi",
                "published_date": item.get("date"),
                "query_used":     query,
                "language":       language or "en",
                "score":          0,
            })

    return results[:max_results]


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Paid providers gateway
# ─────────────────────────────────────────────────────────────────────────────

def google_search_api(
    parameters:    dict = None,
    player                = None,
    session_memory        = None,
) -> dict:
    """
    Try paid search providers (Google CSE → SerpAPI).
    Returns {"ok": True, "results": [...]} or {"ok": False, "error": "..."}.

    This is an OPTIONAL supplementary layer — only called when free providers
    fail and user has configured keys in config/secrets.json.
    """
    from core.provider_health import is_healthy, report_success, report_error

    params      = parameters or {}
    query       = params.get("query", "").strip()
    max_results = int(params.get("max_results", 8))
    search_type = params.get("search_type", "web")
    language    = params.get("language") or None

    if not query:
        return {"ok": False, "error": "Empty query"}

    # ── Google CSE ──────────────────────────────────────────────────────────
    if is_healthy("google_cse"):
        api_key, cx = _get_google_cse_config()
        if api_key and cx:
            try:
                results = _search_google_cse(query, max_results, search_type, language)
                if results:
                    report_success("google_cse")
                    return {"ok": True, "results": results, "provider": "google_cse"}
            except Exception as e:
                report_error("google_cse", str(e)[:80])
                print(f"[GoogleSearchAPI] CSE error: {e}")

    # ── SerpAPI ─────────────────────────────────────────────────────────────
    if is_healthy("serpapi"):
        if _get_serpapi_key():
            try:
                results = _search_serpapi(query, max_results, search_type, language)
                if results:
                    report_success("serpapi")
                    return {"ok": True, "results": results, "provider": "serpapi"}
            except Exception as e:
                report_error("serpapi", str(e)[:80])
                print(f"[GoogleSearchAPI] SerpAPI error: {e}")

    return {
        "ok":    False,
        "error": "No paid providers configured or all failed (this is normal)",
    }
