# actions/web_fetch.py
# MARK XXXV — Web Page Fetch & Content Extraction (REFACTORED v2)
#
# Two-tier fetch strategy:
#   Tier 1 (fast): requests + BeautifulSoup — always tried first
#   Tier 2 (deep): Playwright — used automatically when tier 1 fails:
#     - Response is HTTP 403 / 429 / blocked
#     - Content is suspiciously short (< 300 chars after cleaning)
#     - Page contains JS-shell signatures (common SPA patterns)
#     - No meaningful text found in main content areas
#
# Page content is cached to avoid re-fetching the same URL.
#
# Result format (consistent with all providers):
#   {ok, url, title, content, excerpt, domain, published_date, content_length,
#    method, error}

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Union
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_TIMEOUT    = 10   # seconds, requests
PLAYWRIGHT_TIMEOUT = 15   # seconds, Playwright
MAX_CONTENT_CHARS  = 8000
MAX_EXCERPT_CHARS  = 500
MAX_CONCURRENT     = 5
MIN_CONTENT_CHARS  = 300  # below this → try Playwright fallback

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NOISE_TAGS = [
    "script", "style", "nav", "footer", "header", "aside",
    "noscript", "iframe", "form", "button", "svg", "canvas",
]

NOISE_CLASS_PATTERNS = [
    r"nav", r"footer", r"header", r"sidebar", r"menu", r"cookie",
    r"popup", r"modal", r"ad-", r"ads-", r"advert", r"banner",
    r"comment", r"share", r"social", r"related", r"recommend",
]

# JS-shell signatures: pages that render content only via JS
JS_SHELL_PATTERNS = [
    r"<div\s+id=['\"]root['\"]>\s*</div>",
    r"<div\s+id=['\"]app['\"]>\s*</div>",
    r"<div\s+id=['\"]__next['\"]>\s*</div>",
    r"window\.__INITIAL_STATE__",
    r"window\.__NUXT__",
    r"<noscript>You need to enable JavaScript",
    r"Please enable JavaScript",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    try:
        d = urlparse(url).netloc
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]
    return truncated + "..."


def _is_valid_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if not parsed.netloc:
            return False
        domain = parsed.netloc.lower()
        if any(b in domain for b in ["localhost", "127.0.0.1", "0.0.0.0"]):
            return False
        return True
    except Exception:
        return False


def _build_accept_language(language: Optional[str]) -> str:
    try:
        from core.search_locale import get_accept_language
        return get_accept_language(language)
    except ImportError:
        return "en-US,en;q=0.9"


def _is_js_shell(html: str) -> bool:
    """Return True if page looks like a JS-rendered SPA shell with no real content."""
    for pattern in JS_SHELL_PATTERNS:
        if re.search(pattern, html, re.IGNORECASE):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: BeautifulSoup extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_with_bs4(html: str, url: str, max_chars: int = MAX_CONTENT_CHARS) -> dict:
    """Extract content from HTML using BeautifulSoup."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()

    # Excerpt from meta
    excerpt = ""
    for attr_name, attr_val in [("name", "description"), ("property", "og:description")]:
        meta = soup.find("meta", attrs={attr_name: attr_val})
        if meta and meta.get("content"):
            excerpt = meta["content"].strip()
            break

    # Published date
    published_date = None
    date_metas = [
        ("property", "article:published_time"),
        ("name", "pubdate"), ("name", "publishdate"),
        ("name", "date"), ("itemprop", "datePublished"),
    ]
    for attr_name, attr_val in date_metas:
        meta = soup.find("meta", attrs={attr_name: attr_val})
        if meta and meta.get("content"):
            published_date = meta["content"].strip()
            break

    # Remove noise tags
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for pattern in NOISE_CLASS_PATTERNS:
        for el in soup.find_all(class_=re.compile(pattern, re.I)):
            el.decompose()

    # Extract main content
    content = ""
    main_selectors = ["article", "main", '[role="main"]', ".content",
                      ".article-body", ".post-body", ".entry-content", "#content"]
    for sel in main_selectors:
        main = soup.select_one(sel)
        if main:
            content = main.get_text(separator="\n")
            break

    if not content:
        body = soup.find("body")
        if body:
            content = body.get_text(separator="\n")

    content = _clean_text(content)
    if not excerpt and content:
        excerpt = _truncate(content, MAX_EXCERPT_CHARS)

    return {
        "title":          title,
        "content":        _truncate(content, max_chars),
        "excerpt":        excerpt,
        "published_date": published_date,
        "content_length": len(content),
    }


def _fetch_with_requests(
    url:      str,
    timeout:  int           = DEFAULT_TIMEOUT,
    language: Optional[str] = None,
    max_chars: int          = MAX_CONTENT_CHARS,
) -> dict:
    """
    Tier 1: fetch page with requests and extract with BS4.
    Returns the result dict (ok=True/False).
    """
    import requests

    headers = {
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": _build_accept_language(language),
        "Accept-Encoding": "gzip, deflate",
        "Connection":      "keep-alive",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)

        if resp.status_code in (403, 429, 503):
            return {"ok": False, "url": url, "error": f"HTTP {resp.status_code}", "blocked": True}

        resp.raise_for_status()
        html = resp.text

        extracted = _extract_with_bs4(html, url, max_chars)
        content   = extracted.get("content", "")

        return {
            "ok":             True,
            "url":            url,
            "domain":         _extract_domain(url),
            "title":          extracted["title"],
            "content":        content,
            "excerpt":        extracted["excerpt"],
            "published_date": extracted["published_date"],
            "content_length": extracted["content_length"],
            "method":         "requests",
            "js_shell":       _is_js_shell(html),
            "needs_playwright": (
                extracted["content_length"] < MIN_CONTENT_CHARS
                or _is_js_shell(html)
            ),
        }

    except Exception as e:
        return {"ok": False, "url": url, "error": str(e), "method": "requests"}


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Playwright extraction
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_with_playwright(
    url:      str,
    timeout:  int           = PLAYWRIGHT_TIMEOUT,
    language: Optional[str] = None,
    max_chars: int          = MAX_CONTENT_CHARS,
) -> dict:
    """
    Tier 2: use Playwright to render JS and extract content.
    Called when requests fetch fails or returns too little text.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print(f"[WebFetch] Playwright not installed — skipping JS fallback for {url}")
        return {"ok": False, "url": url, "error": "Playwright not available", "method": "playwright"}

    print(f"[WebFetch] 🎭 Playwright fetch: {url[:80]}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale=_build_accept_language(language).split(",")[0].replace(";q=0.9", ""),
                java_script_enabled=True,
            )
            page = context.new_page()

            # Block heavy resources to speed up loading
            page.route("**/*.{png,jpg,jpeg,gif,svg,mp4,mp3,woff,woff2,ttf,otf}", lambda route: route.abort())

            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(1500)  # brief wait for JS to settle

            html = page.content()

            # Try to get title from page
            try:
                page_title = page.title()
            except Exception:
                page_title = ""

            browser.close()

        extracted = _extract_with_bs4(html, url, max_chars)
        if page_title and not extracted["title"]:
            extracted["title"] = page_title

        return {
            "ok":             True,
            "url":            url,
            "domain":         _extract_domain(url),
            "title":          extracted["title"],
            "content":        extracted["content"],
            "excerpt":        extracted["excerpt"],
            "published_date": extracted["published_date"],
            "content_length": extracted["content_length"],
            "method":         "playwright",
        }

    except Exception as e:
        print(f"[WebFetch] Playwright error for {url}: {e}")
        return {"ok": False, "url": url, "error": str(e), "method": "playwright"}


# ─────────────────────────────────────────────────────────────────────────────
# Combined fetch (Tier 1 → Tier 2 if needed)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_one(
    url:       str,
    timeout:   int           = DEFAULT_TIMEOUT,
    language:  Optional[str] = None,
    max_chars: int           = MAX_CONTENT_CHARS,
) -> dict:
    """
    Fetch a single URL with automatic Playwright fallback.
    Returns a result dict with ok=True/False.
    """
    from core.search_cache import get_page, set_page

    # Cache check
    cached = get_page(url)
    if cached:
        return cached

    result = _fetch_with_requests(url, timeout, language, max_chars)

    # Playwright fallback conditions
    needs_playwright = (
        not result.get("ok") and result.get("blocked")
    ) or (
        result.get("ok") and result.get("needs_playwright")
    )

    if needs_playwright:
        print(f"[WebFetch] Falling back to Playwright for: {url[:80]}")
        playwright_result = _fetch_with_playwright(url, PLAYWRIGHT_TIMEOUT, language, max_chars)
        if playwright_result.get("ok") and playwright_result.get("content_length", 0) > 0:
            result = playwright_result

    if result.get("ok"):
        set_page(url, result)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def web_fetch(
    parameters:     dict = None,
    player                = None,
    session_memory        = None,
) -> dict:
    """
    Fetch one or multiple URLs and extract clean page content.

    Parameters:
      url       — single URL string
      urls      — list of URL strings
      timeout   — request timeout seconds (default 10)
      max_chars — max chars to extract (default 8000)
      language  — ISO-639-1 code for Accept-Language header

    Returns:
      {"ok": True, "results": [<page_dict>, ...]}
      or {"ok": False, "error": "..."}
    """
    params    = parameters or {}
    timeout   = int(params.get("timeout",   DEFAULT_TIMEOUT))
    max_chars = int(params.get("max_chars", MAX_CONTENT_CHARS))
    language  = params.get("language") or None

    # Collect URLs
    urls: List[str] = []
    if params.get("url"):
        urls = [params["url"]]
    elif params.get("urls"):
        urls = params["urls"]

    if not urls:
        return {"ok": False, "error": "No URL provided"}

    # Filter valid
    valid_urls = [u for u in urls if _is_valid_url(u)]
    if not valid_urls:
        return {"ok": False, "error": "No valid URLs provided"}

    # Fetch (parallel for multiple URLs)
    results: List[dict] = []

    if len(valid_urls) == 1:
        results.append(_fetch_one(valid_urls[0], timeout, language, max_chars))
    else:
        batch = valid_urls[:MAX_CONCURRENT]
        with ThreadPoolExecutor(max_workers=min(len(batch), MAX_CONCURRENT)) as executor:
            futures = {
                executor.submit(_fetch_one, url, timeout, language, max_chars): url
                for url in batch
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"ok": False, "url": futures[future], "error": str(e)})

    # Sort so successful results come first
    results.sort(key=lambda r: (0 if r.get("ok") else 1, r.get("url", "")))

    return {"ok": True, "results": results}
