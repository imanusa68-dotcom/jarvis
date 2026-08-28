# core/lang.py
# MARK XXXVI — определение языка без сети, без модели и без сторонних библиотек.
#
# Зачем этот файл появился
# ────────────────────────
# Раньше вопрос «на каком это языке» уходил по сети к языковой модели:
# от 300 до 1500 миллисекунд ожидания, расход суточной квоты и молчаливый
# провал при ошибке 503 (ночь 7 августа 2026 ушла именно на этот сбой).
# Определение языка — задача уровня «посчитать буквы», и искусственный интеллект
# здесь не нужен. Замер прототипа: 25,6 микросекунды на вызов, +0,5 МБ памяти.
#
# Лестница решения (сверху вниз, остановка на первой сработавшей ступени)
# ───────────────────────────────────────────────────────────────
#   1. Письменность — кириллица, вязь, иероглифы, кана, хангыль и так далее.
#      Ответ точный, спорить не о чем.
#   2. Частые слова — только для латиницы, где буквы у всех одинаковые.
#   3. Своеобразные буквы — выручают на коротких фразах, где слов почти нет.
#   4. Значение fallback из конфига — честное «не знаю».
#
# Все слова, буквы и пороги живут в config/languages.yaml. Здесь их нет и быть
# не должно: новый язык добавляется правкой конфига без единой строчки в core/.
# Исключение одно и осознанное: границы юникод-диапазонов — это факты
# стандарта, а не настройки владельца; они никогда не меняются.
#
# Все функции без собственного состояния — безопасны из любого потока.
# Конфиг читается один раз и ложится в кеш.

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Dict, FrozenSet, Optional, Tuple

# ════════════════════════════════════════════════════════════════════════
# ГРАНИЦЫ ПИСЬМЕННОСТЕЙ (факты юникода, не настройки)
# ════════════════════════════════════════════════════════════════════════

_CYRILLIC_RANGE   = (0x0400, 0x04FF)
_ARABIC_RANGE     = (0x0600, 0x06FF)
_CJK_RANGE        = (0x4E00, 0x9FFF)
_HIRAGANA_RANGE   = (0x3040, 0x309F)
_KATAKANA_RANGE   = (0x30A0, 0x30FF)
_HANGUL_RANGE     = (0xAC00, 0xD7AF)
_DEVANAGARI_RANGE = (0x0900, 0x097F)
_GREEK_RANGE      = (0x0370, 0x03FF)
_THAI_RANGE       = (0x0E00, 0x0E7F)
_HEBREW_RANGE     = (0x0590, 0x05FF)

_UKRAINIAN_LETTERS = "іїєґІЇЄҐ"

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Аварийные значения — работают только тогда, когда конфиг не прочёлся
# вовсе (файл стёрт, испорчен, старый архив распакован поверх нового).
# Система обязана продолжить работать по письменностям, а не упасть.
_EMERGENCY = {
    "fallback": "ru",
    "fallback_latin": "en",
    "script_share": 0.15,
    "ukrainian_share": 0.05,
    "word_min_hits": 2,
    "sample_chars": 500,
}


# ════════════════════════════════════════════════════════════════════════
# КОНФИГ
# ════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _config() -> dict:
    """Разобранный config/languages.yaml. Отсутствие файла — не причина падать.

    Ловится именно Exception целиком, и это намеренно: определение языка
    вызывается из горячих мест (поиск, перевод), и уронить задачу из-за
    одной сломанной строчки в конфиге было бы хуже, чем ответить по буквам.
    """
    try:
        import yaml
        from config.loader import CONFIG_DIR

        raw = (CONFIG_DIR / "languages.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _setting(name: str):
    """Порог из конфига; при любой беде — аварийное значение."""
    if name in ("fallback", "fallback_latin"):
        value = _config().get(name)
        return str(value) if isinstance(value, str) and value else _EMERGENCY[name]

    block = _config().get("thresholds")
    if isinstance(block, dict) and isinstance(block.get(name), (int, float)):
        return block[name]
    return _EMERGENCY[name]


@lru_cache(maxsize=1)
def _word_index() -> Dict[str, FrozenSet[str]]:
    """Язык → множество его частых слов, уже очищенных от значков."""
    out: Dict[str, FrozenSet[str]] = {}
    latin = _config().get("latin")
    if not isinstance(latin, dict):
        return out
    for code, spec in latin.items():
        if not isinstance(spec, dict):
            continue
        words = spec.get("words")
        if not isinstance(words, list):
            continue
        cleaned = {fold(str(w)) for w in words if str(w).strip()}
        cleaned.discard("")
        if cleaned:
            out[str(code)] = frozenset(cleaned)
    return out


@lru_cache(maxsize=1)
def _letter_index() -> Dict[str, FrozenSet[str]]:
    """Язык → множество его своеобразных букв (без очистки — значки тут суть)."""
    out: Dict[str, FrozenSet[str]] = {}
    latin = _config().get("latin")
    if not isinstance(latin, dict):
        return out
    for code, spec in latin.items():
        if not isinstance(spec, dict):
            continue
        letters = spec.get("letters")
        if not isinstance(letters, str) or not letters:
            continue
        out[str(code)] = frozenset(unicodedata.normalize("NFC", letters))
    return out


def reset_cache() -> None:
    """Сбросить кеш конфига. Нужно только тестам и горячей перечитке."""
    _config.cache_clear()
    _word_index.cache_clear()
    _letter_index.cache_clear()


# ════════════════════════════════════════════════════════════════════════
# ОЧИСТКА ТЕКСТА
# ════════════════════════════════════════════════════════════════════════

def fold(text: str) -> str:
    """Привести к виду, в котором слова сравнимы между собой.

    Две ловушки, ради которых функция существует:

    1. Турецкая заглавная İ при понижении регистра даёт i ПЛЮС отдельную
       точку сверху — два знака вместо одного. Без снятия значков
       слово "İstanbul" никогда не совпадёт со словом "istanbul".
    2. Буква «ö» бывает записана двумя способами — одним знаком или как
       o со значком. Глаз разницы не видит, компьютер видит.

    Побочная польза: владелец может писать турецкое "cok" вместо "çok" —
    после очистки это одно и то же слово, и список в конфиге не раздувается
    вдвое.
    """
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _count_in_range(text: str, lo: int, hi: int) -> int:
    return sum(1 for ch in text if lo <= ord(ch) <= hi)


# ════════════════════════════════════════════════════════════════════════
# СТУПЕНЬ 1 — ПИСЬМЕННОСТЬ
# ════════════════════════════════════════════════════════════════════════

def _by_script(sample: str) -> Optional[str]:
    share    = float(_setting("script_share"))
    uk_share = float(_setting("ukrainian_share"))
    total    = max(1, sum(1 for ch in sample if not ch.isspace()))

    kana = (_count_in_range(sample, *_HIRAGANA_RANGE)
            + _count_in_range(sample, *_KATAKANA_RANGE))
    cjk = _count_in_range(sample, *_CJK_RANGE)

    # Японский разбирается ПЕРВЫМ и отдельно. До сегодняшнего дня японский
    # текст считался китайским, потому что кана нигде не считалась,
    # и поиск уходил с китайской локалью. Кана есть — значит японский.
    if kana and (kana + cjk) / total > share:
        return "ja"

    scores = {
        "ru": _count_in_range(sample, *_CYRILLIC_RANGE),
        "ar": _count_in_range(sample, *_ARABIC_RANGE),
        "zh": cjk,
        "ko": _count_in_range(sample, *_HANGUL_RANGE),
        "hi": _count_in_range(sample, *_DEVANAGARI_RANGE),
        "el": _count_in_range(sample, *_GREEK_RANGE),
        "th": _count_in_range(sample, *_THAI_RANGE),
        "he": _count_in_range(sample, *_HEBREW_RANGE),
    }

    best_lang, best_count = max(scores.items(), key=lambda kv: kv[1])
    if best_count / total > share:
        if best_lang == "ru":
            uk_chars = sum(1 for ch in sample if ch in _UKRAINIAN_LETTERS)
            if uk_chars / total > uk_share:
                return "uk"
        return best_lang
    return None


# ════════════════════════════════════════════════════════════════════════
# СТУПЕНЬ 2 — ЧАСТЫЕ СЛОВА
# ════════════════════════════════════════════════════════════════════════

def _by_words(sample: str) -> Optional[str]:
    index = _word_index()
    if not index:
        return None

    words = set(_WORD_RE.findall(fold(sample)))
    if not words:
        return None

    scores = [(code, len(words & bag)) for code, bag in index.items()]
    scores.sort(key=lambda kv: kv[1], reverse=True)

    best_code, best_hits = scores[0]
    if best_hits < int(_setting("word_min_hits")):
        return None
    # Ничья — это не ответ. Пусть решают буквы ниже.
    if len(scores) > 1 and scores[1][1] == best_hits:
        return None
    return best_code


# ════════════════════════════════════════════════════════════════════════
# СТУПЕНЬ 3 — СВОЕОБРАЗНЫЕ БУКВЫ
# ════════════════════════════════════════════════════════════════════════

def _by_letters(sample: str) -> Optional[str]:
    index = _letter_index()
    if not index:
        return None

    # Здесь текст НЕ очищается: значки над буквами — это и есть улика.
    text = unicodedata.normalize("NFC", sample)

    scores = []
    for code, letters in index.items():
        hits = sum(1 for ch in text if ch in letters)
        if hits:
            scores.append((code, hits))
    if not scores:
        return None

    scores.sort(key=lambda kv: kv[1], reverse=True)
    if len(scores) > 1 and scores[1][1] == scores[0][1]:
        return None
    return scores[0][0]


# ════════════════════════════════════════════════════════════════════════
# ПУБЛИЧНАЯ ДВЕРЬ
# ════════════════════════════════════════════════════════════════════════

def _has_latin(text: str) -> bool:
    """Есть ли в тексте хоть одна латинская буква.

    Нужно ровно для одного случая: язык не узнан ни по письменности, ни по
    словам, ни по буквам. Обрывок латиницей ("RTX 5060 review") уходит в
    поиск, и русская локаль вернула бы мусор, поэтому у него своё умолчание.
    Значки над буквами снимает fold, поэтому "é" — тоже латиница.
    """
    for ch in fold(text):
        if "a" <= ch <= "z":
            return True
    return False


def detect_with_reason(text: str) -> Tuple[str, str]:
    """Вернуть (код языка, кто именно так решил).

    Причина нужна не для красоты: по правилу «молча не убивать» исполнитель
    обязан вслух назвать, на каком основании выбран язык перевода.
    """
    fallback = _setting("fallback")
    if not text or not text.strip():
        return fallback, "по умолчанию"

    sample = text[:int(_setting("sample_chars"))]

    code = _by_script(sample)
    if code:
        return code, "буквы"

    code = _by_words(sample)
    if code:
        return code, "слова"

    code = _by_letters(sample)
    if code:
        return code, "значки над буквами"

    if _has_latin(sample):
        return _setting("fallback_latin"), "по умолчанию для латиницы"

    return fallback, "по умолчанию"


def detect(text: str) -> str:
    """Код языка по ISO-639-1. Никогда не бросает исключений и никогда не ходит в сеть."""
    return detect_with_reason(text)[0]
