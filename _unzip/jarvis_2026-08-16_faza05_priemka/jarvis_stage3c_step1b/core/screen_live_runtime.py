# core/screen_live_runtime.py
# MARK XXXV — Screen View Persistent Live Runtime
# ═══════════════════════════════════════════════════════════════════════════════
#
# Persistent Screen View live subsystem, built on ai_studio_code.py reference.
#
# Architecture:
#
#   ScreenShareManager (frame buffer)
#     └─ Entire Screen | Specific Window
#          ↓ every FRAME_INTERVAL seconds
#   ScreenViewRuntime._frame_send_loop()
#          ↓ session.send_realtime_input(video=Blob(...))
#   Persistent Live Session (роль live_screen из config/registry.yaml)
#          ↑ session.send(input=question, end_of_turn=True)
#   ScreenViewRuntime._question_loop()
#          ↑ submit_question(q) called from executor thread
#   main.py _analyze_screen_with_live()
#
# Lifecycle:
#   start_runtime()  → background thread + asyncio loop + persistent session
#   stop_runtime()   → clean shutdown of session and thread
#   submit_question() → inject question into live session, wait for answer
#
# Key guarantees:
#   - NEVER opens a new session per question (one session, stays alive)
#   - NEVER touches main.py LIVE_MODEL (Jarvis voice session)
#   - No webcam — frames come from ScreenShareManager buffer only
#   - Isolates its own asyncio event loop from main Jarvis event loop
#   - Full screen / specific window preserved via ScreenShareManager source
#
# ── Latency fixes (2025) ─────────────────────────────────────────────────────
#
#   FIX 1: response_modalities=["TEXT"]  (было ["AUDIO"] + output_audio_transcription)
#          AUDIO: модель генерит аудио-токены, ждёт тишины, транскрибирует → +6–10 с.
#          TEXT: прямой ответ текстом, без промежуточного аудио-шага.
#
#   FIX 2: session.send(input=question, end_of_turn=True)
#          (было send_realtime_input(text=question))
#          send_realtime_input без end_of_turn — это стриминг, модель ждёт VAD
#          тишины (1–2 с) перед ответом. send() с end_of_turn=True даёт явный
#          сигнал конца хода и немедленно запускает генерацию.
#
#   FIX 3: asyncio.Event (_async_q_ready_event) + loop.call_soon_threadsafe()
#          (было asyncio.to_thread(self._q_ready.wait, 1.0))
#          Поллинг с 1-секундным таймаутом добавлял до 1 с задержки.
#          Теперь submit_question() сигналит asyncio.Event через call_soon_threadsafe —
#          question_loop реагирует мгновенно.
#
#   FIX 4: _is_answering флаг — пауза фреймов во время сбора ответа
#          send_realtime_input(video=...) каждые 2 с может прерывать генерацию
#          ответа (Live API: realtime input = interrupt). Фреймы останавливаются
#          на время ожидания turn_complete и возобновляются сразу после.
#
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import asyncio
import base64
import threading
from typing import Optional

from google import genai as _genai
from google.genai import types as _gtypes

# ─────────────────────────────────────────────────────────────────────────────
# Model config — Screen View subsystem ONLY
#
#   Main Jarvis voice model (DO NOT TOUCH, lives in main.py):
#     (роль live_voice из config/registry.yaml)
#
#   Screen View dedicated live model:
# ─────────────────────────────────────────────────────────────────────────────

from config.loader import get_model as _get_model
SCREEN_VIEW_LIVE_MODEL = _get_model("live_screen")

# Send one frame every N seconds — practical rate, not spammy
FRAME_INTERVAL: float = 2.0

_SYSTEM_INSTRUCTION = (
    "You are JARVIS, analyzing the user's screen in real time. "
    "Screen frames arrive continuously as context. "
    "Answer questions about what you see concisely, directly, and helpfully. "
    "Describe relevant UI elements, text, and content visible on screen. "
    "Give actionable guidance when asked. "
    "Respond in Russian by default unless the user's question is in English."
)


# ─────────────────────────────────────────────────────────────────────────────
# ScreenViewRuntime — the persistent subsystem
# ─────────────────────────────────────────────────────────────────────────────

class ScreenViewRuntime:
    """
    Persistent Screen View live subsystem.

    One live session stays open as long as Screen View is ON.
    Screen frames are streamed continuously into that session.
    User questions are injected into the already-open session and answered.

    Thread safety:
      - start() / stop() may be called from any thread.
      - submit_question() is designed to be called from a ThreadPoolExecutor.
      - The asyncio event loop runs in its own dedicated daemon thread.
    """

    def __init__(self, api_key: str, ssm) -> None:
        self._api_key = api_key
        self._ssm     = ssm          # ScreenShareManager instance

        # Background thread / loop
        self._thread:          Optional[threading.Thread]               = None
        self._loop:            Optional[asyncio.AbstractEventLoop]      = None
        self._async_stop_evt:  Optional[asyncio.Event]                  = None  # set from any thread

        # Startup synchronisation
        self._started_evt:    threading.Event = threading.Event()
        self._startup_error:  Optional[str]   = None

        # Question channel (thread-safe)
        self._q_lock:         threading.Lock  = threading.Lock()
        self._pending_q:      Optional[str]   = None
        self._answer_ready:   threading.Event = threading.Event()
        self._last_answer:    str             = ""

        # FIX 3: asyncio.Event для мгновенной сигнализации вопроса из потока
        # Создаётся в _session_main() (в правильном event loop).
        # submit_question() сигналит через loop.call_soon_threadsafe().
        self._async_q_ready_event: Optional[asyncio.Event] = None

        # FIX 4: флаг «сейчас собираем ответ» — frame_send_loop проверяет его
        # перед каждой отправкой. Оба цикла работают в одном event loop — безопасно.
        self._is_answering: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> str:
        """
        Launch the persistent Screen View live runtime.
        Blocks up to 20s waiting for the live session to open.
        Returns a status string.
        """
        if self._thread and self._thread.is_alive():
            return "Screen View runtime already running."

        self._started_evt.clear()
        self._startup_error = None

        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="ScreenViewRuntime",
            daemon=True,
        )
        self._thread.start()

        # Wait for live session to open (or fail)
        if not self._started_evt.wait(timeout=20.0):
            return f"Screen View runtime startup timed out (model: {SCREEN_VIEW_LIVE_MODEL})."

        if self._startup_error:
            return f"Screen View runtime failed to start: {self._startup_error}"

        return f"Screen View runtime started ({SCREEN_VIEW_LIVE_MODEL})."

    def stop(self) -> None:
        """Clean shutdown of the persistent live session and background thread."""
        # Signal the asyncio stop event (safe cross-thread)
        if self._loop and not self._loop.is_closed() and self._async_stop_evt is not None:
            self._loop.call_soon_threadsafe(self._async_stop_evt.set)

        # Unblock any waiting submit_question
        self._last_answer = "Screen View остановлен."
        self._answer_ready.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=6.0)

        print("[ScreenViewRuntime] 🛑 Stopped.")

    def is_running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._startup_error is None
            and self._started_evt.is_set()
        )

    def submit_question(self, question: str, timeout: float = 25.0) -> str:
        """
        Inject a question into the running Screen View live session.
        Blocks until an answer is received or timeout expires.

        Thread-safe — designed to be called from a ThreadPoolExecutor thread.
        Returns plain-text answer for Jarvis to speak aloud.
        """
        if not self.is_running():
            return "Screen View runtime не активен. Переключите Screen View выкл/вкл."

        with self._q_lock:
            self._pending_q   = question
            self._answer_ready.clear()

        # FIX 3: мгновенно будим asyncio event loop — никакого 1-секундного поллинга
        if self._loop and not self._loop.is_closed() and self._async_q_ready_event is not None:
            self._loop.call_soon_threadsafe(self._async_q_ready_event.set)

        answered = self._answer_ready.wait(timeout=timeout)
        if not answered:
            return (
                f"Screen View анализ не ответил за {timeout:.0f}с. "
                "Попробуйте ещё раз."
            )

        return self._last_answer

    # ── Background thread entry point ─────────────────────────────────────────

    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        # Тот же глушитель, что и в главном цикле. Циклов событий в проекте
        # два, и обработчик у каждого свой: без этой строки та же трассировка
        # вернётся через экранную сессию, и искать её будем заново.
        try:
            from core.quiet_loop import install as _install_quiet
            _install_quiet(loop)
        except Exception as _qe:      # noqa: BLE001
            print(f"[ScreenViewRuntime] quiet loop handler skipped: {_qe}")

        try:
            loop.run_until_complete(self._session_main())
        except Exception as e:
            print(f"[ScreenViewRuntime] ❌ Event loop crashed: {e}")
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()
                self._loop = None
                print("[ScreenViewRuntime] Event loop closed.")

    # ── Persistent session main ───────────────────────────────────────────────

    async def _session_main(self) -> None:
        """
        Opens the persistent Gemini Live session and runs:
          - _frame_send_loop  (streams screen frames)
          - _question_loop    (injects user questions, collects answers)
        until stop() is called.
        """
        # Dedicated client for Screen View — never shared with main Jarvis
        client = _genai.Client(
            api_key=self._api_key,
            http_options={"api_version": "v1beta"},
        )

        # FIX 1: TEXT модальность — прямой ответ без промежуточного аудио-шага.
        # Было: response_modalities=["AUDIO"], output_audio_transcription={}
        # Аудио-путь: генерация аудио-токенов → VAD тишина → транскрипция → текст (+6–10 с).
        # Текстовый путь: прямой ответ → текст (как в AI Studio при text-chat).
        config = _gtypes.LiveConnectConfig(
            response_modalities=["TEXT"],
            system_instruction=_SYSTEM_INSTRUCTION,
        )

        # FIX 3: создаём asyncio.Event в контексте правильного event loop
        self._async_q_ready_event = asyncio.Event()

        # Asyncio stop event — set via loop.call_soon_threadsafe from stop()
        self._async_stop_evt = asyncio.Event()

        print(
            f"[ScreenViewRuntime] 🔗 Opening persistent live session"
            f"\n                    model  : {SCREEN_VIEW_LIVE_MODEL}"
            f"\n                    source : {self._ssm.get_source()}"
        )

        try:
            async with client.aio.live.connect(
                model=SCREEN_VIEW_LIVE_MODEL, config=config
            ) as session:

                print("[ScreenViewRuntime] ✅ Live session open — streaming begins")
                self._started_evt.set()    # unblock start()

                # Create concurrent tasks
                frame_task    = asyncio.create_task(
                    self._frame_send_loop(session), name="sv-frame-loop"
                )
                question_task = asyncio.create_task(
                    self._question_loop(session), name="sv-question-loop"
                )
                stop_task     = asyncio.create_task(
                    self._async_stop_evt.wait(), name="sv-stop-wait"
                )

                # Run until stop is requested or a task crashes
                done, pending = await asyncio.wait(
                    {frame_task, question_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                print(f"[ScreenViewRuntime] Shutting down — cancelling tasks...")
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
            print(f"[ScreenViewRuntime] ❌ Session failed — {err}")
            self._startup_error = err
            self._started_evt.set()   # unblock start() with error
            # Unblock any waiting question
            self._last_answer = f"Screen View live session упала: {err}"
            self._answer_ready.set()
        finally:
            try:
                await client.aio.aclose()
            except Exception:
                pass

    # ── Frame streaming loop ──────────────────────────────────────────────────

    async def _frame_send_loop(self, session) -> None:
        """
        Continuously send screen frames to the live session.

        FIX 4: пропускаем отправку фрейма пока _is_answering=True.
        send_realtime_input(video=...) — это realtime input, который может
        прервать текущую генерацию ответа (interrupt semantics Live API).
        Пауза во время сбора ответа устраняет это сбрасывание.
        """
        print(
            f"[ScreenViewRuntime] 🎬 Frame streaming started"
            f" (interval={FRAME_INTERVAL}s, source={self._ssm.get_source()})"
        )
        frames_sent = 0

        while True:
            try:
                # FIX 4: не шлём фрейм пока идёт сбор ответа
                if not self._is_answering:
                    frame = self._ssm.get_latest_frame()
                    if frame and frame.is_fresh(max_age=10.0):
                        await session.send_realtime_input(
                            video=_gtypes.Blob(data=frame.data, mime_type=frame.mime_type)
                        )
                        frames_sent += 1
                        if frames_sent % 10 == 0:
                            src = frame.source_desc or str(self._ssm.get_source())
                            print(
                                f"[ScreenViewRuntime] 🖥️  Frame #{frames_sent}"
                                f" sent ({src}, {len(frame.data)} bytes)"
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[ScreenViewRuntime] ⚠️  Frame send error: {e.__class__.__name__}: {e}")

            await asyncio.sleep(FRAME_INTERVAL)

        print(f"[ScreenViewRuntime] 🎬 Frame streaming stopped ({frames_sent} frames sent)")

    # ── Question injection loop ───────────────────────────────────────────────

    async def _question_loop(self, session) -> None:
        """
        Wait for submitted questions, send them into the live session,
        collect the text response, and deliver it back to submit_question().

        FIX 2: session.send(input=question, end_of_turn=True)
          Было: send_realtime_input(text=question) — стриминг без end_of_turn,
          модель ждала VAD тишины перед ответом (+1–3 с).
          Теперь: явный конец хода — генерация стартует немедленно.

        FIX 3: await self._async_q_ready_event.wait()
          Было: await asyncio.to_thread(self._q_ready.wait, 1.0) — поллинг 1 с.
          Теперь: asyncio.Event.wait() — реагируем мгновенно через call_soon_threadsafe.

        FIX 1: только response.text — никакой output_transcription (не нужна с TEXT).
        """
        print("[ScreenViewRuntime] ❓ Question loop started")

        while True:
            try:
                # FIX 3: мгновенное ожидание через asyncio.Event (0 мс задержки)
                await self._async_q_ready_event.wait()
                self._async_q_ready_event.clear()

                # Забираем вопрос
                with self._q_lock:
                    question      = self._pending_q
                    self._pending_q = None

                if not question:
                    continue

                # FIX 4: поднимаем флаг — frame_send_loop делает паузу
                self._is_answering = True

                try:
                    # FIX 2: явный end_of_turn — модель отвечает сразу,
                    # без ожидания VAD silence detection
                    print(f"[ScreenViewRuntime] 📤 Sending question (end_of_turn): {question!r}")
                    await session.send(input=question, end_of_turn=True)

                    # FIX 1: собираем только response.text (TEXT модальность)
                    # Никакой output_transcription — она нужна только при AUDIO.
                    print("[ScreenViewRuntime] 📥 Waiting for response...")
                    parts: list[str] = []
                    async for response in session.receive():
                        # Прямой текстовый ответ (TEXT modality)
                        if getattr(response, "text", None):
                            parts.append(response.text)

                        # Проверяем turn_complete для выхода из цикла
                        content = getattr(response, "server_content", None)
                        if content and getattr(content, "turn_complete", False):
                            break

                    answer = "".join(p.strip() for p in parts if p and p.strip()).strip()
                    if answer:
                        print(f"[ScreenViewRuntime] 📋 Response: {len(answer)} chars")
                    else:
                        print("[ScreenViewRuntime] ⚠️  Response was empty")

                    self._last_answer = (
                        answer or "Screen View: ответ модели пустой. Попробуйте ещё раз."
                    )

                finally:
                    # FIX 4: снимаем флаг — frame_send_loop возобновляется
                    self._is_answering = False
                    self._answer_ready.set()

            except asyncio.CancelledError:
                break
            except Exception as e:
                err_msg = f"{e.__class__.__name__}: {e}"
                print(f"[ScreenViewRuntime] ❌ Question loop error: {err_msg}")
                # Deliver error as answer so submit_question() doesn't hang
                self._is_answering = False
                self._last_answer = f"Screen View ошибка анализа: {err_msg}"
                self._answer_ready.set()
                # For session-level errors, stop the loop — session is dead
                break

        print("[ScreenViewRuntime] ❓ Question loop stopped")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + convenience functions
# ─────────────────────────────────────────────────────────────────────────────

_runtime:      Optional[ScreenViewRuntime] = None
_runtime_lock: threading.Lock              = threading.Lock()


def get_runtime() -> Optional[ScreenViewRuntime]:
    """Return the current ScreenViewRuntime instance, or None."""
    return _runtime


def is_runtime_active() -> bool:
    """True if the persistent Screen View session is up and ready."""
    rt = _runtime
    return rt is not None and rt.is_running()


def start_runtime(api_key: str, ssm) -> str:
    """
    Create and start the persistent Screen View live runtime.
    Idempotent — returns immediately if already running.
    """
    global _runtime
    with _runtime_lock:
        if _runtime and _runtime.is_running():
            return "Screen View runtime already running."
        _runtime = ScreenViewRuntime(api_key=api_key, ssm=ssm)

    return _runtime.start()


def stop_runtime() -> None:
    """Stop and discard the persistent Screen View live runtime."""
    global _runtime
    with _runtime_lock:
        rt      = _runtime
        _runtime = None

    if rt:
        rt.stop()


def submit_question(question: str, timeout: float = 25.0) -> str:
    """
    Module-level convenience: submit a question to the running runtime.
    Returns answer string or error message.
    """
    rt = get_runtime()
    if rt is None or not rt.is_running():
        return (
            "Screen View runtime не активен. "
            "Убедитесь, что Screen View включён и попробуйте снова."
        )
    return rt.submit_question(question, timeout=timeout)
