# actions/computer_control.py
# MARK XXV — Computer Control
#
# Atomic computer control functions using PyAutoGUI + keyboard + clipboard.
# Used by the agent when no existing action file covers the task.
#
# Capabilities:
#   - Type text anywhere (active window, forms, fields)
#   - Mouse click, double-click, right-click, drag
#   - Keyboard shortcuts and key combinations
#   - Scroll (up/down/left/right)
#   - Window management (minimize, maximize, close, focus)
#   - Clipboard (copy, paste, get content)
#   - Screenshot + locate element on screen
#   - Wait / smart wait for element to appear
#   - Random data generation (name, email, username, password, phone, address)
#   - Hotkey sequences
#   - Find and click image/element on screen

import json
import sys
import time
import random
import string
import subprocess
import platform
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()


def _load_user_profile() -> dict:
    """Load user profile from long_term.json for form filling."""
    memory_path = BASE_DIR / "memory" / "long_term.json"
    try:
        if memory_path.exists():
            data = json.loads(memory_path.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {
                "name":  identity.get("name",  {}).get("value", ""),
                "age":   identity.get("age",   {}).get("value", ""),
                "city":  identity.get("city",  {}).get("value", ""),
                "email": identity.get("email", {}).get("value", ""),
            }
    except Exception:
        pass
    return {}


def _ensure_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError(
            "PyAutoGUI not installed. Run: pip install pyautogui"
        )


_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper"
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson"
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def generate_random_data(data_type: str) -> str:
    """
    Generates random realistic data for form filling.

    Types: name | first_name | last_name | email | username |
           password | phone | birthday | address | zip_code
    """
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    elif dt == "last_name":
        return random.choice(_LAST_NAMES)

    elif dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    elif dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    elif dt == "username":
        first = random.choice(_FIRST_NAMES).lower()
        num   = random.randint(100, 9999)
        return f"{first}{num}"

    elif dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        pwd   = (
            random.choice(string.ascii_uppercase) +
            random.choice(string.digits) +
            random.choice("!@#$%") +
            "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(pwd, len(pwd)))

    elif dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1000000,9999999)}"

    elif dt == "birthday":
        year  = random.randint(1980, 2000)
        month = random.randint(1, 12)
        day   = random.randint(1, 28)
        return f"{month:02d}/{day:02d}/{year}"

    elif dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    elif dt == "zip_code":
        return str(random.randint(10000, 99999))

    elif dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000,9999)}"


def _type_text(text: str, interval: float = 0.03) -> str:
    """
    Types text at the current cursor position.
    Uses clipboard to avoid keyboard layout issues.
    """
    _ensure_pyautogui()
    time.sleep(0.3)
    
    # Use clipboard instead of typewrite to avoid keyboard layout issues
    if _PYPERCLIP:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.typewrite(text, interval=interval)
    
    return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"


def _click(x: int = None, y: int = None, button: str = "left",
           clicks: int = 1, image: str = None) -> str:
    """
    Clicks at coordinates or on a screen image.
    If image path given, locates it on screen and clicks.
    """
    _ensure_pyautogui()

    if image:
        try:
            loc = pyautogui.locateCenterOnScreen(image, confidence=0.8)
            if loc:
                pyautogui.click(loc.x, loc.y, button=button, clicks=clicks)
                return f"Clicked image: {image}"
            return f"Image not found on screen: {image}"
        except Exception as e:
            return f"Image click failed: {e}"

    if x is not None and y is not None:
        pyautogui.click(x, y, button=button, clicks=clicks)
        return f"Clicked ({x}, {y}) with {button} button"

    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position"


def _hotkey(*keys) -> str:
    """Presses a key combination. E.g. hotkey('ctrl', 'c')"""
    _ensure_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    """Presses a single key."""
    _ensure_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    """Scrolls in the specified direction."""
    _ensure_pyautogui()
    clicks = amount if direction in ("up", "right") else -amount
    if direction in ("up", "down"):
        pyautogui.scroll(clicks)
    else:
        pyautogui.hscroll(clicks)
    return f"Scrolled {direction} {amount} times"


def _move_mouse(x: int, y: int, duration: float = 0.3) -> str:
    """Moves mouse to coordinates."""
    _ensure_pyautogui()
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse moved to ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    """Drags from (x1,y1) to (x2,y2)."""
    _ensure_pyautogui()
    pyautogui.drag(x1 - pyautogui.position()[0], y1 - pyautogui.position()[1])
    pyautogui.dragTo(x2, y2, duration=duration)
    return f"Dragged from ({x1},{y1}) to ({x2},{y2})"


def _clipboard_copy() -> str:
    """Gets current clipboard content."""
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "Copied to clipboard"


def _clipboard_set(text: str) -> str:
    """Sets clipboard content and pastes it."""
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _hotkey("ctrl", "v")
        return f"Pasted: {text[:50]}"
    return "pyperclip not available"


def _screenshot(save_path: str = None) -> str:
    """Takes a screenshot."""
    _ensure_pyautogui()
    if not save_path:
        save_path = str(Path.home() / "Desktop" / "screenshot.png")
    img = pyautogui.screenshot()
    img.save(save_path)
    return f"Screenshot saved: {save_path}"


def _wait(seconds: float) -> str:
    """Waits for specified seconds."""
    time.sleep(seconds)
    return f"Waited {seconds}s"


def _wait_for_image(image_path: str, timeout: int = 10) -> str:
    """Waits until an image appears on screen (up to timeout seconds)."""
    _ensure_pyautogui()
    start = time.time()
    while time.time() - start < timeout:
        try:
            loc = pyautogui.locateCenterOnScreen(image_path, confidence=0.8)
            if loc:
                return f"Image found at ({loc.x}, {loc.y})"
        except Exception:
            pass
        time.sleep(0.5)
    return f"Image not found within {timeout}s: {image_path}"


def _get_screen_size() -> str:
    """Returns current screen resolution."""
    _ensure_pyautogui()
    w, h = pyautogui.size()
    return f"{w}x{h}"


def _focus_window(title: str) -> str:
    """Brings a window to focus by title (Windows)."""
    if platform.system() == "Windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, timeout=5
            )
            time.sleep(0.8)  # wait for window to render before screenshot
            return f"Focused window: {title}"
        except Exception as e:
            return f"Could not focus window: {e}"
    return "Window focus only supported on Windows"


def _select_all() -> str:
    return _hotkey("ctrl", "a")


def _clear_field() -> str:
    """Selects all and deletes — clears an input field."""
    _hotkey("ctrl", "a")
    time.sleep(0.1)
    _press("delete")
    return "Field cleared"


def _smart_type(text: str, clear_first: bool = True) -> str:
    """
    Types text into the currently focused field.
    Optionally clears the field first.
    Always uses clipboard to avoid keyboard layout issues.
    """
    _ensure_pyautogui()

    if clear_first:
        _clear_field()
        time.sleep(0.1)

    # Always use clipboard to avoid keyboard layout issues (Russian/English)
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return f"Smart-typed: {text[:50]}"
    else:
        # Fallback only if pyperclip not available
        pyautogui.typewrite(text, interval=0.04)
        return f"Smart-typed (fallback): {text[:50]}"


def _analyze_screen_for_element(description: str) -> tuple[int, int] | None:
      """
      Takes a screenshot and asks the model for the pixel coordinates
      of a described UI element. Returns (x, y) center or None.
      Uses the "vision" role from config/registry.yaml for better accuracy.

      Запрос уходит через общую дверь core/aux_model.aux_call: остывание
      после 429, один повтор на 503 и учёт расхода живут там, а не здесь.
      """
      try:
          import io
          import re
          import time as _t

          # Дверь открывается лениво: SDK не должен грузиться при старте,
          # и роль модели спрашивается в момент вызова, а не на импорте.
          from core.aux_model import aux_call
          from config.loader import get_api_key, get_model as _get_model
          api_key = get_api_key()

          _ensure_pyautogui()
          w, h = pyautogui.size()
          _t.sleep(0.2)  # brief pause for screen to settle
          _shot_started = _t.perf_counter()
          img  = pyautogui.screenshot()
          buf  = io.BytesIO()
          img.save(buf, format="PNG")
          buf.seek(0)
          png = buf.getvalue()
          _shot_seconds = _t.perf_counter() - _shot_started

          prompt = (
              f"This is a screenshot of a Windows computer screen ({w}x{h} pixels).\n"
              f"Task: Find the UI element described as: '{description}'.\n"
              f"Look carefully at all visible text, buttons, icons, and interface elements.\n"
              f"If the element is visible, respond with ONLY the pixel coordinates of its center: x,y\n"
              f"Example response: 450,320\n"
              f"If the element is NOT visible on screen, respond with only: NOT_FOUND\n"
              f"Do not include any other text, explanation, or formatting."
          )

          _call_started = _t.perf_counter()
          ok, answer = aux_call(
              prompt,
              api_key,
              model=_get_model("vision"),
              image_parts=[(png, "image/png")],
              caller="Vision",
          )
          _call_seconds = _t.perf_counter() - _call_started

          # Замер живёт здесь, а не в двери: дверь не знает ни про экран,
          # ни про вес снимка. Своё падение замер гасит сам: счётчик
          # времени не имеет права отменить нажатие.
          try:
              print(f"[ComputerControl] зрение: снимок {w}x{h}, "
                    f"{len(png) // 1024} КБ PNG — {_shot_seconds:.2f} с; "
                    f"ответ модели — {_call_seconds:.2f} с")
          except Exception:
              pass

          if not ok:
              # Причину уже назвала дверь; здесь — почему нажатия не будет.
              print(f"[ComputerControl] Screen analysis failed: {answer}")
              return None

          text = (answer or "").strip()
          print(f"[ComputerControl] Gemini element response: '{text}'")

          if not text:
              print("[ComputerControl] Screen analysis failed: пустой ответ модели")
              return None

          if "NOT_FOUND" in text.upper():
              return None

          match = re.search(r"(\d+)\s*,\s*(\d+)", text)
          if match:
              x, y = int(match.group(1)), int(match.group(2))
              # Sanity check: coordinates must be within screen bounds
              if 0 <= x <= w and 0 <= y <= h:
                  return x, y
              print(f"[ComputerControl] Coordinates out of bounds: ({x},{y}) for screen {w}x{h}")
              return None

      except Exception as e:
          print(f"[ComputerControl] Screen analysis failed: {e}")

      return None


# ── Лестница поиска: одна дверь к ответу «где эта вещь» ─────────────
# Раньше ветки действий звали поиск напрямую, и способ узнать координаты
# был ровно один — догадка модели по картинке. Источников будет больше
# (дерево окон Windows), поэтому решение вынесено в отдельную ступеньку:
# теперь ветки спрашивают у лестницы, а не у конкретного источника.
#
# Сегодня в лестнице ровно одна ступень и поведение не меняется ни на букву.

from typing import NamedTuple


class Target(NamedTuple):
    """Найденная вещь: куда жать, кто это сказал и насколько уверен.

    x, y       — точка на экране в пикселях;
    source     — кто ответил: "model", позже также "windows";
    confidence — 0.0 означает «источник не умеет оценивать себя»;
    label      — подпись, которую источник действительно увидел.
    """

    x: int
    y: int
    source: str
    confidence: float = 0.0
    label: str = ""


def _locate_by_model(description: str):
    """Старый путь: снимок экрана и догадка модели. Не изменён."""
    coords = _analyze_screen_for_element(description)
    if not coords:
        return None
    x, y = coords
    return Target(x=int(x), y=int(y), source="model",
                  confidence=0.0, label=description)


_locate_by_model.source_name = "model"

# Порядок ступеней. Список лежит на уровне модуля нарочно: так проверка
# может подставить поддельные источники без сети и экрана, а следующие вечера
# — дописать чтение окон Windows перед моделью, не трогая ветки действий.
_LOCATORS = [_locate_by_model]


def _locate(description: str):
    """Спросить у источников по очереди. Первый ответ побеждает.

    Сорвавшийся источник не отменяет остальные: он называет причину вслух
    и уступает очередь следующему. Если промолчали все — возвращается None,
    и это означает «не нашёл», а не «жми наугад».
    """
    for finder in _LOCATORS:
        name = getattr(finder, "source_name", "?")
        try:
            target = finder(description)
        except Exception as e:
            print(f"[Локатор] источник '{name}' сорвался: {e}")
            continue
        if target is not None:
            return target
    return None


# ── Screen control helper ───────────────────────────────────────────────────
# Screen control state lives on player.screen_control (UI toggle button).
# This function is kept for import compatibility only.


def set_screen_control(enabled: bool) -> None:
    """Compatibility shim. Screen state lives on player.screen_control."""
    print("[ComputerControl] set_screen_control: use UI SCREEN button instead")


def _screen_off_message() -> str:
    """
    The refusal returned when an interactive action is requested while the
    SCREEN toggle is OFF.

    The single source of truth is core.gate.SCREEN_OFF_MSG, imported lazily so
    this module still answers when the gate is unavailable. This text used to
    exist twice, in two files; whoever edited one left the other one lying.
    core.gate imports only stdlib + core.security, so there is no import cycle.
    """
    try:
        from core.gate import SCREEN_OFF_MSG
        return SCREEN_OFF_MSG
    except Exception:
        return (
            "Screen control is currently disabled. "
            "Please press the SCREEN button in the interface, then repeat."
        )


def _screen_status(player=None) -> str:
    """
    Read-only truth about the two screen features, taken straight from the live
    UI button -- never from memory, never from the system prompt.

    Why this exists: the system prompt is assembled once, at connect. A toggle
    pressed in the middle of a conversation therefore never reaches the model,
    which then answers from a frozen snapshot and asks the user to switch on
    something that is already on.

    Touches no mouse, no keyboard, no model, no network.
    """
    ctrl = bool(getattr(player, "screen_control", False))

    # Screen View is a DIFFERENT feature; reported here so the model stops
    # confusing the two and stops reaching for screen_share_control.
    try:
        from core.dialogue_state import get as _ds_get
        view = "ON" if _ds_get().get("screen_share_active") else "OFF"
    except Exception:
        view = "unknown"

    if ctrl:
        return (
            "Screen control (clicking/typing on screen): ON. Interactive "
            "actions such as screen_click, click and type are permitted RIGHT "
            "NOW -- carry out the request instead of asking the user to enable "
            "anything. Screen View (vision streaming, a separate feature): "
            f"{view}."
        )
    return (
        "Screen control (clicking/typing on screen): OFF. The SCREEN button in "
        "the interface is not pressed, so clicking and typing are blocked. Ask "
        "the user to press the SCREEN button, then call screen_status again "
        "before retrying. Do NOT use screen_share_control for this -- that is "
        f"Screen View (vision streaming, a separate feature), currently {view}."
    )


def computer_control(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Computer control dispatcher.
    Interactive actions require Screen Control ON (SCREEN button in interface).
    """
    action = (parameters or {}).get("action", "").lower().strip()

    if not action:
        return "Please specify an action for computer_control, sir."

    # Screen control comes from UI button only - no module-level global
    screen_ctrl = bool(getattr(player, "screen_control", False))

    INTERACTIVE_ACTIONS = {
        "type", "smart_type", "click", "left_click", "double_click", "right_click",
        "hotkey", "press", "scroll", "move", "drag", "paste", "clear_field",
        "focus_window", "screen_click", "screen_find", "wait_image"
    }

    if action in INTERACTIVE_ACTIONS and not screen_ctrl:
        print(f"[ComputerControl] BLOCKED: '{action}' -- screen control OFF")
        return _screen_off_message()

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] Action={action}  ScreenCtrl={screen_ctrl}")

    try:

        if action == "copy":
            return _clipboard_copy()

        elif action == "screenshot":
            return _screenshot(parameters.get("path"))

        elif action == "wait":
            return _wait(float(parameters.get("seconds", 1.0)))

        elif action == "screen_size":
            return _get_screen_size()

        elif action == "screen_status":
            return _screen_status(player)

        elif action == "random_data":
            return generate_random_data(parameters.get("type", "name"))

        elif action == "user_data":
            field   = parameters.get("field", "name")
            profile = _load_user_profile()
            value   = profile.get(field, "")
            if not value:
                value = generate_random_data(field)
            return value

        # -- Interactive (screen_ctrl must be True) --

        elif action in ("type", "smart_type"):
            text = parameters.get("text", "")
            if not text:
                return "No text provided."
            if action == "smart_type":
                return _smart_type(text, clear_first=parameters.get("clear_first", True))
            return _type_text(text)

        elif action in ("click", "left_click"):
            return _click(x=parameters.get("x"), y=parameters.get("y"), button="left")

        elif action == "double_click":
            return _click(x=parameters.get("x"), y=parameters.get("y"), button="left", clicks=2)

        elif action == "right_click":
            return _click(x=parameters.get("x"), y=parameters.get("y"), button="right")

        elif action == "hotkey":
            keys_str = parameters.get("keys", "")
            if not keys_str:
                return "No keys provided."
            keys = [k.strip() for k in keys_str.replace("+", " ").split()]
            return _hotkey(*keys)

        elif action == "press":
            key = parameters.get("key", "")
            return _press(key) if key else "No key provided."

        elif action == "scroll":
            return _scroll(parameters.get("direction", "down"), int(parameters.get("amount", 3)))

        elif action == "move":
            return _move_mouse(int(parameters.get("x", 0)), int(parameters.get("y", 0)))

        elif action == "drag":
            return _drag(
                int(parameters.get("x1", 0)), int(parameters.get("y1", 0)),
                int(parameters.get("x2", 0)), int(parameters.get("y2", 0)),
            )

        elif action == "paste":
            text = parameters.get("text", "")
            if text:
                _clipboard_set(text)
                import time; time.sleep(0.1)
            return _hotkey("ctrl", "v")

        elif action == "clear_field":
            return _clear_field()

        elif action == "focus_window":
            title = parameters.get("title", "")
            return _focus_window(title) if title else "No window title provided."

        elif action == "screen_find":
            description = parameters.get("description", "")
            if not description:
                return "No element description provided."
            target = _locate(description)
            if target:
                return f"Found '{description}' at ({target.x}, {target.y})."
            return f"Element '{description}' not found on screen."

        elif action == "screen_click":
            description = parameters.get("description", "")
            if not description:
                return "No element description provided."
            print(f"[ComputerControl] Clicking: '{description}'")
            target = _locate(description)
            if target:
                import time; time.sleep(0.3)
                _click(x=target.x, y=target.y)
                return f"Clicked '{description}' at ({target.x}, {target.y})."
            return f"Could not find '{description}' on screen."

        elif action == "wait_image":
            image_path = parameters.get("path", "")
            if not image_path:
                return "No image path provided."
            return _wait_for_image(image_path, timeout=int(parameters.get("timeout", 10)))

        else:
            return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] Error in '{action}': {e}")
        return f"computer_control '{action}' failed: {e}"
