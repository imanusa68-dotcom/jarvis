# -*- coding: utf-8 -*-
"""Шаг 3 фазы 0.7, заход первый — тишина вместо чужой трассировки.

Живой прогон без интернета показал: на каждую из семи попыток в окно
падало двадцать строк трассировки из чужой библиотеки websockets
(connection.py:1029, self.recv_messages.close()). Наш try/except её не ловит:
исключение рождается в колбеке цикла событий.

Здесь сторожится главная опасность глушителя: что он со временем начнёт
глотать настоящие поломки. Поэтому большая часть проверок — про то, что
он НЕ глушит.

Главный файл здесь НЕ ввозится: import main тянет pyaudio, tkinter и SDK.
Факт врезки в цикл судится по AST живого исходника, а не по пересказу.

Runner-style (pytest-free): module-level test_* + хвост в __main__.
"""
import ast
import importlib
import socket
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
SCREEN_SRC = (ROOT / "core/screen_live_runtime.py").read_text(encoding="utf-8")
QUIET_SRC = (ROOT / "core/quiet_loop.py").read_text(encoding="utf-8")


# ───── стенд ─────

class _Loop:
    """Двойник цикла событий: помнит обработчик и что ушло в стандартный."""

    def __init__(self):
        self.handler = None
        self.defaults = []

    def get_exception_handler(self):
        return self.handler

    def set_exception_handler(self, fn):
        self.handler = fn

    def default_exception_handler(self, context):
        self.defaults.append(context)


class _Handle:
    """То, что asyncio кладёт в context['handle'] — важен только его текст."""

    def __init__(self, text):
        self._text = text

    def __str__(self):
        return self._text


def _noise():
    """Слепок живого происшествия с машины владельца (11.08.2026)."""
    return {
        "message": "Exception in callback Connection.connection_lost(ConnectionResetError())",
        "exception": AttributeError(
            "'ClientConnection' object has no attribute 'recv_messages'"),
        "handle": _Handle(
            "<Handle Connection.connection_lost(ConnectionResetError())>"),
    }


def _catcher():
    lines = []
    return lines, lines.append


def _find_function(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _mentions_quiet_loop(node):
    for inner in ast.walk(node):
        if isinstance(inner, ast.ImportFrom) and (inner.module or "").endswith("quiet_loop"):
            return True
    return False


# ═════ 1. ГЛУШИТ СВОЁ ═════

def test_the_known_noise_is_replaced_by_one_line():
    from core.quiet_loop import make_handler, NOISE_LINE
    loop = _Loop()
    lines, printer = _catcher()
    make_handler(printer=printer)(loop, _noise())
    assert lines == [NOISE_LINE], "известный шум не заменён строкой: %r" % (lines,)
    assert loop.defaults == [], "известный шум всё равно ушёл в стандартный обработчик"


def test_the_replacement_line_is_one_line_and_ascii():
    """Консоль Windows у владельца ломает даже длинное тире."""
    from core.quiet_loop import NOISE_LINE
    assert "\n" not in NOISE_LINE, "строка замены многострочная"
    assert NOISE_LINE.isascii(), "в строке замены есть не-ASCII: %r" % (NOISE_LINE,)
    assert "recv_messages" in NOISE_LINE, "строка не называет причину"


# ═════ 2. ЧУЖОГО НЕ ГЛУШИТ ═════

def test_a_stranger_error_reaches_the_default_handler():
    from core.quiet_loop import make_handler
    loop = _Loop()
    lines, printer = _catcher()
    context = {"message": "Task exception was never retrieved",
               "exception": ValueError("что-то настоящее сломалось")}
    make_handler(printer=printer)(loop, context)
    assert loop.defaults == [context], "чужая ошибка не дошла до стандартного обработчика"
    assert lines == [], "чужая ошибка подменена нашей строкой: %r" % (lines,)


def test_attribute_error_without_the_needle_is_not_swallowed():
    from core.quiet_loop import make_handler
    loop = _Loop()
    lines, printer = _catcher()
    context = {"message": "Exception in callback Connection.connection_lost()",
               "exception": AttributeError("'Session' object has no attribute 'send'")}
    make_handler(printer=printer)(loop, context)
    assert loop.defaults == [context], "погасили чужую нехватку атрибута"
    assert lines == []


def test_the_needle_without_attribute_error_is_not_swallowed():
    from core.quiet_loop import make_handler
    loop = _Loop()
    lines, printer = _catcher()
    context = {"message": "Exception in callback Connection.connection_lost()",
               "exception": RuntimeError("recv_messages exploded for real")}
    make_handler(printer=printer)(loop, context)
    assert loop.defaults == [context], "погасили настоящую поломку по одному слову"
    assert lines == []


def test_the_needle_from_another_callback_is_not_swallowed():
    from core.quiet_loop import make_handler
    loop = _Loop()
    lines, printer = _catcher()
    context = {"message": "Exception in callback Session.reader()",
               "exception": AttributeError("'X' object has no attribute 'recv_messages'"),
               "handle": _Handle("<Handle Session.reader()>")}
    make_handler(printer=printer)(loop, context)
    assert loop.defaults == [context], "погасили беду из другого места"
    assert lines == []


def test_a_broken_context_does_not_crash_the_handler():
    """Глушитель не имеет права упасть сам: он последний в очереди."""
    from core.quiet_loop import make_handler
    loop = _Loop()
    lines, printer = _catcher()
    make_handler(printer=printer)(loop, {})
    assert loop.defaults == [{}], "пустое происшествие потерялось"
    assert lines == []


# ═════ 3. КАК СТАВИТСЯ ═════

def test_install_puts_the_handler_on_the_loop():
    from core.quiet_loop import install
    loop = _Loop()
    assert install(loop) is True, "install не сообщил об установке"
    assert callable(loop.handler), "обработчик не встал на цикл"


def test_install_keeps_the_previous_handler_in_the_chain():
    from core.quiet_loop import install
    loop = _Loop()
    seen = []
    loop.set_exception_handler(lambda lp, ctx: seen.append(ctx))
    install(loop)
    context = {"message": "boom", "exception": ValueError("x")}
    loop.handler(loop, context)
    assert seen == [context], "прежний обработчик потерян: %r" % (seen,)
    assert loop.defaults == [], "при живом предшественнике ушло в стандартный"


def test_install_twice_does_not_stack():
    from core.quiet_loop import install
    loop = _Loop()
    install(loop)
    first = loop.handler
    assert install(loop) is False, "повторная установка сочла себя первой"
    assert loop.handler is first, "обработчик наслоился на самого себя"


def test_install_survives_a_loop_without_a_getter():
    """Чужой цикл может не уметь отдавать прежний обработчик."""
    from core.quiet_loop import install

    class _Bare:
        def __init__(self):
            self.handler = None

        def set_exception_handler(self, fn):
            self.handler = fn

        def default_exception_handler(self, context):
            pass

    bare = _Bare()
    assert install(bare) is True
    assert callable(bare.handler)


# ═════ 4. ГЛУШИТЕЛЬ ДЕЙСТВИТЕЛЬНО СТОИТ НА ЖИВЫХ ЦИКЛАХ ═════

def test_the_main_runtime_installs_the_silencer():
    node = _find_function(MAIN_SRC, "run")
    assert node is not None, "в main.py не нашёлся run()"
    assert _mentions_quiet_loop(node), "главный цикл не ставит глушитель"


def test_the_screen_runtime_installs_the_silencer():
    node = _find_function(SCREEN_SRC, "_run_event_loop")
    assert node is not None, "в screen_live_runtime.py не нашёлся _run_event_loop()"
    assert _mentions_quiet_loop(node), \
        "второй цикл событий остался шумным: глушитель туда не поставлен"


# ═════ 5. НИ СЕТИ, НИ ТЯЖЕСТИ ═════

def test_the_silencer_is_imported_with_the_network_mined():
    mines = ("socket", "create_connection", "getaddrinfo")
    saved = {name: getattr(socket, name) for name in mines}
    original = sys.modules.get("core.quiet_loop")

    def boom(*a, **k):
        raise AssertionError("глушитель полез в сеть")

    try:
        for name in mines:
            setattr(socket, name, boom)
        sys.modules.pop("core.quiet_loop", None)
        fresh = importlib.import_module("core.quiet_loop")
        assert callable(fresh.install)
    finally:
        for name, value in saved.items():
            setattr(socket, name, value)
        if original is not None:
            sys.modules["core.quiet_loop"] = original


def test_the_silencer_stays_light():
    for needle in ("import main", "genai", "requests", "urllib", "import socket",
                   "aux_call", "import asyncio"):
        assert needle not in QUIET_SRC, "в глушителе появилась лишняя зависимость: %s" % needle


if __name__ == "__main__":
    names = sorted(n for n in globals() if n.startswith("test_"))
    passed = 0
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print("  FAIL  %s -- %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  ERROR %s -- %s: %s" % (name, type(exc).__name__, exc))
        else:
            passed += 1
            print("  PASS  %s" % name)
    total = passed + failed
    print("RESULT: %d/%d %s" % (passed, total, "ALL PASS" if failed == 0 else "SOME FAILED"))
    sys.exit(0 if failed == 0 else 1)
