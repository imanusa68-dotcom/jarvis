import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

import json
import re
import sys
from pathlib import Path
from enum import Enum


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()


class ErrorDecision(Enum):
    RETRY       = "retry"      
    SKIP        = "skip"       
    REPLAN      = "replan"     
    ABORT       = "abort"    


ERROR_ANALYST_PROMPT = """You are the error recovery module of MARK XXV AI assistant.

A task step has failed. Analyze the error and decide what to do.

DECISIONS:
- retry   : Transient error (network timeout, temporary file lock, race condition).
             The same step can succeed if tried again.
- skip    : This step is not critical and the task can succeed without it.
- replan  : The approach was wrong. A different tool or method should be tried.
- abort   : The task is fundamentally impossible or unsafe to continue.

Also provide:
- A brief explanation of WHY it failed (1 sentence)
- A fix suggestion if decision is replan (what to try instead)
- Max retries: how many times to retry if decision is retry (1 or 2)

Return ONLY valid JSON:
{
  "decision": "retry|skip|replan|abort",
  "reason": "why it failed",
  "fix_suggestion": "what to try instead (for replan)",
  "max_retries": 1,
  "user_message": "Short message to tell the user (max 15 words)"
}
"""


from config.loader import get_api_key as _get_api_key
from config.loader import get_model as _get_model


def _replan_stub(reason: str) -> dict:
    """Решение «перепланировать» одной строкой.

    Три места отвечают одним и тем же при неудаче: нет ключа, дверь
    отказала, ответ не разобрался. Текст живёт в одном месте, чтобы три
    копии не разошлись при правке, как уже было с текстом отказа экрана.
    """
    return {
        "decision":       ErrorDecision.REPLAN,
        "reason":         reason,
        "fix_suggestion": "Try alternative approach",
        "max_retries":    1,
        "user_message":   "Encountered an issue, adjusting approach, sir."
    }


def analyze_error(
    step: dict,
    error: str,
    attempt: int = 1,
    max_attempts: int = 2
) -> dict:
    """
    Analyzes a failed step and returns a recovery decision.

    Args:
        step         : The step dict that failed
        error        : Error message/traceback
        attempt      : Current attempt number
        max_attempts : How many times we've already tried

    Returns:
        {
            "decision": ErrorDecision,
            "reason": str,
            "fix_suggestion": str,
            "max_retries": int,
            "user_message": str
        }
    """
    if attempt >= max_attempts:
        print(f"[ErrorHandler] ⚠️ Max attempts reached for step {step.get('step')} — forcing replan")
        return {
            "decision":      ErrorDecision.REPLAN,
            "reason":        f"Failed {attempt} times: {error[:100]}",
            "fix_suggestion": "Try a completely different approach or tool",
            "max_retries":   0,
            "user_message":  "Trying a different approach, sir."
        }

    # Дверь к модели во всём проекте одна: core/aux_model.aux_call. Правила
    # разбора едут первым куском промпта — системной инструкции у двери нет.
    from core.aux_model import aux_call

    try:
        api_key = _get_api_key()
    except Exception as _key_err:
        print(f"[ErrorHandler] ⚠️ ключ не найден ({type(_key_err).__name__}) — replan")
        return _replan_stub(f"key missing: {type(_key_err).__name__}")
    model_name = _get_model("aux_light")

    prompt = f"""Failed step:
Tool: {step.get('tool')}
Description: {step.get('description')}
Parameters: {json.dumps(step.get('parameters', {}), indent=2)}
Critical: {step.get('critical', False)}

Error:
{error[:500]}

Attempt number: {attempt}"""

    full_prompt = (
        f"{ERROR_ANALYST_PROMPT}\n\n"
        f"----- FAILED STEP -----\n"
        f"{prompt}\n\n"
        f"Return ONLY the JSON object described above, nothing else."
    )

    ok, answer = aux_call(full_prompt, api_key, model=model_name,
                          caller="ErrorHandler")
    if not ok:
        print(f"[ErrorHandler] ⚠️ разбор не вышел ({answer[:48]}) — replan")
        return _replan_stub(f"model unavailable: {answer[:48]}")

    try:
        text     = re.sub(r"```(?:json)?", "", answer.strip()).strip().rstrip("`").strip()

        result = json.loads(text)
        decision_str = result.get("decision", "replan").lower()
        decision_map = {
            "retry":  ErrorDecision.RETRY,
            "skip":   ErrorDecision.SKIP,
            "replan": ErrorDecision.REPLAN,
            "abort":  ErrorDecision.ABORT,
        }
        result["decision"] = decision_map.get(decision_str, ErrorDecision.REPLAN)


        if step.get("critical") and result["decision"] == ErrorDecision.SKIP:
            result["decision"]     = ErrorDecision.REPLAN
            result["user_message"] = "This step is critical — finding alternative approach, sir."

        print(f"[ErrorHandler] Decision: {result['decision'].value} — {result.get('reason', '')}")
        return result

    except Exception as e:
        print(f"[ErrorHandler] ⚠️ Analysis failed: {e} — defaulting to replan")
        return _replan_stub(str(e))


def generate_fix(step: dict, error: str, fix_suggestion: str) -> dict:
    """Запасной шаг вместо упавшего — без единого вызова модели.

    До 9 августа 2026 здесь просили модель написать Python-скрипт и
    подставляли его шагом code_helper с действием "run". Ветка была мёртвой
    с момента запрета исполнения кода: code_helper держит "run" в
    BLOCKED_ACTIONS, а _run_generated_code в исполнителе отключён. Вызов жёг
    квоту и секунды ради кода, который гарантированно отбрасывался.

    Осталась честная деградация: тот же запасной шаг на cmd_control, который
    раньше отдавался только при ошибке генерации. Аргументы error и
    fix_suggestion сохранены в подписи: их передаёт исполнитель.
    """
    print(f"[ErrorHandler] \U0001f501 запасной шаг (cmd) вместо: {str(step.get('description', ''))[:60]}")
    return {
        "step":        step.get("step"),
        "tool":        "cmd_control",
        "description": f"Fallback (cmd) for: {step.get('description')}",
        "parameters":  {
            "task":    step.get("description", ""),
        },
        "depends_on":  step.get("depends_on", []),
        "critical":    step.get("critical", False)
    }
