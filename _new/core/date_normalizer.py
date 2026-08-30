# core/date_normalizer.py
# MARK XXXV — Relative Date Normalizer
#
# Converts relative date expressions in Russian and English into absolute dates.
# Used by query_rewriter.py to build date-specific search queries.
#
# Supports:
#   Russian: вчера, сегодня, позавчера, на прошлой неделе, за последние сутки...
#   English: yesterday, today, day before yesterday, last week, past 24 hours...
#
# Zero external dependencies — uses only stdlib datetime + re.

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Russian month name tables
# ─────────────────────────────────────────────────────────────────────────────

_RU_MONTH_GENITIVE = {
    1: "января",  2: "февраля", 3: "марта",    4: "апреля",
    5: "мая",     6: "июня",    7: "июля",     8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

_EN_MONTH_NAME = {
    1: "January",  2: "February", 3: "March",    4: "April",
    5: "May",      6: "June",     7: "July",     8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


# ─────────────────────────────────────────────────────────────────────────────
# Date formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def format_date_ru(d: date) -> str:
    """Format date in Russian style: '10 апреля 2026'"""
    return f"{d.day} {_RU_MONTH_GENITIVE[d.month]} {d.year}"


def format_date_en(d: date) -> str:
    """Format date in English style: 'April 10 2026' (no comma — better for search)"""
    return f"{_EN_MONTH_NAME[d.month]} {d.day} {d.year}"


def format_date_iso(d: date) -> str:
    """ISO-8601: '2026-04-10'"""
    return d.strftime("%Y-%m-%d")


def format_date_slash(d: date) -> str:
    """Slash format: '10/04/2026' — used in some news providers"""
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


# ─────────────────────────────────────────────────────────────────────────────
# DateInfo: result of date extraction
# ─────────────────────────────────────────────────────────────────────────────

class DateInfo:
    """
    Holds information about a detected date in a user query.

    Attributes:
        date        — the resolved absolute date (or None)
        relative    — the raw relative expression that was matched
        is_range    — True if the query implies a multi-day range
        range_days  — number of days in the range (1 if single day)
        found       — True if any date expression was detected
    """

    def __init__(
        self,
        date_found:  Optional[date] = None,
        relative:    str            = "",
        is_range:    bool           = False,
        range_days:  int            = 1,
    ):
        self.date       = date_found
        self.relative   = relative
        self.is_range   = is_range
        self.range_days = range_days

    @property
    def found(self) -> bool:
        return self.date is not None

    # -- Formatted representations --

    def ru(self) -> str:
        """'10 апреля 2026'"""
        return format_date_ru(self.date) if self.date else ""

    def en(self) -> str:
        """'April 10 2026'"""
        return format_date_en(self.date) if self.date else ""

    def iso(self) -> str:
        """'2026-04-10'"""
        return format_date_iso(self.date) if self.date else ""

    def date_range_ru(self) -> list[str]:
        """Return list of Russian-formatted dates in the range (up to range_days)."""
        if not self.date:
            return []
        days = []
        for i in range(min(self.range_days, 7)):
            days.append(format_date_ru(self.date + timedelta(days=i)))
        return days

    def date_range_en(self) -> list[str]:
        """Return list of English-formatted dates in the range."""
        if not self.date:
            return []
        days = []
        for i in range(min(self.range_days, 7)):
            days.append(format_date_en(self.date + timedelta(days=i)))
        return days

    def __repr__(self) -> str:
        return (
            f"DateInfo(date={self.iso()!r}, relative={self.relative!r}, "
            f"is_range={self.is_range}, range_days={self.range_days})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Relative date patterns
#
# Each entry: (regex_pattern, day_delta_or_'range', optional_range_days)
# day_delta: int offset from today (0 = today, -1 = yesterday, etc.)
# ─────────────────────────────────────────────────────────────────────────────

# Single-day patterns: (pattern, delta_days, is_range, range_days)
_SINGLE_PATTERNS: list[tuple[str, int]] = [
    # Russian — ordered from most specific to least
    (r"\bпозавчера\b",                  -2),
    (r"\bза\s+последн\w*\s+двое?\s+суток\b", -2),
    (r"\bза\s+вчера\b",                 -1),
    (r"\bвчера\b",                      -1),
    (r"\bза\s+последн\w*\s+сутки\b",    -1),
    (r"\bза\s+последн\w*\s+день\b",     -1),
    (r"\bза\s+день\b",                  -1),
    (r"\bза\s+сегодня\b",               0),
    (r"\bсегодня\b",                    0),
    (r"\bсейчас\b",                     0),
    # English — ordered from most specific to least
    (r"\bday\s+before\s+yesterday\b",   -2),
    (r"\byesterday\b",                  -1),
    (r"\bpast\s+24\s+hours?\b",         -1),
    (r"\blast\s+24\s+hours?\b",         -1),
    (r"\btoday\b",                      0),
    (r"\bright\s+now\b",                0),
    (r"\bcurrently\b",                  0),
]

# Multi-day range patterns: (pattern, start_delta, range_days)
_RANGE_PATTERNS: list[tuple[str, int, int]] = [
    # Russian
    (r"\bна\s+прошл\w+\s+недел\w+\b",  -7,  7),
    (r"\bза\s+прошл\w+\s+недел\w+\b",  -7,  7),
    (r"\bза\s+последн\w+\s+недел\w+\b", -7, 7),
    (r"\bэт\w+\s+недел\w+\b",          -6,  7),
    (r"\bза\s+последн\w+\s+3\s+дн\w+\b", -3, 3),
    (r"\bза\s+последн\w+\s+три\s+дн\w+\b", -3, 3),
    # English
    (r"\blast\s+week\b",                -7,  7),
    (r"\bthis\s+week\b",                -6,  7),
    (r"\bpast\s+week\b",                -7,  7),
    (r"\blast\s+(\d+)\s+days?\b",       None, None),  # dynamic — handled separately
    (r"\bpast\s+(\d+)\s+days?\b",       None, None),  # dynamic — handled separately
    # Russian — dynamic
    (r"\bза\s+последн\w+\s+(\d+)\s+дн\w+\b", None, None),
]


# ─────────────────────────────────────────────────────────────────────────────
# Main extraction function
# ─────────────────────────────────────────────────────────────────────────────

def extract_date_info(query: str, language: str = "en") -> DateInfo:
    """
    Detect and extract relative date information from a user query.

    Args:
        query    — the raw user query string
        language — ISO-639-1 code ("ru", "en", etc.)

    Returns:
        DateInfo object. DateInfo.found == False if no date expression found.
    """
    today = date.today()
    q = query.lower().strip()

    # ── Check range patterns first (more specific) ──────────────────────────
    for pattern, start_delta, r_days in _RANGE_PATTERNS:
        m = re.search(pattern, q, re.IGNORECASE | re.UNICODE)
        if m:
            if start_delta is None:
                # Dynamic pattern with captured number
                try:
                    n = int(m.group(1))
                except (IndexError, ValueError):
                    n = 7
                n = max(1, min(n, 30))
                start = today - timedelta(days=n)
                return DateInfo(
                    date_found=start,
                    relative=m.group(0),
                    is_range=True,
                    range_days=n,
                )
            else:
                start = today + timedelta(days=start_delta)
                return DateInfo(
                    date_found=start,
                    relative=m.group(0),
                    is_range=True,
                    range_days=r_days,
                )

    # ── Check single-day patterns ────────────────────────────────────────────
    for pattern, delta in _SINGLE_PATTERNS:
        m = re.search(pattern, q, re.IGNORECASE | re.UNICODE)
        if m:
            target = today + timedelta(days=delta)
            return DateInfo(
                date_found=target,
                relative=m.group(0),
                is_range=False,
                range_days=1,
            )

    return DateInfo()


def remove_date_words(query: str) -> str:
    """
    Strip relative date expressions from a query string.
    Useful for extracting the 'topic' part of a query separately from the date.

    Example:
        "что нового было вчера в новостях" → "что нового было в новостях"
        "главные события за прошлую неделю" → "главные события"
    """
    q = query

    # Remove single-day patterns
    for pattern, _ in _SINGLE_PATTERNS:
        q = re.sub(pattern, "", q, flags=re.IGNORECASE | re.UNICODE)

    # Remove range patterns (static ones)
    for pattern, start_delta, _ in _RANGE_PATTERNS:
        if start_delta is not None:
            q = re.sub(pattern, "", q, flags=re.IGNORECASE | re.UNICODE)

    # Clean up filler words left behind and excess whitespace
    noise = [
        r"\bбыло\b", r"\bбыл\b", r"\bбыли\b",
        r"\bв\s+новостях\b", r"\bнового\b",
    ]
    for n in noise:
        q = re.sub(n, "", q, flags=re.IGNORECASE | re.UNICODE)

    q = re.sub(r"\s+", " ", q).strip(" ,.:;?!")
    return q


def get_today_iso() -> str:
    """Convenience — today as ISO-8601 string."""
    return format_date_iso(date.today())
