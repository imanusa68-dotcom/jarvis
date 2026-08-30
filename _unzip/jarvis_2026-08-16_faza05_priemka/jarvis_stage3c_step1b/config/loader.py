# -*- coding: utf-8 -*-
"""
Единственная точка доступа к конфигурации и секретам (этап 0.5 миграции).

Раскладка:
  config/secrets.json      — API-ключи (в .gitignore, никогда не в репозитории)
  ~/.jarvis/settings.json  — несекретные настройки: timezone, camera_index, флаги

Одна дверь к ключу (фаза 0, шаг 1, 2026-08-05):
  Легаси-файл config/api_keys.json и fallback на него УДАЛЕНЫ, а не выключены
  (срок перехода истёк 2026-08-02). Ключ попадает в проект ровно одним путём:
  окно «◈ INITIALISATION REQUIRED» → set_secret() → config/secrets.json.
  Возвращать второй путь нельзя: пока он существовал, мёртвый ключ из
  api_keys.json проходил как валидный, is_configured() врал True, и владелец
  видел невнятную ошибку вместо формы ввода. Сторож: tests/test_config_loader.py.

Одна дверь к настройкам (фаза 0, шаг 2, 2026-08-06):
  Настройки уехали из папки проекта в ~/.jarvis/settings.json — туда же, где
  уже живут база, память и личность (Р-9). Причину записал сам код в
  core/feature_flags.py: пока флаг лежал внутри проекта, каждая распаковка
  свежего zip молча возвращала значение по умолчанию, и «выключатель» зависел
  от того, вспомнит ли человек команду в правильной папке.
  Старый config/settings.json остался ровно одним — источником разового
  переноса: читается, пока домашнего файла нет, и переезжает один раз при
  старте. Сторож: tests/test_flag_home_settings.py.

Правила:
  * Никто в проекте не читает эти файлы напрямую — только через этот модуль
    (grep-гейт этапа 0.7).
  * Запись — только read-merge-write + атомарная замена (temp + os.replace):
    закрывает баг деструктивной перезаписи из ui._save_api_keys и гонку
    двух писателей.
  * Отсутствие ключа — явная ConfigError, а не сырой KeyError из глубины тула.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

__all__ = [
    "ConfigError", "get_base_dir", "get_api_key", "get_secret", "set_secret",
    "get_setting", "set_setting", "settings_file", "migrate_project_settings",
    "is_configured", "get_model", "get_limit",
]


class ConfigError(RuntimeError):
    """Конфигурация отсутствует или неполна."""


def get_base_dir() -> Path:
    """Корень проекта; поддержка PyInstaller-заморозки сохранена из прототипа."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_DIR    = get_base_dir() / "config"
SECRETS_FILE  = CONFIG_DIR / "secrets.json"
REGISTRY_FILE = CONFIG_DIR / "registry.yaml"
TASK_TYPES_FILE = CONFIG_DIR / "task_types.yaml"

# Старое место настроек — внутри папки проекта. Осталось ровно одним:
# источником разового переноса. Ничего нового сюда не пишется никогда.
PROJECT_SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Подмена пути настроек для тестов; None → ~/.jarvis/settings.json.
# Путь вычисляется в settings_file() при каждом обращении, а не один раз при
# импорте: иначе JARVIS_STATE_DIR, выставленный тестом уже после импорта, не
# действует — и прогон тестов пишет в настоящую домашнюю папку владельца.
SETTINGS_FILE = None


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _secrets() -> dict:
    """Секреты. Отсутствующий файл — это пустой словарь, а не второй путь."""
    return _read_json(SECRETS_FILE)


def settings_file() -> Path:
    """Единственное место настроек: ~/.jarvis/settings.json.

    Дом переживает распаковку нового zip, папка проекта — нет. Путь берётся у
    core.safe_json: там же лежат база, память и личность, и переменная
    окружения JARVIS_STATE_DIR остаётся одна на всё состояние, а не вторая
    своя.
    """
    if SETTINGS_FILE is not None:
        return Path(SETTINGS_FILE)
    from core.safe_json import state_path
    return state_path("settings.json")


def _settings() -> dict:
    """Настройки на чтение. Не создаёт ни файла, ни папки — принципиально.

    Прогон тестов частично идёт по настоящему домашнему пути (см.
    tests/test_stage3b5_master.py, где JARVIS_STATE_DIR намеренно стирается).
    Если бы чтение флага что-то создавало или дописывало, один прогон мог бы
    молча переключить владельцу режим подтверждений.

    Порядок: домашний файл главнее всего. Пока его нет — читаем старый файл в
    проекте, чтобы значения не пропали в промежутке между обновлением и первым
    запуском. Именно читаем: перенос — отдельное явное действие
    migrate_project_settings().
    """
    from core.safe_json import load_json_safe
    path = settings_file()
    if path.exists():
        return load_json_safe(path, dict, label="Settings")
    if SETTINGS_FILE is None and PROJECT_SETTINGS_FILE.exists():
        return _read_json(PROJECT_SETTINGS_FILE)
    return {}


# ── Публичный API ────────────────────────────────────────────────────────────

def get_api_key() -> str:
    """Ключ Gemini. Бросает ConfigError с внятным сообщением, если не настроен."""
    key = (_secrets().get("gemini_api_key") or "").strip()
    if not key:
        raise ConfigError(
            "gemini_api_key не найден: введите ключ в окне "
            "«◈ INITIALISATION REQUIRED» при запуске — оно сохранит ключ в "
            "config/secrets.json само. Править файл руками не нужно."
        )
    return key


def get_secret(name: str, default=None):
    return _secrets().get(name, default)


def set_secret(name: str, value) -> None:
    data = _read_json(SECRETS_FILE)
    data[name] = value
    _write_json_atomic(SECRETS_FILE, data)


def get_setting(name: str, default=None):
    return _settings().get(name, default)


def set_setting(name: str, value) -> None:
    """Пишет настройку в ~/.jarvis/settings.json: read-merge-write, атомарно.

    Слияние идёт поверх _settings(), поэтому первая же запись забирает с собой
    старые значения из папки проекта — они не теряются, даже если явный перенос
    ни разу не вызывали.

    БЛОК 9: ЧТЕНИЕ И ЗАПИСЬ ТЕПЕРЬ ПОД ОДНИМ ЗАМКОМ, и это исправление
    обещания, которое здесь было написано, но не выполнялось. Шапка модуля
    утверждала, что атомарная замена «закрывает гонку двух писателей». Не
    закрывает: атомарная замена бережёт ФАЙЛ от разрыва, но не бережёт ПРАВКУ.
    Два потока читали одни настройки, каждый добавлял своё, и последний затирал
    чужое — переключённый тумблер молча возвращался назад.

    Проверено грепом 21.08.2026: замка вокруг этой записи не было ни здесь, ни
    у вызывающих. Теперь он один и живёт в кассе состояния.
    """
    from core.safe_json import update as safe_update

    def change(data: dict) -> dict:
        # Основа — _settings(): она подхватывает старые значения из папки
        # проекта. Дом главнее, поэтому накладывается сверху.
        merged = _settings()
        merged.update(data or {})
        merged[name] = value
        return merged

    safe_update(settings_file(), change, dict, label="Settings")


def migrate_project_settings() -> bool:
    """Разовый переезд config/settings.json → ~/.jarvis/settings.json.

    Механизм не новый: им уже перевезены память и счётчик вызовов
    (core.safe_json.import_legacy_once). Его четыре правила и есть причина не
    писать своё: не сработает, если домашний файл уже есть; не сработает второй
    раз (метка «<имя>.imported» рядом со старым файлом); никогда не удаляет и не
    меняет оригинал; откажется переносить пустое или битое.

    Вызывается один раз при старте из main.run(). Возвращает True, только если
    перенос действительно состоялся.
    """
    if SETTINGS_FILE is not None:      # тестовая подмена — проект не трогаем
        return False
    from core.safe_json import import_legacy_once
    return import_legacy_once(PROJECT_SETTINGS_FILE, settings_file(),
                              label="Settings")


def is_configured() -> bool:
    try:
        get_api_key()
        return True
    except ConfigError:
        return False


def _registry() -> dict:
    """Разобранный config/registry.yaml. Один читатель на весь модуль."""
    import yaml
    try:
        return yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Не удалось прочитать config/registry.yaml: {e}") from e


def task_types() -> dict:
    """Закрытый список типов задач из config/task_types.yaml (блок 4).

    Один читатель на весь проект — как у реестра моделей. Отсутствие файла
    это ConfigError, а не пустой список: пустой список молча разрешил бы
    любой тип, а закрытый список, который ничего не закрывает, вреднее
    отсутствующего.
    """
    import yaml
    try:
        data = yaml.safe_load(TASK_TYPES_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(
            f"Не удалось прочитать config/task_types.yaml: {e}") from e
    types = data.get("types")
    if not isinstance(types, dict) or not types:
        raise ConfigError(
            "config/task_types.yaml: раздел 'types' пуст или отсутствует. "
            "Закрытый список, который ничего не закрывает, опаснее отсутствия")
    return types


def get_model(role: str) -> str:
    """Имя модели для логической роли из config/registry.yaml.

    Единственный легальный источник имён моделей в проекте (grep-гейт этапа 0.7).
    """
    roles = _registry().get("roles") or {}
    if role not in roles:
        raise ConfigError(
            f"Роль '{role}' не найдена в config/registry.yaml "
            f"(есть: {', '.join(sorted(roles))})"
        )
    return str(roles[role])


def get_limit(role: str, name: str, default=None):
    """Числовой предел роли из раздела limits в config/registry.yaml.

    Предел принадлежит модели, а не владельцу: при смене модели он меняется
    той же строкой, поэтому лежит рядом с ней, а не в ~/.jarvis/settings.json.
    Отсутствие предела — это default, а не ошибка: он есть не у каждой роли.
    """
    role_limits = (_registry().get("limits") or {}).get(role)
    if not isinstance(role_limits, dict) or name not in role_limits:
        return default
    return role_limits[name]
