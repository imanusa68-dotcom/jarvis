# -*- coding: utf-8 -*-
"""Шаг 3 фазы 0.7, заход второй — без сети не долбим и не врём.

Живой прогон 11.08.2026 без интернета: семь попыток подряд, каждая заново
собирала промпт памяти (9 facts, 718 chars), печатала уборку и кончалась
трассировкой из чужой библиотеки, а в конце трёхминутная пауза.

Здесь сторожатся три вещи:
  1. Дверь связи (core/link.py) отвечает тремя словами, а не двумя, и берёт
     адрес из реестра, а не из кода.
  2. Цикл не поднимает сессию, когда связи нет, но первая попытка идёт
     всегда, а раз в минуту пробует вслепую: проверка тоже умеет врать.
  3. Окно показывает OFFLINE, а не ONLINE. До этого шага любое незнакомое
     состояние молча превращалось в ONLINE — то есть окно врало бы.

main.py и ui.py здесь НЕ ввозятся: они тянут pyaudio, tkinter и SDK. Методы
вырезаются из живого исходника по AST и исполняются над двойником — так же,
как в тесте шага 24. Судится код, а не пересказ кода.

Runner-style (pytest-free): module-level test_* + хвост в __main__.
"""
import ast
import importlib
import socket
import sys
import types
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
UI_SRC = (ROOT / "ui.py").read_text(encoding="utf-8")
LINK_SRC = (ROOT / "core/link.py").read_text(encoding="utf-8")


# ───── стенд: вырезка методов из живого файла ─────

def _cut(source, names, space=None):
    """Вырезать методы по именам и вернуть их готовыми функциями."""
    found = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            found[node.name] = node
    missing = [n for n in names if n not in found]
    assert not missing, "в исходнике не нашлись методы: %s" % (missing,)
    module = ast.Module(body=[found[n] for n in names], type_ignores=[])
    ast.fix_missing_locations(module)
    bag = {"print": lambda *a, **k: None}
    bag.update(space or {})
    exec(compile(module, "cut", "exec"), bag)
    return bag


def _find(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


class _Sock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Recorder:
    """Подмена socket.create_connection: запоминает, куда стучались."""

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or _Sock()

    def __call__(self, target, timeout=None):
        self.calls.append((target, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


# ═════ 1. ДВЕРЬ СВЯЗИ: ТРИ ОТВЕТА, А НЕ ДВА ═════

def test_the_probe_says_yes_when_the_socket_opens():
    from core.link import probe, ALIVE
    rec = _Recorder()
    assert probe(connector=rec) == ALIVE
    assert rec.outcome.closed, "проверка оставила соединение открытым"


def test_the_probe_says_no_only_on_a_network_refusal():
    from core.link import probe, DOWN
    assert probe(connector=_Recorder(OSError("getaddrinfo failed"))) == DOWN


def test_a_strange_failure_is_unknown_not_no():
    """Сомнение трактуется в пользу попытки, иначе запрём себя в оффлайне."""
    from core.link import probe, UNKNOWN
    assert probe(connector=_Recorder(RuntimeError("что-то совсем странное"))) == UNKNOWN


def test_says_no_is_true_only_for_a_refusal():
    from core.link import says_no
    assert says_no(connector=_Recorder(OSError("no route"))) is True
    assert says_no(connector=_Recorder()) is False
    assert says_no(connector=_Recorder(RuntimeError("?"))) is False


def test_the_address_comes_from_the_registry_not_from_the_code():
    from core.link import address, probe
    host, port, timeout = address()
    assert host and port, "адрес проверки пропал из config/registry.yaml"
    assert 0 < timeout <= 5, "срок проверки вне разумных границ: %r" % (timeout,)
    rec = _Recorder()
    probe(connector=rec)
    assert rec.calls == [((host, port), timeout)], \
        "проверка стучалась не туда, что в реестре: %r" % (rec.calls,)
    assert host not in LINK_SRC, "адрес поставщика зашит в код — ему место в реестре"


def test_the_registry_keeps_the_probe_address():
    from config.loader import get_limit
    assert isinstance(get_limit("provider", "probe_host", None), str)
    assert isinstance(get_limit("provider", "probe_port", None), int)
    assert get_limit("provider", "timeout_seconds", None) == 60, \
        "старый срок ожидания клиента потерялся при врезке"


def test_the_door_is_imported_with_the_network_mined():
    mines = ("socket", "create_connection", "getaddrinfo")
    saved = {name: getattr(socket, name) for name in mines}
    original = sys.modules.get("core.link")

    def boom(*a, **k):
        raise AssertionError("дверь связи полезла в сеть при ввозе")

    try:
        for name in mines:
            setattr(socket, name, boom)
        sys.modules.pop("core.link", None)
        fresh = importlib.import_module("core.link")
        assert callable(fresh.probe)
        # И сам вызов при заминированной сети не имеет права солгать «связь есть».
        assert fresh.probe() != fresh.ALIVE
    finally:
        for name, value in saved.items():
            setattr(socket, name, value)
        if original is not None:
            sys.modules["core.link"] = original


def test_the_door_imports_socket_lazily_and_stays_silent():
    for node in ast.parse(LINK_SRC).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, "names", [])]
            module = getattr(node, "module", "") or ""
            assert "socket" not in module and not any("socket" in n for n in names), \
                "socket ввозится на верхнем уровне — тесты с минами этого не простят"
    assert "print(" not in LINK_SRC, "у двери связи появился свой рот"


# ═════ 2. ЦИКЛ: БЕЗ СЕТИ СЕССИЯ НЕ ПОДНИМАЕТСЯ ═════

def _fake_link_module(answer):
    module = types.ModuleType("core.link")
    module.says_no = lambda *a, **k: answer
    return module


def test_the_owner_method_reports_no_network():
    bag = _cut(MAIN_SRC, ["_link_says_no"])
    saved = sys.modules.get("core.link")
    try:
        sys.modules["core.link"] = _fake_link_module(True)
        assert bag["_link_says_no"](object()) is True
        sys.modules["core.link"] = _fake_link_module(False)
        assert bag["_link_says_no"](object()) is False
    finally:
        if saved is not None:
            sys.modules["core.link"] = saved
        else:
            sys.modules.pop("core.link", None)


def test_a_broken_probe_means_try_anyway():
    """Если сама проверка сломалась — это не повод уйти в оффлайн."""
    bag = _cut(MAIN_SRC, ["_link_says_no"])
    saved = sys.modules.get("core.link")
    try:
        sys.modules["core.link"] = types.ModuleType("core.link")   # без says_no
        assert bag["_link_says_no"](object()) is False
    finally:
        if saved is not None:
            sys.modules["core.link"] = saved
        else:
            sys.modules.pop("core.link", None)


def test_the_link_is_checked_before_the_session_starts():
    node = _find(MAIN_SRC, "run")
    assert node is not None
    checks = [n.lineno for n in ast.walk(node)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "_link_says_no"]
    sessions = [n.lineno for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_run_session"]
    assert checks, "цикл вообще не спрашивает про связь"
    assert sessions, "в цикле пропал вызов сессии"
    assert min(checks) < min(sessions), \
        "проверка связи стоит после сессии — память снова собирается впустую"


def test_the_offline_branch_waits_and_never_opens_a_session():
    node = _find(MAIN_SRC, "run")
    branch = None
    for inner in ast.walk(node):
        if isinstance(inner, ast.If) and any(isinstance(x, ast.Continue)
                                             for x in ast.walk(inner)):
            branch = inner
            break
    assert branch is not None, "в цикле нет ветки ожидания связи"
    text = ast.dump(branch)
    assert "_run_session" not in text, "без сети всё равно поднимается сессия"
    assert "_build_config" not in text, "без сети всё равно собирается память"
    assert "sleep" in text, "ветка ожидания крутится без паузы — это съест процессор"
    assert "LINK_POLL_SECONDS" in text, "срок ожидания зашит числом вместо имени"


def test_the_first_attempt_never_waits_for_the_probe():
    """Холодный старт не должен зависеть от исправности проверки."""
    node = _find(MAIN_SRC, "run")
    text = ast.dump(node)
    assert "first_attempt" in text, "признак первой попытки пропал"
    assert "blind_countdown" in text, "слепая попытка пропала: проверка стала приговором"
    assert "LINK_BLIND_TRY_EVERY" in text


def test_the_owner_hears_about_the_link_with_a_sys_prefix():
    """Строка с Jarvis: перевела бы окно в SPEAKING, хотя никто не говорит."""
    node = _find(MAIN_SRC, "run")
    said = [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and ("Сети нет" in n.value or "Связь вернулась" in n.value)]
    assert len(said) >= 2, "владелец не узнаёт ни об обрыве, ни о возврате связи"
    for line in said:
        assert line.startswith("SYS:"), "статус связи притворяется речью: %r" % (line,)


# ═════ 3. ОКНО НЕ ВРЁТ ═════

class _Win:
    def __init__(self, state="LISTENING"):
        self.typing_queue = []
        self.is_typing = True
        self.speaking = False
        self.muted = False
        self.status_text = ""
        self._jarvis_state = state
        self.asked = []

    def _set_state_impl(self, state):
        self.asked.append(state)


def test_the_window_shows_offline_instead_of_lying_online():
    bag = _cut(UI_SRC, ["_set_state_impl"])
    win = _Win()
    bag["_set_state_impl"](win, "OFFLINE")
    assert win.status_text == "OFFLINE", \
        "окно показало %r вместо OFFLINE" % (win.status_text,)
    assert win.speaking is False


def test_an_unknown_state_still_falls_back_to_online():
    """Старое поведение для всего остального обязано сохраниться."""
    bag = _cut(UI_SRC, ["_set_state_impl"])
    win = _Win()
    bag["_set_state_impl"](win, "ЧТО-ТО НОВОЕ")
    assert win.status_text == "ONLINE"


def test_offline_survives_the_end_of_typing():
    bag = _cut(UI_SRC, ["_start_typing"])
    win = _Win(state="OFFLINE")
    bag["_start_typing"](win)
    assert win.asked == [], \
        "после печати окно сбросило OFFLINE в %r" % (win.asked,)
    assert win.is_typing is False


def test_the_usual_state_still_returns_to_listening():
    bag = _cut(UI_SRC, ["_start_typing"])
    win = _Win(state="SPEAKING")
    bag["_start_typing"](win)
    assert win.asked == ["LISTENING"], \
        "обычное возвращение в LISTENING сломано: %r" % (win.asked,)


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
