# actions/web_search.py
# MARK XXXV — Web Search Orchestration Layer (REFACTORED v3)
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  INTENT-AWARE SEARCH PIPELINE                                            │
# │                                                                          │
# │  headline_digest           ──► date normalisation → digest pipeline      │
# │  topic_news                ──► topic-specific news queries               │
# │  topic_capabilities        ──► topic features/docs queries               │
# │  topic_news_and_capabilities ► mixed topic news + features queries       │
# │  news                      ──► generic news search with rewrite          │
# │  docs                      ──► docs-oriented search + English expansion  │
# │  compare                   ──► per-item retrieval + Gemini comparison    │
# │  verify                    ──► deep_research in verify mode              │
# │  research                  ──► deep_research full pipeline               │
# │  web (default)             ──► quick search with rewrite                │
# │                                                                          │
# │  RETRIEVAL CHAIN (free-first, Gemini only for synthesis):                │
# │    A. DDGS news      — DuckDuckGo news endpoint                          │
# │    B. DDGS text      — DuckDuckGo text with rewritten queries            │
# │    C. Playwright     — browser-based search as last retrieval fallback   │
# │    D. Dedup + rank   — merge & score all results                         │
# │    E. Gemini synth   — ONE synthesis call to build the final answer      │
# │    F. Graceful fail  — honest message only after A-C all failed          │
# └──────────────────────────────────────────────────────────────────────────┘
#
# Key fixes in v3 over v2:
#   - headline_digest intent: "что нового было вчера" → date-specific queries
#   - Relative dates normalised: "вчера" → "10 апреля 2026"
#   - DDGS news tried first, unconditionally for news/digest queries
#   - Playwright browser search as step C (scrapes DuckDuckGo search page)
#   - "nothing found" only after all A-C steps have genuinely failed
#   - News digest synthesized as structured summary, not generic answer

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, quote_plus

# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()


from config.loader import get_api_key as _get_api_key
from config.loader import get_model as _get_model


# ─────────────────────────────────────────────────────────────────────────────
# Quality domain tables
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_DOMAINS = {
    "docs.python.org", "developer.mozilla.org", "stackoverflow.com",
    "github.com", "wikipedia.org", "arxiv.org", "w3.org",
    "docs.microsoft.com", "learn.microsoft.com", "developer.apple.com",
    "reuters.com", "bbc.com", "bbc.co.uk", "apnews.com",
    "theguardian.com", "nytimes.com", "techcrunch.com",
    "habr.com", "3dnews.ru", "ixbt.com", "rbc.ru", "kommersant.ru",
    "lenta.ru", "ria.ru", "tass.ru", "interfax.ru",
    "ars technica.com", "arstechnica.com",
}

_JUNK_DOMAINS = {
    "pinterest.com", "quora.com",
}

_NEWS_DOMAINS = {
    "reuters.com", "bbc.com", "bbc.co.uk", "apnews.com", "theguardian.com",
    "nytimes.com", "techcrunch.com", "rbc.ru", "kommersant.ru", "lenta.ru",
    "ria.ru", "tass.ru", "interfax.ru", "gazeta.ru", "vedomosti.ru",
    "aif.ru", "m.lenta.ru", "iz.ru", "life.ru", "mk.ru", "novayagazeta.ru",
}


# ─────────────────────────────────────────────────────────────────────────────
# Result scoring & deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _score_result(r: dict, search_type: str) -> float:
    score  = float(r.get("score") or 0)
    domain = r.get("domain", "").lower()

    if any(qd in domain for qd in _QUALITY_DOMAINS):
        score += 2.0
    if any(qd in domain for qd in _NEWS_DOMAINS):
        score += 1.5  # bonus for real news domains in news searches
    if any(jd in domain for jd in _JUNK_DOMAINS):
        score -= 2.0

    snippet = r.get("snippet", "") or ""
    if len(snippet) > 80:
        score += 0.4
    if len(snippet) > 200:
        score += 0.4

    # Fresh date matters for news and topic news searches
    if search_type in (
        "news", "headline_digest",
        "topic_news", "topic_news_and_capabilities",
    ) and r.get("published_date"):
        score += 1.2

    rank = r.get("rank", 99)
    score += max(0, (10 - rank) * 0.1)

    return score


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
    except Exception:
        return url


def deduplicate_and_rank(
    results:     List[dict],
    search_type: str = "web",
    max_results: int = 8,
) -> List[dict]:
    seen_urls:    set = set()
    seen_domains: Dict[str, int] = {}
    deduped: List[dict] = []

    for r in results:
        url    = r.get("url", "")
        domain = r.get("domain", "")
        if not url:
            continue
        norm = _normalize_url(url)
        if norm in seen_urls:
            continue
        seen_urls.add(norm)

        # Cap per-domain results (looser for news/topic searches — prefer diversity)
        max_per_domain = 3 if search_type in (
            "news", "headline_digest",
            "topic_news", "topic_news_and_capabilities",
        ) else 2
        if seen_domains.get(domain, 0) >= max_per_domain:
            continue
        seen_domains[domain] = seen_domains.get(domain, 0) + 1

        r["_score"] = _score_result(r, search_type)
        deduped.append(r)

    deduped.sort(key=lambda x: x.get("_score", 0), reverse=True)
    for i, r in enumerate(deduped[:max_results], 1):
        r["rank"] = i

    return deduped[:max_results]


# ─────────────────────────────────────────────────────────────────────────────
# Source registry
# ─────────────────────────────────────────────────────────────────────────────

def _register_sources(query: str, search_type: str, results: List[dict]) -> None:
    try:
        from core.search_source_registry import set_last_sources
        set_last_sources(query, search_type, [
            {"index": i+1, "title": r.get("title",""), "url": r.get("url",""), "domain": r.get("domain","")}
            for i, r in enumerate(results[:10])
        ])
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Step A: DDGS news retrieval
# ─────────────────────────────────────────────────────────────────────────────

def _ddgs_news_retrieve(
    query:       str,
    max_results: int = 8,
    language:    Optional[str] = None,
) -> List[dict]:
    """
    DuckDuckGo news endpoint (ddgs.news()).
    Returns normalized result list.
    """
    from core.provider_health import is_healthy, report_success, report_error

    if not is_healthy("ddgs_news"):
        print("[Retrieve:DDGS-news] In cooldown — skipping")
        return []

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        from urllib.parse import urlparse as _up

        def _dom(u):
            try:
                d = _up(u).netloc
                return d[4:] if d.startswith("www.") else d
            except Exception:
                return ""

        with DDGS() as ddgs:
            raw = list(ddgs.news(query, max_results=max_results))

        results: List[dict] = []
        for i, r in enumerate(raw, 1):
            url = r.get("url", "") or r.get("href", "")
            results.append({
                "title":          r.get("title", ""),
                "url":            url,
                "snippet":        r.get("body", "") or r.get("excerpt", ""),
                "domain":         _dom(url),
                "rank":           i,
                "provider":       "ddgs_news",
                "published_date": r.get("date"),
                "query_used":     query,
                "language":       language or "en",
                "score":          0,
            })

        if results:
            report_success("ddgs_news")
            print(f"[Retrieve:DDGS-news] {len(results)} results for {query[:60]!r}")
        return results

    except Exception as e:
        report_error("ddgs_news", str(e)[:80])
        print(f"[Retrieve:DDGS-news] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Step B: DDGS text retrieval
# ─────────────────────────────────────────────────────────────────────────────

def _ddgs_text_retrieve(
    query:       str,
    max_results: int = 8,
    language:    Optional[str] = None,
) -> List[dict]:
    """
    DuckDuckGo text search (ddgs.text()).
    Returns normalized result list.
    """
    from core.provider_health import is_healthy, report_success, report_error

    if not is_healthy("ddgs_text"):
        print("[Retrieve:DDGS-text] In cooldown — skipping")
        return []

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        from urllib.parse import urlparse as _up

        def _dom(u):
            try:
                d = _up(u).netloc
                return d[4:] if d.startswith("www.") else d
            except Exception:
                return ""

        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))

        results: List[dict] = []
        for i, r in enumerate(raw, 1):
            url = r.get("href", "") or r.get("url", "")
            results.append({
                "title":          r.get("title", ""),
                "url":            url,
                "snippet":        r.get("body", "") or r.get("snippet", ""),
                "domain":         _dom(url),
                "rank":           i,
                "provider":       "ddgs_text",
                "published_date": r.get("date"),
                "query_used":     query,
                "language":       language or "en",
                "score":          0,
            })

        if results:
            report_success("ddgs_text")
            print(f"[Retrieve:DDGS-text] {len(results)} results for {query[:60]!r}")
        return results

    except Exception as e:
        report_error("ddgs_text", str(e)[:80])
        print(f"[Retrieve:DDGS-text] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Step C: Playwright browser search (last retrieval fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _playwright_search_retrieve(
    query:       str,
    max_results: int = 6,
    language:    Optional[str] = None,
) -> List[dict]:
    """
    Step D fallback: use Playwright to load DuckDuckGo search in a real browser
    and scrape results when all API-based providers have failed.
    """
    print(f"[Retrieve:Playwright] Attempting browser search for {query[:60]!r}")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("[Retrieve:Playwright] Playwright not installed — skipping browser fallback")
        return []

    try:
        from urllib.parse import quote_plus as qp, urlparse as _up

        def _dom(u):
            try:
                d = _up(u).netloc
                return d[4:] if d.startswith("www.") else d
            except Exception:
                return ""

        search_url = f"https://duckduckgo.com/?q={qp(query)}&ia=news"

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)

            html = page.content()
            browser.close()

        # Parse results from DuckDuckGo HTML
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        results: List[dict] = []

        # DuckDuckGo news result cards
        for card in soup.select("[data-testid='news-tile'], .tile--news, article")[:max_results]:
            a = card.find("a", href=True)
            if not a:
                continue
            href = a.get("href", "")
            if not href or href.startswith("#"):
                continue

            title_el = card.find(["h2", "h3", "span"])
            title = title_el.get_text(strip=True) if title_el else ""

            snippet_el = card.find("p") or card.find(class_=re.compile(r"snippet|body|desc"))
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if not title:
                continue

            results.append({
                "title":          title,
                "url":            href,
                "snippet":        snippet,
                "domain":         _dom(href),
                "rank":           len(results) + 1,
                "provider":       "playwright_ddg",
                "published_date": None,
                "query_used":     query,
                "language":       language or "en",
                "score":          0,
            })

        # If DDG-specific selectors failed, fall back to generic link extraction
        if not results:
            for a in soup.select("a[href]")[:30]:
                href = a.get("href", "")
                if not href or href.startswith("#") or "duckduckgo.com" in href:
                    continue
                if not href.startswith("http"):
                    continue
                title = a.get_text(strip=True)[:120]
                if len(title) < 10:
                    continue
                results.append({
                    "title":       title,
                    "url":         href,
                    "snippet":     "",
                    "domain":      _dom(href),
                    "rank":        len(results) + 1,
                    "provider":    "playwright_ddg",
                    "query_used":  query,
                    "language":    language or "en",
                    "score":       0,
                })

        print(f"[Retrieve:Playwright] {len(results)} results for {query[:60]!r}")
        return results[:max_results]

    except Exception as e:
        print(f"[Retrieve:Playwright] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Core retrieval: free provider chain (A → B → C)
# ─────────────────────────────────────────────────────────────────────────────

def _retrieve(
    query:       str,
    max_results: int           = 8,
    search_type: str           = "web",
    language:    Optional[str] = None,
    use_playwright_fallback: bool = True,
) -> List[dict]:
    """
    Retrieval chain: DDGS news → DDGS text → (Playwright).

    - DDGS news is tried first; text search if results are still thin.
    - Playwright only if use_playwright_fallback=True and all others empty.
    - Returns combined list (may have overlaps — caller should deduplicate).
    """
    from actions.google_search_api import search_ddgs

    all_results: List[dict] = []

    # ── A: DDGS news (unconditional for news/digest) ─────────────────────────
    if search_type in ("news", "headline_digest") or len(all_results) < 3:
        ddgs_news = _ddgs_news_retrieve(query, max_results, language)
        all_results.extend(ddgs_news)

    # ── B: DDGS text (if still thin) ────────────────────────────────────────
    if len(all_results) < 3:
        ddgs_text = _ddgs_text_retrieve(query, max_results, language)
        all_results.extend(ddgs_text)

    # ── Optional paid providers ──────────────────────────────────────────────
    if len(all_results) < 3:
        from actions.google_search_api import google_search_api
        paid = google_search_api({"query": query, "max_results": max_results,
                                  "search_type": search_type, "language": language})
        if paid.get("ok") and paid.get("results"):
            print(f"[Retrieve:Paid] {len(paid['results'])} results")
            all_results.extend(paid["results"])

    # ── C: Playwright browser fallback ───────────────────────────────────────
    if len(all_results) < 2 and use_playwright_fallback:
        playwright_results = _playwright_search_retrieve(query, max_results, language)
        all_results.extend(playwright_results)

    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Multi-query retrieval with rewriting
# ─────────────────────────────────────────────────────────────────────────────

def _retrieve_with_rewrite(
    query:       str,
    language:    str           = "en",
    search_type: str           = "web",
    max_results: int           = 8,
) -> List[dict]:
    """
    Run retrieval using the original query + rewritten variants.

    Routing:
    - headline_digest intent → delegates to _retrieve_digest_queries()
    - topic_* intents        → iterates generated topic_queries variants
    - everything else        → original + keyword variant + English expansion
    """
    from core.query_rewriter import rewrite, classify_intent

    intent = classify_intent(query)

    # ── Digest: delegate to specialised date-aware function ───────────────────
    if intent == "headline_digest":
        return _retrieve_digest_queries(query, language, max_results)

    variants = rewrite(query, language=language, search_type=search_type)
    all_results: List[dict] = []

    # ── Topic-specific intents: iterate generated topic query variants ─────────
    if intent in ("topic_news", "topic_capabilities", "topic_news_and_capabilities"):
        topic_queries = variants.get("topic_queries", [])
        retrieval_type = "news" if "news" in intent else "web"

        print(f"[WebSearch] Topic intent={intent!r} entity={variants.get('topic')!r}")
        print(f"[WebSearch] Topic queries ({len(topic_queries)}): {topic_queries}")

        for tq in topic_queries[:5]:  # up to 5 topic variants
            r = _retrieve(tq, max_results, retrieval_type, language)
            all_results.extend(r)
            print(f"[WebSearch] Topic query {tq!r}: {len(r)} results")
            if len(all_results) >= max_results * 2:
                break

        # Also try the original user query if results still thin
        if len(all_results) < 3:
            r = _retrieve(query, max_results, retrieval_type, language)
            all_results.extend(r)
            print(f"[WebSearch] Topic fallback (original): {len(r)} results")

        return all_results

    # ── Standard path: original + keyword variant + English expansion ─────────
    results = _retrieve(query, max_results, search_type, language)
    all_results.extend(results)
    print(f"[WebSearch] Primary query: {len(results)} raw results")

    # Keyword variant (if primary thin)
    kw = variants.get("keywords", "")
    if len(all_results) < 3 and kw and kw.lower() != query.lower():
        kw_results = _retrieve(kw, max_results, search_type, language)
        all_results.extend(kw_results)
        print(f"[WebSearch] Keyword variant: {len(kw_results)} raw results")

    # English expansion
    if variants.get("en_translation") and len(all_results) < 5:
        en_results = _retrieve(variants["en_translation"], max_results, search_type, "en")
        all_results.extend(en_results)
        print(f"[WebSearch] English expansion: {len(en_results)} raw results")

    return all_results


def _retrieve_digest_queries(
    query:       str,
    language:    str = "en",
    max_results: int = 8,
) -> List[dict]:
    """
    Retrieve news for a headline_digest query.
    Runs multiple date-specific queries through the full provider chain.

    Full fallback sequence per query: DDGS news → DDGS text → (Playwright)
    """
    from core.query_rewriter import make_news_digest_queries

    digest_queries = make_news_digest_queries(query, language)
    print(f"[DigestSearch] Generated {len(digest_queries)} digest queries:")
    for q in digest_queries:
        print(f"  · {q!r}")

    all_results: List[dict] = []
    tried_playwright = False

    for dq in digest_queries:
        # DDGS news for each digest query
        ddgs_news = _ddgs_news_retrieve(dq, max_results, language)
        all_results.extend(ddgs_news)

        # DDGS text if still thin
        if len(all_results) < 3:
            ddgs_text = _ddgs_text_retrieve(dq, max_results, language)
            all_results.extend(ddgs_text)

        # If we have enough results, stop iterating through more queries
        if len(all_results) >= max_results:
            break

    # Also try English queries separately for international coverage
    from core.date_normalizer import extract_date_info, format_date_en
    date_info = extract_date_info(query, language)
    if date_info.found and language != "en":
        en_date = date_info.en()
        en_queries = [f"top news {en_date}", f"world news {en_date}"]
        for eq in en_queries:
            en_res = _ddgs_news_retrieve(eq, max_results, "en")
            all_results.extend(en_res)
            if en_res:
                break  # one success is enough

    # Playwright as last resort for digest
    if len(all_results) < 2 and not tried_playwright and digest_queries:
        tried_playwright = True
        pw_results = _playwright_search_retrieve(digest_queries[0], max_results, language)
        all_results.extend(pw_results)

    print(f"[DigestSearch] Total raw results: {len(all_results)}")
    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Gemini synthesis
# ─────────────────────────────────────────────────────────────────────────────

def _synthesize_news_digest(
    query:             str,
    results:           List[dict],
    fetched_content:   str = "",
    response_language: str = "ru",
    date_str:          str = "",
) -> str:
    """
    Synthesize a news digest response using Gemini.
    Produces a structured summary of key events, not a generic answer.

    Дверь к модели одна на весь проект: core/aux_model.aux_call.
    Свой клиент здесь был слеп: не знал про остывание после 429
    и не повторял временный 503.
    """
    try:
        api_key = _get_api_key()
    except Exception:
        return _format_digest_plain(query, results, date_str)

    context_parts: List[str] = []
    for i, r in enumerate(results[:8], 1):
        title   = r.get("title",   "") or ""
        snippet = r.get("snippet", "") or ""
        url     = r.get("url",     "") or ""
        date    = r.get("published_date", "") or ""
        context_parts.append(
            f"{i}. [{date[:10] if date else '?'}] {title}\n"
            f"   {snippet[:300]}\n"
            f"   {url}"
        )

    if fetched_content:
        context_parts.append(f"\n--- Content from top result ---\n{fetched_content[:2000]}")

    context = "\n\n".join(context_parts)

    if date_str:
        date_note = f"The user is asking about news from: {date_str}."
    else:
        date_note = "The user is asking about recent news."

    lang_note = f"Respond in {_language_name(response_language)}."

    prompt = f"""You are JARVIS, an AI assistant. {date_note}
{lang_note}

Your task: create a concise news digest / summary from the search results below.

FORMAT:
- Start with a brief intro sentence naming the date/period.
- List 4-7 key events as bullet points (•), each 1-2 sentences.
- Each bullet should name the topic + what happened.
- End with a list of 3-5 key sources (domain only).
- Address the user as 'sir'.
- Do NOT include events that are not backed by the sources below.
- If the sources do not cover the requested date, say so honestly.
- Never guess whether something exists, was announced, released or cancelled: missing results are not proof.

SEARCH RESULTS:
{context}

Create the news digest now."""

    try:
        from core.aux_model import aux_call

        ok, answer = aux_call(
            prompt,
            api_key,
            model=_get_model("aux_light"),
            caller="WebSearch-Digest",
        )
        if not ok or not answer.strip():
            print(f"[WebSearch] 🔁 сводка собрана без модели ({answer[:48]})")
            return _format_digest_plain(query, results, date_str)
        return answer.strip()
    except Exception as e:
        print(f"[WebSearch] Gemini digest error: {e}")
        return _format_digest_plain(query, results, date_str)


def _synthesize_with_gemini(
    query:             str,
    results:           List[dict],
    fetched_content:   str = "",
    response_language: str = "en",
) -> str:
    """
    Standard Gemini synthesis for non-digest queries.

    Дверь к модели одна: core/aux_model.aux_call.
    """
    try:
        api_key = _get_api_key()
    except Exception:
        return _format_results_plain(query, results)

    context_parts: List[str] = []
    for i, r in enumerate(results[:5], 1):
        title   = r.get("title",   "") or ""
        snippet = r.get("snippet", "") or ""
        url     = r.get("url",     "") or ""
        context_parts.append(f"{i}. {title}\n   {snippet}\n   {url}")

    if fetched_content:
        context_parts.append(f"\n--- Page content ---\n{fetched_content[:2000]}")

    context = "\n\n".join(context_parts)

    lang_note = (
        f"Answer in {_language_name(response_language)}."
        if response_language and response_language != "en"
        else "Answer in the language of the user's question."
    )

    prompt = f"""You are JARVIS, an AI assistant. Synthesize a clear, concise answer from these search results.

{lang_note}
Rules:
- Base your answer on the provided search results only.
- Be concise. 2-4 sentences for quick search.
- Mention the most credible source if relevant.
- Address the user as 'sir'.
- Do NOT add information not in the results.
- If the results do not cover the exact subject of the question, say that plainly and name what they do cover instead.
- Never guess whether something exists, was announced, released or cancelled: missing results are not proof.

User question: {query}

Search results:
{context}

Provide a clear, direct answer."""

    try:
        from core.aux_model import aux_call

        ok, answer = aux_call(
            prompt,
            api_key,
            model=_get_model("aux_light"),
            caller="WebSearch-Synthesis",
        )
        if not ok or not answer.strip():
            print(f"[WebSearch] 🔁 ответ собран без модели ({answer[:48]})")
            return _format_results_plain(query, results)
        return answer.strip()
    except Exception as e:
        print(f"[WebSearch] Gemini synthesis error: {e}")
        return _format_results_plain(query, results)


# ─────────────────────────────────────────────────────────────────────────────
# Plain-text fallback formatters (when Gemini unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def _format_results_plain(query: str, results: List[dict]) -> str:
    lines = [f"Search results for: {query}\n"]
    for r in results[:5]:
        lines.append(f"• {r.get('title', 'No title')}")
        if r.get("snippet"):
            lines.append(f"  {r['snippet'][:200]}")
        if r.get("url"):
            lines.append(f"  {r['url']}")
        lines.append("")
    return "\n".join(lines)


def _format_digest_plain(query: str, results: List[dict], date_str: str = "") -> str:
    header = f"News digest{' for ' + date_str if date_str else ''}:\n"
    lines  = [header]
    for r in results[:7]:
        title  = r.get("title",  "") or "—"
        domain = r.get("domain", "") or ""
        lines.append(f"• {title} ({domain})")
        if r.get("snippet"):
            lines.append(f"  {r['snippet'][:200]}")
    if not results:
        lines.append("No news results were found for this time period.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# News Digest Pipeline (headline_digest intent)
# ─────────────────────────────────────────────────────────────────────────────

def _news_digest_pipeline(
    query:             str,
    language:          str           = "ru",
    response_language: Optional[str] = None,
    max_results:       int           = 10,
    player                           = None,
) -> str:
    """
    Dedicated pipeline for headline_digest queries.

    Steps:
      1. Normalise date ("вчера" → DateInfo with absolute date)
      2. Build date-specific query variants
      3. DDGS news → DDGS text → Playwright (all steps)
      4. Deduplicate + rank
      5. Fetch top result content
      6. Gemini digest synthesis
      7. Only at step 7 we can say "not found"
    """
    from core.search_cache import get_search, set_search
    from core.date_normalizer import extract_date_info
    from actions.web_fetch import web_fetch

    resp_lang = response_language or language

    # ── Cache check ──────────────────────────────────────────────────────────
    cached = get_search(query, "headline_digest", language)
    if cached:
        print(f"[DigestSearch] Cache hit for: {query[:60]!r}")
        return cached

    # ── Date extraction ──────────────────────────────────────────────────────
    date_info = extract_date_info(query, language)
    date_str  = date_info.ru() if language == "ru" else date_info.en()
    if not date_info.found:
        from datetime import date as date_cls
        from core.date_normalizer import format_date_ru, format_date_en
        d = date_cls.today()
        date_str = format_date_ru(d) if language == "ru" else format_date_en(d)

    print(f"[DigestSearch] Date resolved: {date_str!r}")

    # ── Retrieve ─────────────────────────────────────────────────────────────
    raw_results = _retrieve_digest_queries(query, language, max_results)

    if not raw_results:
        msg = (
            f"К сожалению, мне не удалось найти надёжных новостей за {date_str}, сэр. "
            f"Это может быть связано с недоступностью поисковых серверов. "
            f"Попробуйте повторить запрос через несколько минут."
            if language == "ru"
            else
            f"I was unable to retrieve news results for {date_str}, sir. "
            f"All search providers were temporarily unavailable. "
            f"Please try again in a moment."
        )
        return msg

    # ── Deduplicate + rank ──────────────────────────────────────────────────
    results = deduplicate_and_rank(raw_results, "news", max_results)
    print(f"[DigestSearch] {len(results)} ranked results")

    # ── Register sources ────────────────────────────────────────────────────
    _register_sources(query, "headline_digest", results)

    # ── Fetch top result for richer context ─────────────────────────────────
    fetched_content = ""
    for r in results[:3]:
        top_url = r.get("url")
        if top_url:
            try:
                fetch_result = web_fetch({"url": top_url, "timeout": 6, "language": language})
                if fetch_result.get("ok") and fetch_result.get("results"):
                    page = fetch_result["results"][0]
                    if page.get("ok") and page.get("content"):
                        fetched_content = page["content"][:2000]
                        break
            except Exception as e:
                print(f"[DigestSearch] Fetch error (non-fatal): {e}")

    # ── Synthesize ──────────────────────────────────────────────────────────
    answer = _synthesize_news_digest(
        query, results, fetched_content, resp_lang, date_str
    )

    # ── Source footer ────────────────────────────────────────────────────────
    source_lines = ["\n\nИсточники:" if language == "ru" else "\n\nSources:"]
    for r in results[:5]:
        source_lines.append(
            f"  [{r.get('rank','?')}] {r.get('title','')} — {r.get('domain','')}\n"
            f"       {r.get('url','')}"
        )

    final = answer + "\n".join(source_lines)

    # ── Cache and return ─────────────────────────────────────────────────────
    set_search(query, final, "news", language)  # 5 min TTL
    return final


# ─────────────────────────────────────────────────────────────────────────────
# Quick search (standard web / news / docs queries)
# ─────────────────────────────────────────────────────────────────────────────

def _quick_search(
    query:             str,
    max_results:       int           = 8,
    search_type:       str           = "web",
    language:          Optional[str] = None,
    response_language: Optional[str] = None,
    player                           = None,
) -> str:
    from core.search_cache import get_search, set_search
    from core.query_rewriter import classify_intent
    from actions.web_fetch import web_fetch

    eff_lang  = language or "en"
    resp_lang = response_language or eff_lang
    intent    = classify_intent(query)

    # Route digest queries to the dedicated pipeline
    if intent == "headline_digest":
        return _news_digest_pipeline(
            query, language=eff_lang,
            response_language=resp_lang,
            max_results=max_results,
            player=player,
        )

    search_type = search_type or intent

    # ── Cache ────────────────────────────────────────────────────────────────
    cached = get_search(query, search_type, eff_lang)
    if cached:
        print(f"[WebSearch] Cache hit for: {query[:60]!r}")
        return cached

    # ── Retrieve ─────────────────────────────────────────────────────────────
    raw_results = _retrieve_with_rewrite(
        query, language=eff_lang, search_type=search_type,
        max_results=max_results,
    )
    print(f"[WebSearch] Total raw results: {len(raw_results)}")

    if not raw_results:
        # Step D was already tried inside _retrieve; this is a genuine failure
        return (
            f"Мне не удалось найти результаты по запросу: «{query}». "
            f"Поисковые сервисы временно недоступны, сэр. Попробуйте позже."
            if eff_lang == "ru"
            else
            f"I was unable to find results for your query, sir. "
            f"All search providers appear temporarily unavailable. "
            f"Please try rephrasing or try again later."
        )

    # ── Deduplicate + rank ───────────────────────────────────────────────────
    results = deduplicate_and_rank(raw_results, search_type, max_results)
    _register_sources(query, search_type, results)

    # ── Fetch top result ─────────────────────────────────────────────────────
    fetched_content = ""
    if results:
        top_url = results[0].get("url")
        if top_url:
            try:
                fetch_result = web_fetch({"url": top_url, "timeout": 6, "language": eff_lang})
                if fetch_result.get("ok") and fetch_result.get("results"):
                    page = fetch_result["results"][0]
                    if page.get("ok") and page.get("content"):
                        fetched_content = page["content"][:2000]
            except Exception as e:
                print(f"[WebSearch] Fetch error (non-fatal): {e}")

    # ── Synthesize ───────────────────────────────────────────────────────────
    answer = _synthesize_with_gemini(query, results, fetched_content, resp_lang)

    source_lines = ["\n\nSources:"]
    for r in results[:5]:
        source_lines.append(
            f"  [{r.get('rank','?')}] {r.get('title','')} — {r.get('domain','')}\n"
            f"       {r.get('url','')}"
        )

    final = answer + "\n".join(source_lines)
    set_search(query, final, search_type, eff_lang)
    return final


# ─────────────────────────────────────────────────────────────────────────────
# Compare / Verify / Research modes
# ─────────────────────────────────────────────────────────────────────────────

def _compare_items(
    items:    List[str],
    aspect:   str           = "general",
    language: Optional[str] = None,
    player                  = None,
) -> str:
    from actions.web_fetch import web_fetch

    if not items or len(items) < 2:
        return "Please provide at least 2 items to compare, sir."

    eff_lang = language or "en"
    all_data: Dict[str, Any] = {}

    for item in items:
        query = f"{item} {aspect}" if aspect != "general" else item
        raw   = _retrieve(query, 3, "compare", eff_lang, use_playwright_fallback=False)
        res   = deduplicate_and_rank(raw, "compare", 3)

        item_data: Dict[str, Any] = {"results": res, "fetched": None}
        if res:
            top_url = res[0].get("url")
            if top_url:
                try:
                    fr = web_fetch({"url": top_url, "timeout": 5, "language": eff_lang})
                    if fr.get("ok") and fr.get("results"):
                        page = fr["results"][0]
                        if page.get("ok"):
                            item_data["fetched"] = page.get("content","")[:1500]
                except Exception:
                    pass
        all_data[item] = item_data

    context_parts = []
    for item, data in all_data.items():
        part = f"=== {item.upper()} ===\n"
        for r in data["results"]:
            part += f"- {r.get('title','')}: {r.get('snippet','')[:150]}\n"
        if data["fetched"]:
            part += f"\nDetails:\n{data['fetched'][:800]}\n"
        context_parts.append(part)

    prompt = (
        f"Compare the following items based on {aspect}:\n\n"
        + "\n".join(context_parts)
        + "\n\nProvide a clear, structured comparison with specific facts. "
          f"Address the user as 'sir'. Language: {_language_name(eff_lang)}"
    )

    try:
        from core.aux_model import aux_call

        ok, answer = aux_call(
            prompt,
            _get_api_key(),
            model=_get_model("aux_light"),
            caller="WebSearch-Compare",
        )
        if not ok or not answer.strip():
            print(f"[WebSearch] 🔁 сравнение собрано без модели ({answer[:48]})")
            return f"Comparison ({aspect}):\n" + "\n".join(f"▸ {i}" for i in items)
        return answer
    except Exception as e:
        print(f"[WebSearch] Compare error: {e}")
        return f"Comparison ({aspect}):\n" + "\n".join(f"▸ {i}" for i in items)


def _verify_facts(query: str, language: Optional[str] = None, player=None) -> str:
    from actions.deep_research import deep_research
    return deep_research({"query": query, "mode": "verify", "language": language}, player=player)


def _deep_research_mode(query: str, language: Optional[str] = None, player=None) -> str:
    from actions.deep_research import deep_research
    return deep_research({"query": query, "mode": "research", "language": language}, player=player)


# ─────────────────────────────────────────────────────────────────────────────
# Mode detection
# ─────────────────────────────────────────────────────────────────────────────

_VERIFY_KEYWORDS = [
    "проверь", "правда ли", "это точно", "подтвер", "verify",
    "fact-check", "is it true", "are you sure", "confirm",
]


def _detect_mode(
    query:         str,
    explicit_mode: str,
    items:         List[str],
    strict:        bool,
) -> str:
    if explicit_mode and explicit_mode not in ("search", ""):
        return explicit_mode
    if strict:
        return "verify"
    if items and len(items) >= 2:
        return "compare"
    q = query.lower()
    if any(kw in q for kw in _VERIFY_KEYWORDS):
        return "verify"
    return "search"


def _language_name(lang: Optional[str]) -> str:
    """Имя языка для промпта — из общей таблицы core/search_locale.py.

    До 7 августа 2026 в трёх промптах поиска язык называли по-своему:
    в одном месте выбор был из двух языков ("Russian" или "English"),
    в двух других модели показывали код вида code \'tr\'. Турецкий вопрос
    получал английский ответ не из-за модели, а из-за этих строк.
    """
    try:
        from core.search_locale import get_label
    except ImportError:
        print("[WebSearch] ⚠ ТАБЛИЦА ЯЗЫКОВ НЕ ЗАГРУЗИЛАСЬ — прошу русский")
        return "Russian"
    return get_label(lang, fallback="ru")


def _resolve_language(query: str, explicit_language: Optional[str]) -> tuple[str, str]:
    """Язык запроса И имя того, кто его выбрал.

    Возвращает пару, а не одну строку: без второго значения в логе видно
    только lang=ru, и непонятно, чей это выбор — голосовой модели или наш.
    Сама развилка живёт в core/search_locale.py одной общей функцией:
    у глубокого исследования была своя копия того же кода, и копии уже
    начали расходиться.
    """
    try:
        from core.search_locale import resolve_language
    except ImportError:
        return "en", "ОПРЕДЕЛИТЕЛЬ НЕ ЗАГРУЗИЛСЯ — беру английский"
    return resolve_language(query, explicit_language)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def web_search(
    parameters:     dict = None,
    response:       Optional[str] = None,
    player                        = None,
    session_memory                = None,
) -> str:
    """
    Main search entry point called by main.py tool dispatcher.

    Parameters (all optional):
      query             — search query (required)
      mode              — "search" | "compare" | "research" | "verify"
      items             — list of items to compare (for compare mode)
      strict            — force verify mode
      language          — ISO-639-1 source language
      response_language — ISO-639-1 language for the answer
      max_results       — int, default 8
      search_type       — "web"|"news"|"docs" (auto-detected if absent)
    """
    from core.query_rewriter import classify_intent

    params    = parameters or {}
    query     = params.get("query", "").strip()
    items     = params.get("items", []) or []
    strict    = bool(params.get("strict", False))
    max_res   = int(params.get("max_results", 8))
    s_type    = params.get("search_type", "")

    explicit_lang = params.get("language") or params.get("lang")
    resp_lang     = params.get("response_language") or explicit_lang
    explicit_mode = (params.get("mode") or "").strip().lower()

    if not query:
        return "Please tell me what you'd like me to search for, sir."

    language, lang_source = _resolve_language(query, explicit_lang)
    intent   = classify_intent(query)

    # If intent not explicitly overridden by caller, use our detection
    if not s_type:
        s_type = intent

    mode = _detect_mode(query, explicit_mode, items, strict)

    print(f"[WebSearch] 🔤 язык {language} — {lang_source}")
    print(f"[WebSearch] mode={mode} lang={language} intent={intent} type={s_type} query={query[:60]!r}")

    if player:
        player.write_log(f"[Search] {query[:60]} [{mode}|{intent}]")

    # ── Route ────────────────────────────────────────────────────────────────
    if mode == "compare":
        if not items:
            items = [p.strip() for p in query.split(" vs ")]
            if len(items) < 2:
                items = [query]
        return _compare_items(items, aspect="general", language=language, player=player)

    if mode in ("verify", "fact_check"):
        return _verify_facts(query, language=language, player=player)

    if mode == "research":
        return _deep_research_mode(query, language=language, player=player)

    # headline_digest gets its own dedicated pipeline
    if intent == "headline_digest":
        return _news_digest_pipeline(
            query, language=language,
            response_language=resp_lang or language,
            max_results=max_res,
            player=player,
        )

    # Default: quick search
    return _quick_search(
        query,
        max_results=max_res,
        search_type=s_type,
        language=language,
        response_language=resp_lang or language,
        player=player,
    )
