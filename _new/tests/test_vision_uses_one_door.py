"""У зрения больше нет своей двери к модели.

Что проверяем:
  1. в actions/computer_control.py не осталось старого SDK;
  2. общая дверь открывается лениво — только внутри функции;
  3. поиск элемента идёт ровно один раз и берёт роль vision (та же модель);
  4. снимок уходит парой (байты, "image/png") — тем форматом, который
     понимает дверь, и ровно одной картинкой;
  5. промпт называет модели настоящий размер экрана;
  6. хорошие координаты доезжают до мыши целыми;
  7. NOT_FOUND — это «не нашёл», а не клик в случайное место;
  8. координаты за границей экрана отбрасываются;
  9. отказ по квоте не двигает мышь и не роняет инструмент;
 10. пустой ответ модели — тоже не клик;
 11. отсутствие ключа не роняет зрение и не идёт к модели;
 12. строка замера печатается и её собственное падение не отменяет нажатие;
 13. число старых дверей по проекту не растёт (сегодня их не больше пяти).

Запуск: python -m pytest -q  или  python tests/test_vision_uses_one_door.py
"""

import builtins
import io
import sys
import time as _time_mod
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.loader as loader
import core.aux_model as aux_model
import actions.computer_control as cc

# Имя старого SDK собирается из кусков нарочно: иначе сам этот
# файл попадёт в свой же греп и проверка станет ложью.
OLD_SDK = "google." + "generativeai"
SKIP_DIRS = {"tests", "__pycache__", ".pytest_cache", "logs", "docs"}

SOURCE = (ROOT / "actions" / "computer_control.py").read_text(encoding="utf-8")

# Снимок-пустышка: важны только то, что это байты и что их можно сравнить.
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 4096
SIZE = (1920, 1080)


def _live_old_sdk_points():
    """Живые строки с импортом старого SDK: (файл, номер строки)."""
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue          # комментарий — не дверь
            if OLD_SDK in stripped and "import" in stripped:
                found.append((str(rel).replace("\\", "/"), number))
    return found


class _FakeImage:
    """Картинка, которая умеет только сохраниться в буфер."""

    def __init__(self, blob):
        self._blob = blob

    def save(self, buf, format=None):     # noqa: A002 — имя как у PIL
        buf.write(self._blob)


class _FakeGui:
    """Подставной pyautogui: ни одного настоящего снимка экрана."""

    def __init__(self, size=SIZE, blob=PNG):
        self._size = size
        self._blob = blob
        self.shots = 0

    def size(self):
        return self._size

    def screenshot(self):
        self.shots += 1
        return _FakeImage(self._blob)


class _Door:
    """Подставная общая дверь: запоминает заходы и отдаёт заготовленный ответ."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, api_key, model=None, image_parts=None, caller="unknown"):
        self.calls.append({
            "prompt": prompt,
            "model": model,
            "caller": caller,
            "key": api_key,
            "image_parts": image_parts,
        })
        return self.reply


def _find_with(reply, description="кнопка ОК", key_getter=None,
               size=SIZE, blob=PNG, printer=None):
    """Зовём зрение с подменённой дверью, экраном и ключом.

    Ключ подменяется нарочно: проверка не должна зависеть от того,
    введён ли на этой машине настоящий. Сон в 0,2 с тоже снимается:
    пауза на успокоение экрана не должна растягивать прогон тестов.
    """
    door = _Door(reply)
    gui = _FakeGui(size, blob)

    saved_door = aux_model.aux_call
    saved_gui = getattr(cc, "pyautogui", None)
    saved_flag = cc._PYAUTOGUI
    saved_key = loader.get_api_key
    saved_sleep = _time_mod.sleep
    saved_print = builtins.print

    aux_model.aux_call = door
    cc.pyautogui = gui
    cc._PYAUTOGUI = True
    loader.get_api_key = key_getter or (lambda: "ключ-для-теста")
    _time_mod.sleep = lambda _seconds: None
    if printer is not None:
        builtins.print = printer

    out = io.StringIO()
    try:
        with redirect_stdout(out):
            coords = cc._analyze_screen_for_element(description)
    finally:
        aux_model.aux_call = saved_door
        cc.pyautogui = saved_gui
        cc._PYAUTOGUI = saved_flag
        loader.get_api_key = saved_key
        _time_mod.sleep = saved_sleep
        builtins.print = saved_print

    return coords, door, out.getvalue(), gui


def test_the_eye_has_no_door_of_its_own():
    assert OLD_SDK not in SOURCE, "зрение снова лезет в старый SDK своей дверью"
    assert "GenerativeModel" not in SOURCE, "старая дверь GenerativeModel всё ещё в файле"
    assert "aux_call" in SOURCE, "зрение не зовёт общую дверь"


def test_the_door_opens_lazily():
    """Импорт двери и роль модели — только внутри функции.

    На уровне модуля это дало бы две беды: SDK грузится при старте, а
    опечатка в registry.yaml роняет весь запуск (test_registry_roles.py).
    """
    imports = [
        line for line in SOURCE.splitlines()
        if "core.aux_model" in line and "import" in line and not line.strip().startswith("#")
    ]
    assert imports, "импорт общей двери пропал совсем"
    for line in imports:
        assert line.startswith(" ") or line.startswith("\t"), (
            "импорт двери вылез на уровень модуля: " + line.strip()
        )
    for line in SOURCE.splitlines():
        if line and not line[0].isspace() and "get_model(" in line:
            raise AssertionError("роль спрашивается на импорте: " + line.strip())


def test_the_eye_walks_through_the_shared_door():
    from config.loader import get_model

    coords, door, printed, gui = _find_with((True, "450,320"))
    assert len(door.calls) == 1, f"заходов в дверь {len(door.calls)}, а должен быть один"
    call = door.calls[0]
    assert call["model"] == get_model("vision"), (
        f"модель подменилась: {call['model']} вместо роли vision — зрение оглупело"
    )
    assert "Vision" in call["caller"], f"дверь не знает, кто стучит: {call['caller']}"
    assert gui.shots == 1, f"снимков экрана {gui.shots}, а должен быть один"
    assert coords == (450, 320), f"координаты не доехали: {coords!r}"


def test_the_screenshot_travels_in_the_format_the_door_understands():
    """Дверь ждёт список пар (байты, mime), а не словарь старого SDK."""
    _coords, door, _printed, _gui = _find_with((True, "10,20"))
    parts = door.calls[0]["image_parts"]
    assert isinstance(parts, list) and len(parts) == 1, f"картинок в запросе: {parts!r}"
    part = parts[0]
    assert isinstance(part, tuple) and len(part) == 2, f"картинка не пара: {part!r}"
    data, mime = part
    assert isinstance(data, (bytes, bytearray)), f"в дверь ушли не байты: {type(data)}"
    assert bytes(data) == PNG, "в дверь ушла не та картинка, что снял экран"
    assert mime == "image/png", f"mime-тип поехал: {mime!r}"


def test_the_prompt_tells_the_model_the_real_screen_size():
    _coords, door, _printed, _gui = _find_with((True, "1,2"), size=(1600, 900))
    prompt = door.calls[0]["prompt"]
    assert "1600x900" in prompt, f"в промпте нет размера экрана: {prompt[:120]!r}"
    assert "кнопка ОК" in prompt, "описание элемента до модели не доехало"
    assert "NOT_FOUND" in prompt, "модели больше не говорят, как сказать «не вижу»"


def test_not_found_is_not_a_click():
    coords, door, _printed, _gui = _find_with((True, "NOT_FOUND"))
    assert coords is None, f"на NOT_FOUND вернулись координаты: {coords!r}"
    assert len(door.calls) == 1


def test_coordinates_outside_the_screen_are_refused():
    """Мышь не имеет права уехать за край экрана."""
    coords, _door, printed, _gui = _find_with((True, "5000,90"))
    assert coords is None, f"координаты за границей прошли: {coords!r}"
    assert "out of bounds" in printed, f"о выходе за границу ничего не сказали: {printed!r}"


def test_a_quota_refusal_never_moves_the_mouse():
    for refusal in ("[quota-cooldown:65s]", "[quota-429:cooldown 30s]", "[error:503 UNAVAILABLE]"):
        coords, door, printed, _gui = _find_with((False, refusal))
        assert coords is None, f"при отказе {refusal} зрение вернуло координаты: {coords!r}"
        assert len(door.calls) == 1, "при отказе стучали в дверь не один раз"
        assert refusal in printed, f"причина отказа не названа в логе: {printed!r}"


def test_an_empty_answer_is_not_a_click():
    coords, _door, printed, _gui = _find_with((True, "   "))
    assert coords is None, f"пустой ответ превратился в клик: {coords!r}"
    assert "пустой ответ" in printed, f"о пустом ответе ничего не сказали: {printed!r}"


def test_a_missing_key_never_explodes():
    """Без ключа зрение обязано вернуть None, а не упасть исключением."""

    def _no_key():
        raise RuntimeError("gemini_api_key не найден")

    coords, door, printed, _gui = _find_with((True, "1,1"), key_getter=_no_key)
    assert coords is None, f"без ключа вернулись координаты: {coords!r}"
    assert not door.calls, "без ключа всё равно пошли к модели"
    assert "failed" in printed.lower(), f"отсутствие ключа прошло молча: {printed!r}"


def test_the_measurement_is_printed():
    """Цифры вместо догадок: сколько ушло на снимок и сколько на ответ."""
    _coords, _door, printed, _gui = _find_with((True, "5,6"))
    assert "снимок 1920x1080" in printed, f"замер не называет экран: {printed!r}"
    assert "КБ" in printed, f"замер не называет вес снимка: {printed!r}"
    assert "ответ модели" in printed, f"замер не называет время ответа: {printed!r}"


def test_a_broken_measurement_never_kills_the_click():
    """Счётчик времени — слуга, а не хозяин: его падение не отменяет нажатие."""
    real_print = builtins.print

    def _print_that_hates_the_measurement(*args, **kwargs):
        if args and "снимок" in str(args[0]):
            raise UnicodeEncodeError("cp1251", "x", 0, 1, "консоль не в UTF-8")
        return real_print(*args, **kwargs)

    coords, door, _printed, _gui = _find_with(
        (True, "450,320"), printer=_print_that_hates_the_measurement
    )
    assert coords == (450, 320), f"падение замера унесло с собой нажатие: {coords!r}"
    assert len(door.calls) == 1


def test_the_number_of_old_doors_never_grows():
    points = _live_old_sdk_points()
    files = {name for name, _ in points}
    assert len(points) <= 5, (
        "старых дверей стало больше, а не меньше: "
        + ", ".join(f"{n}:{ln}" for n, ln in points)
    )
    assert "actions/computer_control.py" not in files, "зрение вернулось в старый SDK"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} зелёных")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
