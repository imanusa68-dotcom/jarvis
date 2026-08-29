# -*- coding: utf-8 -*-
"""Сторожа против подмены наших пакетов чужим кодом.

ПОЧЕМУ ЭТОТ ФАЙЛ ПОЯВИЛСЯ 28.08.2026, СЛОВАМИ ВЛАДЕЛЬЦА, А НЕ ТЕОРИИ.
Прогон на его машине оборвался семью ошибками сбора, и в одной из них
стоял путь, которого в проекте нет вообще:

    ImportError: cannot import name 'contracts' from 'agent'
    (C:/Users/rdrr/Downloads/browser-use-perf-final/browser-use-main/agent.py)

`import agent` привёл в СОСЕДНИЙ проект, лежащий в Downloads. Наш код не
исполнялся: 180 тестов не собрались, и BUILD.txt записал 1623 вместо 1803,
то есть метка сборки соврала числом, а не молчанием.

ПОЧЕМУ ЭТОГО НЕ БЫЛО ВИДНО У МЕНЯ. В песочнице рядом нет чужого agent.py,
и папка без __init__.py притворялась пакетом успешно. Дефект жил в проекте
с самого начала и был невидим на той машине, где идёт работа. Сторож,
который краснеет только на машине владельца, бесполезен — поэтому тесты
ниже устроены так, чтобы краснеть ВЕЗДЕ: они не ждут появления чужого
файла, а проверяют само свойство пакета.

ПОЧЕМУ НЕ «ПОПРАВИТЬ ПОРЯДОК ПУТЕЙ». Замерено опытом: обычный файл
agent.py побеждает namespace-папку agent/ и когда чужая папка стоит в
sys.path ПОСЛЕ корня проекта. Порядок не спасает. Спасает только
настоящий пакет — папка с __init__.py.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Папки, которые обязаны быть НАСТОЯЩИМИ пакетами. Список не выведен из
# файловой системы нарочно: если завтра кто-то заведёт пятую папку и
# забудет __init__.py, автоматический список молча примет её как есть, и
# сторож перестанет стеречь именно новый случай.
MUST_BE_PACKAGES = ("agent", "core", "actions", "tools", "memory", "config")


def test_every_top_level_package_has_an_init_file():
    """Без __init__.py папка проигрывает одноимённому чужому файлу."""
    missing = [name for name in MUST_BE_PACKAGES
               if not (ROOT / name / "__init__.py").exists()]
    assert not missing, (
        f"папки без __init__.py: {missing}. Такую папку подменяет любой "
        f"одноимённый .py в пути поиска — 28.08.2026 её подменил чужой "
        f"проект из Downloads")


def test_our_packages_win_against_a_stranger_with_the_same_name(tmp_path):
    """Живая проба подмены: кладём чужой agent.py и требуем НАШ пакет.

    Это единственный тест здесь, который воспроизводит поломку владельца
    целиком, а не её признак. Он краснеет и на Linux: чужой файл создаём
    сами, а не ждём, пока он окажется рядом.

    Отдельный процесс нужен потому, что в текущем `agent` уже загружен, и
    внутри этого же процесса подмену не показать — sys.modules помнит.
    """
    rogue = tmp_path / "rogue"
    rogue.mkdir()
    (rogue / "agent.py").write_text("ЧУЖОЙ = 1\n", encoding="utf-8")

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import agent\n"
        "print(agent.__file__ or '')\n",
        encoding="utf-8")

    # Чужая папка идёт ПОСЛЕ корня — самый выгодный для нас порядок.
    # Если подмена случится даже так, значит пакет ненастоящий.
    env_path = str(ROOT) + ";" + str(rogue) if sys.platform == "win32" \
        else str(ROOT) + ":" + str(rogue)
    out = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True, text=True, timeout=60,
        cwd=str(tmp_path),                    # НЕ из корня: иначе '' в путь
        env={**_clean_env(), "PYTHONPATH": env_path})

    got = (out.stdout or "").strip()
    assert got, f"проба ничего не сказала: {out.stderr[:400]}"
    assert "rogue" not in got, (
        f"наш пакет `agent` подменён чужим файлом: {got}. Ровно это "
        f"случилось на машине владельца 28.08.2026")
    assert str(ROOT) in got, f"взялся какой-то третий agent: {got}"


def _clean_env() -> dict:
    """Окружение без PYTHONPATH, чтобы проба мерила только то, что задали."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return env


def test_the_init_files_stay_empty_of_imports():
    """Пустые нарочно: импорт здесь исполнялся бы при обращении к ЛЮБОМУ
    модулю пакета, то есть на каждом старте. Бюджет быстрых путей 3-5 с,
    и платить из него за удобство мы не согласились."""
    for name in ("agent", "core", "actions", "tools"):
        body = (ROOT / name / "__init__.py").read_text(encoding="utf-8")
        code = [l for l in body.splitlines()
                if l.strip() and not l.strip().startswith("#")]
        # Строки внутри тройных кавычек — это docstring, не код.
        joined = "\n".join(code)
        without_doc = joined.split('"""')
        outside = "".join(without_doc[0::2]) if len(without_doc) > 1 else joined
        assert "import" not in outside, (
            f"{name}/__init__.py исполняет импорты — это плата временем "
            f"на каждом старте")


def test_the_reason_is_written_down_next_to_the_fix():
    """Пустой файл без объяснения удалят как лишний. Проверено историей
    проекта: колонка ms прожила полтора блока пустой именно потому, что
    рядом с ней не было ни строчки о том, зачем она."""
    for name in ("agent", "core", "actions", "tools"):
        body = (ROOT / name / "__init__.py").read_text(encoding="utf-8")
        assert "НЕ УДАЛЯТЬ" in body, f"{name}/__init__.py без предупреждения"
        assert "namespace" in body, f"{name}/__init__.py без причины"
