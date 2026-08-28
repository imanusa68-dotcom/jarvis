# core/awareness/_app_index.py
# Index of launchable applications = Start Menu shortcuts (.lnk). Launching a
# shortcut (os.startfile) is as reliable as clicking it in the Start menu, and
# the names are clean ("Telegram", "Adobe Photoshop 2024").
#
# Fuzzy matcher so a Russian-speaking user can name an English app imprecisely:
# the query is transliterated Cyrillic→Latin ("фотошоп"→"fotoshop"≈"photoshop")
# and scored against names by substring/token/difflib. Pure & local — no LLM.

from __future__ import annotations

import difflib
import os
import re
import time
from pathlib import Path

_MATCH_THRESHOLD = 0.62
_CACHE_TTL = 60.0
_cache: tuple[float, list[dict]] | None = None

# Cyrillic → Latin (pronunciation-ish), enough to bridge RU spelling of EN apps.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(s: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in (s or "").lower())


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)          # (x64), (64-bit)
    s = re.sub(r"[,._\-]+", " ", s)
    s = re.sub(r"\bверси[яи]\b", " ", s)     # "версия"
    s = re.sub(r"\b\d[\d.]*\b", " ", s)      # version numbers
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def list_shortcuts() -> list[dict]:
    """[{name, path}] of Start Menu .lnk shortcuts (cached). Seam for tests."""
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]
    items: list[dict] = []
    seen: set[str] = set()
    for d in _start_menu_dirs():
        try:
            for lnk in d.rglob("*.lnk"):
                key = lnk.stem.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append({"name": lnk.stem, "path": str(lnk)})
        except Exception:
            continue
    _cache = (now, items)
    return items


def score(query: str, name: str) -> float:
    forms = {_normalize(query), _normalize(_translit(query))}
    nn = _normalize(name)
    candidates = [nn] + nn.split()
    best = 0.0
    for f in forms:
        if not f:
            continue
        for c in candidates:
            if not c:
                continue
            if f == c:
                best = max(best, 1.0)
            elif len(f) >= 3 and len(c) >= 3 and (f in c or c in f):
                # len(c) >= 3 обязателен: однобуквенные токены имени («V» из
                # Hyper-V Manager, «K» из K-Lite) давали 0.85 любому запросу,
                # содержащему эту букву — «блокнот» открывал деинсталлятор.
                best = max(best, 0.85)
            else:
                best = max(best, difflib.SequenceMatcher(None, f, c).ratio())
    return best


# Ярлыки-деинсталляторы/инсталляторы никогда не запускаются голосом:
# fuzzy-совпадение «открой X» с «Uninstall X» — инцидент, а не запуск.
_DANGEROUS_SHORTCUT = re.compile(
    r"\b(uninstall|uninst|deinstall|repair|setup|installer)\b|удал|деинстал",
    re.IGNORECASE,
)


def _ranked(query: str) -> list[tuple[float, dict]]:
    scored = [
        (score(query, sc["name"]), sc)
        for sc in list_shortcuts()
        if not _DANGEROUS_SHORTCUT.search(sc["name"])
    ]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


def best_match(query: str, threshold: float = _MATCH_THRESHOLD) -> dict | None:
    """The best-matching shortcut for `query`, or None if nothing is close enough."""
    ranked = _ranked(query)
    if ranked and ranked[0][0] >= threshold:
        return ranked[0][1]
    return None


def suggestions(query: str, n: int = 3) -> list[str]:
    """Top-n shortcut NAMES by score (for a 'did you mean…' when nothing matched)."""
    return [sc["name"] for sco, sc in _ranked(query)[:n] if sco > 0]
