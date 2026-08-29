# core/query_rewriter.py
# MARK XXXV — Query Rewriting & Intent Classification (REFACTORED v4)
#
# Classifies user intent and generates optimised search query variants.
# No Gemini, no API calls — all pure keyword heuristics + date normalisation.
#
# Intent types:
#   headline_digest            — "что нового было вчера", "главные новости"
#   topic_news                 — "Claude Code последние новости"
#   topic_capabilities         — "возможности Claude Code"
#   topic_news_and_capabilities — "Claude Code возможности последние новости"
#   news                       — generic news search (no strong topic entity)
#   docs                       — technical documentation search
#   compare                    — comparison query
#   verify                     — fact-check query
#   web                        — generic web search (default)
#
# CRITICAL RULE: headline_digest fires ONLY when there is NO strong topic entity.
# "Claude Code последние новости" → topic_news (NOT headline_digest).
# "последние новости" → headline_digest.
#
# Exported public API:
#   detect_topic_entity(query)        → Optional[str]  — named entity or None
#   classify_intent(query)            → intent string (includes topic_* and headline_digest)
#   classify_search_type(query)       → legacy compat (maps topic_*/digest → news/web)
#   should_add_english(q, lang, type) → bool
#   rewrite(query, language, intent)  → dict of variants (may include topic_queries)
#   make_keyword_query(query)         → shorter keyword string
#   make_news_digest_queries(query, language)           → list[str]
#   make_topic_news_queries(topic, query, language)     → list[str]
#   make_topic_capabilities_queries(topic, query, lang) → list[str]
#   make_topic_news_and_capabilities_queries(...)       → list[str]

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from core.date_normalizer import (
    extract_date_info,
    remove_date_words,
    format_date_ru,
    format_date_en,
    DateInfo,
)


# ─────────────────────────────────────────────────────────────────────────────
# Keyword banks
# ─────────────────────────────────────────────────────────────────────────────

_NEWS_KW = {
    # Russian
    "новости", "новость", "сегодня", "вчера", "позавчера",
    "только что", "обновление", "обновления", "последние", "свежие", "актуальн",
    "произошло", "случилось", "происходит", "событи", "нов",
    # English
    "news", "today", "yesterday", "latest", "recent", "breaking", "update",
    "just", "happening", "current", "now", "live", "announced", "released",
}

_CAPABILITIES_KW = {
    # Russian
    "возможности", "возможность", "функции", "функция", "функционал",
    "умеет", "что умеет", "умеет делать", "как работает", "что делает",
    # English
    "features", "feature", "capabilities", "capability", "can do",
    "abilities", "ability", "what can", "how does", "overview",
}

_DIGEST_PHRASES_RU = [
    r"что\s+нового",
    r"что\s+произошло",
    r"что\s+случилось",
    r"какие\s+(?:были|есть|главные)",
    r"главные\s+новости",
    r"главные\s+события",
    r"расскажи\s+новости",
    r"дай\s+кратко",
    r"краткие?\s+новости",
    r"сводка\s+новостей",
    r"обзор\s+новостей",
    r"дайджест",
    r"что\s+(?:нового|новое)\s+в\s+мире",
    r"что\s+за\s+день",
    r"новости\s+за",
    r"события\s+за",
]

_DIGEST_PHRASES_EN = [
    r"what(?:'s|\s+is|\s+are|s)\s+(?:new|happening|going\s+on)",
    r"what\s+happened",
    r"top\s+(?:stories|news|headlines|events)",
    r"latest\s+(?:news|headlines|updates)",
    r"news\s+(?:today|yesterday|digest|summary|recap|roundup)",
    r"morning\s+briefing",
    r"news\s+brief",
    r"daily\s+(?:news|digest|summary|recap)",
    r"headlines?\s+(?:for|from|of)",
    r"what\s+happened\s+(?:today|yesterday|last)",
    r"catch\s+me\s+up",
]

_DOCS_KW = {
    "api", "sdk", "docs", "documentation", "документация", "official",
    "reference", "guide", "tutorial", "quickstart", "install", "setup",
    "github", "npm", "pip", "pypi", "maven", "nuget", "crate",
    "library", "package", "module", "framework", "changelog", "readme",
    "openai", "anthropic", "gemini", "langchain", "react", "vue",
    "django", "fastapi", "flask", "express", "kubernetes", "docker",
}

_COMPARE_KW = {
    "vs", "versus", "compare", "comparison", "difference", "differences",
    "better", "worse", "сравни", "сравнение", "разница", "отличие",
    "что лучше", "which is better", "alternative", "alternatives",
}

_VERIFY_KW = {
    "verify", "fact", "fact-check", "true", "false", "really", "actually",
    "проверь", "правда ли", "это правда", "действительно", "точно ли",
    "is it true", "confirm", "подтвер", "убедись",
}

_LOCAL_TOPICS = {
    "погода", "weather", "ресторан", "кафе", "маршрут", "адрес",
    "расписание", "автобус", "метро", "троллейбус", "аптека",
}

_TECH_EN_TOPICS = {
    "api", "sdk", "library", "framework", "github", "npm", "pip",
    "python", "javascript", "typescript", "rust", "golang", "docker",
    "kubernetes", "machine learning", "deep learning", "neural",
    "openai", "anthropic", "langchain", "transformer",
}

# Date-relative words that trigger digest detection even without full phrase
_DATE_RELATIVE_WORDS = {
    "вчера", "сегодня", "позавчера", "yesterday", "today",
    "последние", "latest", "recent", "breaking",
}

# Noise words that are NOT topic entities (used in detect_topic_entity)
_NOISE_EN: frozenset = frozenset({
    "news", "latest", "recent", "update", "updates", "new", "and", "or", "the",
    "is", "are", "was", "were", "has", "have", "in", "at", "on", "for", "of",
    "to", "by", "a", "an", "vs", "what", "how", "why", "when", "where", "who",
    "today", "yesterday", "breaking", "live", "now", "just", "happened",
    "happening", "features", "feature", "capabilities", "capability", "docs",
    "documentation", "official", "release", "notes", "changelog", "version",
    "about", "can", "do", "does", "me", "my", "i", "its", "it", "with", "get",
    "use", "using", "used",
})

# Generic Cyrillic words that are NOT topic entities
_GENERIC_WORDS_RU: frozenset = frozenset({
    "мире", "мир", "свете", "свет", "всём", "всем", "все",
    "день", "дне", "сегодня", "вчера", "позавчера", "неделе", "неделя",
    "технологиях", "технологии", "новостях", "новости", "новость",
    "интернете", "интернет", "сети", "всего", "мира", "вообще",
    "нового", "новое", "нове",
})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _contains_any(text: str, keywords: set) -> bool:
    t = text.lower()
    return any(kw in t for kw in keywords)


def _matches_any_phrase(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    for pat in patterns:
        if re.search(pat, t, re.IGNORECASE | re.UNICODE):
            return True
    return False


# Context preposition pattern for entity extraction (Russian)
_CTX_ENTITY_PAT = re.compile(
    r'(?:про|о|об|для|в|возможности|новости|новость|обновления?|функции|'
    r'что\s+умеет|умеет)\s+'
    r'([^\s,;?!.]{2,}(?:\s+[^\s,;?!.]{2,}){0,2})',
    re.IGNORECASE | re.UNICODE,
)

_CTX_TRAILING_NOISE = frozenset({
    "сегодня", "вчера", "позавчера", "последние", "свежие",
    "today", "yesterday", "latest", "recent",
})


# A model number belongs to the name in front of it: "RTX 5060", "GTA 6".
_MODEL_TAIL_RE = re.compile(r"^[0-9][A-Za-z0-9+.\-]*$")

# Prepositions that glue themselves to a Cyrillic topic: "про Сбер" -> "Сбер".
_CTX_LEADING_PREPOSITIONS: frozenset = frozenset({
    "про", "о", "об", "обо", "для", "в", "во", "на", "по", "от",
    "за", "из", "с", "со", "у", "к", "ко", "при", "над", "под",
    "без", "через", "между", "около", "после", "до",
})

# Words that name a time span, not a subject: "новости за неделю" is a request
# for the daily digest, not seven searches for the word "неделю".
_CTX_TIME_SPAN_WORDS: frozenset = frozenset({
    "секунда", "минута", "минуты", "час", "часа", "часов",
    "день", "дня", "дней", "сутки", "суток",
    "неделю", "неделя", "недели", "неделе",
    "месяц", "месяца", "месяце", "месяцев",
    "год", "года", "году", "лет",
    "утро", "утром", "вечер", "вечером", "ночь", "ночью",
    "выходные", "будни", "завтра",
    "прошлую", "прошлый", "прошлой", "прошедшую",
    "минувшую", "минувшие", "текущую", "последнюю",
    "последний", "последнее", "всю", "эту", "этот", "этой",
})


def detect_topic_entity(query: str) -> Optional[str]:
    """
    Detect a strong named entity / topic using rule-based heuristics.
    Returns a clean entity string if found, else None.

    Priority:
    1. Latin word runs  — strongest signal; handles "Claude Code", "OpenAI API"
    2. Context phrases  — "про X", "в X", "возможности X" (Cyrillic topics)
    3. Mid-sentence capitalised Cyrillic word  — "Яндекс", "Сбер"

    Filters out generic noise words on both ends.
    """
    # ── 1. Latin word runs ────────────────────────────────────────────────────
    # Find all contiguous Latin-word runs (handles product names with spaces)
    # A run may continue into a token that starts with a digit, otherwise
    # "RTX 5060" reaches the search engine as bare "RTX" and the answer is
    # about the whole product line instead of the model that was asked about.
    latin_runs = re.findall(
        r'[A-Za-z][A-Za-z0-9+.\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9+.\-]*)*',
        query,
    )
    for run in sorted(latin_runs, key=len, reverse=True):
        meaningful: list[str] = []
        previous_kept = False
        for part in run.split():
            if _MODEL_TAIL_RE.match(part):
                # A bare number is a model number only right after a kept name:
                # "GTA 6" yes, "news 5060" no.
                if previous_kept:
                    meaningful.append(part)
                else:
                    previous_kept = False
                continue
            if part.lower() in _NOISE_EN or len(part) < 2:
                previous_kept = False
                continue
            meaningful.append(part)
            previous_kept = True
        if meaningful:
            return " ".join(meaningful)

    # ── 2. Context-phrase extraction ──────────────────────────────────────────
    m = _CTX_ENTITY_PAT.search(query)
    if m:
        candidate = m.group(1).strip()
        # Drop trailing date/noise words
        words = candidate.split()
        clean: list[str] = []
        for w in words:
            if w.lower() in _CTX_TRAILING_NOISE:
                break
            clean.append(w)
        # Drop leading prepositions: "новости про Сбер" must give "Сбер",
        # not "про Сбер" — the preposition would poison every search query.
        while clean and clean[0].lower() in _CTX_LEADING_PREPOSITIONS:
            clean.pop(0)
        # A time span is not a subject: "новости за неделю" must fall through to
        # the daily digest instead of searching for the word "неделю".
        clean = [w for w in clean
                 if w.lower().strip(".,;:!?") not in _CTX_TIME_SPAN_WORDS]
        if clean:
            first = clean[0].lower()
            candidate_text = " ".join(clean)
            # A topic without a single letter is not a topic: "новости 5060"
            # must not turn into seven searches for the bare number "5060".
            has_letter = any(ch.isalpha() for ch in candidate_text)
            if has_letter and first not in _GENERIC_WORDS_RU and len(clean[0]) >= 2:
                return candidate_text

    # ── 3. Mid-sentence capitalised Cyrillic word ─────────────────────────────
    words = query.split()
    for i, word in enumerate(words):
        if i == 0:
            continue  # first word is capitalised by grammar — skip
        clean_w = re.sub(r'[^\w]', '', word, flags=re.UNICODE)
        if (len(clean_w) >= 3
                and re.match(r'^[А-ЯЁ][а-яё]+$', clean_w, re.UNICODE)
                and clean_w.lower() not in _GENERIC_WORDS_RU):
            return clean_w

    return None


def _is_headline_digest(query: str) -> bool:
    """
    Detect if the query is asking for a GENERAL news digest / headline summary.

    Returns False immediately if a strong topic entity is detected —
    topic-specific queries ("Claude Code новости") must never be treated
    as a general digest.

    Positive signals (only when no topic entity):
     - matches a digest phrase (RU or EN)
     - "что нового" without a named topic
     - relative date word AND a news context word
    """
    # ── Topic guard: topic queries are NEVER general digests ─────────────────
    if detect_topic_entity(query) is not None:
        return False

    q = query.lower()

    # Direct digest phrase match
    if _matches_any_phrase(q, _DIGEST_PHRASES_RU):
        return True
    if _matches_any_phrase(q, _DIGEST_PHRASES_EN):
        return True

    # "что нового" pattern — only valid when no topic entity (guard above)
    if re.search(r"что\s+нов\w+", q, re.UNICODE):
        return True

    # Relative date word + news context
    has_date_word = _contains_any(q, _DATE_RELATIVE_WORDS)
    has_news_word = any(w in q for w in ("новост", "событи", "произошл", "случил", "мире"))
    if has_date_word and has_news_word:
        return True

    return False


_STRIP_PREFIXES = [
    "как ", "how to ", "how do i ", "how can i ",
    "что такое ", "what is ", "что означает ", "what does ",
    "объясни ", "explain ", "почему ", "why ", "зачем ",
    "когда ", "when ", "где ", "where ", "кто ", "who ",
    "расскажи о ", "tell me about ", "расскажи про ",
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_intent(query: str) -> str:
    """
    Classify user query into one of:
      topic_news_and_capabilities — "Claude Code возможности последние новости"
      topic_news                  — "последние новости Claude Code"
      topic_capabilities          — "возможности Claude Code"
      headline_digest             — "что нового было вчера" (no topic entity)
      news                        — generic news search (no topic entity)
      docs                        — technical documentation search
      compare                     — comparison query
      verify                      — fact-check query
      web                         — generic web search (default)

    RULE: topic_* intents fire BEFORE headline_digest.
    headline_digest is only returned when NO strong topic entity is found.
    """
    if _contains_any(query, _VERIFY_KW):
        return "verify"
    if _contains_any(query, _COMPARE_KW):
        return "compare"

    # ── Topic entity check: must happen before digest check ──────────────────
    topic = detect_topic_entity(query)
    if topic:
        has_news = _contains_any(query, _NEWS_KW)
        has_caps = _contains_any(query, _CAPABILITIES_KW)

        if has_news and has_caps:
            return "topic_news_and_capabilities"
        if has_caps:
            return "topic_capabilities"
        if has_news:
            return "topic_news"
        # Topic present but no explicit signal → check docs, else broad topic search
        if _contains_any(query, _DOCS_KW):
            return "docs"
        # Default for any named-entity query: broad topic news search
        return "topic_news"

    # ── No topic entity: check for general digest ─────────────────────────────
    if _is_headline_digest(query):
        return "headline_digest"

    if _contains_any(query, _NEWS_KW):
        return "news"

    if _contains_any(query, _DOCS_KW):
        return "docs"

    return "web"


def classify_search_type(query: str) -> str:
    """
    Legacy-compatible version of classify_intent().
    Maps topic_* and headline_digest → simpler types for older callers.
    """
    intent = classify_intent(query)
    if intent in ("headline_digest", "topic_news", "topic_news_and_capabilities"):
        return "news"
    if intent == "topic_capabilities":
        return "web"
    return intent


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Bilingual expansion
# ─────────────────────────────────────────────────────────────────────────────

def should_add_english(
    query:       str,
    language:    str,
    search_type: str,
) -> bool:
    """
    Decide whether to run a parallel English-language search.

    Rules:
    - Already English → no.
    - Local/geo topic → no.
    - Tech/API/docs → yes (far better English coverage).
    - compare/verify → yes (broader coverage).
    - News digest (headline_digest) → yes (for international coverage).
    - Simple Russian conversational query → no.
    """
    if not language or language.lower() == "en":
        return False
    if _contains_any(query, _LOCAL_TOPICS):
        return False
    if _contains_any(query, _TECH_EN_TOPICS):
        return True
    if search_type in ("docs", "compare", "verify"):
        return True
    # News / digest / topic searches benefit from English coverage
    if search_type in (
        "news", "headline_digest",
        "topic_news", "topic_capabilities", "topic_news_and_capabilities",
    ):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Query manipulation
# ─────────────────────────────────────────────────────────────────────────────

def make_keyword_query(query: str) -> str:
    """
    Strip question/explanation prefixes and trailing punctuation.
    Also removes relative date noise for news digest queries.

    "Как установить Python на Windows" → "установить Python Windows"
    "что нового было вчера в новостях" → "новости"
    """
    q = query.strip()
    for prefix in _STRIP_PREFIXES:
        if q.lower().startswith(prefix.lower()):
            q = q[len(prefix):].strip()
            break
    return q.rstrip("?").strip()


def make_docs_query(query: str) -> str:
    """Make a docs-oriented query."""
    kw = make_keyword_query(query)
    if not any(k in kw.lower() for k in ["docs", "documentation", "api", "reference"]):
        kw = f"{kw} documentation official"
    return kw


def make_news_query(query: str, language: str) -> str:
    """Make a simple news-oriented query (for non-digest news searches)."""
    kw = make_keyword_query(query)
    if language == "en":
        return f"{kw} latest news"
    return kw


# ─────────────────────────────────────────────────────────────────────────────
# Topic-specific Query Builders
# ─────────────────────────────────────────────────────────────────────────────

def _dedup_queries(queries: list[str]) -> list[str]:
    """Deduplicate a list of queries while preserving order."""
    seen: set = set()
    result: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            result.append(q)
    return result


def make_topic_news_queries(topic: str, query: str, language: str = "ru") -> list[str]:
    """
    Build topic-specific news search queries (4-7 variants).

    "Claude Code последние новости" →
      ["Claude Code последние новости", "новости Claude Code",
       "что нового в Claude Code", "обновления Claude Code",
       "Claude Code latest news", "Claude Code updates", "Claude Code release notes"]
    """
    queries: list[str] = []

    if language in ("ru", "uk"):
        queries.extend([
            f"{topic} последние новости",
            f"новости {topic}",
            f"что нового в {topic}",
            f"обновления {topic}",
        ])

    # English queries always useful — tech topics have extensive English coverage
    queries.extend([
        f"{topic} latest news",
        f"{topic} updates",
        f"{topic} release notes",
    ])

    return _dedup_queries(queries)


def make_topic_capabilities_queries(topic: str, query: str, language: str = "ru") -> list[str]:
    """
    Build topic-specific capability / feature search queries (4-7 variants).

    "возможности Claude Code" →
      ["возможности Claude Code", "что умеет Claude Code", "Claude Code функции",
       "Claude Code capabilities", "Claude Code features", "Claude Code documentation"]
    """
    queries: list[str] = []

    if language in ("ru", "uk"):
        queries.extend([
            f"возможности {topic}",
            f"что умеет {topic}",
            f"{topic} функции",
            f"{topic} документация",
        ])

    queries.extend([
        f"{topic} capabilities",
        f"{topic} features",
        f"{topic} documentation",
        f"{topic} overview",
    ])

    return _dedup_queries(queries)


def make_topic_news_and_capabilities_queries(
    topic: str, query: str, language: str = "ru"
) -> list[str]:
    """
    Build mixed topic-specific search queries (news + capabilities, 5-9 variants).

    "Claude Code возможности последние новости" →
      ["Claude Code последние новости", "что нового в Claude Code",
       "возможности и обновления Claude Code", "Claude Code новые функции",
       "Claude Code latest news", "Claude Code new features", ...]
    """
    queries: list[str] = []

    if language in ("ru", "uk"):
        queries.extend([
            f"{topic} последние новости",
            f"что нового в {topic}",
            f"возможности и обновления {topic}",
            f"{topic} новые функции",
        ])

    queries.extend([
        f"{topic} latest news",
        f"{topic} new features",
        f"{topic} feature updates",
        f"{topic} capabilities",
        f"{topic} updates",
    ])

    return _dedup_queries(queries)


# ─────────────────────────────────────────────────────────────────────────────
# News Digest Query Generator (the core of the fix)
# ─────────────────────────────────────────────────────────────────────────────

def make_news_digest_queries(query: str, language: str = "ru") -> list[str]:
    """
    Build a list of concrete, date-specific search queries for a news digest request.

    For "что нового было вчера в новостях" (ru, date=2026-04-10) returns:
      - "главные новости 10 апреля 2026"
      - "главные события 10 апреля 2026"
      - "новости за 10 апреля 2026"
      - "что произошло в мире 10 апреля 2026"
      - "top news April 10 2026"
      - "world news April 10 2026"
      - "latest headlines April 10 2026"

    If no date expression found, uses "today/сегодня" as default.
    Returns between 3 and 8 query variants.
    """
    date_info = extract_date_info(query, language)

    # Default to today if no date found
    if not date_info.found:
        from datetime import date as date_cls
        date_info = DateInfo(
            date_found=date_cls.today(),
            relative="сегодня",
            is_range=False,
            range_days=1,
        )

    d_ru = date_info.ru()    # "10 апреля 2026"
    d_en = date_info.en()    # "April 10 2026"

    queries: list[str] = []

    # ── Russian queries ────────────────────────────────────────────────────
    if language in ("ru", "uk"):
        queries.extend([
            f"главные новости {d_ru}",
            f"новости за {d_ru}",
            f"главные события {d_ru}",
            f"что произошло в мире {d_ru}",
        ])
        # If range (week), also add without specific date
        if date_info.is_range and date_info.range_days >= 7:
            queries.append("главные новости за прошлую неделю")

    # ── English queries (always added for international coverage) ─────────
    queries.extend([
        f"top news {d_en}",
        f"world news {d_en}",
        f"latest headlines {d_en}",
    ])

    # ── Non-Russian original language (e.g., user writes English) ────────
    if language == "en":
        queries = [
            f"top news {d_en}",
            f"world news {d_en}",
            f"latest headlines {d_en}",
            f"breaking news {d_en}",
            f"news today {d_en}",
        ]

    # Deduplicate while preserving order
    seen: set = set()
    result: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            result.append(q)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Main rewrite function
# ─────────────────────────────────────────────────────────────────────────────

def rewrite(
    query:   str,
    language: str            = "en",
    search_type: Optional[str] = None,
) -> dict:
    """
    Generate query variants for any query.

    Returns a dict with keys:
      original         — unchanged query
      keywords         — stripped shorter query
      intent           — detected intent (includes topic_* and headline_digest)
      search_type      — legacy-compatible type
      topic            — detected entity (topic_* intents only)
      topic_queries    — list of topic-specific search variants (topic_* intents only)
      docs             — docs-oriented variant (docs intent only)
      news             — news-oriented variant (news/headline_digest only)
      digest_queries   — list of concrete date queries (headline_digest only)
      date_info        — DateInfo object if date was found, else None
      en_translation   — same query to be searched with language="en"
                         (only when should_add_english() is True)
    """
    intent = classify_intent(query) if not search_type else search_type

    # Legacy search_type mapping
    if intent in ("headline_digest", "topic_news", "topic_news_and_capabilities"):
        legacy_type = "news"
    elif intent == "topic_capabilities":
        legacy_type = "web"
    else:
        legacy_type = intent

    date_info = extract_date_info(query, language)
    topic = detect_topic_entity(query)

    variants: dict = {
        "original":    query,
        "keywords":    make_keyword_query(query),
        "intent":      intent,
        "search_type": legacy_type,
        "date_info":   date_info if date_info.found else None,
    }

    # ── Topic-specific query variants ─────────────────────────────────────────
    if topic and intent == "topic_news":
        variants["topic"]         = topic
        variants["topic_queries"] = make_topic_news_queries(topic, query, language)

    elif topic and intent == "topic_capabilities":
        variants["topic"]         = topic
        variants["topic_queries"] = make_topic_capabilities_queries(topic, query, language)

    elif topic and intent == "topic_news_and_capabilities":
        variants["topic"]         = topic
        variants["topic_queries"] = make_topic_news_and_capabilities_queries(topic, query, language)

    # ── Digest / docs / news variants ────────────────────────────────────────
    if intent == "headline_digest":
        variants["digest_queries"] = make_news_digest_queries(query, language)

    if intent == "docs":
        variants["docs"] = make_docs_query(query)

    if intent in ("news", "headline_digest"):
        variants["news"] = make_news_query(query, language)

    if should_add_english(query, language, intent):
        variants["en_translation"] = query  # searched with language="en"

    return variants
