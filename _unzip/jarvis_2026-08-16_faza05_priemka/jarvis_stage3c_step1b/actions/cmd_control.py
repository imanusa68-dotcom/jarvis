# -*- coding: utf-8 -*-
"""Read-only system information over natural language.

Phase 0, step 3 (2026-08-06) - hygiene pass. What was removed and why:

  * _ask_gemini()  - called the model directly with its own SDK and its own
                     copy of the API key, bypassing core/gate.py and the
                     quota meter. It had no callers, but it was one edit
                     away from becoming a second door. There is one door.
  * pip install    - `_find_hardcoded` turned any phrase containing
                     'install <name>' into `pip install <name>`. Reachable:
                     'free space, then install requests' passed the safe-word
                     list and installed a package with no confirmation.
  * _run_visible() - opened a new console window (cmd /k). No callers, and
                     stealing focus is forbidden by the behaviour rules.
  * shell=True     - POSIX branch only, never executed on Windows. Removed
                     rather than left loaded; this build is Windows-only.
  * `visible` param- advertised in the tool contract, ignored by the code.
                     A promise the system never kept.

Guarded by tests/test_cmd_control_hygiene.py - if any of the above comes
back, that test fails.
"""
import subprocess
import sys
import re
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()


# No model, no API key: this module resolves a fixed map of read-only
# commands and nothing else. Anything not in the map is refused.


def _get_platform() -> str:
    if sys.platform == "win32":  return "windows"
    if sys.platform == "darwin": return "macos"
    return "linux"

WIN_COMMAND_MAP = [
    (["disk space", "disk usage", "storage", "free space", "c drive space"],
     "wmic logicaldisk get caption,freespace,size /format:list", False),
    (["running processes", "list processes", "show processes", "active processes", "tasklist"],
     "tasklist /fo table", False),
    (["ip address", "my ip", "network info", "ipconfig"],
     "ipconfig /all", False),
    (["ping", "internet connection", "connected to internet"],
     "ping -n 4 google.com", False),
    (["open ports", "listening ports", "netstat"],
     "netstat -an | findstr LISTENING", False),
    (["wifi networks", "available wifi", "wireless networks"],
     "netsh wlan show networks", False),
    (["system info", "computer info", "hardware info", "pc info", "specs"],
     "systeminfo", False),
    (["cpu usage", "processor usage"],
     "wmic cpu get loadpercentage", False),
    (["memory usage", "ram usage"],
     "wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value", False),
    (["windows version", "os version"],
     "ver", False),
    # Step 36: `wmic product get name,version` was removed. That command asks
    # Windows for the Win32_Product class, which re-validates every installed
    # MSI package, so it is slow and has side effects on the installer
    # service. The registry uninstall key answers the same question by
    # reading, not by probing. Shape kept identical to the netstat entry
    # (one command, one pipe into findstr) on purpose.
    (["installed programs", "installed software", "installed apps"],
    # Step 36.1: no double quotes here on purpose. _run_silent hands the
    # command over as ["cmd", "/c", command]; Python builds the Windows
    # command line with list2cmdline, which escapes an embedded quote the
    # C runtime way, and cmd.exe does not undo that escape. reg.exe used
    # to receive a key name starting with backslash-quote and answered
    # 'cannot find the specified registry key'. Proven live, step 36.
    # The key path has no spaces, so quotes buy nothing and cost the
    # whole answer.
     'reg query HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall'
     ' /s /v DisplayName | findstr /i DisplayName', False),
    (["battery", "battery level", "power status"],
     "powershell (Get-WmiObject -Class Win32_Battery).EstimatedChargeRemaining", False),
    (["current time", "what time", "system time"],
     "time /t", False),
    (["current date", "what date", "system date"],
     "date /t", False),
    (["desktop files", "files on desktop"],
     "dir Desktop", False),
    (["downloads", "files in downloads"],
     "dir Downloads", False),
]

# Step 36: the `large files` entry was deleted, not disabled. It walked all of
# C:\ recursively. It happened to be unreachable only because no safe phrase
# sat below it in this list -- one insertion above it would have armed it again
# on an 8 GB laptop. Reachability by luck is not a safety property.

# Filter 1 of cmd_control lives here, next to the map, so a test can compare
# the two. Until step 36 this list was a local variable inside cmd_control:
# one rule in two copies, which is exactly how the sideways match survived.
SAFE_READ_ONLY_COMMANDS = [
    "disk space", "disk usage", "storage", "free space",
    "running processes", "list processes", "tasklist",
    "ip address", "my ip", "network info", "ipconfig",
    "system info", "computer info", "hardware info", "specs",
    "cpu usage", "memory usage", "ram usage",
    "windows version", "os version",
    "battery", "battery level",
    "current time", "current date",
    "desktop files", "files on desktop", "downloads",
    # Step 36: reachable through the front door now. Before, the direct
    # question was refused while "battery and installed programs" was served.
    "installed programs", "installed software", "installed apps",
]


def _resolve_front_door(task_lower: str) -> str | None:
    """Filter 2, forced to agree with filter 1.

    _find_hardcoded() returns the first entry whose keyword appears anywhere
    in the text. That is why "battery and installed programs" used to reach a
    command that "show installed programs" was refused: the safe-phrase list
    and the map disagreed, and the map had the last word.

    Here the entry that matched must have matched on a phrase that is also a
    safe phrase. If the first matching entry is not front-door legal we stop
    instead of walking on to the next one: passing a locked door and taking
    the next one is the bug, not the fix.
    """
    for entry in WIN_COMMAND_MAP:
        keywords, command = entry[0], entry[1]
        if not command:
            continue
        hits = [kw for kw in keywords if kw in task_lower]
        if not hits:
            continue
        if not any(kw in SAFE_READ_ONLY_COMMANDS for kw in hits):
            print(f"[CMD] BLOCKED: sideways match on {keywords[0]!r}")
            return None
        return command
    return None

def _find_hardcoded(task: str) -> str | None:
    task_lower = task.lower()
    
    if "notepad" in task_lower or any(ext in task_lower for ext in [".txt", ".log", ".md", ".csv"]):
        file_match = re.search(r'[\"\']?([\S]+\.(?:txt|log|md|csv|json|xml))[\"\']?', task, re.IGNORECASE)
        if file_match:
            filename = file_match.group(1)
            desktop  = Path.home() / "Desktop"
            filepath = Path(filename) if Path(filename).is_absolute() else desktop / filename
            return f'notepad "{filepath}"'
        if "notepad" in task_lower:
            return "notepad"
    # Phase 0, step 3: the `pip install` branch used to live here. It was
    # reachable through phrases that also contained a safe word, and it
    # installed packages into the system Python without asking.

    for keywords, command, _ in WIN_COMMAND_MAP:
        if command and any(kw in task_lower for kw in keywords):
            return command

    return None

BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b", r"\brmdir\s+/s\b", r"\bdel\s+/[fqs]",
    r"(?<![/:-])\bformat\b", r"\bdiskpart\b", r"\bfdisk\b",
    r"\breg\s+(delete|add)\b", r"\bbcdedit\b",
    r"\bnet\s+localgroup\b",
    r"\bshutdown\b", r"\brestart-computer\b",
    r"\bstop-process\b", r"\bkill\s+-9\b", r"\btaskkill\b",
    r"\beval\b", r"\b__import__\b",
]
_BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)


def _is_safe(command: str) -> tuple[bool, str]:
    match = _BLOCKED_RE.search(command)
    if match:
        return False, f"Blocked pattern: '{match.group()}'"
    return True, "OK"

# Command generation by a model used to live here (_ask_gemini). Removed in
# phase 0, step 3: it opened a second door to the model, outside the gate
# and outside the quota meter. Commands come from the map above only.

def _run_silent(command: str, timeout: int = 20) -> str:
    try:
        platform = _get_platform()
        if platform == "windows":
            is_ps = command.strip().lower().startswith("powershell")
            if is_ps:
                cmd_inner = re.sub(r'^powershell\s+"?', '', command, flags=re.IGNORECASE).rstrip('"')
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd_inner],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout
                )
            else:
                result = subprocess.run(
                    ["cmd", "/c", command],
                    capture_output=True, text=True,
                    # Step 36: cp1252 mangled every non-Latin byte cmd
                    # produced (Russian file names, localised systeminfo), and
                    # Jarvis then read the mojibake out loud. "oem" is the
                    # console code page this cmd actually writes in.
                    encoding="oem", errors="replace",
                    timeout=timeout, cwd=str(Path.home())
                )
        else:
            # Windows-only build. The old POSIX branch ran the command through
            # a shell; it never executed here and only kept a loaded gun in
            # the file.
            return "cmd_control is available on Windows only in this build."

        output = result.stdout.strip()
        error  = result.stderr.strip()
        if output:  return output[:2000]
        if error:   return f"[stderr]: {error[:500]}"
        return "Command executed with no output."

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Execution error: {e}"


# A second runner used to live here (_run_visible): it opened a new console
# window the owner did not ask for. Nothing called it, and stealing focus is
# forbidden, so it is gone.

def cmd_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Read-only system information only.

    Three filters in a row, in this order:
      1. the task text must contain a safe read-only phrase;
      2. the task must resolve to a command from the fixed map above;
      3. the resolved command must survive the blocked-pattern check and
         the launcher check (no notepad / explorer / start / del / rm).

    There is no model-generated command path and no raw `command` parameter:
    both were removed on purpose. Execution still happens only after
    core/gate.py has allowed the call.
    """
    task    = (parameters or {}).get("task", "").strip()

    if not task:
        return "Please describe what you want to do, sir."

    # SECURITY: Only allow read-only informational commands. The list itself
    # lives at module level next to WIN_COMMAND_MAP (step 36) so that a test
    # can hold the two side by side.
    
    task_lower = task.lower() if task else ""
    
    # Check if this is a safe read-only request
    is_safe_request = any(kw in task_lower for kw in SAFE_READ_ONLY_COMMANDS)
    
    if not is_safe_request:
        print(f"[CMD] BLOCKED: Potentially unsafe task: {task}")
        return (
            "SECURITY: This command type is blocked for safety. "
            "Only read-only system information commands are allowed (disk space, system info, etc.)."
        )

    # Stage 1: the raw `command` escape hatch is removed. Commands are ONLY
    # derived from the natural-language task against the read-only safe map,
    # so the allowlist can never be bypassed by a pre-baked command string.
    command = _resolve_front_door(task_lower)
    if not command:
        print(f"[CMD] BLOCKED: No safe hardcoded command for: {task}")
        return "SECURITY: Command generation is disabled. Only predefined safe commands are allowed."

    # SECURITY: Double-check safety
    safe, reason = _is_safe(command)
    if not safe:
        return f"SECURITY: Command blocked - {reason}"

    # SECURITY: Block all shell execution, Popen, etc.
    # Only allow _run_silent for read-only commands
    if any(x in command.lower() for x in ["notepad", "explorer", "start ", "del ", "rm "]):
        print(f"[CMD] BLOCKED: Potentially dangerous command: {command}")
        return "SECURITY: This command type is blocked for safety."

    if player:
        player.write_log(f"[CMD] (safe) {command[:60]}")

    # Silent run only: output comes back as text and Jarvis reads it out.
    return _run_silent(command, timeout=10)
