# actions/code_helper.py
# AI-powered code assistant — read-only code analysis.
#
# Actions:
#   explain → Explain what a piece of code or file does
#
# All other actions (write/edit/run/build/optimize/screen_debug/auto) are
# blocked by policy (core/security.py) and by the local guard in code_helper().

import sys
import json
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
from config.loader import get_model as _get_model
GEMINI_MODEL    = _get_model("aux_heavy")


from config.loader import get_api_key as _get_api_key


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    p = Path(file_path)
    if not p.exists():
        return "", f"File not found: {file_path}"
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:
        return "", f"Could not read file: {e}"


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain, sir."

    if player:
        player.write_log("[Code] Analyzing code...")

    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""

    # Дверь открываем лениво: общая дверь тянет за собой SDK, а стартовый
    # бюджет памяти дорог. Пока никто не просил 'explain', SDK в память не попадает.
    from core.aux_model import aux_call

    # Ключ берём осторожно: без ключа загрузчик бросает исключение,
    # а владелец должен услышать фразу, а не получить падение инструмента.
    try:
        api_key = _get_api_key()
    except Exception as e:
        print(f"[CodeHelper] 🔁 разбор кода не вышел (ключ не найден: {type(e).__name__})")
        return "Could not explain the code right now, sir - the model is unavailable."

    ok, answer = aux_call(
        prompt,
        api_key,
        model=GEMINI_MODEL,
        caller="CodeHelper",
    )
    if ok and answer.strip():
        return answer.strip()

    print(f"[CodeHelper] 🔁 разбор кода не вышел ({answer[:48]})")
    return "Could not explain the code right now, sir - the model is unavailable."


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    """
    SECURED: Code helper is limited to read-only operations.

    ALLOWED actions (read-only, no system modification):
        explain     : Explain what code does (safe, read-only)

    BLOCKED actions (can modify files or execute code):
        write, edit, run, build, optimize, screen_debug
    """
    p = parameters or {}
    action = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    file_path = p.get("file_path", "").strip()
    code = p.get("code", "").strip()

    # SECURITY: Block dangerous actions
    BLOCKED_ACTIONS = {"write", "edit", "run", "build", "optimize", "screen_debug", "auto"}
    SAFE_ACTIONS = {"explain"}

    if action == "auto":
        # Auto-detection disabled for security - only allow explicit explain
        print(f"[Code] BLOCKED: Auto-detection disabled for security")
        return (
            "SECURITY: Auto action detection is disabled. "
            "Only 'explain' action is allowed (read-only code analysis)."
        )

    if action in BLOCKED_ACTIONS:
        print(f"[Code] BLOCKED: Action '{action}' disabled for security")
        return (
            f"SECURITY: Code helper action '{action}' is blocked for safety. "
            "Code writing, editing, execution, and file modification are disabled. "
            "Only 'explain' action is allowed (read-only code analysis)."
        )

    if action not in SAFE_ACTIONS:
        return f"SECURITY: Unknown action '{action}'. Only 'explain' is allowed."

    # SAFE: Explain action (read-only)
    if action == "explain":
        return _explain_action(file_path, code, player)

    return "SECURITY: Action not allowed."
