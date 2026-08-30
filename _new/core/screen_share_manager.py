# core/screen_share_manager.py
# MARK XXXV — Screen Vision / Screen Share Manager
# ═══════════════════════════════════════════════════════════════════════════════
#
# Single owner of all screen-capture-for-vision state.
#
# Design rules:
#   1. UI calls request_start() / request_stop() / set_source() — never touches
#      internal state directly.
#   2. Only _set_state_locked() mutates self._state, and it MUST be called
#      under self._lock.
#   3. The capture loop is the only writer of self._frame.
#   4. All public read methods are thread-safe.
#
# State machine:
#   OFF ──► STARTING ──► ON ──► STOPPING ──► OFF
#             │                    │
#             └──► ERROR ◄─────────┘
#   ERROR ──► OFF  (via request_stop)
#
# Sources:
#   ScreenShareSource(type="full", ...)   — entire monitor
#   ScreenShareSource(type="window", ...) — specific app window (win32gui)
#
# Analysis:
#   analyze_current_view(question, api_key) — uses Gemini text API (NOT Live API)
#   so it returns a text string, not an audio response.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

try:
    import mss
    import mss.tools
    _MSS_OK = True
except ImportError:
    _MSS_OK = False

try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False


# ─────────────────────────────────────────────────────────────────────────────
# State machine
# ─────────────────────────────────────────────────────────────────────────────

class ScreenShareState(Enum):
    OFF      = auto()   # not running
    STARTING = auto()   # request_start() called, thread spinning up
    ON       = auto()   # capture loop active, frames flowing
    STOPPING = auto()   # request_stop() called, thread draining
    ERROR    = auto()   # capture loop died unexpectedly


# ─────────────────────────────────────────────────────────────────────────────
# Source model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScreenShareSource:
    """
    Describes the capture target.
    type:          "full" — entire monitor; "window" — specific app window
    monitor_index: 1-based mss monitor index (full screen only)
    hwnd:          Windows HWND (window source only; 0 = not set)
    title:         Human-readable label shown in UI
    """
    type:          str = "full"
    monitor_index: int = 1
    hwnd:          int = 0
    title:         str = "Entire Screen"

    def display_name(self) -> str:
        if self.type == "full":
            if self.monitor_index == 1:
                return "Entire Screen"
            return f"Monitor {self.monitor_index}"
        return self.title[:60] if self.title else f"Window HWND {self.hwnd}"

    def __str__(self) -> str:
        return self.display_name()


# ─────────────────────────────────────────────────────────────────────────────
# Frame buffer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FrameBuffer:
    data:        bytes = field(default_factory=bytes)
    mime_type:   str   = "image/jpeg"
    captured_at: float = 0.0      # time.monotonic()
    source_desc: str   = ""

    def age_seconds(self) -> float:
        if self.captured_at == 0.0:
            return float("inf")
        return time.monotonic() - self.captured_at

    def is_fresh(self, max_age: float = 30.0) -> bool:
        return self.age_seconds() <= max_age

    def is_empty(self) -> bool:
        return self.captured_at == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Manager
# ─────────────────────────────────────────────────────────────────────────────

class ScreenShareManager:
    """
    Single owner of screen-vision state.

    Thread-safe. UI calls request_start() / request_stop() / set_source().
    UI reads get_state() / get_status() to display current reality.
    """

    CAPTURE_INTERVAL: float = 1.0    # seconds between frame captures
    JPEG_QUALITY:     int   = 65
    MAX_SIZE:         tuple = (1280, 720)

    def __init__(self) -> None:
        self._lock       = threading.Lock()
        self._state      = ScreenShareState.OFF
        self._source     = ScreenShareSource()
        self._frame      = FrameBuffer()
        self._error_msg  = ""

        self._stop_evt   = threading.Event()
        self._thread:    Optional[threading.Thread] = None

        # Optional callback — invoked (from bg thread) on every state change.
        self._state_cb:  Optional[Callable[[ScreenShareState], None]] = None

    # ── State observation ──────────────────────────────────────────────────────

    def get_state(self) -> ScreenShareState:
        with self._lock:
            return self._state

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state":      self._state.name,
                "source":     str(self._source),
                "error":      self._error_msg,
                "frame_age":  round(self._frame.age_seconds(), 1),
                "has_frame":  not self._frame.is_empty(),
            }

    def is_running(self) -> bool:
        return self.get_state() == ScreenShareState.ON

    def set_state_callback(self, cb: Callable[[ScreenShareState], None]) -> None:
        with self._lock:
            self._state_cb = cb

    # ── Control ───────────────────────────────────────────────────────────────

    def request_start(self) -> str:
        """
        Request capture start. Idempotent — ignored if already STARTING or ON.
        Returns a brief status string for logging / UI.
        """
        with self._lock:
            state = self._state
            if state in (ScreenShareState.STARTING, ScreenShareState.ON):
                return f"Already {state.name} — ignoring start."
            if state == ScreenShareState.STOPPING:
                return "Still stopping — wait before restarting."
            self._error_msg = ""
            self._set_state_locked(ScreenShareState.STARTING)

        self._launch_capture_thread()
        return "Screen View starting..."

    def request_stop(self) -> str:
        """
        Request capture stop. Idempotent — ignored if already OFF or STOPPING.
        Returns a brief status string.
        """
        with self._lock:
            state = self._state
            if state in (ScreenShareState.OFF, ScreenShareState.STOPPING):
                return f"Already {state.name} — ignoring stop."
            self._set_state_locked(ScreenShareState.STOPPING)

        self._stop_evt.set()
        return "Screen View stopping..."

    def set_source(self, source: ScreenShareSource) -> str:
        """
        Change capture source.
        If ON: the capture loop picks up the new source on its next iteration
               (no restart, no race — the loop reads self._source each cycle).
        If OFF/ERROR: stores it for next start.
        """
        with self._lock:
            self._source = source
            state = self._state

        if state == ScreenShareState.ON:
            return f"Source switched to: {source}"
        return f"Source set to: {source} (applies on next start)"

    def get_source(self) -> ScreenShareSource:
        with self._lock:
            return self._source

    # ── Frame access ──────────────────────────────────────────────────────────

    def get_latest_frame(self) -> Optional[FrameBuffer]:
        """Return a copy of the latest frame, or None if no frame captured yet."""
        with self._lock:
            if self._frame.is_empty():
                return None
            return FrameBuffer(
                data        = self._frame.data,
                mime_type   = self._frame.mime_type,
                captured_at = self._frame.captured_at,
                source_desc = self._frame.source_desc,
            )

    # ── Window listing ─────────────────────────────────────────────────────────

    @staticmethod
    def list_windows() -> list[ScreenShareSource]:
        """
        Return a list of capturable sources.
        Always starts with "Entire Screen".
        On Windows with pywin32: adds all visible titled windows.
        Multiple monitors: added if mss sees more than one.
        """
        results: list[ScreenShareSource] = [
            ScreenShareSource(type="full", monitor_index=1, title="Entire Screen")
        ]

        # Additional monitors
        try:
            if _MSS_OK:
                with mss.mss() as sct:
                    for i in range(2, len(sct.monitors)):
                        results.append(ScreenShareSource(
                            type="full", monitor_index=i, title=f"Monitor {i}"
                        ))
        except Exception:
            pass

        # Windows: enumerate visible titled windows
        try:
            import win32gui

            _SKIP_TITLES = {
                "", "Default IME", "MSCTFIME UI",
                "Program Manager", "Windows Input Experience",
            }

            def _enum_cb(hwnd: int, out: list) -> None:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd).strip()
                if not title or title in _SKIP_TITLES or len(title) < 2:
                    return
                out.append(ScreenShareSource(
                    type="window", hwnd=hwnd, title=title
                ))

            win_list: list[ScreenShareSource] = []
            win32gui.EnumWindows(_enum_cb, win_list)
            results.extend(win_list[:50])
        except ImportError:
            pass
        except Exception as e:
            print(f"[ScreenShare] Window enumeration error: {e}")

        return results

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Force-stop all resources. Call on application shutdown."""
        with self._lock:
            if self._state not in (ScreenShareState.OFF, ScreenShareState.STOPPING):
                self._set_state_locked(ScreenShareState.STOPPING)

        self._stop_evt.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4.0)

        with self._lock:
            self._set_state_locked(ScreenShareState.OFF)
            self._frame = FrameBuffer()

    # ── Internal state helpers ─────────────────────────────────────────────────

    def _set_state_locked(self, new: ScreenShareState) -> None:
        """MUST be called under self._lock. Fires optional callback off-lock."""
        old = self._state
        self._state = new
        if old != new:
            print(f"[ScreenShare] {old.name} → {new.name}")
            cb = self._state_cb

        if old != new and cb:
            threading.Thread(target=cb, args=(new,), daemon=True).start()

    # ── Thread lifecycle ───────────────────────────────────────────────────────

    def _launch_capture_thread(self) -> None:
        self._stop_evt.clear()
        t = threading.Thread(
            target=self._capture_loop,
            name="ScreenShareCaptureLoop",
            daemon=True,
        )
        self._thread = t
        t.start()

    def _capture_loop(self) -> None:
        """
        Background capture loop.
        STARTING → (first frame ok) → ON → (stop_evt) → STOPPING → OFF
        STARTING → (first frame fails) → ERROR
        ON       → (window disappeared) → ERROR
        """
        print("[ScreenShare] Capture loop started.")

        # ── Warm-up: verify source works ────────────────────────────────────
        try:
            frame = self._do_capture()
            with self._lock:
                self._frame = frame
                self._set_state_locked(ScreenShareState.ON)
            print("[ScreenShare] ✅ First frame OK — state ON")
        except Exception as e:
            msg = str(e)[:200]
            print(f"[ScreenShare] ❌ Startup capture failed: {msg}")
            with self._lock:
                self._error_msg = f"Capture failed: {msg}"
                self._set_state_locked(ScreenShareState.ERROR)
            return

        # ── Main loop ────────────────────────────────────────────────────────
        while not self._stop_evt.is_set():
            t0 = time.monotonic()

            try:
                frame = self._do_capture()
                with self._lock:
                    self._frame = frame
            except Exception as e:
                msg = str(e)[:200]
                print(f"[ScreenShare] ⚠️ Frame error: {msg}")

                # Check if window source disappeared
                with self._lock:
                    src = self._source

                if src.type == "window" and src.hwnd:
                    try:
                        import win32gui
                        if not win32gui.IsWindow(src.hwnd):
                            with self._lock:
                                self._error_msg = f"Window closed: {src.title}"
                                self._set_state_locked(ScreenShareState.ERROR)
                            return
                    except ImportError:
                        pass

                # Generic transient error — keep trying
                self._stop_evt.wait(timeout=1.5)
                continue

            elapsed = time.monotonic() - t0
            wait    = max(0.0, self.CAPTURE_INTERVAL - elapsed)
            self._stop_evt.wait(timeout=wait)

        # ── Clean stop ───────────────────────────────────────────────────────
        print("[ScreenShare] Capture loop exiting cleanly.")
        with self._lock:
            self._set_state_locked(ScreenShareState.OFF)
            self._frame = FrameBuffer()

    # ── Capture dispatch ──────────────────────────────────────────────────────

    def _do_capture(self) -> FrameBuffer:
        with self._lock:
            src = ScreenShareSource(
                type          = self._source.type,
                monitor_index = self._source.monitor_index,
                hwnd          = self._source.hwnd,
                title         = self._source.title,
            )

        if src.type == "window" and src.hwnd:
            return self._capture_window(src)
        return self._capture_monitor(src)

    def _capture_monitor(self, src: ScreenShareSource) -> FrameBuffer:
        if not _MSS_OK:
            raise RuntimeError("mss is not installed")

        with mss.mss() as sct:
            monitors = sct.monitors
            idx = src.monitor_index
            if idx < 1 or idx >= len(monitors):
                idx = 1
            mon  = monitors[idx]
            shot = sct.grab(mon)
            raw  = mss.tools.to_png(shot.rgb, shot.size)

        return FrameBuffer(
            data        = self._to_jpeg(raw),
            mime_type   = "image/jpeg",
            captured_at = time.monotonic(),
            source_desc = src.display_name(),
        )

    def _capture_window(self, src: ScreenShareSource) -> FrameBuffer:
        """Capture a specific application window region using win32gui + mss."""
        try:
            import win32gui
        except ImportError:
            # pywin32 not installed — fall back to full screen capture
            return self._capture_monitor(
                ScreenShareSource(type="full", monitor_index=1, title="Full Screen")
            )

        hwnd = src.hwnd
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(f"Window no longer exists: '{src.title}'")

        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        w = right - left
        h = bottom - top

        if w <= 0 or h <= 0:
            raise RuntimeError(
                f"Window '{src.title}' is minimized or has zero size"
            )

        if not _MSS_OK:
            raise RuntimeError("mss is not installed")

        with mss.mss() as sct:
            region = {"left": left, "top": top, "width": w, "height": h}
            shot   = sct.grab(region)
            raw    = mss.tools.to_png(shot.rgb, shot.size)

        return FrameBuffer(
            data        = self._to_jpeg(raw),
            mime_type   = "image/jpeg",
            captured_at = time.monotonic(),
            source_desc = src.display_name(),
        )

    def _to_jpeg(self, png_bytes: bytes) -> bytes:
        if not _PIL_OK:
            return png_bytes
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img.thumbnail(self.MAX_SIZE, Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.JPEG_QUALITY, optimize=False)
        return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_manager:      Optional[ScreenShareManager] = None
_manager_lock: threading.Lock               = threading.Lock()


def get_manager() -> ScreenShareManager:
    """Return the process-wide ScreenShareManager singleton."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ScreenShareManager()
        return _manager


# ─────────────────────────────────────────────────────────────────────────────
# Legacy one-shot analysis — FALLBACK ONLY
#
# The primary Screen View analysis path is now core.screen_live_session,
# which uses a dedicated Gemini Live session (TEXT-only, separate model).
# This function is kept as an emergency fallback and direct diagnostic tool.
# It is NO LONGER called from main.py's analyze_screen_view handler.
# ─────────────────────────────────────────────────────────────────────────────

def analyze_current_view(question: str, api_key: str) -> str:
    """
    [FALLBACK] Grab the latest buffered frame from ScreenShareManager and send
    it to the vision role through the ONE door core/aux_model.aux_call.

    This is the OLD one-shot path. The PRIMARY path is now:
      core.screen_live_session.analyze_screen_live()

    Kept for diagnostics and emergency fallback only.
    Respects the shared ModelQuotaGuard cooldown to prevent 429 storms.
    """
    mgr   = get_manager()
    state = mgr.get_state()

    if state != ScreenShareState.ON:
        state_map = {
            ScreenShareState.OFF:      "Screen View is OFF. Enable it first.",
            ScreenShareState.STARTING: "Screen View is starting up. Please wait.",
            ScreenShareState.STOPPING: "Screen View is stopping.",
            ScreenShareState.ERROR:    f"Screen View has an error: {mgr.get_status().get('error', 'unknown')}",
        }
        return state_map.get(state, f"Screen View not active (state: {state.name}).")

    frame = mgr.get_latest_frame()
    if frame is None:
        return "No frame available yet — please wait a moment and try again."

    if not frame.is_fresh(max_age=30.0):
        return (
            f"The last captured frame is {frame.age_seconds():.0f} seconds old. "
            "Screen capture may have stalled — try disabling and re-enabling Screen View."
        )

    # ── Одна дверь к модели: core/aux_model.aux_call ─────────────────
    # Здесь лежала КОПИЯ этой двери: свой клиент SDK, своя проверка
    # остывания, свой разбор 429 — шестьдесят строк, повторяющих то, что
    # общая дверь уже умеет, включая картинки и порядок «картинка → текст».
    #
    # Удалена в блоке 5, а не оставлена под флагом (правило 9): пока копия
    # жива, вызов через неё не попадает в учёт расхода, и инвариант I16 «ни
    # один вызов мимо метеринга» — ложь. Учитывать копию было бы дешевле,
    # чем удалить, и это ровно та экономия, из-за которой через месяц никто
    # не знает, какая из двух дверей работает.
    prompt = (
        f"You are analyzing a screen capture from the user's computer.\n"
        f"Capture source: {frame.source_desc}\n"
        f"Frame captured {round(frame.age_seconds(), 1)}s ago.\n\n"
        f"User question: {question}\n\n"
        "Analyze what is visible on the screen and answer the user's question "
        "directly, concisely, and helpfully. If you can see text, UI elements, "
        "or other context, describe what you see and give actionable guidance. "
        "Be concise and actionable."
    )
    from core.aux_model import aux_call
    ok, answer = aux_call(prompt, api_key,
                          image_parts=[(frame.data, frame.mime_type)],
                          caller="ScreenShare", role="vision")
    if ok:
        return answer
    # Отказ уже назван вслух внутри aux_call; здесь — чем ответим владельцу.
    if answer.startswith("[quota-cap"):
        return ("Screen View is capturing normally, but today's own limit for "
                "vision calls is used up. The screen is still being watched.")
    if answer.startswith(("[quota-cooldown", "[quota-429")):
        return ("Screen View is ON and capturing frames normally, but the "
                "vision model is cooling down after a rate limit. Your screen "
                "is still being captured — ask again shortly.")
    return f"Screen analysis failed: {answer}"
