import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

import json
import re
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()


PLANNER_PROMPT = """You are the planning module of MARK XXXV, a personal AI assistant.
Your job: understand the user's GOAL and build the minimal, effective sequence of steps
to achieve it using the available tools.

GOAL-FIRST MINDSET:
- Think about what the user actually wants to achieve, not just which words map to which tool.
- Choose the minimum number of steps that genuinely serves the goal.
- If the goal can be answered directly without tools, say so in the goal field and use zero steps.
- Prefer a single focused step over a verbose multi-step plan when one step is sufficient.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts. It does not exist.
- NEVER reference previous step results in parameters. Every step is independent.
- Use file_controller to save content to disk.
- Use cmd_control ONLY to read system information (disk space, processes,
  network, specs). It cannot open, install or change anything.

STEP COUNT POLICY:
- Use the minimum steps genuinely required to achieve the goal well.
- Simple lookup or action goals: 1 step.
- Moderately complex research: 2–3 steps.
- Multi-phase research + save + open: up to 5 steps.
- Never add steps for the sake of it. Quality over quantity.

FALLBACK POLICY:
- If the goal is a simple factual question you can answer confidently, return steps: [].
- If the goal is unclear but low-risk, make a reasonable assumption and search for it.
- Do NOT default everything to web_search. Consider whether the goal actually needs a search.

BROWSER RULES - CRITICAL:
- "yandex", "яндекс" = Yandex Browser (NOT Chrome, NOT Chromium!)
- "chrome", "хром" = Google Chrome (NOT Yandex, NOT Chromium!)
- ALWAYS include "browser" parameter in browser_control: browser: "yandex" or browser: "chrome"
- For YouTube tasks: go directly to youtube.com/@ChannelName, then click Videos, then click first video
- Keep browser parameter SAME across all steps (if started with yandex, continue with yandex)

MULTILINGUAL SEARCH RULES:
- When the user's message is in a non-English language, include language: "<code>" in web_search parameters.
  Examples: Russian → language: "ru", German → language: "de", French → language: "fr"
- For topics where both native-language and English sources are valuable, add locale_strategy: "bilingual".
  This runs two searches (native + English) and merges results for broader coverage.
- NEVER change the query language — always write the query in the user's own language.
  The `language` parameter tells the search engine which locale to use; it does NOT translate the query.

SEARCH QUERY QUALITY:
- Build queries from the understood INTENT, not just the literal user phrase.
- "что нового у claude code" → query: "Claude Code latest updates features 2025"
- "найди официальную инфу" → add "official documentation" or "site:docs." to the query
- "нормальный источник" → target authoritative domains in the query

AVAILABLE TOOLS AND THEIR PARAMETERS:

open_app
  app_name: string (required)

web_search
  query: string (required) — write a clear, focused search query IN THE USER'S LANGUAGE
  mode: "search" or "compare" (optional, default: search)
  items: list of strings (optional, for compare mode)
  aspect: string (optional, for compare mode)
  language: string (optional) — ISO-639-1 code, e.g. "ru", "de", "fr", "zh"
            Set when user writes in a non-English language for locale-aware results.
  locale_strategy: "bilingual" | "monolingual" (optional)
                   Use "bilingual" when both native + English sources are valuable.

game_updater
  action: "list" | "download_status" | "schedule_status" (required; install/update are DISABLED)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional)

browser_control
  action: "go_to" | "search" | "youtube_search" | "click" | "type" | "scroll" | "press" | "close" (required)
  browser: "yandex" | "chrome" | "firefox" | "edge" (CRITICAL - always specify!)
  same_tab: boolean (IMPORTANT! true = use current tab, false = open new tab)
  url: string (for go_to)
  query: string (for search/youtube_search)
  engine: "google" | "yandex" | "bing" | "youtube" (for search)
  text: string (for click - text of element to click)
  direction: "up" | "down" (for scroll)
  
  SAME_TAB RULES:
  - same_tab: true - when user says "той же вкладке", "этой странице", "тут же", "same tab"
  - same_tab: false (default) - for new searches, first request, or when not specified

open_search_source
  source_index: integer (optional) — 1-based number of the source shown to the user
  source_match: string (optional) — title/domain/URL substring, e.g. "Forbes", "TechCrunch", "openai"
  browser: "yandex" | "chrome" (optional) — preferred browser
  prefer_existing_browser: boolean (optional, default: true)
  prefer_new_tab: boolean (optional, default: true)

  Use this when the user asks to open one of the sources from the most recent search or research answer.
  Defaults: prefer an already running browser, prefer a new tab.

  Examples:
  - "Открой источник 2 в Яндексе" → source_index: 2, browser: "yandex", prefer_existing_browser: true, prefer_new_tab: true
  - "Open source 1 in Chrome"      → source_index: 1, browser: "chrome"
  - "Открой Forbes source в Яндексе" → source_match: "forbes", browser: "yandex"
  - "Открой этот источник в Chrome" → (no index, no match = first/most relevant), browser: "chrome"


file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)

cmd_control
  task: string (required) — read-only system info request

computer_control
  action: "type" | "click" | "hotkey" | "press" | "scroll" | "screenshot" | "screen_find" | "screen_click" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "camera" (optional)

reminder
  date: string YYYY-MM-DD (required)
  time: string HH:MM (required)
  message: string (required)

desktop_control
  action: "list" | "stats" | "current_wallpaper" (required; wallpaper/organize/clean are DISABLED)

youtube_video
  action: "get_info" | "trending" (required; play/summarize are DISABLED — use browser_control youtube_search to watch)
  url: string (for get_info)

weather_report
  city: string (required)

code_helper
  action: "explain" (required; write/edit/run are DISABLED)
  description: string (required)
  language: string (optional)
  output_path: string (optional)
  file_path: string (optional)

EXAMPLES:

Goal: "research mechanical engineering and save it to a notepad file"
Steps:

web_search | query: "mechanical engineering overview definition history"
web_search | query: "mechanical engineering applications and future trends"
file_controller | action: write, path: desktop, name: mechanical_engineering.txt, content: "MECHANICAL ENGINEERING RESEARCH\n\nThis file will be filled with web research results."

Goal: "What is the price of Bitcoin"
Steps:

web_search | query: "Bitcoin price today USD"

Goal: "List the files on the desktop and find the largest 5 files"
Steps:

file_controller | action: list, path: desktop
file_controller | action: largest, path: desktop, count: 5

Goal: "What games are installed on Steam?"
Steps:

game_updater | action: list, platform: steam

Goal: "Play latest MrBeast video on Yandex browser"
Steps:

browser_control | action: youtube_search, browser: yandex, query: "MrBeast latest video"
browser_control | action: click, browser: yandex, text: "MrBeast"

Goal: "Watch something on YouTube in Chrome"
Steps:

browser_control | action: youtube_search, browser: chrome, query: "funny videos"
browser_control | action: click, browser: chrome, text: "video"

Goal: "Search for weather in Chrome"
Steps:

browser_control | action: search, browser: chrome, query: "weather today", engine: google

Goal: "Open the clock and set a reminder for 30 minutes later"
Steps:

reminder | date: [today], time: [now+30min], message: "Reminder"

Goal: "Найди информацию о квантовых компьютерах"
Steps:

web_search | query: "квантовые компьютеры принцип работы", language: "ru", locale_strategy: "bilingual"

Goal: "Was sind die besten Python-Frameworks?"
Steps:

web_search | query: "beste Python Frameworks Vergleich", language: "de"

Goal: "что нового у Claude Code"
Steps:

web_search | query: "Claude Code latest features updates 2025", language: "ru", locale_strategy: "bilingual"

Goal: "открой официальную документацию"
Steps:

web_search | query: "official documentation site:docs", language: "ru"

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


from config.loader import get_api_key as _get_api_key
from config.loader import get_model as _get_model


def create_plan(goal: str, context: str = "", semantic_hint: str = "") -> dict:
    """
    Build a plan for the given goal.

    Args:
        goal:          The user's goal (may be enriched by semantic_interpreter)
        context:       Additional context from dialogue_state or prior results
        semantic_hint: Pre-classified action hint from semantic_interpreter

    Returns:
        Plan dict with 'goal' and 'steps' keys.
    """
    # Дверь к модели во всём проекте одна: core/aux_model.aux_call.
    # Параметра «системная инструкция» у общей двери нет и не будет:
    # расширять её ради одного вызывающего значит трогать файл, через который
    # ходят память, характер, зрение и сборщик фраз. Правила планировщика
    # едут первым куском промпта, а требование JSON повторяется в конце.
    from core.aux_model import aux_call

    # Без ключа загрузчик бросает исключение. Раньше оно улетало наружу и роняло
    # весь шаг: взятие ключа стояло ДО блока try. Теперь планировщик честно
    # уходит в запасной план, как при любой другой неудаче.
    try:
        api_key = _get_api_key()
    except Exception as _key_err:
        print(f"[Planner] ⚠️ ключ не найден ({type(_key_err).__name__}) — запасной план")
        return _fallback_plan(goal)
    model_name = _get_model("aux_light")

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nSession context:\n{context}"
    if semantic_hint:
        user_input += f"\n\nSemantic hint: {semantic_hint}"

    full_prompt = (
        f"{PLANNER_PROMPT}\n\n"
        f"----- TASK -----\n"
        f"{user_input}\n\n"
        f"Return ONLY the JSON object described above, nothing else."
    )

    ok, answer = aux_call(full_prompt, api_key, model=model_name, caller="Planner")
    if not ok:
        # Отказ двери — это не битый JSON: скормить его разборщику значит
        # получить в журнале ложную причину «JSON parse failed» вместо квоты.
        print(f"[Planner] ⚠️ планирование не вышло ({answer[:48]}) — запасной план")
        return _fallback_plan(goal)

    try:
        text = re.sub(r"```(?:json)?", "", answer.strip()).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") in ("generated_code",):
                print(f"[Planner] ⚠️ generated_code in step {step.get('step')} — replacing with web_search")
                desc = step.get("description", goal)
                step["tool"] = "web_search"
                step["parameters"] = {"query": desc[:200]}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    """
    Smarter fallback: detect whether the goal is an action, navigation, or search.
    Avoids blindly routing everything to web_search.
    """
    print("[Planner] 🔄 Fallback plan")

    goal_lower = goal.lower()

    # Navigation / open
    if any(k in goal_lower for k in ("открой", "open ", "launch", "запусти", "перейди")):
        if any(k in goal_lower for k in ("яндекс", "yandex", "chrome", "хром", "браузер", "browser")):
            return {
                "goal": goal,
                "steps": [{
                    "step": 1, "tool": "browser_control",
                    "description": f"Navigate: {goal}",
                    "parameters": {"action": "go_to", "browser": "yandex", "url": ""},
                    "critical": True,
                }]
            }
        return {
            "goal": goal,
            "steps": [{
                "step": 1, "tool": "open_app",
                "description": f"Open: {goal}",
                "parameters": {"app_name": goal},
                "critical": True,
            }]
        }

    # Default: search
    return {
        "goal": goal,
        "steps": [{
            "step": 1, "tool": "web_search",
            "description": f"Search: {goal}",
            "parameters": {"query": goal},
            "critical": True,
        }]
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    # Та же одна дверь; роль прежняя — aux_heavy.
    from core.aux_model import aux_call

    try:
        api_key = _get_api_key()
    except Exception as _key_err:
        print(f"[Planner] ⚠️ ключ не найден ({type(_key_err).__name__}) — запасной план")
        return _fallback_plan(goal)
    model_name = _get_model("aux_heavy")

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    full_prompt = (
        f"{PLANNER_PROMPT}\n\n"
        f"----- TASK -----\n"
        f"{prompt}\n\n"
        f"Return ONLY the JSON object described above, nothing else."
    )

    ok, answer = aux_call(full_prompt, api_key, model=model_name,
                          caller="Planner-Replan")
    if not ok:
        print(f"[Planner] ⚠️ перепланирование не вышло ({answer[:48]}) — запасной план")
        return _fallback_plan(goal)

    try:
        text     = re.sub(r"```(?:json)?", "", answer.strip()).strip().rstrip("`").strip()
        plan     = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"] = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)
