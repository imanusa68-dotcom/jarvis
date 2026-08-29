import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

import json
import sys
import threading
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision
from core.response_composer import compose, compose_error


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()


from config.loader import get_api_key as _get_api_key
from config.loader import get_model as _get_model

def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    """
    DISABLED FOR SECURITY: This function previously executed AI-generated code.
    Now it only returns a message explaining that code execution is blocked.
    """
    print(f"[Executor] BLOCKED: Code execution disabled for security")
    if speak:
        speak("Code execution is disabled for security reasons, sir.")
    return (
        f"SECURITY: Code execution is disabled. "
        f"Task description was: {description[:100]}..."
    )

def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)

    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] 💉 Injected + translated content")

    return params


def _detect_language(text: str) -> str:
    """Язык текста человеческим именем ("Russian") — тем, что ждёт перевод.

    Ни сети, ни модели, ни квоты. core/lang.py решает по письменности,
    частым словам и своеобразным буквам примерно за двадцать микросекунд.

    До 7 августа 2026 латиница уходила по сети к дешёвой роли: 300–1500 мс
    ожидания, расход суточной квоты и мерцающий ответ при ошибке 503.
    Определение языка — задача уровня «посчитать буквы»; модель здесь была
    единственным паразитным вызовом из двадцати четырёх в проекте.

    Имя языка берётся из общей таблицы локалей core/search_locale.py, чтобы
    перевод и поиск никогда не разошлись в том, что такое "tr".
    """
    sample = (text or "")[:200]

    from core.lang import detect_with_reason
    from core.search_locale import get_label

    code, why = detect_with_reason(sample)
    label = get_label(code)
    print(f"[Executor] 🔤 язык решён ({why}): {label}")
    return label


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        # Дверь к модели во всём проекте одна: core/aux_model.aux_call.
        # Она знает остывание после 429, повтор на временном отказе и никогда
        # не молчит при неудаче. Своей двери у исполнителя больше нет.
        # Роль прежняя — aux_heavy: перевод целого документа не для лёгкой.
        from core.aux_model import aux_call
        api_key    = _get_api_key()
        model_name = _get_model("aux_heavy")

        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        ok, answer = aux_call(
            prompt,
            api_key,
            model=model_name,
            caller="Executor-Translate",
        )
        if not (ok and answer.strip()):
            # Отказ уже назван вслух внутри aux_call; здесь — чем ответим.
            print(f"[Executor] ⚠️ Translation failed ({answer[:48]})")
            return content
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return answer.strip()
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content

class ToolRefused(RuntimeError):
    """Дверь безопасности не пустила действие. НЕ ошибка инструмента.

    ЗАЧЕМ ЭТО ИСКЛЮЧЕНИЕ ПОЯВИЛОСЬ (блок 8, замер 19.08.2026)
    ---------------------------------------------------------
    Дверь отказывает СТРОКОЙ, а шаг ниже считал удачей любую вернувшуюся
    строку — провал он видел только по исключению. Замер на запрещённом
    инструменте:

        [GATE] blocked send_message — automated messaging is disabled
        [Executor] Step 1 done: SECURITY: Tool 'send_message' is blocked
        ответ владельцу: «Сообщение отправлено, сэр.»

    Действие НЕ произошло, а владельцу сказано, что произошло. Это ложь об
    исходе, и она ломает I19 (молчаливых отказов не бывает).

    Пока задачи жили в памяти, эта ложь была сказана и забыта. С блока 8
    задачи живут в базе — и та же ложь стала бы записанной строкой
    «выполнено», которую в фазе 3 прочитает приёмка и заверит. Поэтому
    чинится здесь и сейчас, до того как база успеет её накопить.

    ПОЧЕМУ ИСКЛЮЧЕНИЕ, А НЕ ПРОВЕРКА ТЕКСТА
    Проверять «начинается ли ответ со слова SECURITY» — это узнавание по
    словам, на котором проект спотыкался шесть раз. Исключение отличимо ПО
    ФОРМЕ: угадывать нечего, и текст отказа может меняться свободно.

    ПОЧЕМУ ОТДЕЛЬНЫЙ ТИП, А НЕ ОБЫЧНОЕ ИСКЛЮЧЕНИЕ
    Отказ двери — это не «попробуй ещё раз»: повтор запрещённого действия
    останется запрещённым, а разбор ошибки моделью сожжёт квоту впустую.
    Отдельный тип позволяет обойти и повтор, и разбор.
    """


def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:

    # Stage 1: the autonomous path now passes through the SAME gate as the
    # interactive path (main._execute_tool). No human is present, so the gate
    # runs FAIL-CLOSED — blocked / confirm / screen-gated actions are denied and
    # logged, and a hallucinated confirmed=true can never self-approve. This
    # closes the pre-Stage-1 hole where the executor called action modules
    # with no security check at all.
    try:
        from core.gate import dispatch as _gate_dispatch
        _g = _gate_dispatch(tool, parameters, mode="autonomous", screen_control=False)
        if not _g.allowed:
            # Блок 8: отказ уходит ИСКЛЮЧЕНИЕМ, а не строкой (см. ToolRefused).
            raise ToolRefused(_g.message)
    except ToolRefused:
        raise
    except Exception as _gate_err:
        # Сама дверь сломалась — это тоже отказ, и тоже не «попробуй ещё раз»:
        # выполнять действие без проверки нельзя (fail-closed).
        raise ToolRefused(
            f"gate error, action not run ({tool}): {_gate_err}")

    if tool == "open_app":
        from actions.open_app import open_app
        return open_app(parameters=parameters, player=None) or "Done."

    elif tool == "web_search":
        from actions.web_search import web_search
        return web_search(parameters=parameters, player=None) or "Done."
    elif tool == "game_updater":
        from actions.game_updater import game_updater
        return game_updater(parameters=parameters, player=None, speak=speak) or "Done."
    elif tool == "browser_control":
        from actions.browser_control import browser_control
        return browser_control(parameters=parameters, player=None) or "Done."

    elif tool == "file_controller":
        from actions.file_controller import file_controller
        return file_controller(parameters=parameters, player=None) or "Done."

    elif tool == "cmd_control":
        from actions.cmd_control import cmd_control
        return cmd_control(parameters=parameters, player=None) or "Done."

    elif tool == "code_helper":
        from actions.code_helper import code_helper
        return code_helper(parameters=parameters, player=None, speak=speak) or "Done."

    elif tool == "screen_process":
        from actions.screen_processor import screen_process
        screen_process(parameters=parameters, player=None)
        return "Screen captured and analyzed."

    elif tool == "reminder":
        from actions.reminder import reminder
        return reminder(parameters=parameters, player=None) or "Done."

    elif tool == "youtube_video":
        from actions.youtube_video import youtube_video
        return youtube_video(parameters=parameters, player=None) or "Done."

    elif tool == "weather_report":
        from actions.weather_report import weather_action
        return weather_action(parameters=parameters, player=None) or "Done."

    elif tool == "desktop_control":
        from actions.desktop import desktop_control
        return desktop_control(parameters=parameters, player=None) or "Done."

    elif tool == "computer_control":
        from actions.computer_control import computer_control
        return computer_control(parameters=parameters, player=None) or "Done."

    elif tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)

    else:
        print(f"[Executor] BLOCKED: Unknown tool '{tool}' — code execution disabled")
        return f"SECURITY: Unknown tool '{tool}' blocked. Code execution is disabled."

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
    ) -> str:
        print(f"\n[Executor] 🎯 Goal: {goal}")

        replan_attempts = 0
        completed_steps = []
        step_results    = {} 
        try:
            from core.dialogue_state import format_for_prompt as _ds_ctx
            _plan_ctx = _ds_ctx()
        except Exception:
            _plan_ctx = ""
        plan            = create_plan(goal, context=_plan_ctx)

        while True:
            steps = plan.get("steps", [])

            if not steps:
                msg = "I couldn't create a valid plan for this task."
                if speak: speak(msg)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled.")
                    return "Task cancelled."

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})

                params = _inject_context(params, tool, step_results, goal=goal)

                print(f"\n[Executor] ▶️ Step {step_num}: [{tool}] {desc}")

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak)
                        step_results[step_num] = result 
                        completed_steps.append(step)
                        print(f"[Executor] ✅ Step {step_num} done: {str(result)[:100]}")
                        step_ok = True
                        break

                    except ToolRefused as refused:
                        # Блок 8. Отказ двери — НЕ ошибка инструмента:
                        #   * повторять нельзя: запрещённое останется
                        #     запрещённым, а три попытки просто утроят отказ;
                        #   * разбирать моделью нельзя: она сожжёт квоту на
                        #     вопрос, ответ на который уже известен;
                        #   * называть удачей нельзя — ровно это и было ложью
                        #     об исходе до блока 8.
                        #
                        # И ПЕРЕПЛАНИРОВАТЬ ТОЖЕ НЕЛЬЗЯ, и это соображение
                        # важнее остальных трёх. Перепланирование после запрета
                        # означает «поищи другой способ сделать то, что тебе
                        # запретили» — то есть автоматический поиск обхода
                        # вокруг границы безопасности. Такое свойство нельзя
                        # закладывать в систему, даже ограниченное двумя
                        # попытками. Останавливаемся и говорим владельцу.
                        #
                        # Наверх уходит ИСКЛЮЧЕНИЕ, а не строка, и это вторая
                        # половина той же правки. Первая версия возвращала
                        # честную фразу — голос стал правдивым, а очередь всё
                        # равно записала в базу «выполнено»: строку она отличить
                        # от отказа не может. Замер поймал это сразу.
                        error_msg = str(refused)
                        print(f"[Executor] 🛡 Step {step_num} отказ двери: "
                              f"{error_msg[:100]}")
                        if speak:
                            speak("Это действие запрещено, сэр — я его не "
                                  "делал. Обходить запрет не буду.")
                        raise

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] ❌ Step {step_num} attempt {attempt} failed: {error_msg}")

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] ⏭️ Skipping step {step_num}")
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else:
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak
                                    )
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except ToolRefused:
                                    # ВТОРОЕ место, где зовётся инструмент, и
                                    # оно опаснее первого: здесь модель СОЧИНЯЕТ
                                    # замену упавшему шагу и пробует её. Если
                                    # заглушить здесь отказ двери, получится
                                    # ровно то, чего быть не должно: модель
                                    # подбирает обход, пока один из вариантов
                                    # не пройдёт мимо запрета.
                                    #
                                    # Найдено сторожем по дереву кода, который
                                    # проверяет ВСЕ места вызова инструмента, а
                                    # не одно. Первая версия правки закрыла
                                    # только первое.
                                    if speak:
                                        speak("Это действие запрещено, сэр — "
                                              "я его не делал. Обходить "
                                              "запрет не буду.")
                                    raise
                                except Exception as fix_err:
                                    print(f"[Executor] ⚠️ Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach.")

            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        """Compose a natural summary using response_composer with personality adaptation."""
        fallback = f"Completed {len(completed_steps)} step(s) for: {goal[:60]}."

        tool_used = completed_steps[-1].get("tool", "unknown") if completed_steps else "unknown"

        try:
            from memory.personality_engine import load_profile
            personality = load_profile()
        except Exception:
            personality = None

        try:
            from core.dialogue_state import get as _ds_get
            ds = _ds_get()
        except Exception:
            ds = None

        steps_summary = "; ".join(
            s.get("description", "") for s in completed_steps if s.get("description")
        )

        try:
            api_key = _get_api_key()
            summary = compose(
                result=steps_summary,
                goal=goal,
                tool_used=tool_used,
                personality=personality,
                dialogue_state=ds,
                language="ru",
                api_key=api_key,
            )
            if speak: speak(summary)
            return summary
        except Exception:
            if speak: speak(fallback)
            return fallback
  
