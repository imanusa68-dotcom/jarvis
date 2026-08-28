# core/gate.py
# ════════════════════════════════════════════════════════════════════════════════════════
# MARK XXXVI — Stage 1: the single execution gate (dispatch)
# ════════════════════════════════════════════════════════════════════════════════════════
#
# EVERY execution path — interactive (main._execute_tool) and autonomous
# (agent.executor._call_tool) — MUST pass through dispatch() before a tool runs.
# This is the ONE place that turns a core/security.py policy decision into an
# actionable verdict AND records it. No tool call may reach an action module
# without a logged verdict.
#
# Division of responsibility:
#   • core/security.py  — the policy BRAIN (allow/block, risk, PolicyMode).
#   • core/gate.py      — the single FUNNEL that enforces the brain uniformly,
#                          adapts it to the caller's mode, and audits 100% of
#                          calls.
#
# Modes:
#   • "interactive" (main, live voice / chat): a human is present. confirm-policy
#     actions return verdict="confirm" so the model can ask and re-call with
#     confirmed=true. The live SCREEN toggle is honoured.
#   • "autonomous" (agent executor, unattended multi-step plan): NO human is in
#     the loop, so the gate runs FAIL-CLOSED — confirm-policy and screen-gated
#     actions are DENIED (there is no way to ask or to flip SCREEN), and a
#     hallucinated confirmed=true can never self-approve (it is stripped).
#
# Fail-closed everywhere: if the gate itself errors, the caller must NOT run
# the tool (see main/executor call sites). Local guards inside action modules
# remain as defense-in-depth, but they are no longer the primary line.
# ════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# NOTE: reference core.security via the MODULE (not `from ... import name`) so
# tests that monkeypatch core.security.check_tool_call are honoured here, and so
# the gate always sees the live policy functions.
import core.security as _sec

Verdict = Literal["run", "blocked", "screen_off", "confirm"]
Mode = Literal["interactive", "autonomous"]

# Exact text the interactive UI expects when the SCREEN toggle is OFF. Kept
# byte-identical to the pre-Stage-1 main._execute_tool message so behaviour of
# the interactive path is preserved (golden-dispatch is the merge gate).
SCREEN_OFF_MSG = (
    # The first sentence is load-bearing: golden dispatch asserts it verbatim.
    "Screen control is currently disabled. "
    "Please press the SCREEN button in the interface, then repeat. "
    # Everything below exists because the model used to treat the sentence above
    # as a permanent truth and kept refusing after the user had switched the
    # toggle ON. The system prompt is built once per session, so a mid-session
    # toggle never reaches the model on its own -- it must ask.
    "IMPORTANT: this describes the toggle at THIS MOMENT only -- it is not a "
    "permanent fact, and the user may have switched it on since. If the user "
    "says they enabled it, or you are not sure, call computer_control with "
    "action='screen_status' to read the live button, then retry the action. "
    "Never refuse from memory. Do NOT call screen_share_control for this -- "
    "that is Screen View (vision streaming), a different system."
)


@dataclass
class GateResult:
    """Outcome of one gate decision. The caller runs the tool only when allowed."""
    verdict: Verdict
    tool: str
    action: Optional[str]
    risk: str
    policy: str
    mode: Mode
    # Tool-result string to return to the model when the verdict is not "run".
    # Empty for "run".
    message: str = ""
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "run"


# ── Audit log (100% of decisions, values never logged — only param KEYS) ─────

# Step 32 (phase 0): the journal itself lives in ~/.jarvis/logs, NOT in the
# build folder, and the writing is owned by core/audit_log.py (path resolved at
# call time, size rotation, never raises). The gate keeps exactly one job here:
# decide WHAT goes into the line. Do NOT reintroduce a module-level path
# constant pointing inside the project: the build folder is replaced on every
# unzip, so a journal living there starts empty every evening and the only
# record of what Jarvis did stays behind in Downloads.

_VERDICT_ICON = {"run": "✅", "blocked": "🛡", "screen_off": "🛡", "confirm": "⚠️"}


def _origin(ctx=None) -> dict:
    """Четыре поля «кто просил» для строки журнала (фаза 1б, I11).

    ПОЧЕМУ ОТКАТ НА КОНТЕКСТ ОБЯЗАТЕЛЕН, А НЕ «ПРИЯТНОЕ ДОПОЛНЕНИЕ»
    Живых вызовов двери четыре (main.py, agent/executor.py,
    core/offline_core.py, check_lang.py) и НИ ОДИН из них пропуск не
    передаёт. Если полагаться только на параметр, поля были бы пусты
    ВСЕГДА, а тест «передал ctx — цепочка на месте» при этом зелёный.

    Это не гипотеза, а грабли №4 проекта, уже случившиеся в учёте
    (core/metering.py:199): «проверял пропуск в КОНТЕКСТЕ, а не то, что
    доехало до базы. Проверять надо результат, а не полпути.» Здесь та же
    мина, и защита от неё — сторож, читающий СТРОКУ ЖУРНАЛА после вызова
    без явного ctx.

    Замер 28.08.2026, чем этот откат живёт:
        main.py       — дверь на строке 1334, run_in_executor на 1364,
                        то есть дверь ДО прыжка в поток: контекст виден;
        _call_tool    — вложенный вызов внутри `with bind(ctx)` из
                        agent/task_queue.py:325, тот же поток: виден.
    Поэтому правок в замороженном main.py фаза не требует вовсе.

    Порядок старшинства взят из шапки core/task_context дословно: явный
    пропуск сильнее контекста. Два источника правды разошлись бы молча.

    Цена: ПЕРЕМЕРЕНА 28.08.2026 на живой двери, и первая цифра здесь была
    неверной. Сначала стояло «1,3 мкс» — это замер отдельно взятых
    `current()` + четырёх полей, в вакууме. На настоящей двери он не
    воспроизводится:
        1500 решений x 5 повторов, порядок чередуется, медиана
        с цепочкой : 89,1 мкс  (разброс 84,2..106,3)
        без неё    : 94,1 мкс  (разброс 85,3..129,9)
    Разбросы ПЕРЕСЕКАЮТСЯ, а медиана «с цепочкой» вышла даже меньше —
    значит подписи в этом шуме не видно вовсе. Честный вывод не «подпись
    стоит 1,3 мкс», а «на фоне самой двери (~90 мкс на решение, из них
    основное — запись строки на диск) цену подписи измерить нельзя».
    Оставлять красивую точную цифру, которая не проверяется, — это ровно
    тот сорт лжи, который потом принимают за замер.
    """
    try:
        from core import task_context
        got = task_context.current(ctx)
        return {
            "task_id": got.task_id,
            "agent_role": got.agent_role,
            # Список, а не кортеж: JSON кортежей не знает, а поле обязано
            # читаться теми же глазами, что писали.
            "origin_chain": list(got.origin_chain),
            "depth": got.depth,
        }
    except Exception:
        # Правило кассы сильнее полноты записи: решение двери важнее подписи
        # под ним. None здесь — честное «не знаем», и оно отличимо от
        # отсутствия поля.
        return {"task_id": None, "agent_role": None,
                "origin_chain": None, "depth": None}


def _audit(result: GateResult, param_keys, ctx=None) -> None:
    """Append exactly one JSON line per decision. MUST never raise."""
    try:
        from core import audit_log  # lazy import: startup stays thin
        line = {
            "tool": result.tool,
            "action": result.action,
            "mode": result.mode,
            "verdict": result.verdict,
            "risk": result.risk,
            "policy": result.policy,
            "reason": (result.reason or "")[:200],
            # KEYS only — never values (avoid logging file contents / PII).
            "param_keys": sorted(str(k) for k in (param_keys or [])),
        }
        # Формат ТОЛЬКО дополняется (audit_log.py, правило 5): старые поля
        # остались на местах, SCHEMA_VER не двигается. Поднять версию было бы
        # заманчиво «на всякий случай», но это сломало бы читателей, которые
        # сверяют её на равенство (core/state_version.py:210), — а ломать
        # нечего: добавление поля никого из них не задевает.
        line.update(_origin(ctx))
        audit_log.append(line)
    except Exception:
        pass  # auditing must never break execution
    # Small console breadcrumb (golden tests do not assert on stdout).
    icon = _VERDICT_ICON.get(result.verdict, "?")
    tail = f"/{result.action}" if result.action else ""
    why = f" — {result.reason}" if result.reason else ""
    try:
        print(f"[GATE] {icon} {result.mode}/{result.verdict} {result.tool}{tail}{why}")
    except Exception:
        pass


# ── Stage 3A: consent tickets ────────────────────────────────────────────────

def _consent_enabled() -> bool:
    try:
        from core.feature_flags import durable_consent_enabled
        return durable_consent_enabled()
    except Exception:
        return False  # unreadable settings must not silently disarm the gate


def _consent_verdict(tool, action, params, decision, mode):
    """Decide a confirm-policy call using consent tickets.

    Returns a GateResult, or None to fall through to the legacy path (which is
    what happens when this call does not need confirming at all, or when the
    consent store is unavailable).

    The shape of the decision is deliberately boring:
        no ticket   -> ask, and mint the ticket that the answer will be bound to
        ticket ok   -> run, exactly once
        anything else -> ask again (never run)

    There is no branch that executes on a doubt. A wrong "ask again" costs the
    user a sentence; a wrong "run" costs them a folder.
    """
    from core import consent as _c
    from core import consent_runtime as _rt
    from core import consent_store as _cs

    if _sec.get_policy(tool, params) != "confirm":
        return None

    conn = _rt.get_conn()
    if conn is None:
        return None  # no store -> legacy path still guards the call

    session_id = _rt.get_session_id()
    # `confirmed` is the OLD mechanism. With tickets on it carries no authority
    # whatsoever, and it must not reach the fingerprint either.
    clean = {k: v for k, v in params.items()
             if k not in ("confirmed", "consent_id")}
    ticket_id = params.get("consent_id")
    reversible = _is_reversible(tool, action)

    if ticket_id:
        try:
            _cs.consume(
                conn, ticket=str(ticket_id), tool=tool, action=action,
                parameters=clean, session_id=session_id,
            )
            return GateResult(
                "run", tool, action, decision.risk, decision.policy, mode,
                reason=f"consent {ticket_id} spent",
            )
        except Exception as e:
            # Includes: unknown id, replay, expiry, wrong session, and the one
            # that matters most - the payload no longer matching what was
            # described out loud.
            fresh = _mint_quietly(_cs, conn, tool, action, clean, session_id,
                                  decision, reversible)
            return GateResult(
                "confirm", tool, action, decision.risk, decision.policy, mode,
                message=_format_consent_request(fresh, reason=str(e)),
                reason=f"consent refused: {e}",
            )

    fresh = _mint_quietly(_cs, conn, tool, action, clean, session_id,
                          decision, reversible)
    if fresh is None:
        return None
    return GateResult(
        "confirm", tool, action, decision.risk, decision.policy, mode,
        message=_format_consent_request(fresh),
        reason="consent required",
    )


def _is_reversible(tool, action) -> bool:
    """Deletes go to the Recycle Bin and can be undone; that earns a longer
    deadline, not a skipped question."""
    return (tool or "").lower().strip() == "file_controller" and \
        (action or "") in ("delete", "move", "rename", "overwrite", "write", "append")


def _mint_quietly(_cs, conn, tool, action, clean, session_id, decision, reversible):
    from core import consent as _c
    try:
        return _cs.mint(
            conn, tool=tool, action=action, parameters=clean,
            preview=_c.describe(tool, action, clean),
            risk=decision.risk, reversible=reversible, session_id=session_id,
        )
    except Exception as e:
        try:
            print(f"[GATE] consent mint failed for {tool}: {e}")
        except Exception:
            pass
        return None


def _format_consent_request(ticket, reason: str = "") -> str:
    """What the model is told when a consent is required.

    The preview is quoted and the model is ordered to repeat it VERBATIM. It is
    not allowed to summarise, because a summary is where '340 files' becomes 'a
    few files' - and then the user is agreeing to a description that was never
    true.
    """
    if ticket is None:
        return (
            "CONFIRMATION_REQUIRED: this action needs confirmation and the "
            "consent store is unavailable. Do NOT proceed. Tell the user "
            "briefly that you cannot confirm safely right now."
        )
    head = f"REFUSED: {reason} " if reason else ""
    return (
        f"{head}CONFIRMATION_REQUIRED (consent_id={ticket['ticket']}): "
        f"Ask the user out loud, in their language, quoting this description "
        f"EXACTLY and without shortening it: \"{ticket['preview']}\" "
        "Do NOT proceed yet. ONLY if they clearly agree, call this same tool "
        f"again with the identical parameters plus consent_id=\"{ticket['ticket']}\". "
        "Never invent or reuse a consent_id. If they decline, do not call it "
        "and acknowledge briefly."
    )


def dispatch(
    tool: str,
    params: dict | None = None,
    *,
    mode: Mode = "interactive",
    screen_control: bool = False,
    ctx=None,
) -> GateResult:
    """
    The single security gate. Returns a GateResult; the caller executes the tool
    ONLY when result.allowed (verdict == "run"). Every call is audited.

    This function does not raise for policy reasons — it encodes them as verdicts.
    It may raise only on a genuine internal error, which the caller must treat as
    fail-closed (do not run the tool).

    Про `ctx` (фаза 1б, I11). Пропуск `TaskCtx` из core/task_context: кто
    просил действие, для какого дела и через какую цепочку роль. Значение по
    умолчанию `None` — ключевое решение фазы: ни один из 1790 существующих
    сторожей и ни один из четырёх живых вызовов не ломается, а откат фазы
    равен «перестать передавать параметр».

    На решение двери пропуск НЕ влияет и влиять не должен: он подписывает
    строку журнала, а не меняет вердикт. Как только он начнёт менять решение,
    у политики появится второй хозяин, и разобрать «почему отказал» станет
    негде. Решения принимает core/security.py, и только он.
    """
    params = dict(params or {})
    # Autonomous path can never self-approve a confirm-gated action.
    if mode == "autonomous":
        params.pop("confirmed", None)

    decision = _sec.check_tool_call(tool, params)
    action = decision.action

    # 0) Заборы (фаза 1б-2, I12 / Г-3). СТОИТ ПЕРВЫМ, и это важно: чего
    # под-агент не делает никогда, то незачем сверять с риском, экраном и
    # подтверждениями. Раньше проверки — меньше шансов, что новая ветка
    # решений однажды проскочит мимо забора.
    #
    # Сломавшийся забор — ОТКАЗ, а не «поехали дальше». Так требует правило
    # fail-closed этого файла: неизвестно, кто просит запись в память —
    # значит не пишем. Именно поэтому здесь нет `except: pass`, хотя в
    # `_audit` он есть: журнал не имеет права мешать делу, а забор — имеет,
    # он для этого и существует.
    try:
        from core import fences
        fence = fences.check(tool, params, ctx=ctx)
    except Exception as fence_err:
        r = GateResult(
            "blocked", tool, action, decision.risk, decision.policy, mode,
            message=("SECURITY: fence check failed, action not run "
                     f"({tool})."),
            reason=f"fence error (fail-closed): {fence_err}",
        )
        _audit(r, params.keys(), ctx)
        return r
    if fence.blocked:
        r = GateResult(
            "blocked", tool, action, decision.risk, decision.policy, mode,
            message=fence.message, reason=fence.reason,
        )
        _audit(r, params.keys(), ctx)
        return r

    # 1) Hard block (unknown tool, blocked tool, blocked action).
    if not decision.allowed:
        r = GateResult(
            "blocked", tool, action, decision.risk, decision.policy, mode,
            message=_sec.format_security_block(decision), reason=decision.reason,
        )
        _audit(r, params.keys(), ctx)
        return r

    # 2) Screen-gated interactive action (click / type / focus_window ...).
    if decision.requires_screen_control:
        if mode == "autonomous":
            r = GateResult(
                "screen_off", tool, action, decision.risk, decision.policy, mode,
                message=(
                    "SECURITY: This is an interactive screen action and cannot "
                    "run inside an autonomous task. Ask the user to do it "
                    "directly with SCREEN control ON."
                ),
                reason="screen action in autonomous task",
            )
            _audit(r, params.keys(), ctx)
            return r
        if not screen_control:
            r = GateResult(
                "screen_off", tool, action, decision.risk, decision.policy, mode,
                message=SCREEN_OFF_MSG, reason="SCREEN toggle OFF",
            )
            _audit(r, params.keys(), ctx)
            return r

    # 3a) Stage 3A: durable consent. When the flag is ON this REPLACES the
    # model-held `confirmed` boolean entirely - the authority is a row in
    # jarvis.db, not a field the model can set on its own call.
    if mode == "interactive" and _consent_enabled():
        cr = _consent_verdict(tool, action, params, decision, mode)
        if cr is not None:
            _audit(cr, params.keys(), ctx)
            return cr

    # 3) Confirm-policy (high/critical risk, not yet confirmed).
    must_confirm, reason = _sec.needs_confirmation(tool, params, mode=mode)
    if must_confirm:
        if mode == "autonomous":
            r = GateResult(
                "blocked", tool, action, decision.risk, decision.policy, mode,
                message=(
                    "SECURITY: This action needs explicit user confirmation and "
                    "cannot run inside an autonomous task. Ask the user to run it "
                    "directly."
                ),
                reason="confirm required (autonomous → deny)",
            )
            _audit(r, params.keys(), ctx)
            return r
        r = GateResult(
            "confirm", tool, action, decision.risk, decision.policy, mode,
            message=_sec.format_confirmation_request(tool, reason), reason=reason,
        )
        _audit(r, params.keys(), ctx)
        return r

    # 4) Cleared to run.
    r = GateResult("run", tool, action, decision.risk, decision.policy, mode)
    _audit(r, params.keys(), ctx)
    return r
