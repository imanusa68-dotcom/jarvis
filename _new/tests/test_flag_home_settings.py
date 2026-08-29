# -*- coding: utf-8 -*-
"""
Сторож шага 2 фазы 0: настройки живут ВНЕ папки проекта.

Почему этот файл вообще есть. Пока флаг лежал в config/settings.json, каждая
распаковка свежего zip молча возвращала значение по умолчанию: человек думал,
что выключил, а код считал, что включено. Одного правильного кода здесь мало:
без сторожа любая будущая правка может тихо вернуть настройки внутрь проекта,
и заметит это владелец только после следующего обновления — то есть поздно.

Каждый тест работает в песочнице: своя временная «домашняя» папка через
$JARVIS_STATE_DIR и свой временный «проект». Настоящие ~/.jarvis и папка
проекта не трогаются ни на чтение, ни на запись.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import loader                       # noqa: E402
from core.safe_json import STATE_DIR_ENV        # noqa: E402


class _Sandbox:
    """Временный дом + временный проект, полностью возвращаемые на выходе."""

    def __enter__(self) -> "_Sandbox":
        self.tmp = Path(tempfile.mkdtemp(prefix="jv_settings_"))
        self.home = self.tmp / "home"                       # намеренно не создаём
        self.project_settings = self.tmp / "project" / "config" / "settings.json"
        self.project_settings.parent.mkdir(parents=True, exist_ok=True)

        self._env = os.environ.get(STATE_DIR_ENV)
        self._project = loader.PROJECT_SETTINGS_FILE
        self._override = loader.SETTINGS_FILE

        os.environ[STATE_DIR_ENV] = str(self.home)
        loader.PROJECT_SETTINGS_FILE = self.project_settings
        loader.SETTINGS_FILE = None                         # боевой путь, не подмена
        return self

    def __exit__(self, *exc) -> bool:
        loader.PROJECT_SETTINGS_FILE = self._project
        loader.SETTINGS_FILE = self._override
        if self._env is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    @property
    def home_settings(self) -> Path:
        return self.home / "settings.json"

    def write_project(self, data: dict) -> None:
        self.project_settings.write_text(json.dumps(data, ensure_ascii=False),
                                         encoding="utf-8")

    def write_home(self, data: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.home_settings.write_text(json.dumps(data, ensure_ascii=False),
                                      encoding="utf-8")


def test_settings_live_outside_the_project():
    """Запись настройки уходит в дом, а не в папку сборки."""
    with _Sandbox() as box:
        loader.set_setting("timezone", "Europe/Moscow")

        assert loader.settings_file() == box.home_settings
        assert box.home_settings.exists(), "домашний файл настроек не создан"
        assert not box.project_settings.exists(), \
            "запись ушла в папку проекта — это регресс шага 2"

        saved = json.loads(box.home_settings.read_text(encoding="utf-8"))
        assert saved["timezone"] == "Europe/Moscow"
        assert loader.get_setting("timezone") == "Europe/Moscow"


def test_home_wins_over_project_file():
    """Старый файл читается, пока нет домашнего; потом дом главнее."""
    with _Sandbox() as box:
        box.write_project({"timezone": "Asia/Tokyo", "camera_index": 3})
        assert loader.get_setting("timezone") == "Asia/Tokyo"
        assert loader.get_setting("camera_index") == 3

        box.write_home({"timezone": "Europe/Moscow"})
        assert loader.get_setting("timezone") == "Europe/Moscow"
        # Дом — единственный источник, а не слой поверх старого: два источника
        # сразу вернули бы ту самую неопределённость, ради которой всё затевалось.
        assert loader.get_setting("camera_index") is None


def test_reading_settings_creates_nothing():
    """Чтение флагов не создаёт ни файла, ни папки."""
    from core import feature_flags as ff

    with _Sandbox() as box:
        assert ff.agents_enabled() is False           # выключены до фазы 2
        assert ff.fileops_enabled() is True
        assert ff.durable_consent_enabled() is True

        assert not box.home.exists(), "чтение создало домашнюю папку"
        assert not box.home_settings.exists(), "чтение создало файл настроек"
        assert not box.project_settings.exists()


def test_agents_flag_defaults_to_off_and_persists():
    """Выключатель агентов есть, выключен по умолчанию, живёт в доме."""
    from core import feature_flags as ff

    with _Sandbox() as box:
        assert ff.AGENTS_SETTING == "JARVIS_AGENTS"
        assert ff.AGENTS_DEFAULT is False
        assert ff.agents_enabled() is False

        ff.set_agents_enabled(True)
        assert ff.agents_enabled() is True
        saved = json.loads(box.home_settings.read_text(encoding="utf-8"))
        assert saved["JARVIS_AGENTS"] is True
        assert not box.project_settings.exists()

        ff.set_agents_enabled(False)
        assert ff.agents_enabled() is False


def test_project_settings_are_imported_once_and_original_kept():
    """Переезд старого файла: один раз, без удаления оригинала."""
    with _Sandbox() as box:
        box.write_project({"timezone": "Asia/Tokyo", "fileops_enabled": False})

        assert loader.migrate_project_settings() is True
        assert box.home_settings.exists()
        moved = json.loads(box.home_settings.read_text(encoding="utf-8"))
        assert moved["timezone"] == "Asia/Tokyo"
        assert moved["fileops_enabled"] is False

        # Оригинал на месте и не тронут: откат должен оставаться возможным.
        assert box.project_settings.exists()
        original = json.loads(box.project_settings.read_text(encoding="utf-8"))
        assert original["timezone"] == "Asia/Tokyo"

        marker = box.project_settings.with_name("settings.json.imported")
        assert marker.exists(), "нет метки — перенос повторится на следующем старте"

        # Второй старт ничего не перезаписывает.
        loader.set_setting("timezone", "Europe/Moscow")
        assert loader.migrate_project_settings() is False
        assert loader.get_setting("timezone") == "Europe/Moscow"


if __name__ == "__main__":
    _tests = [value for name, value in sorted(globals().items())
              if name.startswith("test_") and callable(value)]
    for _fn in _tests:
        _fn()
        print(f"OK   {_fn.__name__}")
    print(f"OK: {len(_tests)} passed (standalone)")
