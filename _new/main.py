import asyncio
import threading
import json
import os
import sys
import traceback
from pathlib import Path

import pyaudio
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)
from memory.personality_engine import (
    should_analyze_personality,
    analyze_personality,
    update_personality,
)

from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.reminder          import reminder
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.cmd_control       import cmd_control
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.open_search_source import open_search_source

from core import instance_lock  # phase 0 step 4: one Jarvis at a time
from core import env  # Р11, шаг 34.1: кодировки и сокрытие секретов
from core.dialogue_state import update as _ds_update, format_for_prompt as _ds_prompt
from core.screen_share_manager import (
    get_manager as _get_ssm,
    ScreenShareSource, ScreenShareState,
)
from core.session_manager import (
    SessionState,
    SessionManager,
    ReconnectGuard,
    is_recoverable_error,
    is_fatal_error,
    classify_error,
)


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
from config.loader import get_model as _get_model
LIVE_MODEL          = _get_model("live_voice")
FORMAT              = pyaudio.paInt16
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

pya = pyaudio.PyAudio()


from config.loader import get_api_key as _get_api_key


def _analyze_screen_with_live(question: str, api_key: str) -> str:
    """
    Screen View analysis via the persistent Screen View live runtime.
    Called from _execute_tool when analyze_screen_view tool is invoked.

    Flow:
      1. Check ScreenShareManager state — must be ON (frame capture active).
      2. Check / lazily start the persistent ScreenViewRuntime.
      3. submit_question() → injects question into already-open live session.
      4. Return plain-text answer for the main Jarvis session to speak.

    Architecture:
      - ScreenViewRuntime holds ONE persistent live session per Screen View session.
      - Screen frames stream continuously in background.
      - Questions are injected into the existing session — no new session per question.
      - Completely separate from main Jarvis LIVE_MODEL and aux_model path.
      - No webcam. No model_guard. No shared quota.
    """
    # 2026-07: Live API отключил TEXT-модальность у ВСЕХ live-моделей
    # (websocket close 1007 на каждой — проверено пробником по models.list).
    # Персистентный ScreenViewRuntime поэтому невозможен; анализ идёт готовым
    # REST one-shot путём (кадр из ScreenShareManager → generate_content).
    # Runtime-код сохранён до этапа 5 (VoiceSession) — там решится его судьба.
    from core.screen_share_manager import analyze_current_view
    return analyze_current_view(question, api_key)


def _load_system_prompt() -> str:
    """
    Loads the master prompt (core/prompt.txt) and all sub-prompts
    from core/prompts/*.txt (alphabetical order).
    Adding a new *.txt file to core/prompts/ is all it takes to extend Jarvis.
    """
    sections = []

    # 1. Master prompt
    try:
        sections.append(PROMPT_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        sections.append(
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the correct tool."
        )

    # 2. Sub-prompts — every *.txt in core/prompts/, sorted alphabetically
    prompts_dir = PROMPT_PATH.parent / "prompts"
    if prompts_dir.is_dir():
        for sub in sorted(prompts_dir.glob("*.txt")):
            try:
                content = sub.read_text(encoding="utf-8").strip()
                if content:
                    sections.append(content)
            except Exception as e:
                print(f"[Prompt] Could not load sub-prompt {sub.name}: {e}")

    # 3. Honest capability limits — generated from the security policy so the
    #    model never advertises or attempts disabled actions (fixes the
    #    declarations/prompts drifting away from real behaviour).
    try:
        from core.security import build_capability_truth_section
        truth = build_capability_truth_section()
        if truth:
            sections.append(truth)
    except Exception as e:
        print(f"[Prompt] Could not build capability truth section: {e}")

    return "\n\n".join(sections)


# ── Global dedup guards (thread-safe) ──────────────────────────────────────────
_last_memory_input      = ""
_last_personality_input = ""
_memory_lock            = threading.Lock()
_personality_lock       = threading.Lock()


def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    """
    Background task: extract memory and personality from the last exchange.

    Guard policy:
      - If the shared ModelQuotaGuard is in cooldown, skip both subsystems
        immediately (single guard check at the top — avoids two separate
        checks that could race each other).
      - If Memory Stage1 returns False due to quota, we skip Personality too.
        Both subsystems share the same Gemini quota so there is no point in
        letting Personality probe right after Memory was refused.
    """
    global _last_memory_input

    # ── ФАЗА 1Е: третий путь в память ходит через дверь ───────────────────
    #
    # ЧТО БЫЛО. Эта функция — единственное место, где дом дописывает что-то
    # САМ, без просьбы владельца. Два вызова ниже (`update_memory` и
    # `update_personality`) шли мимо `core/gate.py`, то есть мимо правила
    # Г-2 «отказать можно ровно в одном месте». Следа в журнале не
    # оставалось: на вопрос «почему ты стал так отвечать?» ответить было
    # нечем, потому что правка личности не фиксировалась нигде.
    #
    # ПОЧЕМУ ЭТО ВАЖНЕЕ, ЧЕМ ЧАСТОТА. Замер живого разговора: 6 явных
    # сохранений против 0 само-записей. Путь РЕДКИЙ (раньше я ошибочно
    # называл его самым частым — утверждение отозвано). Но `personality.json`
    # — это манера речи самого Джарвиса, и тихая правка здесь меняет ЕГО, а
    # не сведения о владельце. Чинится из-за того, ЧТО пишет, не как часто.
    #
    # ЛОВУШКА, ПРОВЕРЕННАЯ ЖИВЫМ ЗАМЕРОМ ДО ПРАВКИ (29.08.2026):
    #     memory_self_write       -> blocked  Unknown tool ...
    #     personality_self_write  -> blocked  Unknown tool ...
    # Дверь по правилу fail-closed блокирует незнакомое имя. Позови её
    # отсюда раньше, чем имена появились в `core/security.py`, — авто-запись
    # умерла бы МОЛЧА, а все сторожа остались бы зелёными. Поэтому порядок
    # был: политика -> забор -> сторожа -> этот вызов.
    #
    # ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ:
    #   * ВЛАДЕЛЬЦА НЕ СПРАШИВАЮТ. risk=low -> policy auto -> вердикт `run`.
    #     Прямое решение владельца от 28.08.2026: «нет, мне надоест мне
    #     всегда подтверждать ему». Поднять риск = переспрашивать после
    #     каждой реплики.
    #   * МОДЕЛЬ ЭТОГО НЕ ВИДИТ. `planner_visible=False` в политике, и
    #     имён нет в TOOL_DECLARATIONS. Иначе модель начнёт звать их сама и
    #     получится ЧЕТВЁРТЫЙ путь в память вместо закрытия третьего.
    #   * НИЧЕГО НЕ ЗАПРЕЩЕНО. Владелец проходит как раньше, поведение то
    #     же. Появился след и одна точка, где это можно будет выключить.
    #   * РОЛЬ НЕ ПЕРЕДАЁТСЯ. Замер: в фоновом потоке `agent_role=None`,
    #     `origin=('owner','main')` — contextvars через границу потока не
    #     переносится. Опираться на роль здесь нельзя, она всегда
    #     «владелец». Запрет под-агенту живёт в `core/fences.py`
    #     (MEMORY_TOOLS) и срабатывает там, где ctx передан явно.
    #
    # ЭТО ФОНОВЫЙ ПОТОК. Исключение отсюда либо съедается молча, либо
    # всплывает в чужом месте — не годится ни то, ни другое. Поэтому
    # «дверь сломалась -> не пишем и печатаем», а не «падаем».
    #
    # ОТКАТ: удалить `_may_write_by_itself` и две проверки ниже.
    def _may_write_by_itself(what: str, fields) -> bool:
        try:
            from core import gate as _gate
            # В журнал уходят ИМЕНА полей, не значения (И45): авто-запись
            # может подхватить из разговора что угодно.
            verdict = _gate.dispatch(
                what,
                {str(f): None for f in (fields or ())},
                mode="interactive",
            )
        except Exception as _gate_err:
            print(f"[Memory] ⚠️ gate error — {what} refused (fail-closed): "
                  f"{_gate_err}")
            return False
        if not getattr(verdict, "allowed", False):
            print(f"[Memory] 🚫 gate refused {what}: "
                  f"{getattr(verdict, 'reason', '') or 'no reason given'}")
            return False
        return True

    user_text   = (user_text   or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    if len(user_text) < 5:
        return

    with _memory_lock:
        if user_text == _last_memory_input:
            return
        _last_memory_input = user_text

    # ── Top-level guard check — abort both subsystems if in cooldown ──────────
    try:
        from core.aux_model import default_model
        from core.model_guard import get_guard
        guard = get_guard()
        aux_m = default_model()
        if not guard.is_available(aux_m):
            rem = guard.cooldown_remaining(aux_m)
            print(f"[Aux] ⏳ Skipping memory+personality — quota cooldown {rem:.0f}s remaining")
            return
    except Exception:
        pass

    try:
        api_key = _get_api_key()
    except Exception as e:
        print(f"[Memory] ⚠️ Could not load API key: {e}")
        return

    # ── Memory Stage1 + Stage2 ────────────────────────────────────────────────
    memory_quota_hit = False
    try:
        if should_extract_memory(user_text, jarvis_text, api_key):
            data = extract_memory(user_text, jarvis_text, api_key)
            if data:
                if not _may_write_by_itself("memory_self_write",
                                            data.keys()):
                    return
                update_memory(data)
                print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        err = str(e)
        if "429" in err or "RESOURCE_EXHAUSTED" in err:
            memory_quota_hit = True
            try:
                from core.aux_model import default_model
                from core.model_guard import get_guard
                get_guard().handle_exception(e, default_model())
            except Exception:
                pass
        else:
            print(f"[Memory] ⚠️ {e}")

    # ── Personality Stage1 + Stage2 — skip if memory just hit quota ──────────
    if memory_quota_hit:
        print("[Personality] ⏭ Skipping Stage1 — memory just hit quota, guard active")
        return

    try:
        if should_analyze_personality(user_text, jarvis_text, api_key):
            p_data = analyze_personality(user_text, jarvis_text, api_key)
            if p_data:
                # Отдельный вердикт, а не общий с памятью: по журналу должно
                # быть видно, ЧТО именно записали — факты или манеру речи.
                if not _may_write_by_itself("personality_self_write",
                                            p_data.keys()):
                    return
                update_personality(p_data)
    except Exception as e:
        err = str(e)
        if "429" not in err and "RESOURCE_EXHAUSTED" not in err:
            print(f"[Personality] ⚠️ {e}")


TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens applications on the computer — ANY installed app by name, including "
            "Russian names ('фотошоп', 'телеграм', 'дискорд') and slightly imprecise "
            "names (it fuzzy-matches installed apps). ALWAYS use this to open an app; do "
            "NOT use computer_control / screen clicks to open or launch an application. "
            "IMPORTANT for browsers: 'yandex'/'яндекс' = Yandex Browser, 'chrome'/'хром' = Google Chrome. "
            "To interact with elements INSIDE a running app (buttons, channels, tabs) → use computer_control with screen_click."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Application name: 'yandex' (Yandex Browser), 'chrome' (Google Chrome), 'firefox', 'spotify', etc."
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web and performs source-backed research. "
            "MODES: 'search' (quick search), 'compare' (compare items), "
            "'research' (deep multi-source research), 'verify' (strict fact-checking with sources). "
            "Use 'verify' or strict=true when user asks for reliable/accurate/confirmed information. "
            "For simple well-known facts (what is Python, what is API), answer directly without search. "
            "For current events, prices, versions, docs, or verification requests — always search. "
            "MULTILINGUAL: pass language='ru'/'de'/etc. when the user writes in a non-English language. "
            "Use locale_strategy='bilingual' for topics where both native + English sources help."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query — always write in the user's own language"},
                "mode":   {"type": "STRING", "description": "search | compare | research | verify (default: search)"},
                "strict": {"type": "BOOLEAN", "description": "Enable strict verification mode — forces multi-source checking"},
                "max_results": {"type": "INTEGER", "description": "Maximum results to inspect (default: 5)"},
                "search_type": {"type": "STRING", "description": "web | news (default: web)"},
                "include_sources": {"type": "BOOLEAN", "description": "Include source list in answer (default: true)"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare (for compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | general"},
                "language": {"type": "STRING", "description": "ISO-639-1 code of query language (e.g. ru, de, fr, zh). Auto-detected when absent."},
                "languages": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Explicit list for bilingual search, e.g. ['ru', 'en']"},
                "locale_strategy": {"type": "STRING", "description": "bilingual | monolingual | auto (default: auto). bilingual merges native + English results."},
                "response_language": {"type": "STRING", "description": "ISO-639-1 code for the answer language. Defaults to language."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "open_search_source",
        "description": (
            "Opens a source from the most recent web_search or deep_research result. "
            "Use when the user says open source 1/2/3, open that source, open the Forbes source, "
            "open the article in Yandex or Chrome. "
            "By default, prefer opening it in a new tab of an already running Yandex or Chrome browser "
            "when possible."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "source_index":            {"type": "INTEGER", "description": "Source number from the most recent displayed sources"},
                "source_match":            {"type": "STRING",  "description": "Optional title/domain substring like Forbes, TechCrunch, OpenAI"},
                "browser":                 {"type": "STRING",  "description": "Preferred browser: yandex | chrome"},
                "prefer_existing_browser": {"type": "BOOLEAN", "description": "Prefer using an already running browser (default: true)"},
                "prefer_new_tab":          {"type": "BOOLEAN", "description": "Prefer opening in a new tab (default: true)"}
            },
            "required": []
        }
    },
    {
        "name": "weather_report",
        "description": "Gets real-time weather information for a city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "reminder",
        "description": (
            "Manages timed reminders in the user's local time zone. "
            "Actions: set (default) — create new reminder; update — change time of existing; "
            "cancel — delete by message keyword; list — show all pending. "
            "For 'update' and 'cancel', set message to keyword of the reminder to target."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "set (default) | update | cancel | list"},
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format (required for set/update)"},
                "time":    {"type": "STRING", "description": "Time in HH:MM 24h local time (required for set/update)"},
                "message": {"type": "STRING", "description": "Reminder text (set/update) or keyword to find reminder (cancel/update)"}
            },
            "required": []
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "YouTube info (read-only): video metadata by URL or trending videos. "
            "Playing/summarizing is DISABLED — to watch, use browser_control youtube_search."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "get_info | trending"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls a web browser. IMPORTANT: Always specify 'browser' parameter! "
            "Use same_tab=true when user says 'в той же вкладке', 'same tab', 'тек��щей вкладке', 'этой странице'. "
            "Use same_tab=false (default) for new searches or when user doesn't specify. "
            "For YouTube: use action='youtube_search' with query. "
            "THIS TOOL ACTS: it navigates and REPLACES what the user is looking at. "
            "It can NOT answer 'какая страница открыта / что открыто в хроме' — "
            "for those questions use resolve_reference with kind=active_page."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | youtube_search | click | type | scroll | smart_click | smart_type | press | close"},
                "browser":     {"type": "STRING", "description": "REQUIRED: chrome | yandex | firefox | edge"},
                "same_tab":    {"type": "BOOLEAN", "description": "Use SAME tab instead of opening new. Set true when user says 'same tab'/'той же вкладке'/'этой странице'. Default: false"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search/youtube_search"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | yandex | youtube"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders inside the user's personal folders "
            "(Desktop, Downloads, Documents, Pictures, Music, Videos). "
            "ALLOWED: list, read, find, largest, disk_usage, info, copy, "
            "create_file, create_folder, write, move, rename, undo. "
            "Can create real Word (.docx) and Excel (.xlsx) documents — just give "
            "the file name that extension. "
            "write/move/rename may ask for confirmation first. "
            "For 'верни как было' / 'отмени изменение' / 'верни ещё назад' after write/move/rename, "
            "use action='undo' with the file path. Undo is MULTI-LEVEL, but each undo call reverts "
            "EXACTLY ONE step. To go back N steps you MUST call undo N separate times — never claim you "
            "performed several undos unless you actually called it that many times. "
            "REDO: undo is FULLY REVERSIBLE. For 'верни как было' / 'верни вперёд' / 'верни обратно' / "
            "'отмени отмену' after one or more undos, use action='redo' — it re-applies the last undone step. "
            "Call redo repeatedly to walk forward N steps. So if you undid one step too many, just redo. "
            "GROUND YOURSELF IN REALITY: never state the file's content from memory or assumption. Every "
            "undo/redo result NOW reports the file's ACTUAL current content ('Сейчас в файле: «...»') and how "
            "many steps remain forward/back — report EXACTLY that content; if it says the file is gone, say so. "
            "To jump to a SPECIFIC or NAMED version (e.g. 'верни версию со стихом', 'верни где привет'), FIRST call "
            "action='history' to see the timeline with a content preview of every step forward and back, THEN "
            "call undo/redo the right number of times until the returned content matches. Do NOT announce you "
            "reached a version until the content in the result actually matches what the user asked for. "
            "NEVER invent or list version steps the tool did not return: if action='history' shows no back/forward "
            "steps, say the timeline is empty for this session instead of reciting remembered versions. "
            "MEMORY ACROSS SESSIONS: per-file undo/redo/history survive app restarts, so ALWAYS pass the file name/path "
            "with undo/redo/history to navigate that file's real saved timeline (do not rely on an abstract 'undo last'). "
            "WHEN THE USER NAMES A FILE, pass that exact name straight to the tool and DO NOT ask which file "
            "they mean - even if the name has spaces or reads like a description (e.g. 'новый файл.txt', "
            "'мой отчёт.txt'). The tool resolves it and reports the real state; only ask for clarification if the "
            "tool itself returns that no such file exists. "
            "UNDO/REDO/HISTORY ARE PER FILE: always pass the file's name/path so navigation stays inside that "
            "file and never touches another file or an earlier session. CREATE vs OVERWRITE: if action='create_file' "
            "targets a file that ALREADY exists, the tool returns a confirmation question instead of writing — relay "
            "it to the user and re-call ONLY after they agree, echoing back the exact consent_id the gate gave you; never claim the file was created "
            "until the tool actually reports Created/Overwrote. "
            "undoing a file's ORIGINAL creation DELETES the file, but this too can be reversed with redo "
            "(redo recreates it). Read each result: if it says the file was deleted ('файл удалён'), tell the "
            "user honestly the file is gone right now, and that you can bring it back with redo. "
            "A NEW write/create/move/rename clears the redo history (you cannot redo after new changes). "
            "Never say you can only undo the last action, and never say a deletion from over-undo is permanent. "
            "GROUND FILE EXISTENCE IN REALITY: NEVER tell the user a file does not exist from memory or assumption, and never give up after one guess. First LOOK: call action='find' with the word the user gave. find matches BOTH file names AND text CONTENT, because people refer to a file by what is INSIDE it (e.g. 'файл привет' may be a file whose CONTENT is привет, such as проба.txt). If find or a failed read returns candidate matches, offer them to the user instead of denying. Only say a file is missing AFTER find returns nothing. "
            "DELETING IS ALLOWED AND REVERSIBLE: action='delete' moves the file to the Windows "
            "Recycle Bin, so it can be brought back. Undo returns it to its original place AND removes it from the Bin, so there is never a leftover copy. The gate will ask you to "
            "confirm first: relay that question to the user out loud, word for word, and only re-call after "
            "they clearly agree — echoing back the EXACT consent_id from that message with every other parameter unchanged. Never invent, guess or reuse a consent_id, and never ask the user for permission BEFORE calling the tool: the gate decides what is risky and hands you the question, so asking first makes the user answer twice. Deletion is REVERSIBLE (Recycle Bin + undo), so never call it high-risk or hard to undo. Never tell the user that deletion is "
            "disabled or unavailable. After a successful delete, say the file was deleted and that "
            "you can restore it with undo. "
            "STILL DISABLED (return a security notice): organize_desktop."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | undo | redo | history | find (matches file NAME and, via content=, text CONTENT) | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "Target folder shortcut (desktop, downloads, documents, pictures, music, videos, home) OR a full path. For create_file/create_folder/write you may give a folder here and put the file name in 'name', OR give the complete file path here directly."},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Text to put in the file (create_file/write). For action=find, text to search for INSIDE files (find a file by what it contains)."},
                "name":        {"type": "STRING", "description": "The file or folder name. REQUIRED for create_file/create_folder/write (e.g. 'notes.txt', 'report'). Also used as the name to look for in find."},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "cmd_control",
        "description": (
            "Reads read-only system information in natural language: disk space, "
            "running processes, IP/network settings, system specs, CPU/RAM usage, "
            "Windows version, battery, current date/time, the list of "
            "installed programs (read from the Windows registry), and "
            "listings of the Desktop and Downloads folders. "
            "It CANNOT install, delete, modify or "
            "open anything, and it cannot invent commands - other requests are refused."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task": {"type": "STRING", "description": "Natural language description of the system information you need"},
            },
            "required": ["task"]
        }
    },
    {
        "name": "desktop_control",
        "description": (
            "Desktop info (read-only): list desktop files, stats, current wallpaper path. "
            "Changing wallpaper / organizing / cleaning is DISABLED."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | stats | current_wallpaper"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Explains code (action='explain' only). Writing/editing/running/building is DISABLED.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "explain"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": (
            "USE THIS to interact with any element on screen or inside any running app. "
            "For in-app navigation: FIRST action='focus_window' to bring app to front, "
            "THEN action='screen_click' with description of element. "
            "Examples: open Telegram channel = focus_window('Telegram') + screen_click('channel name'); "
            "close popup = screen_click('close button on popup'). "
            "Also: type, hotkey, press, scroll, screenshot, clipboard. "
            "action='screen_status' is READ-ONLY and reports whether "
            "clicking is allowed at this moment. If a screen action is "
            "refused, or the user says they just enabled screen control, "
            "call screen_status and retry -- never repeat a refusal from "
            "memory, and never use screen_share_control for this."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | screen_status | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request (read-only): "
            "listing installed games and checking download/schedule status. "
            "Installing/updating is DISABLED. NEVER use agent_task for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "list | download_status | schedule_status"},
                "game_name": {"type": "STRING", "description": "Game name"},
                "platform":  {"type": "STRING", "description": "steam | epic"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save something about the user that will still be true next week. "
            "TWO kinds qualify. (1) Personal facts: name, age, city, job, "
            "preferences, hobbies, relationships, projects, future plans. "
            "(2) STANDING INSTRUCTIONS about how to treat them: how to explain "
            "things, how long answers should be, which terms to leave untranslated, "
            "what to warn about before acting, what never to suggest. "
            "A standing instruction is NOT obeyed-and-forgotten: agreeing in the "
            "conversation lasts only until restart, so you MUST save it as well. "
            # ШАГ 2''. Было «Call this silently whenever either kind comes up».
            # Слово «silently» здесь лишнее: ниже стоит отдельное правило «Do
            # NOT announce that you are saving — just call it silently», и оно
            # разобрано подробнее. Убрал повтор, чтобы влезть в потолок знаков
            # честным сокращением, а не поднятием потолка.
            "Call this whenever either kind comes up. "
            # ШАГ 1, 29.08.2026. Критерий В1+В2 прямым текстом. Раньше здесь
            # был только запрет («не пиши сиюминутное»), и модель понимала его
            # шире, чем нужно: фраза «кот опять лёг на клавиатуру» — событие,
            # поэтому не писалось НИЧЕГО, хотя в ней спрятано свойство «у
            # владельца есть кот», которое верно и через год. Запрет без
            # умения извлечь свойство превращается в глухоту.
            "TWO QUESTIONS decide it. (1) Is this about THEM or about the "
            "WORLD? Facts about the world ('the RTX 5070 ships in March') are "
            "search results, not memory. Only they and the people, animals and "
            "things in their life belong here. (2) Is this a PROPERTY or an "
            "EVENT? A property holds until they change it: lives in Moscow, has "
            "a cat, plays shooters, hates being asked twice. An event is over "
            "when the sentence ends: tired today, went fishing yesterday, opened "
            "notepad just now. Save PROPERTIES. "
            "An event often REVEALS a property, and that is the case you must "
            "not miss: 'the cat is on my keyboard again' is an event, but it "
            "reveals that they have a cat — save the cat, not the keyboard. "
            "'Went fishing with Lyokha' reveals a friend named Lyokha. "
            # ШАГ 2'', 29.08.2026. ДВА ЖИВЫХ ОТКАЗА ИЗ ОДНОГО ЖУРНАЛА, и они
            # ПРОТИВОПОЛОЖНЫЕ — поэтому крутить «строже/мягче» бесполезно:
            #   1) «кот опять лёг на клавиатуру» -> записалось
            #      notes/cat_on_keyboard_event='Cat is on the keyboard again'.
            #      Ключ буквально _event. Запрет на события был НА МЕСТЕ и
            #      разобран на этом же примере — значит правило модель знает.
            #      Причина (probe35): свойство «есть кот» УЖЕ было записано
            #      (cat_name=Tigr), извлекать нечего, а давление писать
            #      осталось. Не было сказано, что бывает НЕЧЕГО записывать.
            #   2) «бл опять алекс сломал мой телефон» -> НЕ записалось НИЧЕГО,
            #      хотя Алекса в relationships нет, а «опять» говорит, что он
            #      в жизни владельца постоянный. Ровно случай Лёхи из Ш1.
            #      Причина (probe36): пример был только РАЗРЕШАЮЩИЙ (рыбалка
            #      открывает друга); что новый ЧЕЛОВЕК — это свойство даже в
            #      брани и жалобе, сказано не было.
            "NOTHING NEW IS A VALID OUTCOME. If the property behind a remark "
            "is ALREADY in the memory block, do not call this tool at all. "
            "'The cat is on my keyboard again' when you already know the cat "
            "adds nothing, and saving it as cat_on_keyboard_event stores a "
            "moment that is false an hour later. Calling this with nothing new "
            "is worse than not calling it. "
            "BUT A NEW PERSON IS ALWAYS A PROPERTY, however the sentence "
            "sounds. Complaints, swearing and anger still carry facts: 'Alex "
            "broke my phone AGAIN' names a person who is not in your memory "
            "yet, and 'again' says he is a permanent part of their life — save "
            "Alex. Never skip a fact because the tone was rude or negative; "
            "you are not being asked to approve of it, only to remember it. "
            "Do NOT call for: passing moods ('I'm tired today'), one-time commands "
            "('open notepad'), questions, reactions, weather, reminders, or searches "
            "when no property hides inside them. "
            # ШАГ 2''. Здесь стояло «Those belong to this conversation and must
            # die with it» — та же мысль, что в правиле про событие двумя
            # абзацами выше («An event is over when the sentence ends»), только
            # другими словами. Убрал вместо того, чтобы поднимать потолок
            # знаков в тесте: повтор занимает промпт и ничего не добавляет.

            # ШАГ 2′, 29.08.2026. ВЛАДЕЛЕЦ УСЛЫШАЛ ДЕЛОПРОИЗВОДИТЕЛЯ, а не
            # Джарвиса: «Отмечено, сэр. У вас есть друг по имени Лёха»,
            # «Обновляю информацию: вашего кота зовут Лев». Разобрал по живому
            # журналу (probe34): запрет ниже говорил только «не объявляй
            # ДЕЙСТВИЕ», и модель его СОБЛЮДАЛА — слова «сохраняю» нет ни в
            # одной реплике. Но она пересказывала РЕЗУЛЬТАТ, а на пересказ
            # запрета не было нигде: он есть только для блока памяти
            # («never recite like a list»), для свежей записи — не было.
            # Мало запретить: модели надо дать, что говорить ВМЕСТО отчёта,
            # иначе пустоту снова займёт расписка. Отсюда вторая половина.
            "Do NOT announce that you are saving, and do NOT read the saved "
            "fact back. Saving is bookkeeping, and bookkeeping is not "
            "conversation: 'Noted, sir, you have a cat' and 'Updating: your "
            "cat is Lev' are filing receipts, not replies — they tell the user "
            "what they just told you. "
            "They said something about their LIFE, so answer THAT. 'The cat is "
            "on my keyboard again' is about a cat walking on a keyboard: react "
            "to the cat. 'Went fishing with Lyokha' is about a day off, not "
            "about a friend record being created. Reply like someone who just "
            "heard it, and file it in the background where they never see it. "
            # ШАГ 2'', 29.08.2026. ЗДЕСЬ БЫЛО «say nothing and just save» —
            # моя же формулировка, и она читается как «промолчи И ЗАПИШИ»,
            # то есть утверждает, что запись всё равно происходит. Замер
            # (probe35): разрешения молчать ГОЛОСОМ в тексте было целых три,
            # а разрешения НЕ ПИСАТЬ В БАЗУ — ни одного. Для модели это
            # разные вещи, и она подчинилась давлению.
            "Some remarks need no reply at all — then say nothing. "
            "A correction ('no, his name is Tigr') needs no receipt either — "
            "least of all a second one. "
            # Владелец 28.08.2026 услышал ответ ДВАЖДЫ подряд («меренговый
            # торт»): модель озвучила фразу, вызвала save_memory и после
            # ответа инструмента озвучила ТО ЖЕ ещё раз. Инструмент при этом
            # сработал один раз — проверено по журналу двери. Правило ниже
            # адресовано ровно этому: у протокола нет способа запретить
            # второй ответ (scheduling=SILENT работает только для
            # NON_BLOCKING, а перевод туда воспроизводит дефект стабильно —
            # см. тред Google от 07.01.2026), поэтому остаётся сказать
            # модели прямым текстом. Это СНИЖАЕТ частоту, а не гарантирует
            # ноль: инструкция — просьба, не запрет.
            "After the save succeeds, do NOT speak again: you have ALREADY "
            "replied in this turn, and repeating the same sentence a second "
            "time makes the assistant sound broken. One reply per turn. "
            # ШАГ 1. Предупреждение написано ПО ЗАМЕРУ, а не по плану. План
            # утверждал, что замена безопасна, потому что в схеме есть
            # superseded_by. Проверил делом (probe26): upsert_fact этот столбец
            # НЕ заполняет — повтор той же пары (категория, ключ) делает UPDATE
            # живой строки, и прежнее значение исчезает без следа. «Mercedes»
            # после записи «BMW» в базе не остаётся ничем.
            # Поэтому текст говорит модели правду: замена — необратима.
            # ШАГ 2′, 29.08.2026. Написано ПО ЗАМЕРУ, против собственного плана.
            # План предлагал искать кандидатов через search_facts перед записью.
            # Замерил (probe27-29): на замене — том самом случае, ради которого
            # это и затевалось — поиск СЛЕПОЙ. «Химки» и «BMW» не имеют ни одного
            # общего слова со «Moscow»/«Mercedes-Benz» в индексе, поиск вернул
            # ПУСТО, и verbatim не спасает (он тоже про старое значение).
            # Зато замерил другое (probe30-33): блок памяти уже печатает
            # «Favorite Cars: Mercedes-Benz cars», и ключ восстанавливается из
            # подписи однозначно — 23 из 23 ключей, включая buy_rtx_5070 и «x».
            # Сведения у модели УЖЕ ЕСТЬ и стоят 0 знаков. Значит нужен текст,
            # а не поиск. Граница честная: выше ~85 фактов часть подписей
            # выпадает из блока по бюджету, и тогда это правило слепнет тоже.
            "BEFORE choosing a key, look at the memory block in your "
            "instructions: each line there IS a saved key, spelled out — "
            "'Favorite Cars: ...' is the key favorite_cars, 'Cat Name: ...' is "
            "cat_name. If the property is already on that list, reuse THAT key "
            "instead of inventing a synonym. "
            "And make the key mean what it holds: naming a key cat_name and "
            "storing 'has a cat' promises a name that is not there, so the next "
            "session believes the cat has been named. If you only know the "
            "animal exists, the key is has_cat; cat_name is for when you know "
            "the name. "
            "ONE PROPERTY, ONE KEY. Reusing a key you already saved REPLACES "
            "the old value and the old value is GONE — there is no history to "
            "restore it from. So reuse a key only when it is the SAME property "
            "changing ('lives in Moscow' -> 'lives in Khimki'). When you are "
            "not sure whether it is the same property, use a NEW key: a spare "
            "duplicate is untidy and can be cleaned up later, but an overwrite "
            "destroys something they told you. "
            "To REMOVE or UNDO a fact, use forget_memory — NEVER overwrite it with "
            "a value like 'disregard' or 'updated', that just leaves stale junk. "
            "Values must be in English regardless of the conversation language. "
            "ALWAYS also pass 'said': the user's own sentence, copied word for word "
            "in the language they spoke it. The English value is for you; their own "
            "words are how they will ask for this again later."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "communication_habits — STANDING INSTRUCTIONS on how to treat them: "
                        "answer length, tone, jargon level, which terms to leave in English, "
                        "warnings required before touching files, things never to suggest | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
                "said":  {"type": "STRING", "description": (
                    "The user's ORIGINAL sentence, verbatim, in their own language. "
                    "Never translate it, never shorten it, never clean it up. This is "
                    "what makes the fact findable later when they ask in their own words."
                )},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "forget_memory",
        "description": (
            "Delete a previously saved fact from long-term memory. "
            "Call this whenever the user asks to forget, remove, delete, or take "
            "back something you saved, or says a saved fact is no longer true "
            "(e.g. 'убери это из памяти', 'забудь', 'я передумал'). "
            "NEVER fake forgetting by calling save_memory with a value like "
            "'disregard previous' or 'updated' — that leaves the stale fact in "
            "memory and is a lie to the user. To forget you MUST call this tool. "
            "If you are unsure of the exact key, pass your best guess for key and "
            "category; the system also searches every category for that key. "
            "Only tell the user it is gone AFTER this returns 'Forgotten:'. If it "
            "returns 'Not found', tell them there was nothing saved to remove."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "Best guess at the category the fact was saved under (identity, preferences, projects, relationships, wishes, notes, or any custom one like habits/hobbies). If unsure, still pass your best guess — all categories are searched by key."},
                "key":      {"type": "STRING", "description": "The snake_case key to delete (e.g. work_schedule, favorite_color, sister_name)."},
            },
            "required": ["key"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Search everything ever saved about the user, in their own words or "
            "yours, in any language. The memory block in your instructions is "
            "only a summary and does not hold everything. "
            "CALL THIS BEFORE saying you do not know, do not remember, or were "
            "never told something: saying that without searching is how you deny "
            "a fact the user is certain they gave you. "
            "Also call it whenever the conversation touches something they may "
            "have mentioned before - a habit, a preference, a project, a person, "
            "a rule they set for you. "
            "Search with the words THEY used, not a translation. "
            "Call it silently; never narrate that you are checking your memory. "
            "If it returns nothing, say plainly that you have nothing saved - "
            "never invent a memory to fill the gap."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "What to look for, in the user's own words. Short queries work best."},
            },
            "required": ["query"]
        }
    },
    {
          "name": "analyze_screen_view",
          "description": (
              "Analyzes what is currently visible on the shared screen or selected window. "
              "ONLY call this tool when Screen View mode is ON (user enabled it via UI). "
              "Use when user asks: what is on the screen, what do you see, explain this interface, "
              "what is happening here, what service is this, what should I click, "
              "explain the current video, what is in this window. "
              "Returns a text description of screen content with actionable guidance."
          ),
          "parameters": {
              "type": "OBJECT",
              "properties": {
                  "question": {
                      "type": "STRING",
                      "description": "The user's question about what is on the screen"
                  }
              },
              "required": ["question"]
          }
      },
      {
          "name": "screen_share_control",
          "description": (
              "Controls Screen View mode. Use 'start' to enable screen capture/vision. "
              "Use 'stop' to disable it. Use 'status' to report the current state. "
              "Do NOT use this to start screen control/clicking — that is a different system."
          ),
          "parameters": {
              "type": "OBJECT",
              "properties": {
                  "action": {
                      "type": "STRING",
                      "description": "start | stop | status"
                  }
              },
              "required": ["action"]
          }
      },
      {
          "name": "system_context",
          "description": (
              "Reports what is currently open on the computer (active window + open "
              "user apps) AND what changed in the user's folders recently. Use when "
              "the user asks 'что у меня сейчас ��ткрыто?', 'что изменилось за "
              "последние N минут?', 'what am I doing right now?'. Read-only."
          ),
          "parameters": {
              "type": "OBJECT",
              "properties": {
                  "minutes": {
                      "type": "INTEGER",
                      "description": "Time window for recent file changes (default: 5)."
                  }
              },
              "required": []
          }
      },
      {
          "name": "resolve_reference",
          "description": (
              "Resolves a vague reference to a CONCRETE file/folder/app using what "
              "the user recently did on the PC. Call BEFORE acting when the user "
              "says: 'что я недавно запускал' (kind=last_launched_app), 'что я только "
              "что установил' (kind=last_installed_app), 'в открытой папке' / 'со��дай "
              "тут' (kind=open_folder), 'этот файл', 'файл, который я скачал' "
              "(kind=downloaded_file), "
              "'какой последний файл в загрузках/документах' (kind=newest_in_folder "
              "+ folder), 'последний документ' (kind=recent_file), 'папка, которую я "
              "добавил' (kind=new_folder), 'сохрани туда же' (kind=same_folder), 'тот "
              "JSON-файл' (kind=by_extension + extension), 'это пр��ложение' "
              "(kind=active_app), 'какой документ у меня сейчас открыт?' / "
              "'что я сейчас редактирую' / 'где лежит этот файл' / "
              "'открой папку этого документа' (kind=active_document — the document "
              "in the FOREGROUND window right now; do NOT use recent_file for "
              "this, that answers a different question and can name a file the "
              "user already closed). "
              "'какая страница сейчас открыта' / 'что открыто в хроме' / "
              "'что сейчас активно' / 'а щас?' / 'а теперь?' / 'что у меня "
              "открыто перед глазами' (kind=active — describes the foreground "
              "WITHOUT guessing a category first; use this whenever the user "
              "does not clearly say 'document' or 'page', and for every "
              "follow-up question that just asks 'а сейчас?'). "
              "'нет, а happ' / 'а что в окне Word' (kind=named_window + name — "
              "read-only description of a window the user named; NEVER call "
              "computer_control/focus_window to answer a question). "
              "'что у меня вообще открыто' (kind=all_windows). "
              "'а в проводнике что' / 'а в браузере?' / 'что в терминале' "
              "— the user named a window by its ROLE, not by the program name: "
              "kind=named_window + name=the user's own word ('проводник', "
              "'браузер', 'терминал', 'папка', 'архив'). "
              "ALWAYS pass query with the user's raw phrase, whatever the "
              "kind: it lets the layer recover the target when the kind is "
              "wrong or the name was dropped. "
              "'какая вкладка активна' (kind=active_page, optional browser — "
              "reads browser window titles; NEVER call browser_control for such "
              "questions, it would navigate away from the very page being asked "
              "about). Read-only: returns the full path/name as text."
          ),
          "parameters": {
              "type": "OBJECT",
              "properties": {
                  "kind": {
                      "type": "STRING",
                      "description": (
                          "active | named_window | all_windows | "
                          "active_document | active_page | active_app | last_launched_app | "
                          "last_installed_app | "
                          "open_folder | downloaded_file | newest_in_folder | recent_file "
                          "| new_folder | same_folder | by_extension"
                      )
                  },
                  "browser": {
                      "type": "STRING",
                      "description": "Optional browser for kind=active_page: chrome | edge | firefox | yandex."
                  },
                  "name": {
                      "type": "STRING",
                      "description": (
                          "Window or application the user named, for "
                          "kind=named_window (e.g. 'happ', 'word', '7-zip'). "
                          "Read-only lookup — NEVER use computer_control/"
                          "focus_window to answer a question about a window."
                      )
                  },
                  "query": {
                      "type": "STRING",
                      "description": (
                          "The user's raw phrase, verbatim, e.g. "
                          "'а в проводнике что'. Always pass it. Read-only."
                      )
                  },
                  "extension": {
                      "type": "STRING",
                      "description": "File extension for kind=by_extension (e.g. 'json', '.pdf')."
                  },
                  "folder": {
                      "type": "STRING",
                      "description": "Folder name for kind=newest_in_folder: downloads | documents | desktop | pictures | music | videos."
                  }
              },
              "required": ["kind"]
          }
      },
      {
          "name": "open_path",
          "description": (
              "Opens a file or folder, given a full path (usually one you just got "
              "from resolve_reference). Use for 'открой этот файл', 'открой то, что я "
              "скачал', 'открой эту папку'. To open in a SPECIFIC application (Notepad/"
              "блокнот, VS Code, Paint, a browser, Word/Excel…), pass 'app' — this is "
              "the CORRECT way to honour 'открой в блокноте'. Do NOT use computer_control "
              "/ screen clicks to open a file in an app. Only paths inside the user's "
              "personal folders are allowed; executable files are refused."
          ),
          "parameters": {
              "type": "OBJECT",
              "properties": {
                  "path": {"type": "STRING", "description": "Full path to the file or folder to open."},
                  "app":  {"type": "STRING", "description": "Optional app to open it in, e.g. 'notepad', 'блокнот', 'vs code', 'paint', 'chrome'. Omit to use the default app."}
              },
              "required": ["path"]
          }
      }
  ]


# ── Stage 3C: the universal `confirmed` parameter is GONE ───────────────────────
# It used to be added to every tool declaration here (Stage 2). Stage 3A replaced
# it with durable consent tickets, but the field stayed in the schema — so the
# model kept asking the user first, setting confirmed=true, and then being told
# by the gate to ask the very same question a second time. A mechanism that is
# still in the contract is still alive, whatever the system prompt says about it.
# The contract is now single: `consent_id` below is the ONLY way to confirm.
# The runtime still strips a hallucinated `confirmed` key (see _execute_tool and
# gate.dispatch) and core.security still understands it for the legacy path when
# the durable-consent flag is OFF, but no tool advertises it any more.


# ── Stage 3A: universal `consent_id` parameter ──────────────────────────────────
# The durable replacement for `confirmed`. The gate hands out a consent id with
# every CONFIRMATION_REQUIRED prompt; the model may only echo that exact id back
# after the user agrees out loud. Unlike `confirmed` — a boolean the model could
# simply assert — an id is worthless unless the gate itself issued it, so the
# model can no longer approve its own actions.
for _decl in TOOL_DECLARATIONS:
    _props = _decl.setdefault("parameters", {}).setdefault("properties", {})
    _props.setdefault("consent_id", {
        "type": "STRING",
        "description": (
            "OMIT THIS PARAMETER ON YOUR FIRST CALL. You cannot obtain a "
            "consent_id by asking the user — only the gate issues them, and it "
            "issues one by refusing your first call. So: call the tool WITHOUT "
            "this parameter, receive a CONFIRMATION_REQUIRED message, ask the "
            "user the quoted question, and only then re-call echoing back the "
            "EXACT id from that message (they look like 'cst_1a2b3c4d5e'). "
            "Never invent, generate or guess one — a made-up id is refused and "
            "costs the user an extra question. Never reuse a spent one, and "
            "never change the other parameters when sending it."
        ),
    })


# ─────────────────────────────────────────────────────────────────────────────
# JarvisLive — refactored runtime (v3 stability)
# ─────────────────────────────────────────────────────────────────────────────

class JarvisLive:
    """
    Live session runtime for JARVIS.

    Lifecycle:
        DISCONNECTED → CONNECTING → CONNECTED → CLOSING → DISCONNECTED → ...

    Key stability improvements over v2:
    - stop_event pattern replaces raw TaskGroup (no ExceptionGroup cascade)
    - All 4 tasks (send/listen/recv/play) catch their own errors and set stop_event
    - send_tool_response guarded: only sent when session is still writable
    - speak() / _on_text_command() guarded: only send when session is alive
    - ReconnectGuard: exponential backoff 3s→60s, circuit breaker after 7 failures
    - SessionManager: single source of truth for session state
    - _cleanup() is idempotent: safe to call twice
    - Detailed, calm logging; no multi-page traceback for recoverable errors
    """

    def __init__(self, ui: JarvisUI):
        self.ui              = ui
        self.session         = None
        self.audio_in_queue: asyncio.Queue | None = None
        self.out_queue:      asyncio.Queue | None = None
        self._loop:          asyncio.AbstractEventLoop | None = None
        self._is_speaking    = False
        self._speaking_lock  = threading.Lock()
        self._turn_done      = False
        self._stop_event:    asyncio.Event | None = None
        # Сколько прожила последняя сессия. Считается в `finally` внутри
        # `_run_session` и потому доступно даже когда сессия умерла с ошибкой:
        # без этого долгая здоровая сессия, оборванная сетью, считалась бы
        # отказом и зря двигала предохранитель (замер probe12).
        self._last_uptime    = 0.0

        # ── Centralized session state ────────��───────────────────────────────
        self._sm = SessionManager()

        self.ui.on_text_command = self._on_text_command
        threading.Thread(target=self._reminder_checker_loop, daemon=True).start()

        # Ambient system-awareness layer (read-only). Soft-guarded: if disabled
        # via feature flag or its OS deps are missing, start() is a no-op and
        # Jarvis behaves exactly as before.
        try:
            import core.awareness as _awareness
            _awareness.start()
        except Exception as _aw_e:
            print(f"[Awareness] start skipped: {_aw_e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Thread-safe public API
    # ─────────────────────────────────────────────────────────────────────────

    def _on_text_command(self, text: str):
        """
        Called from a worker thread when the owner types in the input bar.

        While the session is alive the model owns the mouth: the phrase goes
        there and nowhere else — one mouth, no second voice talking over it.
        With no session the offline core answers. The old line "text command
        queued" was a lie: nothing was ever queued, the phrase died in silence
        and the owner kept waiting for an answer that could not arrive.
        """
        if self._loop and self._sm.is_writable():
            # Сессия может умирать именно в этот миг: тогда отправка
            # вернёт False, и фраза обязана достаться рукам, а не тишине.
            # Старые стенды отдают None вместо будущего — там судьба
            # фразы неизвестна, и второй голос включать нельзя.
            future = asyncio.run_coroutine_threadsafe(
                self._safe_send_text(text),
                self._loop,
            )
            if future is None:
                return
            try:
                delivered = bool(future.result(timeout=5.0))
            except Exception as e:  # noqa: BLE001
                delivered = False
                print(f"[Offline] mouth never answered: {type(e).__name__}")
            if delivered:
                return
            print("[Offline] live mouth refused the phrase - hands take over")
        self._answer_offline(text)

    def _answer_offline(self, text: str):
        """
        No session: the hands still work. Runs on the caller's worker thread,
        never on the UI thread, so a slow disk cannot freeze the window.

        Every branch ends with a visible line. Silence is the one outcome that
        is forbidden here: an unanswered phrase looks exactly like a hang.
        """
        try:
            from core.offline_core import handle as _offline_handle
            from core.offline_core import offline_notice as _offline_notice
            reply = _offline_handle(text)
            answer = _offline_notice(text) if reply is None else reply.text
            done = "none" if reply is None else (reply.tool or "answer")
            verdict = "" if reply is None else (reply.verdict or "ok")
        except Exception as e:  # noqa: BLE001
            print(f"[Offline] core failed: {type(e).__name__}: {e}")
            self.ui.write_log(
                "SYS: Нет связи с моделью, и местное ядро не поднялось — "
                f"{type(e).__name__}. Ничего не выполнено."
            )
            return
        self.ui.write_log(f"Jarvis: {answer}")
        self._say_local(answer)
        print(f"[Offline] {done} {verdict}".rstrip())

    def _say_local(self, text: str):
        """
        Свой рот на тот случай, когда провода нет.

        Голос модели приходит готовым звуком по сети. Пока сессия жива,
        говорит она и только она: второй голос поверх живой речи — это два
        Джарвиса в одной комнате. Замок стоит здесь, а не в core/say_local.py:
        состояние сессии известно только тут.

        Речь уходит в свой поток: COM и синтез не имеют права держать
        рабочий поток владельца. Любая беда внутри — молчание и строка в
        консоль: текст в окне уже написан, потерять его из-за немого
        синтезатора нельзя.
        """
        if self._loop and self._sm.is_writable():
            return
        if not text:
            return

        def _run():
            try:
                from core.say_local import say
                say(text)
            except Exception as e:  # noqa: BLE001
                print(f"[Voice] local voice failed: {type(e).__name__}")

        threading.Thread(target=_run, name="say_local", daemon=True).start()

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        """
        Inject text as a spoken response into the live session.
        Safe to call from any thread; silently ignored if session is not writable.
        """
        if not self._loop or not self._sm.is_writable():
            return
        asyncio.run_coroutine_threadsafe(
            self._safe_send_text(text),
            self._loop,
        )

    def speak_error(self, tool_name: str, error: str | Exception):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    # ─────────────────────────────────────────────────────────────────────────
    # Session-guarded send helpers
    # ────��─────────────────────────────────────────���──────────────────────────

    async def _safe_send_text(self, text: str) -> bool:
        """
        Coroutine: sends text to session, guarded by state check.
        Returns True on success, False if session not writable or send failed.
        """
        if not self._sm.is_writable() or not self.session:
            return False
        try:
            await self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True,
            )
            return True
        except asyncio.CancelledError:
            return False
        except Exception as e:
            err = classify_error(e)
            print(f"[JARVIS] ⚠️ send_text skipped — session closing: {err}")
            return False

    async def _safe_send_tool_response(self, fn_responses: list) -> bool:
        """
        Send tool responses back to the session after tool execution.
        Checks session state before sending; silently drops if session has closed.
        This prevents 'Thread was cancelled when writing StartStep status to channel'.
        """
        if not fn_responses:
            return True
        if not self._sm.is_writable() or not self.session:
            print("[JARVIS] ⚠️ send_tool_response skipped — session no longer writable")
            return False

        # Also check stop_event
        if self._stop_event and self._stop_event.is_set():
            print("[JARVIS] ⚠️ send_tool_response skipped — stop signal active")
            return False

        try:
            await self.session.send_tool_response(function_responses=fn_responses)
            return True
        except asyncio.CancelledError:
            print("[JARVIS] ⚠️ send_tool_response cancelled (session closing)")
            return False
        except Exception as e:
            err = classify_error(e)
            if is_recoverable_error(e):
                print(f"[JARVIS] ⚠️ send_tool_response failed (recoverable): {err}")
            else:
                print(f"[JARVIS] ❌ send_tool_response failed: {err}")
            return False

    # ──────────────────────────��──────────────────────────────────────────────
    # Background helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _reminder_checker_loop(self):
        """Daemon: check reminders.json every 30s and deliver due reminders."""
        import time
        from actions.reminder import check_and_fire
        while True:
            time.sleep(30)
            try:
                msg = check_and_fire()
                if msg:
                    self._deliver_reminder(msg)
            except Exception as e:
                print(f"[Reminder] Checker error: {e}")

    def _deliver_reminder(self, msg: str):
        """
        A reminder that came due while the session was down used to vanish:
        speak() drops the phrase when the channel is not writable, and the
        reminder is already marked as fired on disk, so it never returns.
        Offline the local voice says it out loud and the window shows the same
        human text. The string built in actions/reminder.py is an ORDER TO THE
        MODEL ("say this out loud"); read to the owner verbatim it sounds like
        the assistant is commanding itself. The model still gets it word for
        word — only eyes and ears get the human version.
        """
        if self._loop and self._sm.is_writable():
            self.speak(msg)
            return
        from core.say_local import human_reminder
        human = human_reminder(msg)
        self.ui.write_log(f"Jarvis: {human}")
        self._say_local(human)

    def _build_config(self) -> types.LiveConnectConfig:
        from core.time_utils import format_time_context

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)

        # Observability for Invariant 10. Without this line we are debugging
        # blind: a fact can sit perfectly on disk and still never reach the
        # model, and from the outside both failures look identical ("Jarvis
        # forgot"). Print what the model ACTUALLY received, every connect.
        try:
            # Count what the model actually got, not what sits on disk: junk
            # is filtered out of the block, so counting the raw file would
            # report facts that are not there. A diagnostic that disagrees
            # with reality is worse than no diagnostic.
            try:
                from memory.memory_manager import _without_junk
                _visible = _without_junk(memory or {})
            except Exception:
                _visible = memory or {}
            _facts = {
                k: len(v)
                for k, v in _visible.items()
                if isinstance(v, dict) and v
            }
            _total = sum(_facts.values())
            if mem_str:
                _desc = ", ".join(f"{k}={n}" for k, n in sorted(_facts.items()))
                print(
                    f"[Memory] \U0001f9e0 In prompt: {_total} facts "
                    f"({_desc}), {len(mem_str)} chars"
                )
                # .strip() здесь не украшение, а исправление замеренного
                # дефекта. Владелец запустил ровно то, что я просил:
                #     set JARVIS_DEBUG_PROMPT=1 && python main.py
                # В cmd пробел ПЕРЕД `&&` попадает в значение переменной, то
                # есть внутри оказывается "1 ", а не "1", и строгое == "1"
                # молча не срабатывало: счётчик печатался, а блок — нет.
                # Диагностика, которая тихо не включается от документированной
                # команды, хуже отсутствующей: она заставляет искать поломку
                # в памяти, когда сломан сам выключатель.
                if (os.getenv("JARVIS_DEBUG_PROMPT") or "").strip() == "1":
                    print("[Memory] ---- injected block ----")
                    print(mem_str)
                    print("[Memory] ---- end of block ----")
            elif _total:
                print(
                    f"[Memory] \u26a0\ufe0f {_total} facts are on disk but NOTHING "
                    "reached the prompt \u2014 recall is broken, not storage."
                )
            else:
                print("[Memory] \U0001f9e0 In prompt: nothing stored yet.")
        except Exception as _diag_exc:
            print(f"[Memory] diagnostics failed (non-fatal): {_diag_exc}")

        sys_prompt = _load_system_prompt()
        time_ctx   = format_time_context()
        from memory.personality_engine import format_personality_for_prompt, load_profile
        pers_str   = format_personality_for_prompt(load_profile())
        ds_str     = _ds_prompt()

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        if pers_str:
            parts.append(pers_str)
        if ds_str:
            parts.append(ds_str)

        # Ambient computer state (issue 003) — a fenced snapshot for resolving
        # references at connect. Empty when the layer is idle/off, so the prompt
        # is byte-for-byte unchanged then. Live "what's open now" stays in the
        # system_context tool (the prompt is frozen for the session).
        try:
            import core.awareness as _awareness
            aw_str = _awareness.format_for_prompt()
        except Exception:
            aw_str = ""
        if aw_str:
            parts.append(aw_str)

        # Stage 3A: what was actually DONE, read from the database instead of
        # RAM. dialogue_state above dies with the process, so after a restart
        # Jarvis could not answer "what did you just delete?" about work it had
        # finished moments earlier - the record existed in the journal, nobody
        # read it back. Last few lines only: this is a reminder, not a log.
        try:
            from actions.fileops_bridge import get_fileops as _get_fo
            _jr_str = _get_fo().journal.format_for_prompt()
        except Exception:
            _jr_str = ""
        if _jr_str:
            parts.append(_jr_str)

        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
            thinking_config=types.ThinkingConfig(
                include_thoughts=True
            ),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Tool execution (unchanged logic, safer error surface)
    # ─────────────────────────────────────────────────────────────────────────

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # ── ФАЗА 1в: ЗАПИСЬ В ПАМЯТЬ ТЕПЕРЬ СПРАШИВАЕТ У ДВЕРИ ────────────────
        #
        # ЗАЧЕМ. До этой правки `save_memory` был ЕДИНСТВЕННЫМ действием, которое
        # проходило мимо двери целиком: возврат ниже (строка ~1282) стоит ВЫШЕ
        # общего блока двери, и она была для него недостижима. Измерено на живой
        # машине владельца 28.08.2026: голосовое «запомни, что я не пью кофе
        # после шести» память ЗАПИСАЛО, а журнал двери остался 78 строк — ни
        # одной новой. То есть память — единственное место в доме, куда можно
        # положить факт, не оставив следа.
        #
        # ПОЧЕМУ ЭТО НЕ «ПРОСТО ЛОГ». Забор I12/Г-3 (`core/fences.py`) стоит
        # ВНУТРИ двери и САМЫМ ПЕРВЫМ — до риска, экрана и подтверждений.
        # Значит этот вызов даёт сразу две вещи: владельцу — строку в журнале с
        # подписью «кто просил», а будущему под-агенту (фаза 2) — настоящий
        # замок, потому что забор уже там и сработает сам.
        #
        # ЧЕГО ЗДЕСЬ СОЗНАТЕЛЬНО НЕТ (каждый пункт — измеренная ловушка):
        #
        # 1. [ЗАКРЫТО В ФАЗЕ 1Г — 28.08.2026] Было: «НЕ ПЕРЕНЕСЁН общий блок
        #    двери, потому что `forget_memory` и `recall_memory` отсутствуют
        #    в политике и дверь ответит им `Unknown tool`; перенос убил бы
        #    забывание насмерть, а тесты остались бы зелёными».
        #    Ловушка была настоящей — подтверждена живым замером двери ДО
        #    правки, а не рассуждением. Устранена в правильном порядке:
        #    сначала оба инструмента внесены в `core/security.py` (риск low),
        #    затем написаны сторожа на конечный результат забывания, и только
        #    потом расширен блок ниже. Замер и решение владельца — в шапке
        #    tests/test_forgetting_through_the_door.py.
        #
        # 2. НЕ ПЕРЕДАН `ctx`. Цепочка «кто просил» едет через contextvars, а
        #    `core/task_context.py:44` прямо говорит: «правок в main.py этот
        #    блок не требует вовсе». Явный пропуск здесь создал бы второй
        #    источник правды — запрещено правилом 1 того же файла.
        #
        # 3. НЕ ТРОНУТ риск. `save_memory` остаётся risk=low -> policy auto ->
        #    вердикт `run`. Так и задумано: поднять риск до high значило бы
        #    начать переспрашивать ВЛАДЕЛЬЦА на каждое «запомни», а под-агенту
        #    (который спросить не может) — отказывать в его же задачах.
        #    `core/fences.py:36` предупреждает об этом прямо.
        #
        # 4. НЕ ТРОНУТ silent-контракт: ответ ниже по-прежнему `silent: True`,
        #    Джарвис не начнёт вслух отчитываться о каждой записи.
        #
        # ЗАКРЫТО В ФАЗЕ 1Е. Здесь была честная записка о втором писателе:
        # `_update_memory_async` зовёт `update_memory()` напрямую из фонового
        # потока, минуя `_execute_tool`, и в журнал двери не попадает. Долг
        # снят — та функция теперь спрашивает дверь под своими поводами
        # `memory_self_write` и `personality_self_write`. Номер строки в
        # прежней записке («запуск ~1770») к тому же успел устареть, что само
        # по себе довод не ссылаться на строки.
        #
        # Заодно там нашлось хуже: тот же поток писал `update_personality()` —
        # манеру речи самого Джарвиса — и это не фиксировалось нигде.
        #
        # ОТКАТ: удалить этот блок целиком. Ничего другого не затронуто.
        # ФАЗА 1Г: через дверь идут ВСЕ ТРИ инструмента памяти, а не один.
        #
        # Фаза 1в провела только `save_memory` и оставила выше записку
        # (ловушка №1): расширять нельзя, потому что `forget_memory` и
        # `recall_memory` отсутствовали в политике, и дверь по правилу
        # fail-closed ответила бы им `Unknown tool` — забывание умерло бы
        # молча, а 1837 сторожей остались бы зелёными. Причина устранена:
        # оба инструмента добавлены в `core/security.py` с риском low, и
        # ловушка проверена живым замером ДО правки (см. шапку
        # tests/test_forgetting_through_the_door.py).
        #
        # Почему `recall_memory` тоже здесь, хотя он только читает: журнал
        # двери — единственное место, где видно, что кто-то заглядывал в
        # память владельца. Под-агенту чтение разрешено (в заборе его нет),
        # но разрешение без следа — это разрешение, о котором никто не
        # узнает.
        #
        # ЧЕГО ЗДЕСЬ ПО-ПРЕЖНЕМУ НЕТ:
        #   * НЕ ТРОНУТ риск: все три — low -> auto -> `run`. Владелец
        #     проходит молча. Прямое решение владельца от 28.08.2026:
        #     «нет, мне надоест мне всегда подтверждать ему».
        #   * НЕ ПЕРЕДАН `ctx`: цепочка «кто просил» едет через contextvars
        #     (core/task_context.py:44 — «правок в main.py не требует»).
        #   * НЕ ТРОНУТ silent-контракт: `recall_memory` остаётся НЕ silent
        #     (модель обязана озвучить найденное), два других — silent.
        #
        # ОТКАТ: сузить набор до {"save_memory"}. Ничего другого не затронуто.
        if name in ("save_memory", "forget_memory", "recall_memory"):
            try:
                from core.gate import dispatch as _mem_gate
                _mg = _mem_gate(
                    name, args,
                    mode="interactive",
                    screen_control=getattr(self.ui, "screen_control", False),
                )
            except Exception as _mem_gate_err:
                # Сломавшаяся дверь — ОТКАЗ, а не «делай дальше». Правило
                # fail-closed: неизвестно, кто просит тронуть память, — значит
                # не трогаем. Раньше здесь сработало бы молча.
                print(f"[JARVIS] gate error — refusing {name} "
                      f"(fail-closed): {_mem_gate_err}")
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": f"SECURITY: gate error, memory not "
                                        f"touched ({name}).",
                              "silent": True},
                )
            if not _mg.allowed:
                # Сегодня сюда попадает только под-агент: забор I12/Г-3 на
                # запись и на стирание. Чтение (`recall_memory`) в заборе не
                # значится, поэтому под-агент его проходит.
                # Владелец получает `run` и не замечает разницы.
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": _mg.message, "silent": True},
                )

        # ── save_memory is synchronous, no executor needed ───────��───────────
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            said     = args.get("said", "") or ""
            if key and value:
                entry = {"value": value}
                # The user's own wording is stored beside the English label. The
                # label is what Jarvis reasons with; the original sentence is what
                # the user will search with months later. Storing only the label
                # made Russian recall structurally impossible: 'coffee' was found,
                # "кофе" was not, even though the user only ever said the latter.
                if said.strip():
                    entry["said"] = said.strip()
                update_memory({category: {key: entry}})
                mark = f" | сказано: {said.strip()}" if said.strip() else " | БЕЗ оригинала"
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}{mark}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        # ── forget_memory is synchronous, mirrors save_memory ───────────────
        # Without this the model had no way to delete a fact, so when asked to
        # "forget" it faked success by overwriting the value with junk like
        # "disregard previous" — the stale fact survived and Jarvis lied. The
        # result string is passed straight back so the model can only claim
        # deletion when something was actually deleted.
        if name == "forget_memory":
            from memory.memory_manager import forget as _forget_fact
            key      = args.get("key", "")
            category = args.get("category") or None
            result   = _forget_fact(key, category) if key else "Not found: (no key given)"
            print(f"[Memory] \U0001f5d1 forget_memory: {result}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": result, "silent": True}
            )

        # ── recall_memory: search memory mid-conversation ───────────────────
        # The prompt block is built once at connect and is capped, so it can
        # never be everything Jarvis knows. Until now that block was the only
        # memory available during a conversation, which is why Jarvis could
        # deny a fact that was sitting on disk. Not silent: the model has to
        # speak the answer, and it can only report what the search returned.
        if name == "recall_memory":
            from memory.fact_store import recall as _recall
            query = args.get("query", "") or ""
            try:
                result = (_recall(query) if query.strip()
                          else "No query given - ask what they mean.")
            except Exception as _rex:
                result = (f"Memory search failed: {_rex}. "
                          "Do not guess - say you could not check.")
            first = result.splitlines()[0] if result else ""
            print(f"[Memory] \U0001f50d recall_memory '{query}' -> {first}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": result}
            )

        # ── Central security gate ─────────────────────────────────────────────
        # Single source of truth: core/security.py decides what may run.
        # Stage 0 keeps current behaviour exactly — blocked stays blocked,
        # interactive computer_control still depends on the SCREEN toggle.
        # save_memory is handled above and is intentionally not gated here.
        try:
            from core.gate import dispatch as _gate_dispatch
            _g = _gate_dispatch(
                name, args,
                mode="interactive",
                screen_control=getattr(self.ui, "screen_control", False),
            )
            if not _g.allowed:
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name, response={"result": _g.message}
                )
        except Exception as _sec_e:
            # Stage 1: FAIL-CLOSED. If the gate itself errors we do NOT run the
            # tool — an ungated action is worse than a refused one. Local guards
            # inside action modules remain as defense-in-depth.
            print(f"[JARVIS] gate error — refusing {name} (fail-closed): {_sec_e}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"SECURITY: gate error, action not run ({name})."}
            )

        # Gate-only signals — strip them so action modules receive pristine
        # parameters. `confirmed` is no longer in any tool schema (Stage 3C), but
        # the model can still hallucinate the key, so the strip stays as armour.
        args.pop("confirmed", None)
        args.pop("consent_id", None)

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent ��� vision module will speak directly."

            elif name == "cmd_control":
                r = await loop.run_in_executor(None, lambda: cmd_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from core.semantic_interpreter import enrich_goal
                from core.dialogue_state import get as _ds_get
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                raw_goal     = args.get("goal", "")
                ds_state     = _ds_get()
                enriched     = enrich_goal(raw_goal, ds_state) if raw_goal else raw_goal
                task_id      = get_queue().submit(goal=enriched or raw_goal, priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "open_search_source":
                r = await loop.run_in_executor(None, lambda: open_search_source(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "analyze_screen_view":
                question = args.get("question", "What do you see on the screen?")
                api_key  = _get_api_key()
                r = await loop.run_in_executor(
                    None, lambda: _analyze_screen_with_live(question=question, api_key=api_key)
                )
                result = r or "Could not analyze screen."
                try:
                    from core.dialogue_state import update_screen_share as _ds_sv
                    _ds_sv(analysis=result)
                except Exception:
                    pass

            elif name == "screen_share_control":
                action = args.get("action", "status").lower().strip()
                mgr    = _get_ssm()
                if action == "start":
                    # Start frame capture. Live runtime НЕ стартуем: TEXT-модальность
                    # отключена Live API (2026-07) — анализ идёт REST-путём по требованию.
                    msg = mgr.request_start()
                    self.ui.write_log(f"SYS: Screen View — {msg}")
                    result = f"{msg} | Analysis: on-demand (REST)"

                elif action == "stop":
                    # Stop persistent Screen View live runtime first
                    import core.screen_live_runtime as _slr
                    _slr.stop_runtime()

                    # Stop frame capture
                    msg = mgr.request_stop()
                    result = msg
                    self.ui.write_log(f"SYS: Screen View — {msg}")

                else:
                    status   = mgr.get_status()
                    result   = (
                        f"Screen View status: {status['state']} | "
                        f"source: {status['source']} | "
                        f"frame age: {status['frame_age']}s | "
                        f"analysis: on-demand (REST)"
                    )


            elif name == "system_context":
                # Read-only ambient snapshot (in-memory, non-blocking).
                import core.awareness as _aw
                if not _aw.is_running():
                    result = "Слой осознания системы сейчас выключен."
                else:
                    try:
                        _mins = int(args.get("minutes") or 5)
                    except (TypeError, ValueError):
                        _mins = 5
                    # ONE reader of the screen. This used to render the
                    # watcher snapshot directly, which knows none of the rules
                    # resolve_reference applies — so the same moment could be
                    # described two different ways in one conversation.
                    _subject = await loop.run_in_executor(
                        None, lambda: _aw.describe("foreground")
                    )
                    _parts = [_aw.render_subject(_subject),
                              _aw.render_context(),
                              _aw.render_changes(_mins)]
                    result = _aw.dedupe_answer(
                        "\n\n".join(p for p in _parts if (p or "").strip())
                    )

            elif name == "resolve_reference":
                # Read-only referent → concrete path/app.
                import core.awareness as _aw
                if not _aw.is_running():
                    result = "Слой осознания системы сейчас выключен."
                else:
                    _kind = args.get("kind", "")
                    _hint = (
                        args.get("name") or args.get("folder")
                        or args.get("extension") or args.get("browser") or ""
                    )
                    # The user's own words. The layer re-reads them itself, so a
                    # wrongly classified question can still find the right window.
                    _text = args.get("query") or ""
                    if _aw.is_subject_kind(_kind):
                        # "что сейчас активно", "а щас?", "нет, а happ",
                        # "что у меня открыто" — described, not classified.
                        # Enumerating windows is an OS call, so it runs off the
                        # event loop like the other screen questions.
                        _sub = await loop.run_in_executor(
                            None, lambda: _aw.resolve(_kind, _hint, _text)
                        )
                        result = _aw.dedupe_answer(_aw.render_resolved(_sub))
                    elif _aw.is_document_kind(_kind):
                        # Issue 009. Unlike every other kind this one may talk
                        # to Office over COM and stat files, so it runs on a
                        # worker thread — the voice loop must never block on
                        # it. The inspector caps itself at DEADLINE_S inside.
                        _doc = await loop.run_in_executor(
                            None, lambda: _aw.resolve(_kind, _hint, _text)
                        )
                        result = _aw.dedupe_answer(_aw.render_resolved(_doc))
                        # Remember the concrete file so a follow-up like
                        # "сохрани туда же" / "открой его" has a target.
                        if _doc.get("path"):
                            try:
                                import core.dialogue_state as _ds
                                _ds.record_action(
                                    "resolve_reference", "active_document",
                                    f"active document (full path: {_doc['path']})",
                                )
                            except Exception:
                                pass
                    elif _aw.is_page_kind(_kind):
                        # "Какая страница открыта?" — enumerating windows is an
                        # OS call, so it runs off the event loop like documents.
                        # Read-only by construction: the browser is never driven.
                        _page = await loop.run_in_executor(
                            None, lambda: _aw.resolve(_kind, _hint, _text)
                        )
                        result = _aw.dedupe_answer(_aw.render_resolved(_page))
                    else:
                        result = _aw.dedupe_answer(
                            _aw.render_reference(_kind, _hint)
                        )

            elif name == "open_path":
                from actions.open_path import open_path as _open_path
                r = await loop.run_in_executor(
                    None, lambda: _open_path(parameters=args, player=self.ui)
                )
                result = r or "Done."

            else:
                result = f"Unknown tool: {name}"

        except asyncio.CancelledError:
            # Task was cancelled while tool was executing — return partial result
            result = f"Tool '{name}' was interrupted (session closed during execution)."
            print(f"[JARVIS] ⚠️ Tool {name} cancelled mid-execution")

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            print(f"[JARVIS] ❌ Tool {name}: {e}")
            traceback.print_exc()
            # speak_error uses safe send — won't crash if session is dead
            self.speak_error(name, e)

        # u2500u2500 Update dialogue state (u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500u2500
        try:
            _ds_update(
                tool=name, params=args, result=result,
                query=args.get("query") or args.get("task"),
                url=args.get("url"),
                browser=args.get("browser"),
                app_opened=args.get("app_name") if name == "open_app" else None,
            )
        except Exception:
            pass

        # ── Action journal (Slice A) — remember WHAT was done, with a concrete
        # target, so follow-ups like "change that file" resolve later. ────────
        try:
            from core.action_log import note as _note_action
            res_str = str(result)
            ok = not res_str.startswith(("SECURITY", "Tool '")) and "denied" not in res_str.lower()
            _note_action(
                tool=name,
                action=args.get("action"),
                summary=res_str,
                ok=ok,
            )
        except Exception:
            pass

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Runtime tasks (v3 — stop_event based, no re-raise)
    # ─────────────────────────────────────────────────────────────────────────

    async def _send_realtime(self, stop: asyncio.Event):
        """
        Forward microphone audio chunks to the live session.

        Exits cleanly when stop is set, or on any send error.
        Never re-raises — sets stop_event instead.
        """
        while not stop.is_set():
            # Use timeout so we check stop frequently
            try:
                msg = await asyncio.wait_for(self.out_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            if stop.is_set():
                return

            try:
                await self.session.send_realtime_input(media=msg)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not is_recoverable_error(e):
                    print(f"[JARVIS] ❌ send_realtime unexpected: {e}")
                else:
                    print(f"[JARVIS] ⚠️ send_realtime closed: {classify_error(e)}")
                stop.set()
                return

    async def _listen_audio(self, stop: asyncio.Event):
        """
        Read audio from the microphone and push to out_queue.

        Exits cleanly on cancel or mic error.
        Never re-raises.
        """
        stream = None
        try:
            stream = await asyncio.to_thread(
                pya.open,
                format=FORMAT, channels=CHANNELS,
                rate=SEND_SAMPLE_RATE, input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            print("[JARVIS] 🎤 Mic started")

            while not stop.is_set():
                try:
                    data = await asyncio.to_thread(
                        stream.read, CHUNK_SIZE, exception_on_overflow=False
                    )
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    print(f"[JARVIS] ⚠️ mic read error (continuing): {e}")
                    await asyncio.sleep(0.05)
                    continue

                with self._speaking_lock:
                    speaking = self._is_speaking

                if not speaking and not self.ui.muted:
                    try:
                        self.out_queue.put_nowait({"data": data, "mime_type": "audio/pcm"})
                    except asyncio.QueueFull:
                        pass  # drop frame — queue full means we're sending fast enough

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[JARVIS] ❌ Mic setup error: {e}")
        finally:
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

    async def _receive_audio(self, stop: asyncio.Event):
        """
        Receive responses from the live session and dispatch audio/transcripts/tool calls.

        Exits cleanly when stop is set or when session closes.
        Never re-raises — sets stop_event for recoverable errors.

        CRITICAL FIX: send_tool_response is only called if session is still writable
        AFTER tool execution completes (tools can take 5-30 seconds; session may die).
        """
        print("[JARVIS] 👂 Recv started")
        out_buf: list[str] = []
        in_buf:  list[str] = []

        while not stop.is_set():
            try:
                async for response in self.session.receive():
                    # ── Early exit if stop was signalled ──────────────────────
                    if stop.is_set():
                        return

                    # ── Audio data ────────────────────────────────────────────
                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    # ── Transcription + turn management ───────────────────────
                    if hasattr(response, "server_content") and response.server_content:
                        sc = response.server_content

                        # Thinking parts — log, don't speak
                        if hasattr(sc, "model_turn") and sc.model_turn:
                            mt = sc.model_turn
                            if hasattr(mt, "parts") and mt.parts:
                                for part in mt.parts:
                                    if hasattr(part, "thought") and part.thought:
                                        print("[JARVIS] (thinking)")

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            self._turn_done = True
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                                # ── ЭХО ОТВЕТА: ВИДНО, А НЕ ЗАМЕТЕНО ────────
                                # Владелец 28.08.2026 услышал ответ ДВАЖДЫ
                                # подряд («меренговый торт»), с паузой меньше
                                # секунды. Измерено сразу: инструмент вызван
                                # ОДИН раз, в журнале двери одна строка, в
                                # памяти один факт. Удвоились слова, не
                                # действие.
                                #
                                # Причина не у нас. Модель голоса говорит ДО
                                # вызова инструмента и повторяет ПОСЛЕ его
                                # ответа. Тот же дефект с теми же таймингами
                                # описан разработчиками Google 07.01.2026
                                # («Scheduling "SILENT" ... not preventing
                                # duplicate audio»); там же проверено, что не
                                # помогает ни scheduling=SILENT, ни полный
                                # отказ отвечать инструментом.
                                #
                                # ПОЧЕМУ ЗДЕСЬ ТОЛЬКО ПЕЧАТЬ, А НЕ СКЛЕЙКА.
                                # Склеить дубль в окне — двадцать секунд
                                # работы, но владелец слышал повтор УШАМИ.
                                # Склейка вылечила бы глаза и спрятала уши:
                                # это «починил молча», худшие грабли проекта.
                                # Звук приходит отдельным потоком и к этому
                                # месту уже проигран — гасить нечего.
                                # Печать даёт то, чего нет: ЧИСЛО. Один
                                # прогон — не статистика; решать, стоит ли
                                # лезть в речевой путь, надо по частоте.
                                #
                                # try/except — потому что этот блок живёт
                                # внутри цикла приёма: падение здесь убило бы
                                # сессию, и Джарвис замолчал бы совсем.
                                # Диагностика не имеет права быть опаснее
                                # того, что она диагностирует.
                                try:
                                    from core.echo_guard import describe as _echo
                                    _warn = _echo(full_out)
                                    if _warn:
                                        print(_warn)
                                except Exception:
                                    pass
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True,
                                ).start()

                    # ── Tool calls ────────────────────────────────────────────
                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            if stop.is_set():
                                print("[JARVIS] ⚠️ Tool call aborted — stop signal received")
                                return
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)

                        # ── CRITICAL: check session is still writable ─────────
                        # Tool execution may take 5-30 seconds; session may have
                        # closed during that time.
                        # This is the fix for:
                        #   "Thread was cancelled when writing StartStep status"
                        sent = await self._safe_send_tool_response(fn_responses)
                        if not sent and not stop.is_set():
                            # Session closed during tool execution — signal stop
                            stop.set()
                            return

            except asyncio.CancelledError:
                # Task cancelled cleanly — just exit
                return

            except Exception as e:
                err = classify_error(e)
                if is_recoverable_error(e):
                    print(f"[JARVIS] ⚠️ recv loop ended (recoverable): {err}")
                else:
                    print(f"[JARVIS] ❌ recv loop unexpected error: {e}")
                    # For unexpected errors, print traceback once
                    traceback.print_exc()

                # Signal all other tasks to stop — session is dead
                stop.set()
                return

    async def _play_audio(self, stop: asyncio.Event):
        """
        Write received audio chunks to the speaker stream.

        Exits cleanly when stop is set.
        Never re-raises.
        """
        print("[JARVIS] 🔊 Play started")
        stream = None
        try:
            stream = await asyncio.to_thread(
                pya.open,
                format=FORMAT, channels=CHANNELS,
                rate=RECEIVE_SAMPLE_RATE, output=True,
            )
            while not stop.is_set():
                try:
                    # Timeout so we check stop_event frequently
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(), timeout=0.6
                    )
                    self.set_speaking(True)
                    await asyncio.to_thread(stream.write, chunk)
                except asyncio.TimeoutError:
                    # No audio for 0.6s → Jarvis finished speaking
                    if self._is_speaking:
                        self.set_speaking(False)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    print(f"[JARVIS] ⚠️ play write error: {e}")
                    await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[JARVIS] ❌ Play setup error: {e}")
        finally:
            self.set_speaking(False)
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

    # ──────────────────────────��──────────────────────────────────────────────
    # Session lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def _cleanup(self):
        """
        Clean up after a session ends.
        Idempotent — safe to call multiple times.
        """
        print("[JARVIS] 🧹 Cleanup started")
        self._sm.set_state(SessionState.CLOSING)

        # Signal any still-running tasks
        if self._stop_event and not self._stop_event.is_set():
            self._stop_event.set()

        self.set_speaking(False)

        # Null out session references so old tasks can't accidentally write
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._stop_event    = None

        self._sm.set_state(SessionState.DISCONNECTED)
        print("[JARVIS] 🧹 Cleanup done")

    # ── Связь: спрашиваем до того, как поднимать сессию ────────────────
    # Сколько ждать между опросами, пока сети нет. Пять секунд — это верхняя
    # граница молчания после возвращения интернета.
    LINK_POLL_SECONDS = 5.0
    # Через сколько пустых кругов всё-таки попробовать вслепую.
    LINK_BLIND_TRY_EVERY = 12

    def _link_says_no(self) -> bool:
        """
        Правда ли, что сети нет.

        Сомнение — это не «нет». Любая невнятица трактуется в пользу попытки:
        ложное «сети нет» при живом интернете запрёт Джарвиса в оффлайне,
        и владелец не поймёт, почему.
        """
        try:
            from core.link import says_no
            return bool(says_no())
        except Exception as exc:      # noqa: BLE001
            print(f"[Link] probe unavailable: {exc}")
            return False

    async def _run_session(self, client: genai.Client) -> float:
        """
        Run one complete session lifecycle.

        Returns the session uptime in seconds (used by ReconnectGuard to decide
        whether to reset the consecutive failure counter).

        Architecture:
          1. Create stop_event (shared signal between all 4 tasks)
          2. Open live session via async context manager
          3. Create 4 tasks: send / listen / recv / play
          4. asyncio.wait(FIRST_COMPLETED) — first task to finish = stop signal
          5. Set stop_event → other tasks notice and exit cleanly within 5s
          6. Gather all tasks (return_exceptions=True — no ExceptionGroup)
          7. Cleanup (idempotent)
        """
        stop = asyncio.Event()
        self._stop_event = stop

        self._sm.set_state(SessionState.CONNECTING)
        print("[JARVIS] 🔌 Connecting...")
        self.ui.set_state("THINKING")

        config = self._build_config()
        session_start = asyncio.get_event_loop().time()

        try:
            async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                self.session        = session
                self._loop          = asyncio.get_event_loop()
                self.audio_in_queue = asyncio.Queue()
                self.out_queue      = asyncio.Queue(maxsize=10)

                self._sm.mark_connected()
                print("[JARVIS] ✅ Connected.")
                self.ui.set_state("LISTENING")
                self.ui.write_log("SYS: JARVIS online.")

                tasks = [
                    asyncio.create_task(self._send_realtime(stop),  name="send_realtime"),
                    asyncio.create_task(self._listen_audio(stop),   name="listen_audio"),
                    asyncio.create_task(self._receive_audio(stop),  name="receive_audio"),
                    asyncio.create_task(self._play_audio(stop),     name="play_audio"),
                ]

                # ── Wait for first task completion OR stop signal ────────────
                # Any task completing (even normally) signals the session ended.
                try:
                    done, pending = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    # Outer shutdown requested
                    stop.set()
                    raise

                # Signal all tasks to stop
                stop.set()

                # Log which task triggered the stop
                for t in done:
                    exc = t.exception() if not t.cancelled() else None
                    if exc:
                        print(f"[JARVIS] Task {t.get_name()} triggered stop: {classify_error(exc)}")
                    else:
                        print(f"[JARVIS] Task {t.get_name()} completed (stop triggered)")

                # ── Cancel pending tasks ─────────────────────────────────────
                for t in pending:
                    if not t.done():
                        t.cancel()

                # ── Wait up to 5s for all tasks to finish cleanly ─────────────
                if pending:
                    await asyncio.wait(pending, timeout=5.0)

                # ── Collect results without re-raising ───────────────────────
                # Using gather with return_exceptions=True prevents ExceptionGroup
                all_results = await asyncio.gather(*tasks, return_exceptions=True)
                for t, result in zip(tasks, all_results):
                    if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                        if not is_recoverable_error(result):
                            print(f"[JARVIS] Task {t.get_name()} non-recoverable: {result}")

        finally:
            # Здесь НЕЛЬЗЯ писать `return uptime`. Замер (probe10) показал: `return`
            # внутри `finally` поглощает ЛЮБОЕ исключение, и тогда ниже в `run`
            # становятся недостижимы сразу три ветки — `except CancelledError`
            # (остановка по Ctrl+C), `except Exception` и `if is_fatal_error(...)`.
            # Живьём это значило: при неверном ключе API Джарвис вечно ходил по
            # кругу переподключений вместо остановки с внятной ошибкой, потому что
            # `is_fatal_error` не вызывалась НИКОГДА — единственный её вызов стоял
            # в мёртвой ветке. Уборка обязана идти всегда, а вот возврат значения —
            # только когда сессия закончилась без исключения.
            self._last_uptime = asyncio.get_event_loop().time() - session_start
            await self._cleanup()

        # Досюда доходим только штатным путём. Если сессия умерла с исключением,
        # оно летит выше — в `run`, где его классифицируют.
        return self._last_uptime

    # ─────────────────────────────────────────────────────────────────────────
    # Main reconnect loop
    # ────────────────────────────────────────────────────��────���───────────────

    async def run(self):
        """
        Main runtime loop.

        Lifecycle:
          1. Build genai client (once)
          2. Loop:
             a. Try _run_session()
             b. On recoverable error → record failure + backoff → retry
             c. On fatal error → stop
             d. Circuit breaker: 7 consecutive failures → 3-minute pause → reset
        """
        print("[JARVIS] 🚀 Runtime starting")

        # Живой прогон без интернета (11.08.2026): на каждую из семи попыток
        # в окно падало двадцать строк трассировки из websockets: она зовёт
        # своё же поле recv_messages при уборке недостроенного соединения.
        # Наш try/except вокруг сессии такое не ловит принципиально:
        # исключение рождается в колбеке цикла, а не внутри нашего await.
        # Гасится ровно эта подпись; всё остальное печатается как раньше.
        try:
            from core.quiet_loop import install as _install_quiet
            _install_quiet(asyncio.get_running_loop())
        except Exception as _qe:      # noqa: BLE001
            print(f"[JARVIS] quiet loop handler skipped: {_qe}")

        # Phase 0, step 2: settings live in ~/.jarvis now, not in the project
        # folder. Move the old project file across once, before anything reads
        # a flag, so the first run after an unzip inherits the owner's values
        # instead of silently starting from the defaults.
        try:
            from config.loader import migrate_project_settings, settings_file
            if migrate_project_settings():
                print(f"[JARVIS] 📦 Settings moved to {settings_file()}")
        except Exception as _e:
            print(f"[JARVIS] settings migration skipped: {_e}")

        # Say which confirmation mechanism is live. This was invisible before,
        # and the cost showed up immediately: the setting used to live in the
        # project folder, so a fresh unzip silently ran the legacy path while
        # everyone believed the new one was on. A safety mode you cannot see is
        # a safety mode you cannot trust.
        try:
            from core.feature_flags import durable_consent_enabled as _dce
            print("[JARVIS] 🔐 Confirmations: "
                  + ("durable consent tickets" if _dce()
                     else "LEGACY confirmed-flag (model self-approves)"))
        except Exception:
            pass

        # Same rule for the agents switch: phase 2 will hand work to worker
        # agents, and the owner must be able to see from the first line of the
        # log which Jarvis just started - the monolith or the two-level one.
        try:
            from core.feature_flags import agents_enabled as _ag
            print("[JARVIS] 🤖 Agents: "
                  + ("ON" if _ag()
                     else "OFF (monolith — phase 2 turns this on)"))
        except Exception:
            pass

        # Stage 2.5 - event bus. Actions publish FACTS ("file overwritten"),
        # listeners subscribe. Off by default so the console stays quiet; set
        # JARVIS_BUS_LOG=1 to watch every fact flow through the system.
        try:
            import os as _os
            # .strip() по той же причине, что и у JARVIS_DEBUG_PROMPT выше:
            # `set JARVIS_BUS_LOG=1 && python main.py` в cmd кладёт в значение
            # "1 " с пробелом, и выключатель молча не срабатывает. Дефект
            # найден на соседней переменной живым запуском владельца; правлю
            # здесь сразу, чтобы он не выстрелил вторым.
            if (_os.environ.get("JARVIS_BUS_LOG") or "").strip() == "1":
                from core.bus import attach_console_subscriber
                attach_console_subscriber()
                print("[JARVIS] 📡 Event bus logging ON")
        except Exception as _e:
            print(f"[JARVIS] bus subscriber unavailable: {_e}")

        try:
            client = genai.Client(
                api_key=_get_api_key(),
                http_options={"api_version": "v1beta"},
            )
        except Exception as e:
            print(f"[JARVIS] 💀 Cannot create client: {e}")
            self.ui.write_log(f"ERR: Cannot initialise — {e}")
            return

        guard = ReconnectGuard()

        # Шаг 3 фазы 0.7. Живой прогон без интернета показал семь попыток подряд.
        first_attempt = True      # холодный старт всегда идёт без проверки
        link_down = False         # объявляли ли владельцу про отсутствие сети
        blind_countdown = self.LINK_BLIND_TRY_EVERY

        while True:
            uptime = 0.0

            # Проверка связи стоит ДО _run_session намеренно: внутри сессии
            # первым делом собирается промпт памяти (_build_config), а в finally
            # печатается уборка. Без этой развилки каждая попытка без сети
            # делала обе работы впустую и рвала соединение в чужой библиотеке.
            offline_now = False if first_attempt else self._link_says_no()
            blind = offline_now and blind_countdown <= 0

            if offline_now and not blind:
                blind_countdown -= 1
                if not link_down:
                    link_down = True
                    print("[Link] no network - session not started, waiting")
                    self.ui.write_log(
                        "SYS: Сети нет. Жду связь; простые просьбы выполняю сам.")
                    self.ui.set_state("OFFLINE")
                await asyncio.sleep(self.LINK_POLL_SECONDS)
                continue

            if blind:
                # Проверка тоже умеет врать: прокси, брандмауэр, запрет исходящего
                # 443 именно для python. Раз в минуту пробуем подключиться вслепую.
                print("[Link] probe still says no - trying anyway")
            elif link_down:
                link_down = False
                print("[Link] network is back - reconnecting")
                self.ui.write_log("SYS: Связь вернулась. Поднимаю сессию.")
                self.ui.set_state("THINKING")

            blind_countdown = self.LINK_BLIND_TRY_EVERY
            first_attempt = False
            try:
                uptime = await self._run_session(client)
                link_down = False   # сессия ответила — связь была

                # Session ended without exception → likely a clean close
                # If it was stable long enough, reset failure count
                if uptime >= ReconnectGuard.STABLE_SECONDS:
                    guard.record_success(uptime)
                else:
                    guard.record_failure()

            except asyncio.CancelledError:
                print("[JARVIS] 🔴 Shutdown (CancelledError).")
                break

            except Exception as e:
                # Handle ExceptionGroup (Python 3.11+ TaskGroup artifact)
                # We should not see ExceptionGroup with our stop_event design,
                # but handle it defensively.
                exc_to_classify = e
                if hasattr(e, "exceptions"):
                    # Flatten inner exceptions
                    inner = list(e.exceptions)
                    non_cancel = [ex for ex in inner if not isinstance(ex, asyncio.CancelledError)]
                    if non_cancel:
                        exc_to_classify = non_cancel[0]
                    else:
                        # All inner are CancelledError → clean shutdown
                        print("[JARVIS] 🔴 Shutdown (ExceptionGroup of CancelledErrors).")
                        break

                err_str = classify_error(exc_to_classify)

                if is_fatal_error(exc_to_classify):
                    print(f"[JARVIS] 💀 Fatal error — stopping: {err_str}")
                    self.ui.write_log(f"ERR: Fatal — {err_str}")
                    self.ui.set_state("ONLINE")
                    break

                if is_recoverable_error(exc_to_classify):
                    print(f"[JARVIS] ⚠️ Session ended (recoverable): {err_str}")
                else:
                    print(f"[JARVIS] ❌ Session ended (unexpected): {err_str}")
                    traceback.print_exc()

                # Отказ считаем ТОЛЬКО если сессия не успела стать устойчивой.
                # Иначе долгая здоровая сессия (два часа работы), оборванная
                # сетью на стороне Google, двигала бы предохранитель, и после
                # семи таких обрывов Джарвис молчал бы три минуты на ровном
                # месте. Замер probe12: без этой развилки предохранитель
                # срабатывал 1 раз на 8 двухчасовых сессий, хотя связь была
                # здоровой. `_last_uptime` посчитан в `finally` и потому
                # доступен даже здесь, на пути исключения.
                if self._last_uptime >= ReconnectGuard.STABLE_SECONDS:
                    guard.record_success(self._last_uptime)
                else:
                    guard.record_failure()

            # ── Circuit breaker ───────────────────────────────��──────────────
            if guard.is_circuit_open():
                pause = guard.extended_backoff()
                msg = (
                    f"SYS: JARVIS pausing {int(pause)}s after "
                    f"{guard.consecutive_failures} consecutive failures."
                )
                print(f"[JARVIS] ⚡ {msg}")
                self.ui.write_log(msg)
                self.ui.set_state("THINKING")
                await asyncio.sleep(pause)
                guard.reset_circuit()
                continue

            # ── Normal backoff before reconnect ─────────────────────────────
            delay = guard.next_delay()
            self._sm.set_state(SessionState.RECONNECTING)
            self.ui.set_state("THINKING")
            print(f"[JARVIS] 🔄 Reconnecting in {delay:.1f}s "
                  f"(attempt {guard.consecutive_failures})...")
            await asyncio.sleep(delay)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Р11, шаг 34.1: слой окружения -- САМОЕ ПЕРВОЕ действие запуска,
    # раньше замка. Причина видна прямо ниже: сообщение об уже запущенном
    # Джарвисе пришлось обкладывать try/except UnicodeEncodeError вручную.
    # С настроенными потоками такая защита нужна только как второй слой.
    env.setup()

    # Phase 0, step 4: one Jarvis at a time. Taken BEFORE the window exists,
    # before PortAudio opens a stream and before anything touches
    # ~/.jarvis/jarvis.db - a second instance must not paint a face and only
    # then discover it is redundant. The OS drops this lock when the process
    # dies, which is the only shutdown this program really has: the window's
    # X button calls os._exit(0) (ui.py), so no `finally` can be trusted.
    try:
        instance_lock.acquire()
    except instance_lock.AlreadyRunning as busy:
        try:
            print(f"[JARVIS] \U0001f512 Jarvis уже запущен ({busy.note}). "
                  f"Этот экземпляр закрывается.")
        except UnicodeEncodeError:      # exotic console codepage: say it plainly
            print(f"[JARVIS] Jarvis is already running "
                  f"(pid {busy.info.get('pid', '?')}). This instance exits.")
        return

    # Р6, шаг 33.2: версии состояния проверяются ДО окна, микрофона и
    # первого обращения к базе. Данные новее кода — не рабочее состояние:
    # лучше одна понятная фраза и выход, чем бодрый Джарвис без журнала,
    # без отмены и без одноразовых подтверждений. Замок отдаётся сразу:
    # иначе следующий запуск скажет «уже запущен» про ушедший процесс.
    # Сам сторож никогда не падает; если проверка не выполнилась —
    # запуск продолжается, а причина печатается.
    try:
        from core import state_guard
        if not state_guard.verify_or_refuse():
            instance_lock.release()
            return
        state_guard.record_start()
    except Exception as _sg:
        print(f"[JARVIS] проверка версий пропущена: {_sg}")

    # Страховка дома (шаг 33.3). Собственная попытка: если снимок не
    # вышел, Джарвис всё равно запускается — и говорит об этом вслух.
    try:
        from core import state_snapshot
        state_snapshot.cleanup_temp()
        if state_snapshot.due():
            state_snapshot.create("auto")
        state_snapshot.ensure_phase_snapshot()
    except Exception as _ss:
        print(f"[JARVIS] снимок состояния пропущен: {_ss}")

    # Схема базы (фаза 1, шаг 1.2). Здесь и только здесь она меняется в
    # ИЗВЕСТНЫЙ момент. Иначе схему правит тот, кто первым захотел журнал
    # или память, — то есть в непредсказуемую секунду, некому доложить
    # владельцу, и метка сборки запишет одну версию, а состояние дома
    # другую. Место выбрано не случайно: после уборки мусора в снимках и
    # после авто-снимка (они снимают состояние ДО правки), но до метки
    # сборки — чтобы метка записала правду.
    #
    # Две разные неудачи и два разных ответа:
    #   «база новее программы» -> ВЫХОД. Это та самая защита, что была и
    #     до фазы 1, и ослаблять её до предупреждения нельзя;
    #   любая другая -> сказать и работать дальше. Сломанный новый
    #     механизм не имеет права не пустить владельца к его программе.
    try:
        from core import store as _store
        _schema = _store.ensure_schema(printer=print)
        if _schema.get("ready") is not True:
            print("[JARVIS] схема базы осталась старой: "
                  + str(_schema.get("reason") or "причина не названа"))
        elif _schema.get("changed"):
            # Версия сменилась — состояние дома обязано сказать правду
            # уже сейчас, а не со следующего запуска.
            try:
                from core import state_version as _sv2
                _sv2.write()
            except Exception as _sw:
                print(f"[JARVIS] состояние дома не переписано: {_sw}")
    except Exception as _sc:
        from core.store import StoreError as _StoreErr
        if isinstance(_sc, _StoreErr):
            print(f"[JARVIS] {_sc}")
            instance_lock.release()
            return
        print(f"[JARVIS] схема базы не проверена: {_sc}")

    # Метка сборки (шаг 33.4). Настоящие версии хранилищ видит только
    # старт: под тестами дом — песочница. Собственная попытка:
    # метка — удобство, а не причина не запуститься.
    try:
        from core import build_stamp
        build_stamp.stamp_start()
    except Exception as _bs:
        print(f"[JARVIS] метка сборки пропущена: {_bs}")

    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    try:
        threading.Thread(target=runner, daemon=True).start()
        ui.root.mainloop()
    finally:
        # Best effort only: the X button calls os._exit(0) and never reaches
        # this line. Correctness does not depend on it - the OS releases the
        # lock when the process dies. This covers the paths that do unwind.
        instance_lock.release()


if __name__ == "__main__":
    main()
