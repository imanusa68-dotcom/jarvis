# core/provider/gemini.py
# MARK XXXVI - edinstvennoe mesto vspomogatelnogo puti, gde zhivet SDK.
# ---------------------------------------------------------------------------
# Договор и запреты описаны в core/provider/base.py - читать его первым.
#
# Две вещи, которые легко сломать незаметно:
#
#   1. ПОРЯДОК ЧАСТЕЙ. Сначала картинки, потом текст. Именно так было в
#      core/aux_model.py до переезда, и на такой порядок настроены вопросы
#      зрения в actions/computer_control.py. Переставишь местами - ошибки не
#      будет, будет тихое ухудшение ответов по картинке.
#
#   2. ПУСТОЙ ОТВЕТ. Поле text бывает None: ответ забраковала защита либо
#      модель вернула одни вызовы инструментов. Отдаём "", точно как до
#      переезда. Честно: вызывающие примут пустоту за неудачу и причину не
#      назовут - старый долг, он не чинится здесь нарочно.
#
# ИМПОРТ SDK ТОЛЬКО ВНУТРИ ФУНКЦИЙ. Без этого любой тест, ввозящий
# core.aux_model, потребует установленной зависимости, а фаза 0.7 требует, чтобы
# ядро поднималось без сети. Один ленивый импорт в одном файле делает это
# проверяемым одной строкой.
#
# ЗДЕСЬ НЕТ НИ ОДНОЙ ПЕЧАТИ. Рот у проекта один - главный агент; все слова
# про неудачи говорит core/aux_model.py. Состояние слоя узнаётся опросом
# timeout_status(), и его печатает сторож check_lang.py, а не этот файл.
#
# ---------------------------------------------------------------------------
# КЛИЕНТ ЖИВЁТ МЕЖДУ ВЫЗОВАМИ (шаг Б, 10.08.2026)
#
# До этого шага клиент строился заново на каждый вызов - каждый раз новый
# пул соединений, а значит новое рукопожатие TLS на живой машине. Теперь
# клиент лежит в кэше и переиспользуется.
#
# Правила кэша, каждое написано против конкретной беды:
#
#   * В КЭШЕ РОВНО ОДИН КЛИЕНТ. Владелец вводит ключ заново после каждой
#     распаковки. Если бы кэш рос по ключам, новый ключ добавился бы рядом со
#     старым, и попасть можно было бы в любой. Другой ключ вытесняет прежнего.
#
#   * КЛЮЧ В КЭШЕ НЕ ЛЕЖИТ. Хранится только отпечаток (sha256): его достаточно,
#     чтобы заметить смену ключа, и он бесполезен в чужих руках. Сам ключ и так
#     живёт в памяти процесса, но в кэш, трассировки и печать он не попадает.
#
#   * СТРОИМ ПОД ЗАМКОМ. Рабочие агенты - это потоки (запрет №5 владельца), они
#     ходят сюда одновременно. Без замка два потока построили бы двух клиентов,
#     и один был бы молча выброшен. Постройка клиента в сеть не ходит, поэтому
#     держать замок на это время дёшево.
#
#   * ЛЮБОЙ ОТКАЗ ЗАПРОСА СБРАСЫВАЕТ КЭШ. Повтор после 503 обязан идти свежим
#     клиентом: старый мог остаться с закрытой сессией внутри. Исключение при
#     этом уходит наверх нетронутым - ловить его здесь запрещено договором.
#
# СРОК ОЖИДАНИЯ ОДНОГО ЗАПРОСА берётся строкой из config/registry.yaml
# (роль provider, предел timeout_seconds). Числа здесь нет намеренно: предел
# принадлежит поставщику и меняется там же, где имена моделей. Нет строки в
# реестре - нет срока, поведение ровно прежнее. Если SDK срок не примет,
# клиент строится без него, а факт отказа виден через timeout_status().
#
# НЕ ПРОВЕРЕНО и говорится вслух: живого ключа этот файл не видел ни разу.
# В песочнице нет ни сети, ни SDK - кэш, срок ожидания и откат проверены
# поддельным SDK. Держит ли сам SDK этот срок для обычного (не потокового)
# запроса - вопрос к его версии, а не к этому коду.
# ---------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import threading

from core.provider.base import ImageParts, Provider

# Роль и предел в config/registry.yaml. Не имя модели - имя поставщика.
TIMEOUT_ROLE = "provider"
TIMEOUT_LIMIT = "timeout_seconds"
_MS_IN_SECOND = 1000

_LOCK = threading.Lock()
_CACHE: dict = {}          # {"mark": (отпечаток ключа, срок), "client": клиент}
_TIMEOUT_REFUSED = False   # SDK не принял срок; спрашивается timeout_status()
_TIMEOUT_REASON = ""


def _timeout_ms():
    """Срок ожидания в миллисекундах или None. Кривое значение - это None."""
    try:
        from config.loader import get_limit
        raw = get_limit(TIMEOUT_ROLE, TIMEOUT_LIMIT, None)
    except Exception:
        return None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return int(seconds * _MS_IN_SECOND)


def _fingerprint(api_key) -> str:
    """Отпечаток ключа. В кэше лежит он, а не ключ."""
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()


def _build_client(api_key: str, timeout_ms):
    """Единственная постройка клиента во всём вспомогательном пути."""
    global _TIMEOUT_REFUSED, _TIMEOUT_REASON
    from google import genai as _genai
    if timeout_ms is None:
        return _genai.Client(api_key=api_key)
    try:
        return _genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})
    except Exception as exc:
        _TIMEOUT_REFUSED = True
        _TIMEOUT_REASON = str(exc)
        return _genai.Client(api_key=api_key)


def timeout_status():
    """(срок в миллисекундах или None, отказался ли SDK, причина отказа).

    Слой молчит сам: произносит это сторож, а не поставщик.
    """
    return (_timeout_ms(), _TIMEOUT_REFUSED, _TIMEOUT_REASON)


def _client_for(api_key: str):
    """Клиент из кэша; если ключ или срок сменились - новый, под замком."""
    mark = (_fingerprint(api_key), _timeout_ms())
    with _LOCK:
        if _CACHE.get("mark") == mark and _CACHE.get("client") is not None:
            return _CACHE["client"]
        client = _build_client(api_key, mark[1])
        _CACHE["mark"] = mark
        _CACHE["client"] = client
        return client


def reset_clients() -> None:
    """Забыть клиента. Зовётся при любом отказе запроса и из проверок."""
    with _LOCK:
        _CACHE.clear()


class GeminiProvider(Provider):
    """Поставщик на новом SDK (пакет google.genai, зависимость google-genai).

    Имя модели сюда не зашито ни одним словом: его передают из роли
    config/registry.yaml через core/aux_model.py.
    """

    name = "google-genai"

    def build_payload(self, prompt: str, image_parts: ImageParts = None):
        """Без картинки - сама строка; с картинкой - список частей."""
        if not image_parts:
            return prompt
        from google.genai import types as _gtypes
        payload: list = []
        for data, mime_type in image_parts:
            payload.append(_gtypes.Part.from_bytes(data=data, mime_type=mime_type))
        payload.append(_gtypes.Part.from_text(text=prompt))
        return payload

    def generate(self, model: str, payload, api_key: str) -> str:
        """Отправить и вернуть текст. Исключение уходит наверх как есть."""
        client = _client_for(api_key)
        try:
            response = client.models.generate_content(model=model, contents=payload)
        except Exception:
            reset_clients()
            raise
        return response.text or ""
