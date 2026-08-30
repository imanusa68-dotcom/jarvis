# core/search_models.py
# MARK XXXV — Unified Search Result Models (REFACTORED v2)
#
# Defines the canonical SearchResult and SearchResponse dataclasses used
# across ALL providers: DDGS, Google CSE, SerpAPI.
#
# All provider modules normalize their output to match this schema.
# Downstream modules (web_search, deep_research) only ever see these types.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Literal
from urllib.parse import urlparse


ProviderName = Literal[
    "ddgs",
    "ddgs_news",
    "ddgs_text",
    "google_cse",
    "serpapi",
    "gemini_grounded",   # kept for backward compat — no longer used for retrieval
    "playwright",
    "playwright_ddg",
    "unknown",
]

SearchType = Literal["web", "news", "docs", "compare", "verify", "research", "images"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class SearchResult:
    """
    Universal search result — one entry from any provider.

    All providers must produce results matching this schema.
    Optional fields may be None if the provider doesn't supply them.
    """
    title:          str
    url:            str
    snippet:        str            = ""
    domain:         str            = ""
    rank:           int            = 0
    provider:       ProviderName   = "unknown"
    published_date: Optional[str]  = None   # ISO 8601 or free-text
    query_used:     str            = ""
    language:       str            = "en"
    score:          float          = 0.0

    # Enriched fields (populated after web_fetch)
    content_preview: Optional[str] = None   # extracted page text (truncated)
    page_title:      Optional[str] = None   # title from fetched page (may differ)
    fetch_method:    Optional[str] = None   # "requests" | "playwright" | None

    # Flags
    is_news:     bool = False
    is_official: bool = False   # github.com, docs.python.org, etc.
    is_js_heavy: bool = False   # Playwright was needed

    def __post_init__(self):
        if not self.domain and self.url:
            self.domain = _extract_domain(self.url)

    def to_dict(self) -> dict:
        return {
            "title":           self.title,
            "url":             self.url,
            "snippet":         self.snippet,
            "domain":          self.domain,
            "rank":            self.rank,
            "provider":        self.provider,
            "published_date":  self.published_date,
            "query_used":      self.query_used,
            "language":        self.language,
            "score":           self.score,
            "content_preview": self.content_preview,
            "page_title":      self.page_title,
            "fetch_method":    self.fetch_method,
            "is_news":         self.is_news,
            "is_official":     self.is_official,
            "is_js_heavy":     self.is_js_heavy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SearchResult":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class SearchResponse:
    """
    Response from a search operation — wraps a list of SearchResults
    and adds metadata about the search that produced them.
    """
    ok:          bool
    results:     List[SearchResult] = field(default_factory=list)
    query:       str                = ""
    search_type: SearchType         = "web"
    language:    str                = "en"
    provider:    ProviderName       = "unknown"
    total:       int                = 0
    error:       Optional[str]      = None
    from_cache:  bool               = False

    def __post_init__(self):
        if self.total == 0:
            self.total = len(self.results)

    @classmethod
    def failure(cls, error: str, query: str = "") -> "SearchResponse":
        return cls(ok=False, error=error, query=query)

    @classmethod
    def success(
        cls,
        results:     List[SearchResult],
        query:       str         = "",
        search_type: SearchType  = "web",
        language:    str         = "en",
        provider:    ProviderName = "unknown",
        from_cache:  bool        = False,
    ) -> "SearchResponse":
        return cls(
            ok=True, results=results, query=query,
            search_type=search_type, language=language,
            provider=provider, total=len(results),
            from_cache=from_cache,
        )

    def to_simple_dict(self) -> dict:
        """Return the legacy dict format expected by older code."""
        return {
            "ok":       self.ok,
            "results":  [r.to_dict() for r in self.results],
            "provider": self.provider,
            "error":    self.error,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Normalizer helper
# ─────────────────────────────────────────────────────────────────────────────

_OFFICIAL_DOMAINS = {
    "docs.python.org", "developer.mozilla.org", "github.com",
    "stackoverflow.com", "wikipedia.org", "docs.microsoft.com",
    "learn.microsoft.com", "developer.apple.com", "w3.org",
    "npmjs.com", "pypi.org", "crates.io", "pkg.go.dev",
}

_NEWS_DOMAINS = {
    "reuters.com", "bbc.com", "bbc.co.uk", "apnews.com",
    "theguardian.com", "nytimes.com", "techcrunch.com",
    "rbc.ru", "kommersant.ru", "ria.ru", "tass.ru",
}


def _extract_domain(url: str) -> str:
    try:
        d = urlparse(url).netloc
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def normalize_result(raw: dict) -> SearchResult:
    """
    Convert any provider's raw result dict into a SearchResult.
    Applies flag detection (official / news / js_heavy).
    """
    url    = raw.get("url", "") or ""
    domain = raw.get("domain") or _extract_domain(url)

    is_official = any(od in domain for od in _OFFICIAL_DOMAINS)
    is_news     = any(nd in domain for nd in _NEWS_DOMAINS)

    return SearchResult(
        title          = raw.get("title", "") or "",
        url            = url,
        snippet        = raw.get("snippet", "") or raw.get("content", "") or "",
        domain         = domain,
        rank           = raw.get("rank", 0),
        provider       = raw.get("provider", "unknown"),
        published_date = raw.get("published_date"),
        query_used     = raw.get("query_used", ""),
        language       = raw.get("language", "en"),
        score          = float(raw.get("score", 0) or 0),
        content_preview= raw.get("content_preview"),
        page_title     = raw.get("page_title"),
        fetch_method   = raw.get("fetch_method"),
        is_news        = is_news,
        is_official    = is_official,
        is_js_heavy    = raw.get("is_js_heavy", False),
    )
