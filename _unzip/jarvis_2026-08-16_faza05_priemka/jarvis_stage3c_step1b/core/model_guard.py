# core/model_guard.py
# MARK XXXVI — Gemini auxiliary API quota guard (остывание по каждой модели)
# ──────────────────────────────────────────────────────────────────────────────────
# Cooldown tracker for ALL non-live Gemini REST calls.
#
# Фаза 0.5 (2026-08-06), Р-22: остывание после 429 считается ОТДЕЛЬНО
# ПО КАЖДОЙ МОДЕЛИ. Раньше был один общий рубильник: отказ любой модели
# глушил все подсистемы сразу. После разведения ролей по моделям
# (config/registry.yaml) у ролей разные дневные лимиты, и общий рубильник
# обесценивал само разведение.
#
# Каждый вопрос к сторожу называет модель:
#
#     guard.is_available(model)
#     guard.cooldown_remaining(model)
#     guard.record_429(retry_after, model)
#     guard.handle_exception(exc, model)
#
# model=None означает «модель не названа»: такие вызовы живут в своём
# отдельном ведре и не влияют на остальные модели.
#
# Does NOT affect the main live WebSocket session in main.py.
#
# Usage pattern in any auxiliary caller:
#
#     from core.aux_model import aux_call
#     ok, text = aux_call(prompt, api_key, caller="MySubsystem")
#     if not ok:
#         handle_quota_or_error(text)
#
# ──────────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
import threading
import time


def _say(line: str) -> None:
    """Сказать вслух так, чтобы печать НИКОГДА не уронила вызов.

    НАЙДЕНО ПРОГОНОМ 19.08.2026, и это дыра, а не косметика. Здесь стоял
    обычный `print` со знаком-предупреждением. На канале cp1251 (обычная
    Windows, когда вывод перенаправлен в файл без принудительного UTF-8) он
    бросает UnicodeEncodeError.

    Чем это было опасно: `record_429` зовётся из `handle_exception`, а его
    зовёт `core/aux_model.aux_call` ИЗНУТРИ своего `except`. Значит на
    настоящем исчерпании квоты исключение из печати улетало наверх ВМЕСТО
    честного «(False, квота исчерпана)» — договор двери «на квотных ошибках
    не бросаем» ломался ровно там, где он и нужен. Эта же болезнь лечилась в
    блоке 5 у `aux_model._out`, но жила ещё и здесь, на самом важном пути.

    Почему её не видел прогон: полный прогон идёт с принудительным UTF-8, и
    там знак печатается. Тест краснел только при выводе в файл — то есть был
    плавающим, а плавающему тесту перестают верить.

    Образец не новый: `_out` в core/aux_model.py, `_say` в core/state_snapshot.py.
    """
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            print(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    except Exception:
        pass


class ModelQuotaGuard:
    """
    Thread-safe singleton — tracks Gemini REST API cooldown per model.

    States (для каждой модели отдельно):
        available     — is_available(model) returns True; calls may proceed
        cooldown      — is_available(model) returns False; remaining > 0
    """

    DEFAULT_COOLDOWN: float = 65.0  # seconds to wait after a 429
    UNSPECIFIED: str = "(модель не названа)"

    def __init__(self) -> None:
        self._lock:           threading.Lock   = threading.Lock()
        self._cooldown_until: dict[str, float] = {}
        self._counts:         dict[str, int]   = {}

    # ── Internal ─────────────────────────────────────────────────────────

    @classmethod
    def _key(cls, model: str | None) -> str:
        """Имя модели как ключ ведра; пустое имя — отдельное ведро."""
        name = str(model or "").strip()
        return name or cls.UNSPECIFIED

    # ── Public API ───────────────────────────────────────────────────

    def is_available(self, model: str | None = None) -> bool:
        """True если именно эта модель не остывает и вызов разрешён."""
        key = self._key(model)
        with self._lock:
            return time.monotonic() >= self._cooldown_until.get(key, 0.0)

    def cooldown_remaining(self, model: str | None = None) -> float:
        """Секунды до конца остывания этой модели, или 0.0."""
        key = self._key(model)
        with self._lock:
            return max(0.0, self._cooldown_until.get(key, 0.0) - time.monotonic())

    def record_429(
        self,
        retry_after: float | None = None,
        model: str | None = None,
    ) -> None:
        """
        Record a 429 / RESOURCE_EXHAUSTED event для конкретной модели.
        If retry_after is provided (parsed from response header/body), use it;
        otherwise fall back to DEFAULT_COOLDOWN.
        Never shortens an already-running cooldown.
        Остальные модели остаются доступны.
        """
        key = self._key(model)
        cooldown = (
            float(retry_after) if (retry_after and retry_after > 0)
            else self.DEFAULT_COOLDOWN
        )
        with self._lock:
            candidate = time.monotonic() + cooldown
            if candidate > self._cooldown_until.get(key, 0.0):
                self._cooldown_until[key] = candidate
            self._counts[key] = self._counts.get(key, 0) + 1
            total = self._counts[key]

        remaining = self.cooldown_remaining(model)
        _say(
            f"[ModelGuard] \U0001f6ab 429 от {key} (#{total}) — "
            f"cooldown {cooldown:.0f}s — "
            f"available again in {remaining:.0f}s"
        )

    def handle_exception(
        self,
        exc: Exception,
        model: str | None = None,
    ) -> bool:
        """
        Inspect an exception; if it indicates a quota/429 error, call
        record_429() for this model and return True.  Otherwise return False.
        """
        err = str(exc)
        if (
            "429" not in err
            and "RESOURCE_EXHAUSTED" not in err
            and "quota" not in err.lower()
        ):
            return False

        retry_after: float | None = None

        # Try to parse a suggested retry delay from the error message
        m = re.search(r"retry[\s_-]?(?:after|delay)[^0-9]*([0-9]+)", err, re.IGNORECASE)
        if m:
            retry_after = float(m.group(1))
        else:
            m2 = re.search(r"([0-9]+)\s*second", err, re.IGNORECASE)
            if m2:
                retry_after = float(m2.group(1))

        self.record_429(retry_after, model)
        return True

    def status_summary(self, model: str | None = None) -> str:
        """Human-readable status string for logging/debugging."""
        if model is not None:
            key = self._key(model)
            rem = self.cooldown_remaining(model)
            with self._lock:
                seen = self._counts.get(key, 0)
            if rem > 0:
                return f"{key}: COOLDOWN — {rem:.0f}s remaining (total 429s: {seen})"
            return f"{key}: AVAILABLE (total 429s seen: {seen})"

        with self._lock:
            keys = sorted(set(self._cooldown_until) | set(self._counts))
        if not keys:
            return "AVAILABLE — ни одной модели ещё не отказывали"
        return " | ".join(self.status_summary(k) for k in keys)

    def reset(self) -> None:
        """Забыть всё остывание (только для тестов и диагностики)."""
        with self._lock:
            self._cooldown_until.clear()
            self._counts.clear()


# ── Module-level singleton ───────────────────────────────────────────────

_guard: ModelQuotaGuard = ModelQuotaGuard()


def get_guard() -> ModelQuotaGuard:
    """Return the shared ModelQuotaGuard singleton."""
    return _guard
