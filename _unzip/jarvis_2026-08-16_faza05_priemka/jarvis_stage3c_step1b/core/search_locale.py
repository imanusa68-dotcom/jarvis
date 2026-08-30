# core/search_locale.py
# MARK XXXV — Language Detection & Search Locale Utilities
#
# Maps language codes to the search-provider locale parameters that each
# provider understands.
#
# Само определение языка живёт в core/lang.py с 7 августа 2026 года.
# Здесь осталась обёртка detect_language() с прежним именем и прежним
# ответом — ради тех, кто её уже зовёт.
#
# All public functions are pure / stateless — safe to call from any thread.

from __future__ import annotations
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_language(text: str) -> str:
    """
    Detect the primary language of *text*.  Returns an ISO-639-1 code.

    Тонкая обёртка над core/lang.py — единственным определителем языка
    в проекте.  Имя, аргументы и формат ответа сохранены ради тех, кто
    её уже зовёт: web_search, deep_research, executor и тесты.  Менять их
    означало бы трогать пять файлов ради одной правки.

    Что изменилось внутри 7 августа 2026:
      • латинские языки различаются по частым словам и своеобразным
        буквам, а не сваливаются все в "en";
      • японский больше не выдаёт себя за китайский;
      • таблицы и пороги живут в config/languages.yaml, а не в коде.

    Поведение на нелатинских письменностях и на неузнанной латинице не
    изменилось ни на шаг — это закреплено замораживающими тестами
    в tests/test_search_locale_frozen.py.
    """
    from core.lang import detect
    return detect(text)


def resolve_language(text: str, explicit: Optional[str] = None) -> tuple[str, str]:
    """Вернуть пару: код языка и того, кто этот язык выбрал.

    Зачем пара, а не просто код
    ───────────────────────────
    7 августа 2026 владелец смотрел в лог живого поиска и не смог понять,
    чей это был выбор. Видно было только lang=ru — а решить могли двое:
    голосовая модель, которая присылает language готовым, или наш
    core/lang.py. Когда язык окажется неверным, чинить придётся ровно
    одного из двух, и гадать нельзя.

    Три случая, и все три обязаны быть слышны:
      • модель прислала язык       → "назвала голосовая модель";
      • модель промолчала          → "решил наш код (буквы)" и так далее;
      • определитель не загрузился → английский, но громко, а не молча.

    Ни сети, ни модели, ни квоты: причина уже вычислена внутри
    detect_with_reason и раньше просто выбрасывалась.
    """
    if explicit:
        return explicit.lower()[:2], "назвала голосовая модель"
    try:
        from core.lang import detect_with_reason
    except ImportError:
        return "en", "ОПРЕДЕЛИТЕЛЬ НЕ ЗАГРУЗИЛСЯ — беру английский"
    code, why = detect_with_reason(text)
    return code, "решил наш код (" + why + ")"


# ═══════════════════════════════════════════════════════════════════════════════
# LOCALE TABLE
# ═══════════════════════════════════════════════════════════════════════════════

# lang → (serpapi_hl, cse_lr, accept_language_header, human_label)
_LOCALE_MAP: dict[str, tuple[str, str, str, str]] = {
    "en": ("en",    "lang_en",    "en-US,en;q=0.9",              "English"),
    "ru": ("ru",    "lang_ru",    "ru-RU,ru;q=0.9,en;q=0.5",    "Russian"),
    "uk": ("uk",    "lang_uk",    "uk-UA,uk;q=0.9,ru;q=0.5",    "Ukrainian"),
    "de": ("de",    "lang_de",    "de-DE,de;q=0.9,en;q=0.5",    "German"),
    "fr": ("fr",    "lang_fr",    "fr-FR,fr;q=0.9,en;q=0.5",    "French"),
    "es": ("es",    "lang_es",    "es-ES,es;q=0.9,en;q=0.5",    "Spanish"),
    "it": ("it",    "lang_it",    "it-IT,it;q=0.9,en;q=0.5",    "Italian"),
    "pt": ("pt",    "lang_pt",    "pt-PT,pt;q=0.9,en;q=0.5",    "Portuguese"),
    "pl": ("pl",    "lang_pl",    "pl-PL,pl;q=0.9,en;q=0.5",    "Polish"),
    "tr": ("tr",    "lang_tr",    "tr-TR,tr;q=0.9,en;q=0.5",    "Turkish"),
    "ar": ("ar",    "lang_ar",    "ar-SA,ar;q=0.9,en;q=0.5",    "Arabic"),
    "zh": ("zh-cn", "lang_zh-CN", "zh-CN,zh;q=0.9,en;q=0.5",   "Chinese"),
    "ja": ("ja",    "lang_ja",    "ja-JP,ja;q=0.9,en;q=0.5",    "Japanese"),
    "ko": ("ko",    "lang_ko",    "ko-KR,ko;q=0.9,en;q=0.5",    "Korean"),
    "hi": ("hi",    "lang_hi",    "hi-IN,hi;q=0.9,en;q=0.5",    "Hindi"),
    "el": ("el",    "lang_el",    "el-GR,el;q=0.9,en;q=0.5",    "Greek"),
    "th": ("th",    "lang_th",    "th-TH,th;q=0.9,en;q=0.5",    "Thai"),
    "he": ("iw",    "lang_iw",    "he-IL,he;q=0.9,en;q=0.5",    "Hebrew"),
}

_DEFAULT_LOCALE: tuple[str, str, str, str] = _LOCALE_MAP["en"]


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ACCESSORS
# ═══════════════════════════════════════════════════════════════════════════════

def get_locale(lang: Optional[str]) -> tuple[str, str, str, str]:
    """
    Return ``(serpapi_hl, cse_lr, accept_language, label)`` for *lang*.
    Falls back to English for unknown or None codes.
    """
    if not lang:
        return _DEFAULT_LOCALE
    return _LOCALE_MAP.get(lang.lower()[:2], _DEFAULT_LOCALE)


def get_serpapi_hl(lang: Optional[str]) -> str:
    """Return the ``hl`` parameter value for SerpAPI."""
    return get_locale(lang)[0]


def get_cse_lr(lang: Optional[str]) -> str:
    """Return the ``lr`` parameter value for Google CSE."""
    return get_locale(lang)[1]


def get_accept_language(lang: Optional[str]) -> str:
    """Return the ``Accept-Language`` HTTP header value."""
    return get_locale(lang)[2]


# Обратный указатель: человеческое имя → код. Строится из той же таблицы,
# поэтому разойтись с ней физически не может.
_LABEL_TO_CODE: dict[str, str] = {
    row[3].lower(): code for code, row in _LOCALE_MAP.items()
}


def normalize_lang(value: Optional[str], fallback: str = "ru") -> str:
    """Привести язык к двухбуквенному коду. Принимает и код, и английское имя.

    Зачем это появилось 7 августа 2026
    ──────────────────────────────────
    В проекте язык ходит в двух видах. Поиск и композер оперируют кодом
    ("ru"), а перевод в agent/executor.py — человеческим именем ("Russian"),
    потому что имя уходит прямо в промпт. Пока эти два вида не встречались,
    всё держалось на честном слове: проверки вида `language == "ru"`
    молча провалились бы, получив "Russian", и Jarvis заговорил бы
    по-английски, не сказав ни слова об ошибке.

    Здесь оба вида сводятся к коду. Неизвестное значение даёт *fallback*,
    а не английский: владелец 7 августа решил, что умолчание — русский.
    """
    if not value:
        return fallback
    text = str(value).strip().lower()
    if text in _LABEL_TO_CODE:
        return _LABEL_TO_CODE[text]
    short = text[:2]
    if short in _LOCALE_MAP:
        return short
    return fallback


def get_label(lang: Optional[str], fallback: Optional[str] = None) -> str:
    """Return a human-readable language label.

    Без *fallback* поведение прежнее и заморожено тестами: незнакомый код
    даёт "English", потому что так исторически ведёт себя локаль поиска.

    С *fallback* (обычно "ru") незнакомый код даёт запасной язык. Это нужно
    там, где имя уходит в промпт: ответ владельцу по умолчанию русский.
    Принимается и имя языка, и код — см. normalize_lang.
    """
    if fallback is None:
        return get_locale(lang)[3]
    return _LOCALE_MAP.get(normalize_lang(lang, fallback), _DEFAULT_LOCALE)[3]


# ═══════════════════════════════════════════════════════════════════════════════
# BILINGUAL MERGE UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

def merge_and_dedup(
    primary_results: list[dict],
    secondary_results: list[dict],
    max_total: int = 10,
) -> list[dict]:
    """
    Merge two search-result lists, removing URL duplicates.

    Primary results are kept first; secondary results fill remaining slots.
    The merged list is re-ranked from 1.

    Args:
        primary_results:   Results from the user's own language search.
        secondary_results: Results from the secondary-language search.
        max_total:         Cap on total returned results.

    Returns:
        Merged, de-duplicated, re-ranked list (at most *max_total* items).
    """
    seen_urls: set[str] = set()
    merged: list[dict] = []

    for r in primary_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(r)

    for r in secondary_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(r)

    for i, r in enumerate(merged[:max_total], 1):
        r["rank"] = i

    return merged[:max_total]
