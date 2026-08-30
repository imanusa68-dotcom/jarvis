# tests/test_role_migration.py
# Фаза 0.5, шаг 2 — сторожа переезда ролей на разные модели.
#
# Что охраняем:
#   1. Роль fix_legacy ушла совсем — и из реестра, и из кода.
#      Если кто-то вернёт её в код, но не в реестр — падёт импорт модуля
#      у владельца, а не тест. Поэтому ловим здесь.
#   2. Исчерпание квоты одной модели не глушит остальные (Р-22),
#      и при этом сама виновная модель всё-таки блокируется (регресс).
#   3. Каждый вопрос к сторожу называет модель. Без этого по-модельное
#      остывание тихо вырождается: подсистема спросит про чужое ведро
#      и не увидит своёго остывания.

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_REGISTRY = _ROOT / "config" / "registry.yaml"

# Где ищем рабочий код (tests/, config/, tools/, docs/ — не код продукта)
_CODE_DIRS = ("core", "agent", "actions", "memory")
_CODE_FILES = ("main.py", "ui.py", "consent_mode.py")

_RETIRED_ROLE = "fix" + "_legacy"


def _code_files():
    seen = []
    for name in _CODE_FILES:
        p = _ROOT / name
        if p.exists():
            seen.append(p)
    for d in _CODE_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            seen.append(p)
    return seen


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace")


# ── 1. Старая роль ушла целиком ───────────────────────────────────────

def test_the_retired_role_is_gone_from_registry_and_code():
    text = _read(_REGISTRY)
    assert _RETIRED_ROLE not in text, (
        "роль " + _RETIRED_ROLE + " вернулась в config/registry.yaml"
    )

    guilty = []
    for path in _code_files():
        if _RETIRED_ROLE in _read(path):
            guilty.append(str(path.relative_to(_ROOT)).replace("\\", "/"))
    assert not guilty, (
        "код всё ещё спрашивает удалённую роль " + _RETIRED_ROLE
        + ": " + ", ".join(guilty)
    )


# ── 2. Одна выдохшаяся модель не затыкает все остальные ────────────────────

def test_one_exhausted_model_does_not_silence_the_others():
    from core.model_guard import ModelQuotaGuard

    guard = ModelQuotaGuard()

    guard.record_429(30.0, "model-a")

    # Виновная модель действительно закрыта — защита не сломалась.
    assert guard.is_available("model-a") is False, "выдохшаяся модель осталась открытой"
    assert guard.cooldown_remaining("model-a") > 0.0

    # Соседняя модель продолжает работать — это и есть смысл шага.
    assert guard.is_available("model-b") is True, "отказ одной модели заглушил соседнюю"
    assert guard.cooldown_remaining("model-b") == 0.0

    # То же самое через разбор исключения, как это идёт в живом коде.
    hit = guard.handle_exception(RuntimeError("429 RESOURCE_EXHAUSTED"), "model-c")
    assert hit is True
    assert guard.is_available("model-c") is False
    assert guard.is_available("model-b") is True, "разбор исключения задел чужую модель"

    # Не квотная ошибка не должна закрывать ничего.
    assert guard.handle_exception(RuntimeError("connection reset"), "model-d") is False
    assert guard.is_available("model-d") is True


# ── 3. Каждый вопрос к сторожу называет модель ───────────────────────────

_EMPTY_ASK = re.compile(r"\.(is_available|cooldown_remaining)\(\s*\)")
_LONELY_EXC = re.compile(r"\.handle_exception\(\s*[^(),]*\s*\)")


def test_every_quota_question_names_a_model():
    call_sites = 0
    guilty = []

    for path in _code_files():
        if path.name == "model_guard.py":
            continue  # сам сторож: там определения, а не вопросы
        src = _read(path)
        if "model_guard" not in src:
            continue
        rel = str(path.relative_to(_ROOT)).replace("\\", "/")
        for lineno, line in enumerate(src.splitlines(), 1):
            if _EMPTY_ASK.search(line) or _LONELY_EXC.search(line):
                guilty.append(rel + ":" + str(lineno) + ": " + line.strip())
            if (
                ".is_available(" in line
                or ".cooldown_remaining(" in line
                or ".handle_exception(" in line
            ):
                call_sites += 1

    assert call_sites >= 8, (
        "ожидались вопросы к сторожу квоты, нашлось " + str(call_sites)
        + " — сканер смотрит не туда"
    )
    assert not guilty, (
        "вопрос к сторожу без имени модели (остывание будет чужое): "
        + "; ".join(guilty)
    )


# ── Запуск без pytest ──────────────────────────────────────────────

def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("OK   " + t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL " + t.__name__ + ": " + str(e))
        except Exception as e:
            failed += 1
            print("ERR  " + t.__name__ + ": " + type(e).__name__ + ": " + str(e))
    print()
    if failed:
        print(str(failed) + " failed, " + str(len(tests) - failed) + " passed (standalone)")
        return 1
    print("OK: " + str(len(tests)) + " passed (standalone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
