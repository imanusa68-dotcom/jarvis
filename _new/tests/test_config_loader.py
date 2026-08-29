# -*- coding: utf-8 -*-
"""
Фаза 0, шаг 1 — «одна дверь к ключу».

Два сторожа. Оба за ноль токенов, без сети и без реальной базы:

  1. test_setup_screen_when_key_absent — решение «показать окно ввода ключа»
     принимается по единственному источнику (config/secrets.json). Пока рядом
     жил config/api_keys.json с fallback-ом, мёртвый ключ из него проходил как
     валидный, is_configured() возвращал True, и владелец получал невнятную
     ошибку вместо формы «◈ INITIALISATION REQUIRED» (грабля №20).
     Тест воспроизводит именно эту ловушку и требует False.

  2. test_no_key_literal_anywhere — в дереве проекта нет литерала живого ключа
     Google. Шаблон собирается в рантайме из кусков, поэтому тест не находит
     сам себя (грабля №1); папка tests/ в обход не входит вообще.

Запуск:  python -m pytest tests/test_config_loader.py -q
или:     python tests/test_config_loader.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import loader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ── 1. Ключа нет → форма ввода, а не подхват старого файла ───────────────────

def test_setup_screen_when_key_absent():
    """Единственный источник ключа — secrets.json; api_keys.json не считается."""
    # Старый путь должен быть УДАЛЁН, а не выключен.
    assert not hasattr(loader, "LEGACY_FILE"), \
        "вернулся путь к config/api_keys.json — у ключа снова две двери"
    assert not hasattr(loader, "migrate_legacy"), \
        "вернулась миграция легаси-файла — она читала api_keys.json"

    tmp = Path(tempfile.mkdtemp(prefix="jv_cfg_"))
    saved = (loader.CONFIG_DIR, loader.SECRETS_FILE, loader.SETTINGS_FILE)
    loader.CONFIG_DIR = tmp
    loader.SECRETS_FILE = tmp / "secrets.json"
    loader.SETTINGS_FILE = tmp / "settings.json"
    try:
        # Приманка: рядом лежит старый файл с полноценным на вид ключом.
        decoy = "AI" + "za" + "S" * 35
        (tmp / "api_keys.json").write_text(
            json.dumps({"gemini_api_key": decoy}), encoding="utf-8")

        # (а) настоящего ключа нет → окно ввода. Приманку брать нельзя.
        assert loader.is_configured() is False
        assert not loader.SECRETS_FILE.exists()

        # (б) пустое значение — тоже «не настроен» (состояние архива 05.08.2026).
        loader.set_secret("gemini_api_key", "")
        assert loader.SECRETS_FILE.exists(), "запись должна создавать файл сама"
        assert loader.is_configured() is False

        # (в) окно сохранило ключ → путь открылся.
        loader.set_secret("gemini_api_key", "test-key-not-a-real-one")
        assert loader.get_api_key() == "test-key-not-a-real-one"
        assert loader.is_configured() is True

        # (г) запись одного секрета не стирает остальные (read-merge-write).
        loader.set_secret("google_cse_cx", "cx-1")
        assert loader.get_secret("google_cse_cx") == "cx-1"
        assert loader.get_api_key() == "test-key-not-a-real-one"

        # (д) приманка так и не тронута.
        assert loader.get_secret("gemini_api_key") != decoy
    finally:
        loader.CONFIG_DIR, loader.SECRETS_FILE, loader.SETTINGS_FILE = saved


# ── 2. Литерала ключа нет в дереве проекта ───────────────────────────────────

# Обход — по белому списку, а не по чёрному: у владельца рядом лежат .venv,
# __pycache__ и logs, и чёрный список рано или поздно что-то пропустит
# (а прогон разрастётся на минуты и покраснеет на чужих тестовых данных).
_SCAN_DIRS = ("actions", "agent", "config", "core", "memory", "tools", "docs")
_SCAN_EXTS = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt",
              ".cfg", ".ini", ".bat", ".ps1"}
_SKIP_DIRS = {"tests", "__pycache__", "logs", "node_modules",
              ".venv", "venv", ".git", ".pytest_cache"}
_MAX_BYTES = 2 * 1024 * 1024

# Единственное законное место ключа внутри проекта: шкатулка, куда пишет окно
# запуска. Она в .gitignore и в архивы попадать не должна.
# Исключение ПОСТОЯННОЕ, а не временное (решение владельца 2026-08-05):
# ключ НЕ уезжает в ~/.jarvis — «мне не трудно ввести его один раз после
# распаковки». Цена решения названа вслух и принята: архив никогда не
# собирается вместе с config/secrets.json, а на новом компьютере ключ вводится
# заново в окне «◈ INITIALISATION REQUIRED». Настройки — другое дело: они уехали
# в ~/.jarvis/settings.json на шаге 2, сторож — tests/test_flag_home_settings.py.
_ALLOWED = {Path("config") / "secrets.json"}


def _files_to_scan():
    for name in sorted(os.listdir(ROOT)):
        p = ROOT / name
        if p.is_file() and p.suffix.lower() in _SCAN_EXTS:
            yield p
    for d in _SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames
                           if x not in _SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() not in _SCAN_EXTS:
                    continue
                if p.relative_to(ROOT) in _ALLOWED:
                    continue
                try:
                    if p.stat().st_size > _MAX_BYTES:
                        continue
                except OSError:
                    continue
                yield p


def test_no_key_literal_anywhere():
    """Ни одного живого ключа Google в файлах проекта."""
    # Шаблон собран из кусков: иначе тест находит сам себя (грабля №1).
    pattern = re.compile("A" + "Iza" + "[0-9A-Za-z_-]{35}")
    hits = []
    for path in _files_to_scan():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pattern.finditer(text):
            line = text[:m.start()].count("\n") + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}")
    assert not hits, (
        "ключ Google найден в файлах проекта: " + ", ".join(hits) +
        ". Ключ вводится в окне запуска и живёт только в config/secrets.json."
    )


if __name__ == "__main__":
    test_setup_screen_when_key_absent()
    test_no_key_literal_anywhere()
    print("OK: 2 passed (standalone)")
