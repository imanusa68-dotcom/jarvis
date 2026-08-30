# core/screen_live_session.py
# MARK XXXV — Screen View Live Session
# ═══════════════════════════════════════════════════════════════════════════════
#
# Dedicated Screen View live subsystem.
# Completely separate from the main Jarvis voice session and aux_model path.
#
# Architecture (built exactly on ai_studio_code.py reference):
#
#   ScreenShareManager (frame buffer)
#     ├─ Entire Screen  (mss monitor capture)
#     └─ Specific Window (mss + win32gui region)
#        ↓ FrameBuffer.data (JPEG bytes)
#   analyze_screen_live()
#     └─ _live_analyze()
#          └─ client.aio.live.connect(SCREEN_VIEW_LIVE_MODEL)
#               ├─ session.send({"mime_type": ..., "data": b64})  ← frame (reference format)
#               ├─ session.send(question, end_of_turn=True)
#               └─ session.receive() → TEXT response
#
# Isolation guarantees:
#   - NEVER imports or references main.py LIVE_MODEL
#   - NEVER calls aux_model or model_guard
#   - No webcam — frames ALWAYS come from ScreenShareManager buffer
#   - Dedicated asyncio event loop per call (never conflicts with main loop)
#   - TEXT response only — no audio output
#
# On live path failure:
#   - Logs exact error with model name and error type
#   - Returns honest user-facing message
#   - Does NOT auto-fall to quota-burning REST path
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import asyncio
import base64

# ─────────────────────────────────────────────────────────────────────────────
# Model constants — Screen View subsystem ONLY
#
# Main Jarvis voice model (DO NOT TOUCH — lives in main.py):
#   (роль live_voice из config/registry.yaml)
#
# Screen View dedicated live model (this file only):
# ─────────────────────────────────────────────────────────────────────────────

from config.loader import get_model as _get_model
SCREEN_VIEW_LIVE_MODEL = _get_model("live_screen")

_SYSTEM_INSTRUCTION = (
    "You are JARVIS, analyzing a screenshot from the user's computer. "
    "Answer questions about what you see concisely, directly, and helpfully. "
    "If you see text, UI elements, or other context, describe what matters most. "
    "Give actionable guidance when asked. "
    "Respond in Russian by default unless the user's question is in English."
)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — called from main.py _analyze_screen_with_live()
# ─────────────────────────────────────────────────────────────────────────────

def analyze_screen_live(
    frame_data:  bytes,
    frame_mime:  str,
    source_desc: str,
    question:    str,
    frame_age:   float,
    api_key:     str,
) -> str:
    """
    Analyze a screen frame using the dedicated Screen View live model.

    Model:    роль live_screen из config/registry.yaml
    Protocol: Live API (session.send / session.receive), TEXT-only output.
    Source:   ScreenShareManager buffer — Entire Screen or Specific Window.
    No webcam, no REST fallback, no main model involvement.

    Args:
        frame_data:  JPEG bytes from ScreenShareManager
        frame_mime:  MIME type (typically "image/jpeg")
        source_desc: Human-readable label ("Entire Screen" / window title)
        question:    User's question about the screen
        frame_age:   Frame age in seconds
        api_key:     Gemini API key

    Returns:
        Plain-text answer for the main Jarvis session to speak aloud.
    """
    print(
        f"[ScreenLive] 🖥️  Screen View analysis request"
        f"\n             model  : {SCREEN_VIEW_LIVE_MODEL}"
        f"\n             source : {source_desc!r}"
        f"\n             age    : {frame_age:.1f}s"
        f"\n             frame  : {len(frame_data)} bytes"
    )

    # Dedicated event loop — never touches the main Jarvis asyncio loop
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            _live_analyze(frame_data, frame_mime, source_desc, question, frame_age, api_key)
        )
        print(f"[ScreenLive] ✅ Screen View live path succeeded ({SCREEN_VIEW_LIVE_MODEL})")
        return result

    except Exception as live_err:
        err_type = live_err.__class__.__name__
        err_msg  = str(live_err)[:300]
        print(
            f"[ScreenLive] ❌ Screen View live path FAILED"
            f"\n             model  : {SCREEN_VIEW_LIVE_MODEL}"
            f"\n             error  : {err_type}: {err_msg}"
        )
        # Honest user-facing message — does NOT fall through to REST quota path
        return (
            f"Анализ экрана через Screen View не удался. "
            f"Модель {SCREEN_VIEW_LIVE_MODEL} вернула ошибку: {err_type}. "
            f"Захват экрана продолжает работать, но модель не ответила. "
            f"Попробуйте задать вопрос ещё раз или перезапустите Screen View."
        )

    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# Core: Live session per reference ai_studio_code.py screen mode
# ─────────────────────────────────────────────────────────────────────────────

async def _live_analyze(
    frame_data:  bytes,
    frame_mime:  str,
    source_desc: str,
    question:    str,
    frame_age:   float,
    api_key:     str,
) -> str:
    """
    Short-lived Gemini Live session for Screen View analysis.

    Frame sending protocol follows ai_studio_code.py reference exactly:
      _get_screen() returns:
        {"mime_type": "image/jpeg", "data": base64_string}   ← FLAT DICT
      send_realtime() calls:
        await session.send(input=msg)                         ← flat dict, no "inline_data"

    The inline_data nested format is for the REST generate_content API and
    causes APIError 1011 when sent to a live session. This uses the correct
    flat RealtimeInput format.
    """
    from google import genai as _genai
    from google.genai import types as _gtypes

    # Dedicated Screen View client — v1beta API, same as reference
    client = _genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1beta"},
    )

    # TEXT-only output: no audio playback needed, just the analysis string
    config = _gtypes.LiveConnectConfig(
        response_modalities=["TEXT"],
        system_instruction=_SYSTEM_INSTRUCTION,
    )

    # Encode frame to base64 string — same as reference _get_screen() return value
    frame_b64 = base64.b64encode(frame_data).decode()

    # Frame packet in correct Live API format (flat dict, NOT inline_data)
    # Reference: return {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}
    frame_packet = {
        "mime_type": frame_mime,   # e.g. "image/jpeg"
        "data":      frame_b64,    # base64 string — same as reference
    }

    question_text = (
        f"Источник захвата: {source_desc} (кадр снят {frame_age:.0f}с назад)\n"
        f"Вопрос пользователя: {question}\n\n"
        "Проанализируй скриншот и ответь на вопрос прямо и кратко."
    )

    result_parts: list[str] = []

    print(f"[ScreenLive] 🔗 Opening live session: {SCREEN_VIEW_LIVE_MODEL}")

    async with client.aio.live.connect(model=SCREEN_VIEW_LIVE_MODEL, config=config) as session:
        # ── Step 1: send screen frame in correct Live API format ─────────────
        # Matches reference send_realtime(): await self.session.send(input=msg)
        # where msg = {"mime_type": ..., "data": b64_string}
        print(f"[ScreenLive] 📤 Sending screen frame ({source_desc!r}, {len(frame_data)} bytes)")
        await session.send(input=frame_packet)

        # ── Step 2: send user question — end_of_turn triggers model response ─
        # Matches reference send_text(): await self.session.send(input=text, end_of_turn=True)
        print(f"[ScreenLive] 📤 Sending question (end_of_turn=True): {question!r}")
        await session.send(input=question_text, end_of_turn=True)

        # ── Step 3: collect TEXT response ────────────────────────────────────
        # Matches reference receive_audio() pattern but for TEXT modality:
        # turn = self.session.receive()
        # async for response in turn: if text := response.text: ...
        print(f"[ScreenLive] 📥 Waiting for response from {SCREEN_VIEW_LIVE_MODEL}...")
        turn = session.receive()
        async for response in turn:
            if response.text:
                result_parts.append(response.text)

    answer = "".join(result_parts).strip()
    if answer:
        print(f"[ScreenLive] 📋 Response received ({len(answer)} chars)")
    else:
        print(f"[ScreenLive] ⚠️  Response was empty")

    return answer if answer else "Screen View: ответ модели пустой. Попробуйте задать вопрос снова."


# ─────────────────────────────────────────────────────────────────────────────
# REST diagnostic helper — NOT called automatically, explicit use only
# ─────────────────────────────────────────────────────────────────────────────

def analyze_screen_rest_diagnostic(
    frame_data:  bytes,
    frame_mime:  str,
    source_desc: str,
    question:    str,
    frame_age:   float,
    api_key:     str,
    model:       str | None = None,
) -> str:
    """
    One-shot REST generate_content for diagnostics / manual testing.

    NOT called by analyze_screen_live(). Explicit use only.
    Default: роль aux_light из registry.yaml (тот же REST-путь, что и анализ кадра).
    Does NOT affect main Jarvis runtime or Screen View live path.
    """
    if model is None:
        model = _get_model("aux_light")
    try:
        from google import genai as _genai
        from google.genai import types as _gtypes

        prompt = (
            f"You are JARVIS analyzing a screenshot from the user's computer.\n"
            f"Capture source: {source_desc} (frame captured {frame_age:.0f}s ago)\n\n"
            f"User question: {question}\n\n"
            "Analyze what is visible and answer directly. Respond in Russian by default."
        )

        print(f"[ScreenLive] 🔬 REST diagnostic — model: {model}, source: {source_desc!r}")
        client   = _genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                _gtypes.Part.from_bytes(data=frame_data, mime_type=frame_mime),
                _gtypes.Part.from_text(text=prompt),
            ],
        )
        result = (response.text or "").strip()
        print(f"[ScreenLive] ✅ REST diagnostic succeeded ({model})")
        return result if result else "Пустой ответ от диагностического REST-вызова."

    except Exception as e:
        print(f"[ScreenLive] ❌ REST diagnostic failed ({model}): {e}")
        return f"REST diagnostic unavailable ({model}): {e}"
