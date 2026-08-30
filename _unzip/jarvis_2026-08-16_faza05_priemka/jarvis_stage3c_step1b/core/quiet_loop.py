# -*- coding: utf-8 -*-
"""
Глушитель одного известного шума цикла событий.

Живой случай, с которого начался файл: без интернета каждая попытка
переподключения валила в окно двадцать строк трассировки:

    Exception in callback Connection.connection_lost(ConnectionResetError())
    ...
    File ".../websockets/asyncio/connection.py", line 1029, in connection_lost
        self.recv_messages.close()
    AttributeError: 'ClientConnection' object has no attribute 'recv_messages'

Это не наш код. Соединение оборвалось раньше, чем библиотека websockets
достроила свой объект, и при уборке она зовёт собственное поле, которого ещё
нет. Наш try/except вокруг сессии такое не ловит принципиально: исключение
рождается в колбеке цикла событий, а не внутри нашего await. Единственная
дверь к нему — свой обработчик исключений цикла.

Правило файла: гасится РОВНО одна подпись, по трём признакам сразу. Любая
другая беда уходит дальше: прежнему обработчику, если он был, иначе
стандартному. Глушитель, который глотает всё подряд, опаснее шума:
настоящая поломка тогда исчезает молча.

Строка замены нарочно чисто латинская: чёрное окно Windows у владельца
показывает кракозябры даже на длинном тире. Человеческий текст про связь
идёт в окно Джарвиса, где шрифт честный, а не сюда.

Ни сети, ни модели, ни тяжёлых ввозов: модуль обязан работать там, где socket
заминирован.

Проверки: python -m pytest -q  или  python tests/test_quiet_loop.py
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

# Три признака подписи. По отдельности каждый встречается и в настоящих
# поломках, все трое вместе — только в этой одной.
_KNOWN_ATTRIBUTE = "recv_messages"      # какого поля не хватило
_KNOWN_CALLBACK = "connection_lost"     # кто его спросил

# Одна строка вместо двадцати. Только ASCII и без переводов строки.
NOISE_LINE = ("[Link] connection dropped before handshake finished; "
              "websockets cleanup noise (recv_messages) suppressed")

# Метка на цикле: повторный вызов install() не наслаивает обработчики
# друг на друга: цепочка из семи глушителей — тоже поломка.
_MARK = "_jarvis_quiet_loop"


def is_known_noise(context: Dict[str, Any]) -> bool:
    """Опознать ровно ту одну беду, ради которой существует файл."""
    if not isinstance(context, dict):
        return False
    exc = context.get("exception")
    if not isinstance(exc, AttributeError):
        return False
    if _KNOWN_ATTRIBUTE not in str(exc):
        return False
    where = "%s %s" % (context.get("message", ""), context.get("handle", ""))
    return _KNOWN_CALLBACK in where


def make_handler(printer: Callable[[str], None] = print,
                 previous: Optional[Callable[[Any, Dict[str, Any]], None]] = None):
    """
    Собрать обработчик. Отдельно от install() ради проверки: так его можно
    позвать руками и увидеть, что он делает, без живого цикла событий.
    """

    def handler(handler_loop: Any, context: Dict[str, Any]) -> None:
        try:
            known = is_known_noise(context)
        except Exception:      # noqa: BLE001 — глушитель не имеет права падать
            known = False
        if known:
            printer(NOISE_LINE)
            return
        if previous is not None:
            previous(handler_loop, context)
            return
        handler_loop.default_exception_handler(context)

    return handler


def install(loop: Any, printer: Callable[[str], None] = print) -> bool:
    """
    Поставить глушитель на цикл событий.

    True  — поставлен сейчас;
    False — уже стоял, повторно не ставим.

    Прежний обработчик, если он был, остаётся в цепочке: мы забираем себе
    одну подпись и больше ничего чужого не трогаем.
    """
    if getattr(loop, _MARK, False):
        return False

    previous = None
    getter = getattr(loop, "get_exception_handler", None)
    if callable(getter):
        previous = getter()

    loop.set_exception_handler(make_handler(printer=printer, previous=previous))

    try:
        setattr(loop, _MARK, True)
    except Exception:      # noqa: BLE001 — чужой цикл может запрещать атрибуты
        pass
    return True
