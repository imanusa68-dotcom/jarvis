# core/security.py
# ═══════════════════════════════════════════════════════════════════════════════
# MARK XXXV — Centralized Security Policy
# ═══════════════════════════════════════════════════════════════════════════════
#
# This is the SINGLE SOURCE OF TRUTH for all tool/action security decisions.
#
# All other parts of the system (planner, executor, main, actions) must consult
# this module before advertising capabilities or executing tool calls.
#
# Architecture:
#   1. SECURITY_POLICY — Central registry of all tools and their allowed/blocked actions
#   2. check_tool_call() — Main security gate used by executor
#   3. format_security_block() — Consistent blocking message format
#   4. get_planner_visible_tools() — Tools/actions visible to planner
#   5. get_allowed_actions_text() — For honest tool descriptions in main.py
#
# Defense-in-depth:
#   Local guards (_SAFE_APPS, BLOCKED_PATTERNS, screen_control, etc.) remain
#   as secondary protection inside action modules. This central policy is
#   the first line of defense at orchestration level.
# ═══════════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field
from typing import Literal, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

PolicyStatus = Literal["allowed", "blocked", "requires_screen_control"]
RiskLevel = Literal["low", "medium", "high", "critical"]
#
# Status meanings:
#   "allowed"                 — execute normally
#   "blocked"                 — never execute (returns a security block message)
#   "requires_screen_control" — execute ONLY when the user has the SCREEN button
#                               ON (player.screen_control). This mirrors the real
#                               behaviour of actions/computer_control.py, where
#                               interactive actions (click/type/focus_window…) are
#                               gated by the SCREEN toggle rather than hard-blocked.

# ─────────────────────────────────────────────────────────────────────────────
# PolicyMode — Stage 1 "reasoning rail" (DATA ONLY — not yet acted upon).
# ─────────────────────────────────────────────────────────────────────────────
# This describes HOW an allowed action should be carried out once the reasoning
# layer is switched on in Stage 2. In Stage 1 it is computed and exposed, but
# NOTHING reads it yet — behaviour is unchanged.
#
#   "auto"    — safe / easily reversible → just do it, silently.
#   "confirm" — risky / hard to undo → (Stage 2) ask the user before doing it.
#   "forbid"  — currently turned off in this build. NOT "forbidden forever";
#               a candidate for re-enabling behind confirmation in Stage 3.
#
# The mode is DERIVED from (status, risk) by resolve_policy() so it can never
# drift from the policy. An explicit per-entry override exists only for the rare
# case where the derived default is wrong.
PolicyMode = Literal["auto", "confirm", "forbid"]


@dataclass(frozen=True)
class ActionPolicy:
    """Policy for a specific action within a tool."""
    status: PolicyStatus
    planner_visible: bool = True
    risk: RiskLevel = "low"
    reason: str = ""
    # Optional explicit Stage-1 mode override. When None, resolve_policy()
    # derives the mode from (status, risk). Keep last + defaulted so existing
    # keyword constructions are unaffected.
    policy: Optional[PolicyMode] = None


@dataclass(frozen=True)
class ToolPolicy:
    """Policy for a tool and all its actions."""
    status: PolicyStatus
    planner_visible: bool = True
    risk: RiskLevel = "low"
    reason: str = ""
    actions: dict[str, ActionPolicy] = field(default_factory=dict)
    # Optional explicit Stage-1 mode override (see ActionPolicy.policy).
    policy: Optional[PolicyMode] = None


@dataclass
class SecurityDecision:
    """Result of a security check."""
    allowed: bool
    tool: str
    action: Optional[str] = None
    reason: str = ""
    risk: RiskLevel = "low"
    planner_visible: bool = True
    # True when the action may run, but ONLY if the SCREEN toggle is ON.
    # The caller (main._execute_tool) is responsible for checking the live
    # player.screen_control flag and blocking with `reason` if it is OFF.
    requires_screen_control: bool = False
    # Stage-1 reasoning rail (DATA ONLY in Stage 1 — nothing reads it yet).
    # auto = do silently, confirm = (Stage 2) ask first, forbid = currently off.
    policy: PolicyMode = "auto"


# ─────────────────────────────────────────────────────────────────────────────
# CENTRAL SECURITY POLICY
# ─────────────────────────────────────────────────────────────────────────────
# This is the authoritative source of truth for all tool/action permissions.
#
# Risk calibration philosophy (drives Stage-1 PolicyMode, avoids confirm-fatigue):
#   low      — read-only / display / trivially reversible → auto, never ask.
#   medium   — reversible side effects (open app, copy file, navigate) → auto.
#   high     — hard to undo or intrusive (delete, drag, send) → confirm in Stage 2.
#   critical — destructive / irreversible / security-sensitive → confirm (and is
#              typically also currently blocked → forbid until Stage 3).
# Rule of thumb: a confirm prompt must feel justified. If a normal user would be
# annoyed to be asked, the action is mis-rated as high — keep it medium/auto.
# ─────────────────────────────────────────────────────────────────────────────

SECURITY_POLICY: dict[str, ToolPolicy] = {
    
    # ═══════════════════════════════════════════════════════════════════════
    # FULLY ALLOWED TOOLS (no action-level restrictions)
    # ═══════════════════════════════════════════════════════════════════════
    
    "web_search": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Read-only web search",
    ),
    
    "weather_report": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Read-only weather data",
    ),
    
    "reminder": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Local reminder management",
    ),
    
    "youtube_video": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="YouTube info and trending (play/summarize blocked locally)",
        actions={
            "get_info": ActionPolicy(status="allowed", risk="low"),
            "trending": ActionPolicy(status="allowed", risk="low"),
            "info": ActionPolicy(status="allowed", risk="low"),
            "play": ActionPolicy(status="blocked", planner_visible=False, risk="medium",
                                 reason="Browser automation disabled"),
            "summarize": ActionPolicy(status="blocked", planner_visible=False, risk="medium",
                                      reason="Browser automation disabled"),
        }
    ),
    
    "screen_process": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Screen/camera analysis (read-only)",
    ),
    
    "flight_finder": ToolPolicy(
        status="blocked",
        planner_visible=False,
        risk="low",
        reason="Tool removed at stage 0 (was a stub); blocked as defense-in-depth",
    ),
    
    "open_app": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="App launching with local _SAFE_APPS allowlist",
    ),
    
    "browser_control": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Browser automation for web tasks",
    ),
    
    "cmd_control": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Terminal commands with local safety filters (read-only scenarios)",
    ),
    
    "agent_task": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Multi-step task orchestration",
    ),
    
    "save_memory": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Save user preferences to local memory",
    ),

    # Забывание и вспоминание — фаза 1г. До неё оба инструмента
    # ОТСУТСТВОВАЛИ здесь, и дверь по правилу fail-closed отвечала им
    # "Unknown tool": то есть память была единственным местом в доме,
    # откуда факт можно было УДАЛИТЬ без следа в журнале. Запись уже
    # ходила через дверь (фаза 1в), удаление — нет; запретить писать,
    # разрешив стирать, значит оставить дырку в форме двери.
    #
    # РИСК low У ОБОИХ — ПРЯМОЕ РЕШЕНИЕ ВЛАДЕЛЬЦА (28.08.2026), дословно:
    # «я хочу чтобы когда owner ... говорю запомнить или забыть и т.д.
    # то дверь без проблем пропускала», и на вопрос про подтверждение —
    # «нет, мне надоест мне всегда подтверждать ему». Риск выше low
    # означает "спросить владельца" В РАЗГОВОРЕ ТОЖЕ (см. шапку
    # core/fences.py): владелец сказал бы «забудь про кофе» и услышал
    # «Подтвердите». Запрет ПОД-АГЕНТУ живёт не здесь, а в заборе
    # (core/fences.py, MEMORY_TOOLS): риск отвечает на вопрос
    # «насколько опасно», забор — на «кто просит». Смешав их в одном
    # числе, мы теряем возможность сказать «ему никогда, а тебе сразу».
    "forget_memory": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Delete a fact from local memory (owner passes freely)",
    ),

    # Чтение памяти. Под-агенту РАЗРЕШЕНО (в заборе его нет) — решение
    # владельца: чтение будущего поведения не меняет, а работу улучшает.
    "recall_memory": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Search local memory mid-conversation (read-only)",
    ),

    "system_context": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Read-only snapshot of open windows/apps (ambient awareness)",
    ),

    "resolve_reference": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="low",
        reason="Read-only referent resolution ('this file'/'that app' → concrete path)",
    ),

    "open_path": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Open a file/folder in its default app (personal folders only; executables blocked)",
    ),
    
    # ═══════════════════════════════════════════════════════════════════════
    # PARTIALLY ALLOWED TOOLS (action-level restrictions)
    # ═══════════════════════════════════════════════════════════════════════
    
    "file_controller": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="File ops: read/list/copy/create allowed; write/move/rename/delete need confirm",
        actions={
            # ALLOWED (read-only)
            "list": ActionPolicy(status="allowed", risk="low"),
            "read": ActionPolicy(status="allowed", risk="low"),
            "undo": ActionPolicy(status="allowed", risk="low",
                                 reason="Restore a file's previous content from backup"),
            "restore": ActionPolicy(status="allowed", risk="low",
                                    reason="Restore a file's previous content from backup"),
            "redo": ActionPolicy(status="allowed", risk="low",
                                 reason="Re-apply an action that was just undone"),
            "history": ActionPolicy(status="allowed", risk="low",
                                    reason="Read-only view of the undo/redo timeline"),
            "find": ActionPolicy(status="allowed", risk="low"),
            "largest": ActionPolicy(status="allowed", risk="low"),
            "disk_usage": ActionPolicy(status="allowed", risk="low"),
            "info": ActionPolicy(status="allowed", risk="low"),
            "copy": ActionPolicy(status="allowed", risk="medium"),
            # Stage 3 slice 1 — creating/writing inside the safe user folders.
            # The module enforces the folder boundary; creating new content is
            # easily reversible → auto. Writing can overwrite → confirm.
            "create_file": ActionPolicy(status="allowed", risk="medium",
                                        reason="Create a new file in a safe user folder"),
            "create_folder": ActionPolicy(status="allowed", risk="medium",
                                          reason="Create a folder in a safe user folder"),
            "write": ActionPolicy(status="allowed", risk="high",
                                  reason="Write/overwrite a file in a safe user folder"),
            # Stage 3 slice — move/rename inside the safe user folders, behind a
            # confirmation (they can clobber an existing target). Both source and
            # destination are boundary-checked in the module.
            "move": ActionPolicy(status="allowed", risk="high",
                                 reason="Move a file within the safe user folders"),
            "rename": ActionPolicy(status="allowed", risk="high",
                                   reason="Rename a file within the safe user folders"),
            # Stage 2.6 — deletion is no longer a one-way door: the bytes are
            # staged for undo AND the file goes to the Recycle Bin. risk=high
            # means the user is asked to confirm before anything is removed.
            "delete": ActionPolicy(status="allowed", risk="high",
                                   reason="Delete to Recycle Bin, undoable, confirm first"),
            # STILL BLOCKED — later Stage 3 slices.
            "organize_desktop": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                             reason="Desktop organization disabled for safety"),
        }
    ),
    
    "code_helper": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Code assistance with execution actions blocked",
        actions={
            # ALLOWED (read-only)
            "explain": ActionPolicy(status="allowed", risk="low"),
            # BLOCKED (code execution/modification)
            "auto": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                 reason="Auto action detection disabled for safety"),
            "write": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                  reason="Code writing disabled for safety"),
            "edit": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                 reason="Code editing disabled for safety"),
            "run": ActionPolicy(status="blocked", planner_visible=False, risk="critical",
                                reason="Code execution disabled for safety"),
            "build": ActionPolicy(status="blocked", planner_visible=False, risk="critical",
                                  reason="Code building disabled for safety"),
            "screen_debug": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                         reason="Screen debug disabled for safety"),
            "optimize": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                     reason="Code optimization disabled for safety"),
        }
    ),
    
    "game_updater": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Game management with install/update actions blocked",
        actions={
            # ALLOWED (read-only)
            "list": ActionPolicy(status="allowed", risk="low"),
            "download_status": ActionPolicy(status="allowed", risk="low"),
            "schedule_status": ActionPolicy(status="allowed", risk="low"),
            # BLOCKED (system modification)
            "install": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                    reason="Game installation disabled for safety"),
            "update": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                   reason="Game update disabled for safety"),
            "schedule": ActionPolicy(status="blocked", planner_visible=False, risk="medium",
                                     reason="Update scheduling disabled for safety"),
            "cancel_schedule": ActionPolicy(status="blocked", planner_visible=False, risk="medium",
                                            reason="Schedule cancellation disabled for safety"),
            "shutdown": ActionPolicy(status="blocked", planner_visible=False, risk="critical",
                                     reason="System shutdown disabled for safety"),
        }
    ),
    
    "desktop_control": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="medium",
        reason="Desktop management with modification actions blocked",
        actions={
            # ALLOWED (read-only)
            "list": ActionPolicy(status="allowed", risk="low"),
            "stats": ActionPolicy(status="allowed", risk="low"),
            "current_wallpaper": ActionPolicy(status="allowed", risk="low"),
            # BLOCKED (file/system modification)
            "wallpaper": ActionPolicy(status="blocked", planner_visible=False, risk="medium",
                                      reason="Wallpaper change disabled for safety"),
            "wallpaper_url": ActionPolicy(status="blocked", planner_visible=False, risk="medium",
                                          reason="Wallpaper download disabled for safety"),
            "organize": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                     reason="File organization disabled for safety"),
            "clean": ActionPolicy(status="blocked", planner_visible=False, risk="high",
                                  reason="Desktop cleanup disabled for safety"),
            "task": ActionPolicy(status="blocked", planner_visible=False, risk="critical",
                                 reason="Code execution disabled for safety"),
        }
    ),
    
    "computer_control": ToolPolicy(
        status="allowed",
        planner_visible=True,
        risk="high",
        reason="Computer control; interactive actions require the SCREEN toggle",
        actions={
            # ALLOWED (read-only / low risk) — run regardless of SCREEN toggle
            "screenshot": ActionPolicy(status="allowed", risk="low"),
            "wait": ActionPolicy(status="allowed", risk="low"),
            "screen_size": ActionPolicy(status="allowed", risk="low"),
            # Reads the live SCREEN button and answers in words. Must stay
            # "allowed"+"low": an unlisted action inherits the tool-level
            # "high" risk, which resolve_policy turns into "confirm" -- and
            # then asking "may I click?" would itself need a consent ticket.
            "screen_status": ActionPolicy(status="allowed", risk="low"),
            "random_data": ActionPolicy(status="allowed", risk="low"),
            "user_data": ActionPolicy(status="allowed", risk="low"),
            "copy": ActionPolicy(status="allowed", risk="low"),
            # INTERACTIVE — allowed ONLY when the SCREEN button is ON.
            # This mirrors actions/computer_control.py INTERACTIVE_ACTIONS exactly,
            # so the central gate matches real behaviour (Stage 0: no behaviour change).
            "type": ActionPolicy(status="requires_screen_control", risk="high",
                                 reason="Typing requires the SCREEN toggle"),
            "smart_type": ActionPolicy(status="requires_screen_control", risk="high",
                                       reason="Typing requires the SCREEN toggle"),
            "click": ActionPolicy(status="requires_screen_control", risk="high",
                                  reason="Clicking requires the SCREEN toggle"),
            "left_click": ActionPolicy(status="requires_screen_control", risk="high",
                                       reason="Clicking requires the SCREEN toggle"),
            "double_click": ActionPolicy(status="requires_screen_control", risk="high",
                                         reason="Clicking requires the SCREEN toggle"),
            "right_click": ActionPolicy(status="requires_screen_control", risk="high",
                                        reason="Clicking requires the SCREEN toggle"),
            "hotkey": ActionPolicy(status="requires_screen_control", risk="high",
                                   reason="Hotkeys require the SCREEN toggle"),
            "press": ActionPolicy(status="requires_screen_control", risk="high",
                                  reason="Key press requires the SCREEN toggle"),
            "scroll": ActionPolicy(status="requires_screen_control", risk="medium",
                                   reason="Scrolling requires the SCREEN toggle"),
            "move": ActionPolicy(status="requires_screen_control", risk="medium",
                                 reason="Mouse movement requires the SCREEN toggle"),
            "drag": ActionPolicy(status="requires_screen_control", risk="high",
                                 reason="Dragging requires the SCREEN toggle"),
            "paste": ActionPolicy(status="requires_screen_control", risk="high",
                                  reason="Paste requires the SCREEN toggle"),
            "clear_field": ActionPolicy(status="requires_screen_control", risk="high",
                                        reason="Field clearing requires the SCREEN toggle"),
            "focus_window": ActionPolicy(status="requires_screen_control", risk="medium",
                                         reason="Window focus requires the SCREEN toggle"),
            "screen_click": ActionPolicy(status="requires_screen_control", risk="high",
                                         reason="Screen click requires the SCREEN toggle"),
            "screen_find": ActionPolicy(status="requires_screen_control", risk="low",
                                        reason="Screen find requires the SCREEN toggle"),
            "wait_image": ActionPolicy(status="requires_screen_control", risk="low",
                                       reason="Image wait requires the SCREEN toggle"),
        }
    ),
    
    # ═══════════════════════════════════════════════════════════════════════
    # FULLY BLOCKED TOOLS
    # ═══════════════════════════════════════════════════════════════════════
    
    "volume": ToolPolicy(
        status="allowed",
        planner_visible=False,
        risk="low",
        reason="System output volume only; other system settings stay blocked",
        actions={
            "up": ActionPolicy(status="allowed", risk="low"),
            "down": ActionPolicy(status="allowed", risk="low"),
            "set": ActionPolicy(status="allowed", risk="low"),
            "mute": ActionPolicy(status="allowed", risk="low"),
            "unmute": ActionPolicy(status="allowed", risk="low"),
            "status": ActionPolicy(status="allowed", risk="low"),
        }
    ),
    
    "send_message": ToolPolicy(
        status="blocked",
        planner_visible=False,
        risk="critical",
        reason="Automated messaging is disabled for safety",
    ),
    
    "computer_settings": ToolPolicy(
        status="blocked",
        planner_visible=False,
        risk="critical",
        reason="System settings control is disabled for safety",
    ),
    
    "dev_agent": ToolPolicy(
        status="blocked",
        planner_visible=False,
        risk="critical",
        reason="Project building and code execution is disabled for safety",
    ),
    
    "generated_code": ToolPolicy(
        status="blocked",
        planner_visible=False,
        risk="critical",
        reason="Generated code execution is disabled for safety",
    ),
      "analyze_screen_view": ToolPolicy(
          status="allowed",
          planner_visible=True,
          risk="low",
          reason="Analyze the current screen share frame (read-only vision)",
      ),

      "screen_share_control": ToolPolicy(
          status="allowed",
          planner_visible=True,
          risk="low",
          reason="Start, stop, or query the Screen View mode",
          actions={
              "start":  ActionPolicy(status="allowed", risk="low"),
              "stop":   ActionPolicy(status="allowed", risk="low"),
              "status": ActionPolicy(status="allowed", risk="low"),
          },
      ),
  
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def normalize_action(tool: str, parameters: dict | None) -> str | None:
    """
    Extract action from parameters if the tool uses action-based dispatch.
    Returns None if tool doesn't use actions.
    """
    if parameters is None:
        return None
    
    # Tools that use 'action' parameter
    action_based_tools = {
        "file_controller", "code_helper", "game_updater", "desktop_control",
        "computer_control", "youtube_video", "reminder", "browser_control",
        "volume"
    }
    
    if tool in action_based_tools:
        action = parameters.get("action", "").lower().strip()
        return action if action else None
    
    return None


def check_tool_call(tool: str, parameters: dict | None = None) -> SecurityDecision:
    """
    Main security gate. Check if a tool call is allowed.

    This function MUST be called before executing any tool.

    Stage 1: also stamps decision.policy (auto/confirm/forbid) on the result.
    This is informational only — no caller acts on it yet.

    Returns:
        SecurityDecision with allowed=True/False and relevant metadata.
    """
    decision = _check_tool_call_inner(tool, parameters)
    # Stamp the Stage-1 reasoning mode in one place so every return path agrees.
    if not decision.allowed:
        decision.policy = "forbid"
    else:
        decision.policy = get_policy(tool, parameters)
    return decision


def _check_tool_call_inner(tool: str, parameters: dict | None = None) -> SecurityDecision:
    """Core allow/block logic. check_tool_call wraps this to stamp policy."""
    tool = tool.lower().strip()

    # Unknown tool
    if tool not in SECURITY_POLICY:
        return SecurityDecision(
            allowed=False,
            tool=tool,
            action=None,
            reason=f"Unknown tool '{tool}' is not registered in security policy",
            risk="critical",
            planner_visible=False,
        )
    
    policy = SECURITY_POLICY[tool]
    
    # Tool-level block
    if policy.status == "blocked":
        return SecurityDecision(
            allowed=False,
            tool=tool,
            action=None,
            reason=policy.reason,
            risk=policy.risk,
            planner_visible=policy.planner_visible,
        )
    
    # Check action-level policy if applicable
    action = normalize_action(tool, parameters)
    
    if action and policy.actions:
        if action in policy.actions:
            action_policy = policy.actions[action]
            if action_policy.status == "blocked":
                return SecurityDecision(
                    allowed=False,
                    tool=tool,
                    action=action,
                    reason=action_policy.reason,
                    risk=action_policy.risk,
                    planner_visible=action_policy.planner_visible,
                )
            if action_policy.status == "requires_screen_control":
                # Allowed in principle, but the caller must verify the SCREEN
                # toggle is ON before executing. allowed=True keeps current
                # behaviour; the caller blocks with `reason` when the toggle is OFF.
                return SecurityDecision(
                    allowed=True,
                    tool=tool,
                    action=action,
                    reason=action_policy.reason,
                    risk=action_policy.risk,
                    planner_visible=action_policy.planner_visible,
                    requires_screen_control=True,
                )
            # Action explicitly allowed
            return SecurityDecision(
                allowed=True,
                tool=tool,
                action=action,
                reason="",
                risk=action_policy.risk,
                planner_visible=action_policy.planner_visible,
            )
        else:
            # Action not in policy — allow by default (tool is allowed)
            # Local guards in action modules will handle unknown actions
            return SecurityDecision(
                allowed=True,
                tool=tool,
                action=action,
                reason="Action not explicitly listed, deferring to local guards",
                risk=policy.risk,
                planner_visible=True,
            )
    
    # Tool allowed, no action-level restrictions apply
    return SecurityDecision(
        allowed=True,
        tool=tool,
        action=action,
        reason="",
        risk=policy.risk,
        planner_visible=policy.planner_visible,
    )


def format_security_block(decision: SecurityDecision) -> str:
    """
    Format a consistent blocking message for the user/model.
    """
    if decision.action:
        return (
            f"SECURITY: Action '{decision.action}' in tool '{decision.tool}' is blocked. "
            f"{decision.reason}"
        )
    return (
        f"SECURITY: Tool '{decision.tool}' is blocked. "
        f"{decision.reason}"
    )


def get_planner_visible_tools() -> dict[str, dict]:
    """
    Returns a dict of tools and their allowed actions for the planner.
    Only includes tools/actions that are planner_visible=True.
    
    Returns:
        {
            "tool_name": {
                "visible": True,
                "allowed_actions": ["action1", "action2"] or None,
                "risk": "low"|"medium"|"high"
            },
            ...
        }
    """
    result = {}
    
    for tool_name, policy in SECURITY_POLICY.items():
        if not policy.planner_visible:
            continue
        if policy.status == "blocked":
            continue
        
        allowed_actions = None
        if policy.actions:
            allowed_actions = [
                action_name
                for action_name, action_policy in policy.actions.items()
                if action_policy.status in ("allowed", "requires_screen_control")
                and action_policy.planner_visible
            ]
        
        result[tool_name] = {
            "visible": True,
            "allowed_actions": allowed_actions,
            "risk": policy.risk,
        }
    
    return result


def build_planner_tools_section() -> str:
    """
    Build a formatted text section describing available tools for the planner.
    """
    lines = ["AVAILABLE TOOLS AND ALLOWED ACTIONS:\n"]
    
    visible = get_planner_visible_tools()
    
    for tool_name, info in sorted(visible.items()):
        if info["allowed_actions"]:
            actions_str = ", ".join(sorted(info["allowed_actions"]))
            lines.append(f"  {tool_name}")
            lines.append(f"    Allowed actions: {actions_str}")
        else:
            lines.append(f"  {tool_name}")
            lines.append(f"    All actions allowed (check local guards)")
        lines.append("")
    
    return "\n".join(lines)


def get_allowed_actions_text(tool_name: str) -> str:
    """
    Get a human-readable list of allowed actions for a tool.
    Used for generating honest tool descriptions in main.py.
    """
    tool_name = tool_name.lower().strip()
    
    if tool_name not in SECURITY_POLICY:
        return "Tool not found in policy."
    
    policy = SECURITY_POLICY[tool_name]
    
    if policy.status == "blocked":
        return "Tool is disabled."
    
    if not policy.actions:
        return "All actions allowed."
    
    allowed = [
        name for name, ap in policy.actions.items()
        if ap.status in ("allowed", "requires_screen_control")
    ]
    
    if not allowed:
        return "No actions currently allowed."
    
    return ", ".join(sorted(allowed))


def get_tool_restriction_note(tool_name: str) -> str:
    """
    Get a note about restrictions for a tool.
    Used for adding honest disclaimers to tool descriptions.
    """
    tool_name = tool_name.lower().strip()
    
    if tool_name not in SECURITY_POLICY:
        return ""
    
    policy = SECURITY_POLICY[tool_name]
    
    if policy.status == "blocked":
        return f"(DISABLED: {policy.reason})"
    
    if policy.actions:
        blocked = [
            name for name, ap in policy.actions.items()
            if ap.status == "blocked"
        ]
        if blocked:
            return f"(Note: {', '.join(blocked)} actions are disabled for safety)"
    
    return ""


def _RISK_ORDER(level: RiskLevel) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(level, 1)


def get_risk(tool_name: str, parameters: dict | None = None) -> RiskLevel:
    """
    Risk level for a tool (and action, if applicable) straight from the policy.
    This is the single source of truth for risk — uncertainty_policy reads it
    so risk is never defined in two places.

    A few tools take free-text input whose danger the static table cannot see
    (e.g. cmd_control's natural-language task). For those we promote risk based
    on the parameters. Returns "medium" for unknown tools (conservative).
    """
    tool_name = tool_name.lower().strip()
    policy = SECURITY_POLICY.get(tool_name)
    if policy is None:
        return "medium"

    action = normalize_action(tool_name, parameters)
    if action and policy.actions and action in policy.actions:
        base = policy.actions[action].risk
    else:
        base = policy.risk

    # Param-aware promotion (kept here so security stays the single risk source).
    promoted = base
    if parameters:
        if tool_name == "cmd_control":
            task = str(parameters.get("task", "")).lower()
            danger = ("delete", "rm ", "format", "shutdown", "kill", "drop", "del ")
            if any(w in task for w in danger):
                promoted = "high"
    # Never lower the static risk, only raise it.
    return promoted if _RISK_ORDER(promoted) > _RISK_ORDER(base) else base


def resolve_policy(
    status: PolicyStatus,
    risk: RiskLevel,
    explicit: Optional[PolicyMode] = None,
) -> PolicyMode:
    """
    Derive the Stage-1 PolicyMode from (status, risk).

    Single source of truth: the mode is computed, never hand-maintained, so it
    cannot drift from status/risk. An explicit per-entry override wins when set.

    Mapping:
      blocked                  → forbid   (currently turned off in this build)
      requires_screen_control  → auto     (SCREEN toggle already gates it; we do
                                            not double-prompt on top of that)
      allowed + low/medium     → auto     (safe / easily reversible)
      allowed + high/critical  → confirm  (risky / hard to undo → ask in Stage 2)

    Stage 1 NOTE: the returned value is informational only. No caller acts on
    it yet, so behaviour is unchanged.
    """
    if explicit is not None:
        return explicit
    if status == "blocked":
        return "forbid"
    if status == "requires_screen_control":
        return "auto"
    # status == "allowed"
    if risk in ("high", "critical"):
        return "confirm"
    return "auto"


def get_policy(tool_name: str, parameters: dict | None = None) -> PolicyMode:
    """
    Resolve the PolicyMode for a tool (and action, if applicable), honouring any
    explicit override on the matching ActionPolicy/ToolPolicy. Mirrors how
    check_tool_call resolves status/risk so the two never disagree.
    """
    tool_name = tool_name.lower().strip()
    policy = SECURITY_POLICY.get(tool_name)
    if policy is None:
        return "forbid"  # unknown tool is treated as off

    # Use param-aware risk so free-text-dangerous calls (e.g. a destructive
    # cmd_control task) resolve to "confirm" rather than the static default.
    eff_risk = get_risk(tool_name, parameters)

    action = normalize_action(tool_name, parameters)
    if action and policy.actions and action in policy.actions:
        ap = policy.actions[action]
        return resolve_policy(ap.status, eff_risk, ap.policy)
    return resolve_policy(policy.status, eff_risk, policy.policy)


def _is_confirmed(parameters: dict | None) -> bool:
    """
    True if the model has explicitly confirmed this call by passing confirmed=true.
    Accepts bool True or the strings "true"/"yes"/"1" (Gemini sometimes stringifies).
    """
    if not parameters:
        return False
    val = parameters.get("confirmed", False)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "yes", "1")


# ── Delete "burst" window ────────────────────────────
# User policy: confirm the FIRST delete, then let a short burst of follow-up
# deletes run without re-prompting. The window is refreshed on every delete,
# so a continuous series stays confirmation-free; after a pause past the
# window the next delete asks again. Interactive-only: an autonomous task can
# never ride the burst (delete there still needs explicit confirmation).
_DELETE_BURST_WINDOW_S = 180.0
_delete_burst = {"until": 0.0}


def _mono() -> float:
    import time
    return time.monotonic()


def open_delete_burst(now: float | None = None) -> None:
    """Open/refresh the delete burst window."""
    base = _mono() if now is None else now
    _delete_burst["until"] = base + _DELETE_BURST_WINDOW_S


def _burst_active(now: float | None = None) -> bool:
    return (_mono() if now is None else now) < _delete_burst["until"]


def reset_delete_burst() -> None:
    """Forget any open burst (e.g. new session / cautious reset)."""
    _delete_burst["until"] = 0.0


def _is_delete(tool_name: str, action: str | None) -> bool:
    return (tool_name or "").lower().strip() == "file_controller" and action == "delete"

def needs_confirmation(tool_name: str, parameters: dict | None = None, mode: str = "interactive") -> tuple[bool, str]:
    """
    Decide whether a tool call must be confirmed by the user before running.

    Stage 2 (mechanism only): a call needs confirmation when its resolved
    PolicyMode is "confirm" AND the model has not yet passed confirmed=true.

    The decision is the SOLE gate — the prompt instruction to ask out loud is
    only UX. Even if the model skips asking, an un-confirmed "confirm" call is
    not executed.

    Returns (needs_confirm, human_reason). reason is "" when no confirmation
    is required.
    """
    action = normalize_action(tool_name, parameters)
    is_delete = _is_delete(tool_name, action)

    if _is_confirmed(parameters):
        # A real, user-confirmed delete opens the burst window so a rapid
        # series right after does not re-prompt (interactive only).
        if is_delete and mode == "interactive":
            open_delete_burst()
        return False, ""
    if get_policy(tool_name, parameters) != "confirm":
        return False, ""
    # Ask ONCE per burst: inside the window opened by a confirmed delete, a
    # follow-up delete runs without a fresh prompt (and refreshes the window).
    if is_delete and mode == "interactive" and _burst_active():
        open_delete_burst()
        return False, ""
    # Build a short, honest reason from the policy entry.
    risk = get_risk(tool_name, parameters)
    what = f"{tool_name}" + (f"/{action}" if action else "")
    if is_delete:
        reason = (
            f"This action ({what}) moves the file to the Recycle Bin and is "
            "REVERSIBLE - it can be restored with undo."
        )
    else:
        reason = f"This action ({what}) is {risk}-risk and may be hard to undo."
    return True, reason


def format_confirmation_request(tool_name: str, reason: str) -> str:
    """
    Message returned to the model when confirmation is required on the LEGACY
    path (durable-consent flag OFF, or the consent store unreachable).

    Stage 3C: this text no longer mentions `confirmed`. That field is gone from
    every tool schema, so advertising it here would send the model looking for a
    parameter that does not exist — and, worse, teach it that a boolean it can
    set itself is a form of consent. The legacy path still ACCEPTS `confirmed`
    (see _is_confirmed) so an in-flight call is not stranded, but nothing invites
    the model to use it. This is a normal tool result — it does not block the
    runtime.
    """
    return (
        "CONFIRMATION_REQUIRED: "
        f"{reason} "
        "Do NOT proceed yet. Ask the user out loud for explicit confirmation in "
        "their language, quoting what will happen. ONLY if they clearly agree, "
        "call this same tool again with the identical parameters. If they "
        "decline, do not call it and acknowledge briefly."
    )


def is_tool_fully_blocked(tool_name: str) -> bool:
    """Check if a tool is completely blocked at tool level."""
    tool_name = tool_name.lower().strip()
    if tool_name not in SECURITY_POLICY:
        return True  # Unknown tools are blocked
    return SECURITY_POLICY[tool_name].status == "blocked"


def get_blocked_tools() -> list[str]:
    """Get list of completely blocked tools."""
    return [
        name for name, policy in SECURITY_POLICY.items()
        if policy.status == "blocked"
    ]


def build_capability_truth_section() -> str:
    """
    Render an honest, runtime-generated section describing what is currently
    blocked or restricted — derived directly from SECURITY_POLICY.

    Injected into the system prompt so the model never advertises (and never
    tries) capabilities that the gate will reject. This is what makes the
    tool descriptions and the real policy impossible to drift apart: there is
    nothing to hand-edit, the truth is generated from one place.
    """
    fully_blocked: list[str] = []
    restricted: list[str] = []

    for tool_name, policy in sorted(SECURITY_POLICY.items()):
        if policy.status == "blocked":
            fully_blocked.append(f"  - {tool_name}: DISABLED — {policy.reason}")
            continue
        if policy.actions:
            blocked = [n for n, ap in policy.actions.items() if ap.status == "blocked"]
            screen = [n for n, ap in policy.actions.items()
                      if ap.status == "requires_screen_control"]
            notes = []
            if blocked:
                notes.append(f"blocked actions: {', '.join(sorted(blocked))}")
            if screen:
                notes.append("interactive actions need the SCREEN toggle ON")
            if notes:
                restricted.append(f"  - {tool_name}: {'; '.join(notes)}")

    lines = [
        "[CURRENT CAPABILITY LIMITS — authoritative, generated from the security policy]",
        "These reflect what you can ACTUALLY do right now. Do NOT promise or attempt",
        "anything listed as disabled — it will be rejected. Prefer an allowed alternative,",
        "or tell the user plainly that the capability is currently turned off.",
        "",
        "IMPORTANT — affirmative permissions (do NOT refuse these):",
        "You CAN create files and folders, write/overwrite file contents, and create",
        "real Office documents (.docx, .xlsx) inside the user's personal folders",
        "(Desktop, Downloads, Documents, Pictures, Music, Videos). When the user asks",
        "to create or write a file there, DO IT — never reply that you cannot create",
        "files. Writing over an existing file may ask for confirmation first.",
    ]
    if fully_blocked:
        lines.append("Fully disabled tools:")
        lines.extend(fully_blocked)
    if restricted:
        lines.append("Tools with restricted actions:")
        lines.extend(restricted)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Debug / Inspection
# ─────────────────────────────────────────────────────────────────────────────

def print_security_summary():
    """Print a summary of the security policy for debugging."""
    print("\n" + "=" * 60)
    print("MARK XXXV — SECURITY POLICY SUMMARY")
    print("=" * 60)
    
    blocked_tools = []
    allowed_tools = []
    
    for tool_name, policy in sorted(SECURITY_POLICY.items()):
        if policy.status == "blocked":
            blocked_tools.append(f"  {tool_name}: {policy.reason}")
        else:
            if policy.actions:
                blocked_actions = [n for n, ap in policy.actions.items() if ap.status == "blocked"]
                allowed_actions = [n for n, ap in policy.actions.items() if ap.status == "allowed"]
                allowed_tools.append(
                    f"  {tool_name}: allowed=[{', '.join(allowed_actions)}] "
                    f"blocked=[{', '.join(blocked_actions)}]"
                )
            else:
                allowed_tools.append(f"  {tool_name}: fully allowed")
    
    print("\nBLOCKED TOOLS:")
    print("\n".join(blocked_tools) if blocked_tools else "  (none)")
    
    print("\nALLOWED TOOLS:")
    print("\n".join(allowed_tools))
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    print_security_summary()
