# -*- coding: utf-8 -*-
# tests/test_launchers_step34.py -- сторожа запускателей и зависимостей (шаг 34.3).
#
# Два .cmd-файла -- единственное место, где окружение прогона чинится снаружи.
# Сам набор тестов потоки НЕ настраивает (иначе он перестанет проверять
# то, что будет у владельца), поэтому запускатели проверяются как текст.
#
# ВАЖНО ПРО ЗАПРЕТЫ: запрет касается ВЫЗОВА команды, а не слова в комментарии.
# Сторож, читающий комментарии, запрещает ОБЪЯСНЯТЬ решения -- а без
# объяснений через месяц никто не помнит, почему так. Поэтому строки
# rem и :: выбрасываются перед проверкой -- точно так же, как у доктора.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "run_tests.cmd"
FAST = ROOT / "run_tests_fast.cmd"
LAUNCHERS = (FULL, FAST)
OLD_FILES = ("requirements" + ".txt", "setup" + ".py")


def _raw(path):
    return path.read_bytes()


def _text(path):
    return _raw(path).decode("utf-8", errors="replace")


def _commands(path):
    """Только исполняемые строки: без rem и :: ."""
    kept = []
    for line in _text(path).splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low == "rem" or low.startswith("rem ") or stripped.startswith("::"):
            continue
        kept.append(line)
    return "\n".join(kept)


# -- Запускатели ----------------------------------------------------------

def test_both_launchers_exist():
    for path in LAUNCHERS:
        assert path.exists(), "нет запускателя " + path.name


def test_launchers_are_pure_latin():
    # Главное правило вечера: cmd.exe читает .cmd в кодовой странице 866,
    # и русский текст внутри превращается в мусор или в чужую команду.
    for path in LAUNCHERS:
        try:
            _raw(path).decode("ascii")
        except UnicodeDecodeError as exc:
            raise AssertionError("в " + path.name + " пролезла не-латиница: " + str(exc))


def test_launchers_are_crlf():
    # Файл .cmd с одиночным \n Windows исполняет непредсказуемо.
    for path in LAUNCHERS:
        raw = _raw(path)
        assert raw.count(b"\n") == raw.count(b"\r\n"), path.name + ": есть перевод строки без CR"
        assert raw.count(b"\r\n") >= 5, path.name + ": подозрительно короткий файл"


def test_launchers_work_from_any_folder():
    for path in LAUNCHERS:
        text = _commands(path)
        assert "setlocal" in text, path.name + ": нет setlocal -- переменные утекут в консоль владельца"
        assert 'cd /d "%~dp0"' in text, path.name + ": не переходит в свою папку"


def test_launchers_fix_the_broken_channel():
    for path in LAUNCHERS:
        text = _commands(path)
        assert "set PYTHONUTF8=1" in text, path.name + ": не чинит перенаправление в файл"
        assert "set PYTHONIOENCODING=utf-8" in text, path.name + ": не задаёт кодировку потоков"


def test_launchers_never_touch_the_owners_console():
    # Живая консоль владельца исправна (utf-8). Менять её кодовую страницу --
    # чинить то, что не сломано, и ломать то, что работало. Смотрим только
    # на исполняемые строки: в комментарии это слово разрешено и даже нужно.
    for path in LAUNCHERS:
        assert "chcp" not in _commands(path), path.name + ": зовёт chcp и меняет консоль владельца"


def test_the_reason_is_written_down_in_the_launchers():
    # Обратная сторона предыдущего сторожа: решение должно быть объяснено
    # внутри файла, а не только в дневнике.
    for path in LAUNCHERS:
        assert "rem" in _text(path).lower(), path.name + ": ни одного пояснения в файле"


def test_launchers_pass_the_verdict_outside():
    # Без этого красный прогон выглядит для любой автоматики как успех.
    for path in LAUNCHERS:
        text = _commands(path)
        assert "%ERRORLEVEL%" in text, path.name + ": не читает код возврата pytest"
        assert "exit /b" in text, path.name + ": не отдаёт код возврата наружу"
        assert "python -m pytest" in text, path.name + ": запускает не то"


def test_the_fast_launcher_shows_the_slowest():
    assert "--durations" in _commands(FAST), "быстрый запускатель не показывает самые медленные тесты"
    assert "--durations" not in _commands(FULL), "полный запускатель зашумлён замерами"


# -- Зависимости ----------------------------------------------------------

def test_python_version_is_a_floor_not_a_pin():
    # "~=3.12.0" запрещает 3.13 и однажды остановит проект на ровном месте.
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    assert 'requires-python = ">=3.12"' in text, "версия Python всё ещё прибита гвоздями"


def test_pyproject_keeps_its_line_endings():
    raw = (ROOT / "pyproject.toml").read_bytes()
    assert raw.count(b"\r\n") > 30, "у pyproject.toml пропали родные CRLF"


def test_the_old_dependency_files_are_gone():
    for name in OLD_FILES:
        assert not (ROOT / name).exists(), "старый файл зависимостей вернулся: " + name


def test_nothing_alive_still_points_at_the_old_files():
    # Старые документы в docs/ -- история, их не трогаем. А живые файлы
    # и readme не имеют права советовать то, чего больше нет.
    suspects = []
    for path in sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.cmd")) + sorted(ROOT.glob("*.toml")):
        if path.name.startswith(("apply_step", "doc_step", "fix_step")):
            continue
        suspects.append(path)
    for folder in ("core", "agent", "config", "memory", "tools", "actions"):
        base = ROOT / folder
        if base.exists():
            suspects.extend(sorted(base.rglob("*.py")))
    readme = ROOT / "readme.md"
    if readme.exists():
        suspects.append(readme)

    guilty = []
    for path in suspects:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in OLD_FILES:
            if name in text:
                guilty.append(path.name + " -> " + name)
    assert not guilty, "живые файлы всё ещё зовут удалённое: " + str(guilty)


if __name__ == "__main__":
    failed = 0
    total = 0
    for name in sorted(globals()):
        if not name.startswith("test_"):
            continue
        total += 1
        try:
            globals()[name]()
            print("OK   " + name)
        except Exception as exc:
            failed += 1
            print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("итог: " + str(total - failed) + " зелёных, " + str(failed) + " красных")
    sys.exit(1 if failed else 0)
