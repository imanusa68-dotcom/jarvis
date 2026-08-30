# -*- coding: utf-8 -*-
"""Фаза 0.5, шаг 1 — сторожа реестра «роль → модель».

Почему этот файл существует.

Смена модели в этом проекте — правка одной строки в config/registry.yaml. Цена
ошибки в этой строке несоразмерно высока: шесть мест спрашивают имя модели не
в момент вызова, а во время загрузки файла (main.py:63 и ещё пять). Пропавшая
роль там даёт не «одна функция не работает», а «Jarvis не запускается, и с ним
падают десятки тестов». До этого файла такую опечатку не ловило ничто:
тестов на реестр в проекте не было ни одного.

Сторожа не ходят в сеть и ничего не стоят по квоте. Они проверяют четыре
вещи: всякая спрашиваемая кодом роль есть в реестре; имён моделей нет нигде,
кроме реестра (греп-гейт, I37); неизвестная роль падает громко и со списком;
число мест, берущих модель на импорте, не растёт.

Грабли, которые учтены здесь нарочно: греп-сторож смотрит только на рабочий
код и не смотрит на себя, на tests/ и на config/ — иначе он падает от собственных
слов. И на tools/ тоже не смотрит: там лежат ручные утилиты владельца,
которые не участвуют в работе Jarvis.

Run:  python -m pytest tests/test_registry_roles.py -q
or:   python tests/test_registry_roles.py
"""
import ast as _ast
import os as _os
import re as _re
import sys as _sys
from pathlib import Path

_ROOT = Path(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_sys.path.insert(0, str(_ROOT))

from config.loader import ConfigError, get_model  # noqa: E402

_REGISTRY = _ROOT / "config" / "registry.yaml"

# Где живёт рабочий код Jarvis. tests/, config/, tools/, docs/ — не здесь.
_CODE_DIRS = ("core", "agent", "actions", "memory")
_CODE_FILES = ("main.py", "ui.py", "consent_mode.py")

# Шесть мест, которые берут имя модели прямо при загрузке файла. Это долг,
# а не образец: список может только уменьшаться.
_IMPORT_TIME_BUDGET = {
    "main.py",
    _os.path.join("actions", "screen_processor.py"),
    _os.path.join("actions", "code_helper.py"),
    _os.path.join("actions", "deep_research.py"),
    _os.path.join("core", "screen_live_session.py"),
    _os.path.join("core", "screen_live_runtime.py"),
}

# Куски имён моделей любого поставщика. В рабочем коде их быть не должно.
_MODEL_NEEDLES = (
    "gemini-", "gemma-", "text-embedding", "palm-", "gpt-4", "gpt-3",
    "claude-", "llama-", "mistral-",
)

_CALL_RE = _re.compile(r"\b_?get_model\(\s*[\"']([A-Za-z0-9_]+)[\"']")
_MODULE_LEVEL_RE = _re.compile(r"^\S.*\b_?get_model\(")


def _code_paths():
    """Все .py рабочего кода, без тестов, конфига, утилит и кэша."""
    out = []
    for name in _CODE_FILES:
        p = _ROOT / name
        if p.exists():
            out.append(p)
    for d in _CODE_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return out


def _rel(p: Path) -> str:
    return str(p.relative_to(_ROOT))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _registry_roles() -> dict:
    import yaml
    data = yaml.safe_load(_read(_REGISTRY)) or {}
    return (data.get("roles") or {})


def test_registry_is_a_readable_map_of_roles():
    """Реестр читается, это словарь, и он не пуст."""
    assert _REGISTRY.exists(), "config/registry.yaml пропал"
    roles = _registry_roles()
    assert isinstance(roles, dict), "в registry.yaml под roles не словарь"
    assert roles, "в registry.yaml нет ни одной роли"


def test_every_role_is_a_non_empty_name():
    """Пустая строка в реестре — это не «модель по умолчанию», это ошибка."""
    for role, value in _registry_roles().items():
        assert isinstance(value, str) and value.strip(), (
            f"роль {role!r} в registry.yaml пуста или не строка: {value!r}"
        )
        assert value.strip() == value, (
            f"у роли {role!r} имя модели с краевыми пробелами: {value!r}"
        )


def test_every_role_the_code_asks_for_exists_in_the_registry():
    """Главный сторож: нет роли — нет запуска."""
    roles = set(_registry_roles())
    asked = {}
    for p in _code_paths():
        for role in _CALL_RE.findall(_read(p)):
            asked.setdefault(role, []).append(_rel(p))
    assert asked, "ни одного вызова get_model в коде — сторож ослеп"
    missing = {r: v for r, v in asked.items() if r not in roles}
    assert not missing, (
        "код спрашивает роли, которых нет в config/registry.yaml: "
        + "; ".join(f"{r} ← {', '.join(v)}" for r, v in sorted(missing.items()))
    )


def test_no_model_name_lives_outside_the_registry():
    """I37: в коде только роли. Имена моделей — только в реестре."""
    found = []
    for p in _code_paths():
        low = _read(p).lower()
        for needle in _MODEL_NEEDLES:
            if needle in low:
                found.append(f"{_rel(p)}: {needle!r}")
    assert not found, (
        "имя модели просочилось мимо config/registry.yaml: " + "; ".join(found)
    )


def test_unknown_role_fails_loudly_and_names_the_roles():
    """Молча не умираем: отказ называет роль и список существующих."""
    try:
        get_model("этой-роли-не-бывает")
    except ConfigError as e:
        text = str(e)
    else:
        raise AssertionError("get_model молча вернул что-то на несуществующую роль")
    assert "этой-роли-не-бывает" in text, f"отказ не называет роль: {text!r}"
    for role in _registry_roles():
        assert role in text, f"отказ не перечисляет роль {role!r}: {text!r}"


def test_import_time_model_lookups_do_not_grow():
    """Новых мест, берущих модель на импорте, появляться не должно.

    Такое место превращает опечатку в ячейке yaml в падение всего запуска.
    Шесть унаследованных — долг, который сокращают, а не набирают.
    """
    seen = set()
    for p in _code_paths():
        for line in _read(p).splitlines():
            if _MODULE_LEVEL_RE.match(line):
                seen.add(_rel(p))
                break
    extra = seen - _IMPORT_TIME_BUDGET
    assert not extra, (
        "модель спрашивают на импорте в новых местах: " + ", ".join(sorted(extra))
    )


def test_the_model_lister_never_prints_the_key():
    """Инструмент переписи берёт ключ через одну дверь и никогда его не печатает."""
    path = _ROOT / "tools" / "list_models.py"
    assert path.exists(), "tools/list_models.py пропал"
    src = _read(path)
    assert "from config.loader import" in src and "get_api_key" in src, (
        "инструмент берёт ключ мимо config.loader"
    )
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if not (isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Name)
                and node.func.id == "print"):
            continue
        for arg in node.args:
            for inner in _ast.walk(arg):
                if isinstance(inner, _ast.Name) and "key" in inner.id.lower():
                    raise AssertionError(
                        f"ключ может попасть в печать: строка {node.lineno}"
                    )
    # Шаблон склеивается в рантайме: иначе в дереве появляется литерал префикса
    # ключа Google и поиск «есть ли где-то ключ» начинает находить сам сторож (грабля №1).
    assert ("AI" + "zaSy") not in src, "в инструменте зашит ключ"
    assert "?key=" not in src, "ключ уходит в адресе запроса, а не в заголовке"


def _run():
    fns = [
        test_registry_is_a_readable_map_of_roles,
        test_every_role_is_a_non_empty_name,
        test_every_role_the_code_asks_for_exists_in_the_registry,
        test_no_model_name_lives_outside_the_registry,
        test_unknown_role_fails_loudly_and_names_the_roles,
        test_import_time_model_lookups_do_not_grow,
        test_the_model_lister_never_prints_the_key,
    ]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print(f"\nOK: {len(fns)} passed (standalone)")


if __name__ == "__main__":
    _run()
