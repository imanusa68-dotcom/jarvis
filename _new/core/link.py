# -*- coding: utf-8 -*-
"""
Одна дверь к вопросу «есть ли сейчас связь».

Зачем. Живой прогон без интернета показал семь попыток подряд, и каждая
заново собирала промпт памяти, печатала уборку и рвала соединение внутри
чужой библиотеки. Дешёвый вопрос «а сеть-то есть?» стоит копейки и снимает
всю эту работу разом.

Главное правило файла: ответов три, а не два.

    yes     — соединение состоялось;
    no      — сеть явно отказала (OSError);
    unknown — всё остальное: нет адреса в реестре, странная ошибка, подменён
                socket в тестах.

«unknown» равносильно «yes»: сомнение трактуется в пользу попытки. Ложное
«сети нет» при живом интернете — худший исход из всех возможных: он запрёт
Джарвиса в оффлайне без видимой причины.

Адрес живёт в config/registry.yaml рядом с остальными знаниями о поставщике
и читается той же дверью config.loader.get_limit, что и срок ожидания
клиента. В коде адреса нет намеренно: через месяц его никто там не найдёт.
Нет адреса — ответ «unknown», и поведение возвращается к прежнему.

Модуль ничего не печатает (правило «один рот») и при ввозе не трогает сеть:
socket ввозится внутри вызова, иначе он не прошёл бы тесты с заминированной
сетью. В проверках подменяется один довод connector, а не весь модуль.

Проверки: python -m pytest -q  или  python tests/test_link_and_quiet_offline.py
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

ALIVE = "yes"
DOWN = "no"
UNKNOWN = "unknown"

# Роль та же, что у срока ожидания клиента: это знание о поставщике.
_ROLE = "provider"
_HOST_LIMIT = "probe_host"
_PORT_LIMIT = "probe_port"
_TIMEOUT_LIMIT = "probe_timeout_seconds"

# Если срок в реестре кривой — берём этот. Долгая проверка хуже отсутствующей:
# она задержит возвращение к живой сессии.
_FALLBACK_TIMEOUT = 1.5
_MAX_TIMEOUT = 5.0


def address() -> Tuple[Optional[str], Optional[int], float]:
    """(хост, порт, срок) из реестра. Кривые значения — это отсутствие адреса."""
    try:
        from config.loader import get_limit
        raw_host = get_limit(_ROLE, _HOST_LIMIT, None)
        raw_port = get_limit(_ROLE, _PORT_LIMIT, None)
        raw_timeout = get_limit(_ROLE, _TIMEOUT_LIMIT, None)
    except Exception:      # noqa: BLE001 — нет реестра, нет и проверки
        return (None, None, _FALLBACK_TIMEOUT)

    host = raw_host if isinstance(raw_host, str) and raw_host.strip() else None

    port = None
    if isinstance(raw_port, int) and not isinstance(raw_port, bool):
        port = raw_port if 0 < raw_port < 65536 else None

    timeout = _FALLBACK_TIMEOUT
    if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool):
        if 0 < float(raw_timeout) <= _MAX_TIMEOUT:
            timeout = float(raw_timeout)

    return (host, port, timeout)


def probe(connector: Optional[Callable[..., Any]] = None) -> str:
    """
    Одно короткое соединение с домом поставщика. Ничего не отправляет
    и ничего не читает: вопрос только один — пускают ли нас наружу.

    Проверяется именно соединение, а не имя: бывает, что DNS запрещён,
    а сам 443-й порт работает, и наоборот.
    """
    host, port, timeout = address()
    if not host or not port:
        return UNKNOWN

    if connector is None:
        try:
            import socket as _socket
        except Exception:      # noqa: BLE001
            return UNKNOWN
        connector = _socket.create_connection

    try:
        sock = connector((host, port), timeout)
    except OSError:
        # Сеть ответила отказом: нет маршрута, нет имени, не успели.
        return DOWN
    except Exception:      # noqa: BLE001 — что-то совсем странное
        return UNKNOWN

    try:
        sock.close()
    except Exception:      # noqa: BLE001
        pass
    return ALIVE


def says_no(connector: Optional[Callable[..., Any]] = None) -> bool:
    """Единственный случай, когда сессию поднимать не стоит."""
    return probe(connector=connector) == DOWN
