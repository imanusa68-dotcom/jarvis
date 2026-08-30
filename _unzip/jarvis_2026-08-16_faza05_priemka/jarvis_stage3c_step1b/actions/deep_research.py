# actions/deep_research.py
# MARK XXXV — Deep Research Pipeline (REFACTORED v2)
#
# Multi-step research pipeline, Perplexity-style:
#   1. Classify + rewrite query into focused variants
#   2. Retrieve results via DDGS (free providers ONLY for retrieval)
#   3. Diversify by domain
#   4. Fetch top sources (requests → Playwright fallback)
#   5. Build evidence set
#   6. Synthesize with Gemini (ONE call — synthesis only, NOT retrieval)
#   7. Return structured answer with sources + confidence
#
# Key differences from v1:
#   - Gemini is called ONCE at the end for synthesis, not for each query
#   - No Gemini Grounded Search anywhere in the retrieval chain
#   - Uses query_rewriter for smart variant generation (no extra API calls)
#   - Uses provider_health to skip failing providers
#   - Uses search_cache to avoid redundant searches
#   - Playwright fallback for JS-heavy sources
#   - Smarter bilingual expansion (only when it actually helps)

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = _get_base_dir()


from config.loader import get_api_key as _get_api_key


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_SEARCH_RESULTS = 10
MAX_FETCH_URLS     = 5
MAX_DOMAINS        = 2   # max results per domain (diversification)
from config.loader import get_model as _get_model
SYNTHESIS_MODEL    = _get_model("aux_light")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Build search query variants
# ─────────────────────────────────────────────────────────────────────────────

def _build_query_variants(
    user_query:    str,
    mode:          str           = "research",
    language:      Optional[str] = None,
) -> List[dict]:
    """
    Build a focused set of search query variants.
    Uses query_rewriter (no API calls).

    Returns list of {query, language, purpose} dicts.
    Max 3 variants to avoid over-spending on retrieval.
    """
    from core.query_rewriter import rewrite, classify_search_type, should_add_english

    eff_lang    = language or "en"
    search_type = classify_search_type(user_query) if mode not in ("verify", "research") else mode

    variants_info = rewrite(user_query, language=eff_lang, search_type=search_type)
    queries: List[dict] = []

    # 1. Original query in user's language (always)
    queries.append({
        "query":    user_query,
        "language": eff_lang,
        "purpose":  "primary",
    })

    # 2. Keyword variant — tighter, more searchable form
    kw = variants_info.get("keywords", "")
    if kw and kw.lower() != user_query.lower():
        queries.append({
            "query":    kw,
            "language": eff_lang,
            "purpose":  "keywords",
        })

    # 3. English variant only when it adds real value
    if variants_info.get("en_translation") and should_add_english(user_query, eff_lang, search_type):
        queries.append({
            "query":    user_query,
            "language": "en",
            "purpose":  "english_expansion",
        })

    # 4. For verify mode: add a fact-check-oriented query
    if mode == "verify":
        fact_q = f'"{user_query}" site:snopes.com OR site:factcheck.org OR fact check'
        queries.append({
            "query":    fact_q,
            "language": "en",
            "purpose":  "fact_check",
        })

    return queries[:3]  # cap at 3 to avoid over-retrieval


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Retrieve & diversify
# ─────────────────────────────────────────────────────────────────────────────

def _retrieve_and_diversify(
    queries:     List[dict],
    max_results: int = MAX_SEARCH_RESULTS,
) -> List[dict]:
    """
    Run the free retrieval chain for each query variant.
    Deduplicates by URL and diversifies by domain.
    Gemini is NOT called here.
    """
    from actions.google_search_api import search_ddgs

    all_results: List[dict] = []
    seen_urls: set = set()

    for q_info in queries:
        query    = q_info.get("query", "")
        lang     = q_info.get("language", "en")
        purpose  = q_info.get("purpose", "")

        if not query:
            continue

        print(f"[DeepResearch] 🔍 {purpose}: {query[:60]!r} [{lang}]")

        results = search_ddgs(query, max_results, "web", lang)

        for r in results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)

    # Domain diversification: max MAX_DOMAINS per domain
    domain_counts: Dict[str, int] = {}
    diversified: List[dict] = []

    for r in all_results:
        domain = r.get("domain", "")
        count  = domain_counts.get(domain, 0)
        if count < MAX_DOMAINS:
            diversified.append(r)
            domain_counts[domain] = count + 1

    # Re-rank by original order + domain quality
    for i, r in enumerate(diversified):
        r["rank"] = i + 1

    return diversified[:max_results]


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Fetch top sources
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_top_sources(
    search_results: List[dict],
    max_fetch:      int           = MAX_FETCH_URLS,
    language:       Optional[str] = None,
) -> List[dict]:
    """
    Fetch content from top search results using web_fetch.
    web_fetch handles the requests → Playwright fallback automatically.
    """
    from actions.web_fetch import web_fetch

    urls = [r.get("url") for r in search_results[:max_fetch] if r.get("url")]
    if not urls:
        return []

    fetch_result = web_fetch({
        "urls":      urls,
        "timeout":   8,
        "max_chars": 6000,
        "language":  language,
    })

    if not fetch_result.get("ok"):
        return []

    url_to_search = {r.get("url"): r for r in search_results}
    merged: List[dict] = []

    for page in fetch_result.get("results", []):
        if page.get("ok") and page.get("content_length", 0) > 100:
            search_data = url_to_search.get(page.get("url"), {})
            merged.append({
                **page,
                "search_rank":    search_data.get("rank", 99),
                "search_snippet": search_data.get("snippet", ""),
            })

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Build evidence set
# ─────────────────────────────────────────────────────────────────────────────

def _build_evidence(
    fetched_pages:  List[dict],
    search_results: List[dict],
) -> Dict[str, Any]:
    """
    Merge fetched page content with search snippets into an evidence set.
    Sources with fetched content are prioritized; others use snippet only.
    """
    evidence: Dict[str, Any] = {
        "sources":          [],
        "content_excerpts": [],
        "domains":          set(),
    }

    # Pages we successfully fetched
    fetched_urls = set()
    for page in fetched_pages:
        url = page.get("url", "")
        fetched_urls.add(url)
        evidence["sources"].append({
            "title":          page.get("title", "Unknown"),
            "url":            url,
            "domain":         page.get("domain", ""),
            "excerpt":        page.get("excerpt", "")[:300],
            "content_preview": page.get("content", "")[:2000],
            "published_date": page.get("published_date"),
            "method":         page.get("method", "requests"),
        })
        evidence["domains"].add(page.get("domain", ""))
        if page.get("content"):
            evidence["content_excerpts"].append({
                "domain": page.get("domain", ""),
                "text":   page["content"][:2000],
            })

    # Add remaining search results (snippet only) to fill gaps
    for r in search_results:
        url = r.get("url", "")
        if url and url not in fetched_urls and len(evidence["sources"]) < 8:
            evidence["sources"].append({
                "title":           r.get("title", ""),
                "url":             url,
                "domain":          r.get("domain", ""),
                "excerpt":         r.get("snippet", "")[:300],
                "content_preview": r.get("snippet", ""),
                "published_date":  r.get("published_date"),
                "method":          "snippet_only",
            })
            evidence["domains"].add(r.get("domain", ""))

    evidence["domains"]      = list(evidence["domains"])
    evidence["source_count"] = len(evidence["sources"])

    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Synthesize with Gemini (ONE call only)
# ─────────────────────────────────────────────────────────────────────────────

def _synthesize(
    query:             str,
    evidence:          Dict[str, Any],
    mode:              str           = "research",
    strict:            bool          = False,
    response_language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Use Gemini to synthesize a structured answer from evidence.
    This is the ONLY Gemini call in the entire deep research pipeline.

    И идёт он через общую дверь core/aux_model.aux_call:
    остывание после 429 и повтор при 503 теперь общие со всем проектом.
    """
    # Build evidence text
    context_parts: List[str] = []
    for i, source in enumerate(evidence.get("sources", [])[:6], 1):
        preview = source.get("content_preview") or source.get("excerpt", "")
        context_parts.append(
            f"SOURCE {i}: {source.get('title', '')}\n"
            f"URL: {source.get('url', '')}\n"
            f"Domain: {source.get('domain', '')}\n"
            f"Date: {source.get('published_date') or 'unknown'}\n"
            f"Content:\n{preview[:1500]}\n---"
        )

    evidence_text = "\n\n".join(context_parts)

    lang_instr = ""
    if response_language and response_language.lower() != "en":
        try:
            from core.search_locale import get_label
            lang_named = get_label(response_language, fallback="ru")
        except ImportError:
            print("[DeepResearch] ⚠ ТАБЛИЦА ЯЗЫКОВ НЕ ЗАГРУЗИЛАСЬ — прошу русский")
            lang_named = "Russian"
        lang_instr = (
            f"\nIMPORTANT: Write your ENTIRE response in {lang_named}. "
            f"Do not switch to English.\n"
        )

    if strict or mode == "verify":
        prompt = f"""You are a fact-checking assistant. Analyze only the sources below.
{lang_instr}
RULES:
1. Base your answer ONLY on the provided sources.
2. If sources conflict, state the conflict explicitly.
3. If sources are insufficient, say so honestly.
4. Rate confidence: high / medium / low.
5. Never fabricate information.

QUESTION: {query}

SOURCES:
{evidence_text}

Respond in JSON:
{{
  "answer": "...",
  "facts": ["fact1", "fact2"],
  "confidence": "high|medium|low",
  "uncertainty": "any caveats, or null"
}}"""
    else:
        prompt = f"""You are a research assistant. Synthesize a comprehensive answer from these sources.
{lang_instr}
QUESTION: {query}

SOURCES:
{evidence_text}

Provide a clear, well-structured answer based on these sources.
Cite which sources support which claims where relevant.
Address the user as 'sir'.

Respond in JSON:
{{
  "answer": "...",
  "facts": ["key fact 1", "key fact 2", "key fact 3"],
  "confidence": "high|medium|low",
  "uncertainty": null
}}"""

    try:
        from core.aux_model import aux_call

        ok, answer = aux_call(
            prompt,
            _get_api_key(),
            model=SYNTHESIS_MODEL,
            caller="DeepResearch",
        )
        if not ok:
            print(f"[DeepResearch] 🔁 разбор источников не вышел ({answer[:48]})")
            snippets = [
                s.get("content_preview") or s.get("excerpt", "")
                for s in evidence.get("sources", [])[:3]
            ]
            return {
                "answer":      "Based on available sources:\n\n" + "\n\n".join(snippets[:2]),
                "facts":       [],
                "confidence":  "low",
                "uncertainty": f"model unavailable: {answer[:48]}",
            }

        # Try to parse JSON
        text = answer.strip()
        json_match = __import__("re").search(r"\{[\s\S]*\}", text)
        if json_match:
            data = json.loads(json_match.group(0))
            return {
                "answer":     data.get("answer", text),
                "facts":      data.get("facts", []),
                "confidence": data.get("confidence", "medium"),
                "uncertainty": data.get("uncertainty"),
            }

        return {"answer": text, "facts": [], "confidence": "medium", "uncertainty": None}

    except Exception as e:
        print(f"[DeepResearch] Gemini synthesis error: {e}")
        # Fallback: plain text from snippets
        snippets = [
            s.get("content_preview") or s.get("excerpt", "")
            for s in evidence.get("sources", [])[:3]
        ]
        fallback_answer = f"Based on available sources:\n\n" + "\n\n".join(snippets[:2])
        return {
            "answer":     fallback_answer,
            "facts":      [],
            "confidence": "low",
            "uncertainty": f"Synthesis failed: {e}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Format final response
# ─────────────────────────────────────────────────────────────────────────────

def _format_response(
    synthesis: Dict[str, Any],
    evidence:  Dict[str, Any],
    query:     str,
    mode:      str,
) -> str:
    """Build a readable response string from synthesis + evidence."""
    answer     = synthesis.get("answer", "")
    facts      = synthesis.get("facts", [])
    confidence = synthesis.get("confidence", "medium")
    uncertainty = synthesis.get("uncertainty")

    lines = [answer, ""]

    if facts:
        lines.append("Key findings:")
        for f in facts[:5]:
            lines.append(f"  • {f}")
        lines.append("")

    if uncertainty:
        lines.append(f"Note: {uncertainty}")
        lines.append("")

    # Sources
    sources = evidence.get("sources", [])
    if sources:
        lines.append(f"Sources ({confidence} confidence):")
        for i, s in enumerate(sources[:6], 1):
            domain = s.get("domain", "")
            title  = s.get("title", "")
            url    = s.get("url",   "")
            date   = s.get("published_date", "")
            date_str = f" [{date[:10]}]" if date else ""
            lines.append(f"  [{i}] {title} — {domain}{date_str}")
            if url:
                lines.append(f"       {url}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def deep_research(
    parameters:     dict = None,
    response:       Optional[str] = None,
    player                        = None,
    session_memory                = None,
) -> str:
    """
    Full deep research pipeline.
    Called by web_search.py (research/verify modes) and agent/executor.py.

    Parameters:
      query             — research question (required)
      mode              — "research" | "verify" (default: research)
      language          — ISO-639-1 source language (auto-detected if absent)
      response_language — ISO-639-1 language for the answer
      strict            — bool, force strict fact-check mode
    """
    from core.search_source_registry import set_last_sources

    params    = parameters or {}
    query     = params.get("query", "").strip()
    mode      = params.get("mode",  "research").lower()
    strict    = bool(params.get("strict", False))
    language  = params.get("language") or None
    resp_lang = params.get("response_language") or language

    if not query:
        return "Please provide a query for research, sir."

    # Язык и автор решения — одной общей функцией, той же, что у поиска.
    # До 7 августа 2026 здесь лежала своя копия кода из web_search.py,
    # со своим молчаливым "en" при поломке импорта. Копий больше нет.
    try:
        from core.search_locale import resolve_language
        language, lang_source = resolve_language(query, language)
    except ImportError:
        language = language or "en"
        lang_source = "ОПРЕДЕЛИТЕЛЬ НЕ ЗАГРУЗИЛСЯ — беру английский"

    if not resp_lang:
        resp_lang = language

    print(f"[DeepResearch] 🔤 язык {language} — {lang_source}")
    print(f"[DeepResearch] mode={mode} lang={language} query={query[:60]!r}")

    if player:
        player.write_log(f"[Research] {query[:60]} [{mode}]")

    # ── Step 1: Build query variants ──────────────────────────────────────
    queries = _build_query_variants(query, mode=mode, language=language)

    # ── Step 2: Retrieve & diversify ──────────────────────────────────────
    search_results = _retrieve_and_diversify(queries, max_results=MAX_SEARCH_RESULTS)

    if not search_results:
        # Last resort: single DDGS query
        from actions.google_search_api import search_ddgs
        search_results = search_ddgs(query, MAX_SEARCH_RESULTS, "web", language)

    if not search_results:
        return (
            f"I was unable to find any sources for your query, sir. "
            f"Please try rephrasing: {query}"
        )

    print(f"[DeepResearch] {len(search_results)} search results collected")

    # Register sources for open_search_source
    set_last_sources(query, mode, [
        {"index": i + 1, "title": r.get("title",""), "url": r.get("url",""), "domain": r.get("domain","")}
        for i, r in enumerate(search_results[:10])
    ])

    # ── Step 3: Fetch content ─────────────────────────────────────────────
    fetched_pages = _fetch_top_sources(search_results, MAX_FETCH_URLS, language)
    print(f"[DeepResearch] {len(fetched_pages)} pages fetched with content")

    # ── Step 4: Build evidence ────────────────────────────────────────────
    evidence = _build_evidence(fetched_pages, search_results)
    print(f"[DeepResearch] Evidence: {evidence['source_count']} sources")

    if evidence["source_count"] == 0:
        return (
            f"I found search results but could not extract content from any source, sir. "
            f"The top results were: "
            + ", ".join(r.get("domain","") for r in search_results[:3])
        )

    # ── Step 5: Synthesize (Gemini — ONE call) ────────────────────────────
    synthesis = _synthesize(
        query, evidence,
        mode=mode, strict=strict,
        response_language=resp_lang,
    )

    # ── Step 6: Format response ───────────────────────────────────────────
    return _format_response(synthesis, evidence, query, mode)
