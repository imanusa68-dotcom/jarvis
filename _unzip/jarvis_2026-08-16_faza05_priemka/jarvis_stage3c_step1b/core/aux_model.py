# core/aux_model.py
# MARK XXXV — Centralised auxiliary (non-live) Gemini REST API gateway
# ─────────────────────────────────────────────────────────────────────────────
# ALL non-live generate_content calls (memory, personality, screen analysis,
# agent tools) must go through aux_call().
#
# This module:
#   1. Uses the NEW google.genai SDK (not deprecated google.generativeai)
#      through core/provider/: since 09.08.2026 the SDK itself lives only
#      in core/provider/gemini.py; this file keeps quotas, retries, voice.
#   2. Checks ModelQuotaGuard BEFORE every call
#   3. Updates ModelQuotaGuard on any 429 response
#   4. Returns (ok, text) — never raises on quota errors
#
# The main live WebSocket session in main.py is NOT affected.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from typing import List, Optional, Tuple


def default_model() -> str:
    """Модель роли aux_light — та же, что aux_call берёт по умолчанию."""
    from config.loader import get_model
    return get_model("aux_light")


RETRY_ROLE = "retry"

# Временный отказ — не то же самое, что исчерпание квоты. 503 (модель
# перегружена), обрыв сети и таймаут проходят сами: один повтор их
# обычно закрывает. 429 сюда не попадает никогда — его лечит остывание
# (core/model_guard.py), а лишний заход только сожжёт дневной лимит.
_TRANSIENT_NEEDLES = (
    "503", "unavailable", "overloaded", "502", "504",
    "deadline", "timeout", "timed out", "temporarily", "connection",
)


def _is_transient(exc: Exception) -> bool:
    """Отказ, который проходит сам и оправдывает один повтор."""
    err = str(exc).lower()
    return any(needle in err for needle in _TRANSIENT_NEEDLES)


def _short(text, limit: int = 160) -> str:
    """Однострочная выжимка ошибки для консоли."""
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= limit else one_line[:limit] + "…"


def _retry_plan() -> Tuple[int, float]:
    """Сколько всего заходов и пауза между ними.

    Числа живут в config/registry.yaml рядом с моделями: терпение к отказам —
    свойство поставщика, а не владельца. Отсутствие раздела — не ошибка:
    без него поведение ровно сегодняшнее, один заход без паузы.
    """
    from config.loader import get_limit
    try:
        attempts = int(get_limit(RETRY_ROLE, "attempts", 1) or 1)
    except (TypeError, ValueError):
        attempts = 1
    try:
        pause = float(get_limit(RETRY_ROLE, "pause_seconds", 0) or 0)
    except (TypeError, ValueError):
        pause = 0.0
    return max(1, attempts), max(0.0, pause)


def _build_contents(prompt: str, image_parts):
    """Сборка тела запроса. Отдельно от отправки — чтобы сторожа могли
    подменить только дверь наружу, не трогая сборку.

    Форму тела знает поставщик (core/provider/), а не этот файл: там же
    живёт порядок частей «сначала картинка, потом текст» и единственный
    импорт SDK. Собирает и отправляет один и тот же экземпляр.
    """
    from core.provider import get_provider
    return get_provider().build_payload(prompt, image_parts)


class _Reply:
    """Ответ двери в той форме, которую ждёт aux_call: объект с полем text.

    Слой поставщика отдаёт простую строку — так договор чище и в нём нет
    следов SDK. Но подмена в tests/test_transient_retry_is_audible.py ставит
    вместо _generate функцию, возвращающую объект с полем text, и aux_call
    читает именно .text. Эта обёртка держит обе стороны неизменными:
    поставщик не знает про форму ответа SDK, а сторож повторов
    продолжает работать без правок.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _generate(model: str, contents, api_key: str):
    """Единственная дверь к SDK во всём вспомогательном пути.

    Имя и форма вызова (model, contents, api_key) сохранены нарочно: за них
    держится подмена в tests/test_transient_retry_is_audible.py. Сам SDK с
    09.08.2026 живёт только в core/provider/gemini.py; здесь остались квоты,
    повторы и голос — то есть всё, что от поставщика не зависит.
    """
    from core.provider import get_provider
    return _Reply(get_provider().generate(model, contents, api_key))


def _out(line: str) -> None:
    """Сказать вслух так, чтобы печать НИКОГДА не уронила вызов.

    Найдено тестом 18.08.2026: на консоли cp1251 (обычная Windows без
    принудительного UTF-8) `print` со знаком-предупреждением бросает
    UnicodeEncodeError. Печать стоит ВНУТРИ except, поэтому исключение
    улетало наверх ВМЕСТО честного `(False, причина)` — то есть договор
    этого файла «на квотных ошибках не бросаем» нарушался ровно там, где он
    и нужен: на отказе.

    Образец не новый: так же устроен `_say` в core/state_snapshot.py.
    Р11 включает UTF-8 при старте, но под прогоном тестов и в чужой
    консоли этого никто не гарантирует.
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


def _bb_answer(rec, ok: bool, text: str = "", err_kind=None) -> None:
    """Записать ответ модели в чёрный ящик. Никогда не мешает вызову.

    Отдельной функцией, а не строкой в каждой ветви: исходов у вызова четыре
    (удача, исчерпание квоты, отказ после повторов, выход из цикла), и в
    каждом ответ обязан лечь ровно один раз. Одно место — один шанс забыть.

    Отказ несёт КОД, а не текст ошибки: текст ошибки — чужие слова, и им нет
    места ни в записи, ни тем более наверху.
    """
    if not rec:
        return
    from core import blackbox
    if ok:
        blackbox.write(rec, "model_out", {"ok": True, "t": text})
    else:
        blackbox.write(rec, "model_out", {"ok": False, "e": err_kind})


def aux_call(
    prompt: str,
    api_key: str,
    model: str | None = None,
    image_parts: Optional[List[Tuple[bytes, str]]] = None,
    caller: str = "unknown",
    role: str = "aux_light",
    ctx=None,
) -> Tuple[bool, str]:
    """
    Make a single non-live Gemini REST generate_content call.

    Args:
        prompt:      Text prompt.
        api_key:     Gemini API key.
        model:       Model name (default: роль aux_light из registry.yaml).
        image_parts: Optional list of (bytes, mime_type) tuples for vision calls.
        caller:      Human-readable caller name for logging.

    Returns:
        (True,  text)   — success; text is the model response.
        (False, reason) — failure; reason is one of:
            "[quota-cooldown:NNs]"   model in cooldown, call skipped
            "[quota-429:cooldown NNs]" 429 received, cooldown started
            "[quota-cap:N/N]"        суточный потолок исчерпан (блок 5)
            "[error:...]"            other exception

    Про учёт расхода (блок 5, инвариант I16). Резерв берётся ДО обращения к
    модели и закрывается фактом ПОСЛЕ — потому что вызов может не вернуться
    (процесс убит, свет выключен, сеть повисла), а квоту Google уже списал.
    Учёт никогда не ломает дело: если он недоступен, вызов проходит, а факт
    «расход не считается» называется вслух один раз.
    """
    if model is None:
        model = default_model()
    from core.model_guard import get_guard
    guard = get_guard()

    # Остывание считается по каждой модели отдельно: исчерпание одной
    # роли не должно затыкать остальные (см. core/model_guard.py).
    if not guard.is_available(model):
        rem = guard.cooldown_remaining(model)
        _out(f"[AuxModel] 🚫 {caller}: заход отменён, модель остывает ещё {rem:.0f}с")
        return False, f"[quota-cooldown:{rem:.0f}s]"

    # Учёт расхода. Порядок именно такой: сначала спросить разрешение у
    # СВОЕГО потолка, и только потом трогать сеть.
    from core import metering
    _sweep_lost_once()
    ticket = metering.reserve(role, ctx, len(prompt or ""), model_name=model,
                              api_key=api_key, printer=print)
    if not ticket.get("allowed"):
        spent, limit = ticket.get("spent"), ticket.get("limit")
        # I19: исчерпание НИКОГДА не молчаливое.
        _out(f"[AuxModel] 🚫 {caller}: суточный потолок роли {role} исчерпан "
              f"({spent} из {limit}) — вызов не сделан")
        return False, f"[quota-cap:{spent}/{limit}]"
    call_id = ticket["call_id"]

    # Чёрный ящик (блок 6). Ввоз ленивый и стоит ЗДЕСЬ, а не наверху файла:
    # ядро не должно тащить базу при старте.
    #
    # Место врезки выбрано не случайно — после проверки потолка. Если потолок
    # исчерпан, вызова не будет, а промпт без ответа отравил бы
    # воспроизведение: оно ищет пары «промпт → ответ» и на такой паре
    # молча вернуло бы не то.
    #
    # Сутки поставщика берутся ИЗ ТАЛОНА учёта, а не считаются заново. Тогда
    # дата у записи и у строки расхода не может разойтись, а знание о том, как
    # эти сутки считаются, остаётся ровно в одном месте проекта.
    from core import blackbox
    rec = blackbox.open_rec(ctx=ctx, day=ticket["quota_day"])
    blackbox.write(rec, "prompt", {"t": prompt})

    attempts, pause = _retry_plan()
    last_error = ""

    for attempt in range(1, attempts + 1):
        try:
            response = _generate(model, _build_contents(prompt, image_parts), api_key)
            if attempt > 1:
                _out(f"[AuxModel] ✅ {caller}: повтор удался с {attempt}-го захода")
            text = (response.text or "").strip()
            _bb_answer(rec, True, text)
            metering.commit(call_id, in_tokens=len(prompt or ""),
                            out_tokens=len(text), ok=True)
            return True, text

        except Exception as e:
            # 429 — это исчерпание, а не сбой: повтор только сожжёт квоту.
            if guard.handle_exception(e, model):
                rem = guard.cooldown_remaining(model)
                _out(f"[AuxModel] 🚫 {caller} hit 429 — cooldown {rem:.0f}s")
                _bb_answer(rec, False, err_kind="rpd")
                metering.commit(call_id, ok=False, err_kind="rpd")
                return False, f"[quota-429:cooldown {rem:.0f}s]"

            last_error = str(e)
            if _is_transient(e) and attempt < attempts:
                import time as _time
                _out(f"[AuxModel] ⏳ {caller}: временный отказ [{attempt}/{attempts}] — "
                      f"{_short(last_error)}; повтор через {pause:g}с")
                _time.sleep(pause)
                # Ответа в запись НЕ пишем: вызов ещё не кончился, и на одну
                # пару «промпт → ответ» ответ обязан быть один.
                continue

            # Главное правило шага: ни одна неудача не уходит молча.
            _out(f"[AuxModel] ⚠️ {caller}: вызов не удался — {_short(last_error)}")
            _bb_answer(rec, False, err_kind="other")
            metering.commit(call_id, ok=False, err_kind="other")
            return False, f"[error:{e}]"

    _out(f"[AuxModel] ⚠️ {caller}: вызов не удался — {_short(last_error)}")
    _bb_answer(rec, False, err_kind="other")
    metering.commit(call_id, ok=False, err_kind="other")
    return False, f"[error:{last_error}]"


_lost_swept = False


def _sweep_lost_once() -> None:
    """Один раз за запуск закрыть резервы, брошенные ПРОШЛЫМ запуском.

    Один раз, а не на каждом вызове, и это не мелочь: первая версия чистила
    на каждом резерве и убивала ЖИВОЙ резерв соседнего потока — он
    помечался «потерян», а потом считался второй раз (найдено порчей кода
    18.08.2026: счётчик врал вдвое на двух задачах).
    """
    global _lost_swept
    if _lost_swept:
        return
    _lost_swept = True
    try:
        from core import metering
        metering.close_lost(printer=print)
    except Exception:
        pass


def reset_sweep_for_tests() -> None:
    global _lost_swept
    _lost_swept = False


CHEAP_ROLE = "aux_cheap"


def cheap_model() -> str:
    """Модель самой дешёвой роли — у неё отдельный дневной счётчик."""
    from config.loader import get_model
    # Роль строкой-литералом нарочно: так сторож реестра видит её грепом
    # и падает, если роль исчезнет из config/registry.yaml.
    return get_model("aux_cheap")


def cheap_call(
    prompt: str,
    api_key: str,
    caller: str = "unknown",
) -> Tuple[bool, str]:
    """Дешёвая роль для коротких технических вопросов в одно слово.

    От aux_call отличий ровно два: модель берётся из роли aux_cheap, и вход
    режется ДО отправки по пределу этой роли из config/registry.yaml.
    Обрезка живёт здесь, а не у вызывающего: предел на объём текста в
    минуту — свойство модели, а его превышение даёт ошибку запроса, а не
    задержку. Само число в коде не хранится: код знает только имя предела.
    """
    from config.loader import get_limit
    model = cheap_model()
    text = prompt or ""
    try:
        limit = int(get_limit(CHEAP_ROLE, "max_input_chars", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if 0 < limit < len(text):
        _out(f"[AuxModel] ✂️ {caller}: вход обрезан до {limit} знаков "
              f"(снято {len(text) - limit})")
        text = text[:limit]
    return aux_call(text, api_key, model=model, caller=caller)


def aux_is_quota_error(reason: str) -> bool:
    """Return True if the failure reason string indicates a quota/429 issue."""
    return reason.startswith("[quota-")


def aux_cooldown_seconds(reason: str) -> float:
    """Parse cooldown duration from a reason string, or return 0.0."""
    import re
    m = re.search(r":(\d+(?:\.\d+)?)s]", reason)
    return float(m.group(1)) if m else 0.0
