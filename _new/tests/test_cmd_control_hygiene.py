# -*- coding: utf-8 -*-
"""Фаза 0, шаг 3 — гигиена actions/cmd_control.py.

Почему этот файл существует.

Ворота (core/gate.py) стоят перед выполнением, и они работают. Но внутри
cmd_control жили четыре вещи, которых ворота не видят:

  1. Собственный вызов модели со своим SDK и своей копией ключа: мимо ворот
     и мимо счётчика квот. Вызовов не было, но функция лежала заряженной.
  2. Ветка установки пакетов — достижимая: фраза «free space, then install
     requests» проходила список безопасных слов и ставила пакет в системный
     Python без единого вопроса.
  3. Запуск видимого окна консоли, то есть кража фокуса. Вызовов не было.
  4. Параметр visible — объявлен в контракте инструмента, в подсказке
     планировщику и в аварийном откате, но кодом не читается никогда.

Плюс пятое: список опасных слов существовал в двух копиях
(core/security.py и core/uncertainty_policy.py), и копии уже разошлись —
во второй не было 'del '. Единственный источник — core/security.py.

Этот файл — сторож. Он не проверяет, что система работает; он проверяет,
что удалённое не вернулось. Инвариант: старый путь УДАЛЁН, а не выключен.

Run:  python -m pytest tests/test_cmd_control_hygiene.py -q
or:   python tests/test_cmd_control_hygiene.py
"""
import io as _io
import os as _os
import sys as _sys
import tokenize as _tokenize
from pathlib import Path

_ROOT = Path(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_sys.path.insert(0, str(_ROOT))

from actions.cmd_control import _find_hardcoded, _is_safe  # noqa: E402
from core.uncertainty_policy import classify_risk, RiskLevel  # noqa: E402


def _src(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Исходник без комментариев и строковых литералов.

    Сторож обязан смотреть на код, а не на прозу. Иначе честное объяснение
    в комментарии («здесь раньше был вызов модели, он удалён») само же и
    валит проверку. Это не теория: первый прогон этого файла упал именно так.
    """
    pieces = []
    for tok in _tokenize.generate_tokens(_io.StringIO(src).readline):
        if tok.type in (_tokenize.COMMENT, _tokenize.STRING):
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


_CMD_SRC = _src("actions/cmd_control.py")
_CMD_CODE = _code_only(_CMD_SRC)


def test_no_second_door_to_the_model():
    """Один рот, одна дверь: инструмент не зовёт модель сам."""
    for needle in ("generativeai", "genai", "_ask_gemini",
                   "generate_content", "get_api_key", "get_model"):
        assert needle not in _CMD_CODE, (
            f"в cmd_control вернулся прямой путь к модели: {needle!r}"
        )


def test_no_visible_console_runner():
    """Ничто не крадёт фокус: окна консоли инструмент не открывает."""
    for needle in ("_run_visible", "CREATE_NEW_CONSOLE", "Popen", "creationflags"):
        assert needle not in _CMD_CODE, (
            f"вернулся запуск видимого терминала: {needle!r}"
        )


def test_no_shell_execution():
    """Команда не уходит в шелл: только фиксированный список аргументов."""
    code = _CMD_CODE.replace(" ", "")
    assert "shell=True" not in code, "shell=True вернулся в cmd_control"
    assert "executable=" not in code, "запуск через сторонний шелл вернулся"


def test_install_phrases_never_resolve_to_pip():
    """Доказанная дыра шага 3: безопасное слово + 'install' ставило пакет."""
    sneaky = (
        "free space, then install requests",
        "check storage and install numpy",
        "disk space install pandas",
        "system info, install colorama",
    )
    for task in sneaky:
        cmd = _find_hardcoded(task) or ""
        assert "pip" not in cmd.lower(), (
            f"фраза {task!r} снова превращается в установку пакета: {cmd!r}"
        )


def test_read_only_commands_still_work():
    """Уборка не должна отнять то, ради чего инструмент существует."""
    cmd = _find_hardcoded("show disk space")
    assert cmd and "logicaldisk" in cmd.lower(), f"disk space сломан: {cmd!r}"
    safe, reason = _is_safe(cmd)
    assert safe, f"безопасная команда заблокирована: {reason}"


def test_contract_never_advertises_visible():
    """Контракт сильнее промпта: обещание либо выполняется, либо его нет."""
    assert '"visible"' not in _src("main.py"), (
        "параметр-призрак visible вернулся в схему инструментов"
    )
    assert "visible" not in _CMD_CODE, (
        "cmd_control снова читает параметр visible"
    )
    assert "visible: boolean" not in _src("agent/planner.py"), (
        "планировщику снова обещают visible"
    )
    assert '"visible"' not in _src("agent/error_handler.py"), (
        "аварийный откат снова передаёт visible"
    )


def test_danger_words_live_in_one_place():
    """Одно правило — одно место. Вторая копия уже успела разойтись."""
    assert "danger_words" not in _code_only(_src("core/uncertainty_policy.py")), (
        "вторая копия списка опасных слов вернулась в uncertainty_policy"
    )
    # И повышение риска при этом обязано продолжать работать — через security.
    assert classify_risk("cmd_control", {"task": "del old.txt"}) == RiskLevel.HIGH
    assert classify_risk("cmd_control", {"task": "delete all logs"}) == RiskLevel.HIGH
    assert classify_risk("cmd_control", {"task": "show disk space"}) == RiskLevel.MEDIUM


def _run():
    fns = [
        test_no_second_door_to_the_model,
        test_no_visible_console_runner,
        test_no_shell_execution,
        test_install_phrases_never_resolve_to_pip,
        test_read_only_commands_still_work,
        test_contract_never_advertises_visible,
        test_danger_words_live_in_one_place,
    ]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nOK: {len(fns)} passed (standalone)")



# ---------------------------------------------------------------------------
# Step 36: the installed-programs door.
#
# These guards are semantic on purpose. A text check cannot do this job here:
# _code_only() above drops STRING tokens, so command literals are invisible in
# _CMD_CODE, and _CMD_SRC would happily match the honest comment that records
# the removal. A guard that can only ever be green is worse than no guard.
# ---------------------------------------------------------------------------

_FORBIDDEN_SCAN = "wmic product"

# Each of these passed filter 1 on a harmless phrase and then resolved to the
# Win32_Product scan, because the map matched a later word. Same shape as the
# old "free space, then install requests" bug, different payload.
_SNEAKY_PHRASES = [
    "battery and installed programs",
    "what time, installed apps",
    "downloads and installed software",
    "desktop files installed programs",
]


def test_no_wmic_product_in_map():
    from actions.cmd_control import WIN_COMMAND_MAP
    for entry in WIN_COMMAND_MAP:
        keywords, command = entry[0], (entry[1] or "")
        assert _FORBIDDEN_SCAN not in command.lower(), (
            f"{keywords[0]!r} still resolves to a Win32_Product scan: {command}"
        )


def test_sneaky_phrases_never_reach_wmic_product():
    from actions.cmd_control import _find_hardcoded, _resolve_front_door
    for phrase in _SNEAKY_PHRASES:
        raw = (_find_hardcoded(phrase) or "").lower()
        assert _FORBIDDEN_SCAN not in raw, f"map still arms {phrase!r}: {raw}"
        door = (_resolve_front_door(phrase) or "").lower()
        assert _FORBIDDEN_SCAN not in door, f"door still arms {phrase!r}: {door}"


def test_installed_programs_still_answers():
    """The point of step 36 is not to silence the question, but to answer it
    cheaply and through the front door."""
    from actions.cmd_control import (
        SAFE_READ_ONLY_COMMANDS, _is_safe, _resolve_front_door,
    )
    phrase = "show installed programs"
    assert any(kw in phrase for kw in SAFE_READ_ONLY_COMMANDS), \
        "filter 1 refuses the direct question"
    command = _resolve_front_door(phrase)
    assert command, "filter 2 refuses the direct question"
    assert "reg query" in command.lower()
    safe, reason = _is_safe(command)
    assert safe, f"blocked-pattern check refuses it: {reason}"
    launchers = ["notepad", "explorer", "start ", "del ", "rm "]
    assert not any(x in command.lower() for x in launchers), \
        "launcher check refuses it"


def test_no_disk_wide_recursive_scan():
    from actions.cmd_control import WIN_COMMAND_MAP
    for entry in WIN_COMMAND_MAP:
        keywords, command = entry[0], (entry[1] or "").lower()
        assert "-recurse" not in command, f"{keywords[0]!r} walks a whole tree"
        assert "get-childitem c:\\" not in command, \
            f"{keywords[0]!r} walks all of C:"


def test_sideways_match_is_refused():
    """A phrase whose first map hit is not a safe phrase must be refused, not
    quietly served from the next entry down."""
    from actions.cmd_control import _resolve_front_door
    # Illegal entry first -> refuse. "open ports", "wifi networks" and "ping"
    # live in the map but have never been front-door phrases.
    assert _resolve_front_door("battery and open ports") is None
    assert _resolve_front_door("wifi networks and battery") is None
    assert _resolve_front_door("ping google and current time") is None
    # Legal entry first -> serve it. Map order decides, so a gap word later in
    # the sentence is simply never reached.
    assert _resolve_front_door("disk space and wifi networks")


def test_map_and_safe_words_agree():
    """Freeze the known disagreement between the map and the safe list.

    ping / open ports / wifi networks are in the map but have never been in
    the safe list, so they are unreachable. Step 36 does not change that
    behaviour, it only nails it down: a new disagreement means someone added
    a command in one place and forgot the other, which is the bug this file
    already guards against for danger words.
    """
    from actions.cmd_control import SAFE_READ_ONLY_COMMANDS, WIN_COMMAND_MAP
    gaps = sorted(
        entry[0][0] for entry in WIN_COMMAND_MAP
        if entry[1] and not any(kw in SAFE_READ_ONLY_COMMANDS for kw in entry[0])
    )
    assert gaps == ["open ports", "ping", "wifi networks"], gaps

def test_cmd_commands_carry_no_double_quotes():
    """Step 36.1: quotes never survive the trip to cmd.exe.

    _run_silent runs the command as a three-item argument list.
    Python turns that list into one Windows command line and escapes
    an embedded double quote the C runtime way; cmd.exe does not undo
    that escape. Live proof: a quoted reg query came back with
    'cannot find the specified registry key' although reg.exe ran.
    The powershell entry is exempt: that branch re-parses its own
    string and is proven working live (battery answered).
    """
    from actions.cmd_control import WIN_COMMAND_MAP

    for keywords, command, _flag in WIN_COMMAND_MAP:
        if not command:
            continue
        if command.strip().lower().startswith("powershell"):
            continue
        assert chr(34) not in command, (keywords[0], command)

# Step 36.2: what the map can do and what the model is told it can do
# are two different files. The live model only ever reads the second
# one. A capability missing from the declaration is a capability that
# does not exist, no matter how green the command tests are.
_DECLARED_AS = {
    "disk space": "disk space",
    "running processes": "running processes",
    "ip address": "network",
    "system info": "system specs",
    "cpu usage": "CPU",
    "memory usage": "RAM",
    "windows version": "Windows version",
    "battery": "battery",
    "current time": "time",
    "current date": "date",
    "desktop files": "Desktop",
    "downloads": "Downloads",
    "installed programs": "installed programs",
}


def _cmd_control_declaration() -> str:
    """The text main.py hands to the live model for cmd_control."""
    src = _src("main.py")
    start = src.find(chr(34) + "name" + chr(34) + ": " + chr(34) + "cmd_control" + chr(34))
    assert start != -1, "cmd_control is no longer declared in main.py"
    end = src.find(chr(34) + "parameters" + chr(34), start)
    assert end != -1, "cmd_control declaration has no parameters block"
    return src[start:end]


def test_declaration_lists_what_the_map_answers():
    """Step 36.2: every reachable family must be advertised.

    Proven live: the registry command was correct and the filters let
    it through, but the model never called the tool because the
    declaration said nothing about installed programs. It answered
    'I have no access' instead -- truthfully, from its point of view.

    The table forces the decision to be conscious: a new map entry
    that a safe phrase can reach fails this test until someone writes
    down how it is advertised.
    """
    from actions.cmd_control import SAFE_READ_ONLY_COMMANDS, WIN_COMMAND_MAP

    declaration = _cmd_control_declaration().lower()
    reachable = []
    for keywords, command, _flag in WIN_COMMAND_MAP:
        if not command:
            continue
        safe = [kw for kw in keywords if kw in SAFE_READ_ONLY_COMMANDS]
        if not safe:
            continue
        reachable.append(safe[0])

    untabled = [fam for fam in reachable if fam not in _DECLARED_AS]
    assert not untabled, (
        "map answers these families but nobody said how they are "
        "advertised to the model: " + repr(untabled)
    )

    for fam in reachable:
        word = _DECLARED_AS[fam].lower()
        assert word in declaration, (
            "cmd_control answers " + repr(fam) + " but its declaration in "
            "main.py never mentions " + repr(word)
        )

if __name__ == "__main__":
    _run()
