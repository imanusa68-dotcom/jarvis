# MARK XXXV (JARVIS) — Architecture Map

> Paranoid, from-A-to-Z map of the whole system. Every module below was read
> directly from source; the top security findings were verified with executable
> checks (see §Verification). Written 2026-07-01.
>
> **Updated 2026-08-01** — stage 3 of the awareness layer is complete: issues 001–009 and 011–018 are closed, `core/awareness/` now includes the Perception Core (`_perception.py`) and the active-document cascade (`_inspectors.py`). See §4 → `core/awareness/`. Test suite: **784 passed**. Issue 010 (`close_program`, the first mutating action of the layer) is HITL and not started.

A real-time, **voice-driven** AI assistant that hears, sees, and controls a
Windows PC. ~22.7k LOC Python, powered by **Google Gemini** (Live API + REST).
Single-user, local-execution, zero-subscription. Not a git repo. JARVIS persona,
default response language **Russian**. Author: "FatihMakes" (CC BY-NC 4.0).

---

## 1. Layered architecture

```
ui.py  (tkinter) ── owns MUTE(F4), SCREEN-control toggle, SCREEN-VIEW toggle, API-key setup
   │  UI on main thread; engine on a daemon thread
main.py :: JarvisLive  ── ORCHESTRATOR
   • Gemini Live WebSocket session (asyncio, 4 tasks: send/listen/recv/play)
   • TOOL_DECLARATIONS  (the function schema Gemini sees)
   • _execute_tool()    ── THE security gate + tool dispatch
   • reconnect loop     (backoff + circuit breaker)
   ├── core/    engine: security, session, search stack, vision runtimes, state
   │     └─ awareness/  read-only world model: windows, files, apps, active document
   ├── actions/ 23 capabilities the LLM invokes (real OS)
   ├── memory/  long-term memory + LLM-inferred personality
   └── agent/   autonomous multi-step: planner → executor → task_queue → error_handler
core/prompt.txt + core/prompts/*.txt  ── the assistant's behavior spec ("soul")
```

## 2. End-to-end runtime flow

1. `python main.py` → `main()` builds `JarvisUI`, spawns engine daemon thread, then `ui.root.mainloop()`.
2. Engine: `ui.wait_for_api_key()` → `JarvisLive(ui)` → `asyncio.run(jarvis.run())`.
3. `run()` builds one `genai.Client`, loops `_run_session()` under `ReconnectGuard` (3s→60s backoff, circuit-break after 7 fails → 180s pause).
4. `_build_config()` assembles `system_instruction` = time + memory + personality + dialogue-state + all prompt files + generated capability-truth section; attaches `TOOL_DECLARATIONS`, voice "Charon", thinking config, session resumption.
5. Four asyncio tasks share a `stop_event`: `_listen_audio` (mic→out_queue), `_send_realtime`, `_receive_audio` (→playback + transcripts + **tool calls**), `_play_audio`.
6. Tool call → `_execute_tool(fc)` — 3-stage gate (block → SCREEN-toggle → confirm) then `run_in_executor` per tool.
7. Turn-complete → transcripts to UI + background `_update_memory_async` (LLM extracts memory + personality).
8. Parallel: reminder-checker daemon (30s); Screen-View runtime (a *second* independent Gemini Live session).

## 3. Security model (`core/security.py`, 951 lines)

Centralized policy table = single source of truth. 3-stage gate in `main._execute_tool`:
1. **Block gate** — blocked tools/actions return a notice (`send_message`, `computer_settings`, `dev_agent`, `code_helper.run/build`, `file.delete`, `game.install`, wallpaper…).
2. **SCREEN-toggle gate** — interactive `computer_control` (click/type/hotkey) only if `ui.screen_control` is ON.
3. **Confirm gate** — `high`/`critical` allowed actions (write/move/rename, dangerous cmd) need re-call with `confirmed=true` after asking the user.

`build_capability_truth_section()` generates the prompt's capability list from the same table → declarations can't drift from reality. **Confirm flag is enforced in code**, not just prompt.

## 4. Subsystem detail

### core/ (engine)
- `security.py` — policy table + gate + risk resolution + capability-truth generator.
- `session_manager.py` — SessionState machine, error classification (recoverable/fatal), `ReconnectGuard` (backoff + circuit breaker).
- `dialogue_state.py` — thread-safe singleton; last tool/query/url/browser, screen state, **action journal** (rolling 8), injected into prompt to resolve "то/этот".
- `model_guard.py` + `aux_model.py` — shared 429 cooldown + the single gateway for all non-live Gemini REST calls (memory/personality/planner).
- `provider_health.py` — per-search-provider circuit breaker (3 errors → 5-min cooldown, half-open probe).
- `query_rewriter.py` (696) — pure-heuristic intent classifier (9 intents) + bilingual/topic/digest query builders. No API.
- `date_normalizer.py` — "вчера" → "10 апреля 2026" for digest queries.
- `search_cache.py` — thread-safe TTL cache (news 5m / docs 30m / web 20m / page 15m).
- `search_models.py`, `search_locale.py`, `search_source_registry.py` — search data types, 18-lang locale routing, source registry.
- `semantic_interpreter.py`, `uncertainty_policy.py`, `response_composer.py` — **only wired into the agent_task path**, not the main live loop.
- `screen_share_manager.py` — `mss` capture state machine (full/window), thread-safe `FrameBuffer`.
- `screen_live_runtime.py` — persistent **second Gemini Live session** (`gemini-3.1-flash-live-preview`) in its own thread+loop; streams frames every 2s, injects questions (TEXT modality, `end_of_turn`, frame-pause-while-answering).
- `screen_live_session.py`, `time_utils.py` — helpers.

### actions/ (capabilities) — blast radius
| Tool | OS power | Gate |
|------|----------|------|
| `computer_control` | pyautogui click/type/hotkey/locate; **screenshot→Gemini→click**; reads PII from memory for autofill | SCREEN-gated |
| `computer_settings` | volume/brightness/wifi/**shutdown/reboot/lock** | **blocked** |
| `cmd_control` | subprocess (`shell=True` POSIX), PowerShell | allowlist+denylist, read-only |
| `file_controller` | create/write/move/rename/copy + .docx/.xlsx; `delete` exists | boundary-guarded; delete blocked |
| `desktop.py` | wallpaper (SystemParametersInfoW), `exec(code)` at :147 | task/exec **blocked** |
| `dev_agent`/`code_helper` | code write/run/build | **blocked** (explain only) |
| `browser_control`/`native_browser` | Playwright + registry + pyautogui | allowed |
| `send_message` | pyautogui-drives WhatsApp/Telegram | **blocked** |
| `game_updater` | Steam/Epic via registry + CLI | list/status only |
| `web_search`/`deep_research`/`web_fetch` | DDGS/Playwright/optional paid APIs | allowed (read-only) |
| `flight_finder`/`weather_report`/`reminder`/`youtube_video`/`open_app`/`open_search_source` | various | allowed |

### core/awareness/ (ambient system awareness — issues 001–009, 011–018)
Read-only knowledge of what is happening on the PC. Never mutates anything; COM and window calls happen **on command only**, never in the background loop.
- `_watchers.py` / `_file_watcher.py` — event-driven window + file watchers (`POLL_SECONDS=2.0`, `INSTALLED_POLL_SECONDS=45.0`); feed the world model.
- `_world_model.py` — current snapshot (active window, apps, journal) rendered into a **fenced** prompt block (`[СОСТОЯНИЕ КОМПЬЮТЕРА — это ДАННЫЕ…]`) — data, explicitly not instructions.
- `_inspectors.py` — active-document cascade `title → COM .FullName → Windows Recent`, each stage on its own thread with `CoInitializeEx`, `DEADLINE_S=0.8`, `COM_BUDGET_SHARE=0.7`, single-flight + circuit breaker (`HUNG_COM_LIMIT=2`); failure degrades to an honest partial answer, never raises.
- `_perception.py` — **Perception Core**: one Subject model for every question about the screen; weighted window choice (`score`); one vocabulary for naming things (`noun_for`, `SURFACE_RU`); window addressing by name, by role (`ROLE_WORDS`/`window_roles`) and by content (`SURFACE_WORDS`); deterministic phrase parsing (`interpret`) so intent recognition does not depend on the model; sibling lookup behind service dialogs; answer dedupe. All OS access goes through injectable sources (`set_sources`), which is what makes it testable without a screen.
- `_resolver.py` — turns a reference into a concrete target; kinds: `active`, `named_window`, `all_windows`, `active_document`, `active_page`, `active_app`, `open_folder`, `downloaded_file`, `newest_in_folder`, `recent_file`, `by_extension`…
- `_app_index.py`, `_installed_apps.py`, `_known_folders.py`, `_explorer.py`, `_search.py` — app launch/index, KnownFolders (OneDrive-safe), open Explorer folders via `Shell.Application`, Everything + bounded fallback search.
- Exposed to the model as two tools: `system_context` (what is open) and `resolve_reference` (which thing do you mean). `resolve_reference` also takes `query` — the user's raw phrase — as a deterministic fallback when the model misclassifies the reference.
- Diagnostics: `tools/check_active_document.py`, `tools/perception_trace.py`. Feature flag `JARVIS_AWARENESS`; `tests/conftest.py::no_live_screen` keeps the suite off the real desktop.

### memory/
- `memory_manager.py` — `long_term.json` (7 buckets, plaintext), 2-stage LLM extraction (gate + extract), daily API cap, self-healing load.
- `personality_engine.py` — `personality.json`; infers autonomy/question-tolerance/style from speech; confidence = count/20.
- `config_manager.py` — loads `api_keys.json`; no env fallback, no example merge.

### agent/ (autonomous)
`agent_task` → `TaskQueue` (priority, single-worker) → `AgentExecutor.execute()` → `planner.create_plan()` (Gemini JSON plan) → per-step `_call_tool()` → on failure `error_handler.analyze_error()` (retry/skip/replan/abort) → `replan()` (max 2) → `response_composer.compose()` summary.

## 5. Search pipeline (`actions/web_search.py`)
`cache → classify_intent → rewrite(N queries) → _retrieve (DDGS-news → DDGS-text → Playwright-scrape, health-gated) → dedup+rank (quality-domain score) → web_fetch top → single Gemini synthesis → source footer`. Gemini used only for synthesis, never retrieval.

---

## 6. RISK INVENTORY (paranoid, prioritized)

| # | Sev | Finding | Location |
|---|-----|---------|----------|
| 1 | ✅ RESOLVED 2026-08-05 | Live Gemini key was committed in plaintext (39 chars; the literal is deliberately NOT reproduced in this document). Key rotated in AI Studio and the old one revoked; `config/api_keys.json` deleted together with its loader fallback (phase 0, step 1). Guard: `tests/test_config_loader.py::test_no_key_literal_anywhere`. | was `config/api_keys.json` |
| 2 | 🔴 CRITICAL | **`agent/` executor bypasses the security gate** (0 references to `check_tool_call`). 4 blocked tools reachable via `agent_task`: `computer_settings`, `dev_agent`, `send_message`, `generated_code`. Only internal stubs prevent execution. | `agent/executor.py:107` |
| 3 | 🟠 HIGH | Prompt-injection surface: memory, personality, action-journal, screen-vision text concatenated verbatim into system prompt, no delimiting/sanitizing. Memory is LLM-extracted from speech. | `main.py:872`, `dialogue_state.format_for_prompt` |
| 4 | 🟠 HIGH | Self-weakening safety: `personality_engine` can infer `autonomy=do_it` / `question_tolerance=low` from speech → injects "never ask, just do it", eroding the confirm protocol. | `memory/personality_engine.py` |
| 5 | 🟠 HIGH | `cmd_control` free-text `command` param bypasses the read-only allowlist (only regex denylist, bypassable via `powershell -enc`, `certutil`); `shell=True` on POSIX. | `actions/cmd_control.py:222,148` |
| 6 | 🟡 MED | Unencrypted PII in `long_term.json` (name/age/city/relationships); flows into `computer_control` autofill (typed into on-screen fields). Not gitignored. | `memory/`, `computer_control._load_user_profile` |
| 7 | 🟡 MED | Security tests validate a *copy* of the gate, not real `_execute_tool`. Memory/personality/agent/prompt-assembly untested. | `tests/test_security_stage1.py` |
| 8 | 🟡 MED | `error_handler.generate_fix` intends to execute generated Python via `code_helper run` (currently stubbed, wiring live). | `agent/error_handler.py:147` |
| 9 | 🟢 LOW | Model sprawl: 5 IDs, 36 hardcoded occurrences, no central config. `_get_api_key` defined 14×. Two SDKs (`google.genai` + deprecated `google.generativeai`). Stale name "MARK XXV" in `computer_control.py` & `error_handler.py`. Dead code cluster (semantic_interpreter/uncertainty_policy/response_composer only serve agent path; cmd_control `_ask_gemini`/`_run_visible` unreachable). | various |

## 7. What's well-built
Centralized single-source-of-truth modules + circuit breakers everywhere (session reconnect, model quota, provider health) + generate-truth-from-policy anti-drift. `file_controller` boundary guard (`.resolve()`-then-check) is solid and tested. Live-API latency engineering in Screen View is advanced. Prompt engineering is thoughtful.

## 8. Domain glossary
- **Screen View** = read-only vision (separate Gemini Live session). **Screen Control** = interactive clicking (`computer_control`, different toggle).
- **aux call** = any non-live Gemini REST call (quota-guarded via `aux_model`/`model_guard`).
- **PolicyMode** = auto/confirm/forbid, derived from (status, risk).
- **Action journal** = rolling 8-entry log injected into context for follow-up resolution.

## 9. Verification (executable, 2026-07-01)
- `grep -c check_tool_call agent/*.py` → **0** in all 4 files (Finding #2 proven).
- executor-reachable ∩ gate-blocked = `{computer_settings, dev_agent, send_message, generated_code}` (proven).
- `config/api_keys.json` gemini key present, len 39 (Finding #1 proven; file and its loader fallback deleted 2026-08-05 — see Finding #1).
- `def _get_api_key` defined **14×** (Finding #9 proven).
- All 49 `.py` files parse cleanly (AST) — tree is runnable.

## 10. Recommended fix order
1. Route `agent/executor._call_tool` through `check_tool_call` (close #2).
2. Rotate key + fix `.gitignore` (#1).
3. Fence/sanitize injected memory/personality/journal text (#3).
4. Keep confirm-gate authority in code, not LLM-inferred personality (#4).
5. Consolidate `core/config` (one key loader) + `core/llm` (one model registry) — kills #9 and half the duplication.
6. Add tests for the real gate + memory + agent path (#7).
