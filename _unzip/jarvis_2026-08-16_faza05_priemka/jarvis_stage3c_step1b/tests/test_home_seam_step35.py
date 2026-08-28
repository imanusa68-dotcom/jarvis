# tests/test_home_seam_step35.py
"""Шаг 35.1 — все двери в дом заперты одним ключом.

Почему этот файл появился. Прогон тестов 15.08 при выставленном
JARVIS_STATE_DIR не тронул ни базу, ни STATE.json — но папка
~/.jarvis/staging всё равно получила новое время. Чтение кода назвало
виновного: core/staging.py вычислял дом через Path.home() и переменную
не читал вообще. Рядом нашлась вторая такая же дверь —
actions/file_controller._backup_dir.

Сторож проверяет ПОВЕДЕНИЕ, а не слова в коде: четыре раза за проект
сторож, читавший текст файла, запрещал объяснять решения в комментариях.

Запуск: python -m pytest -q (из корня).
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import staging as S  # noqa: E402
from core import store  # noqa: E402
from core.safe_json import state_dir  # noqa: E402
from actions import file_controller as fc  # noqa: E402

ENV = "JARVIS_STATE_DIR"


class _Home:
    """Временный дом на время одной проверки."""

    def __enter__(self):
        self.saved = os.environ.get(ENV)
        self.dir = Path(tempfile.mkdtemp(prefix="jv-seam-35-")).resolve()
        os.environ[ENV] = str(self.dir)
        return self.dir

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = self.saved
        return False


class _NoHome:
    """Ни одной подмены: так дом видит живой запуск у владельца."""

    def __enter__(self):
        self.saved = os.environ.pop(ENV, None)
        return None

    def __exit__(self, *exc):
        if self.saved is not None:
            os.environ[ENV] = self.saved
        return False


def test_staging_follows_the_seam():
    with _Home() as home:
        assert Path(S.Staging().root).resolve() == home / "staging"


def test_legacy_backups_follow_the_seam():
    with _Home() as home:
        p = Path(S.legacy_backup_path(Path.home() / "Downloads" / "report.txt"))
        assert home in p.resolve().parents, p


def test_file_controller_backups_follow_the_seam():
    with _Home() as home:
        assert Path(fc._backup_dir()).resolve() == home / "backups"


def test_all_the_doors_agree_with_the_base():
    with _Home() as home:
        assert Path(S._app_dir()).resolve() == home
        assert Path(store.app_dir()).resolve() == home
        assert Path(state_dir()).resolve() == home


def test_the_seam_is_read_at_call_time_not_at_import():
    with _Home() as first:
        a = Path(S.Staging().root).resolve()
        b1 = Path(fc._backup_dir()).resolve()
    with _Home() as second:
        a2 = Path(S.Staging().root).resolve()
        b2 = Path(fc._backup_dir()).resolve()
    assert first != second
    assert a != a2, "путь запомнился при импорте — грабли шага 31 вернулись"
    assert b1 != b2, "путь запомнился при импорте — грабли шага 31 вернулись"


def test_without_the_seam_the_real_home_stays_the_default():
    # Живой запуск у владельца обязан работать ровно как раньше.
    with _NoHome():
        real = Path.home() / ".jarvis"
        assert Path(S.Staging().root) == real / "staging"
        assert Path(fc._backup_dir()) == real / "backups"
        assert Path(S._app_dir()) == real


def test_asking_for_the_path_creates_nothing():
    # Сторож дома покраснел бы от собственного дыхания, если бы
    # спрашивать путь означало создавать папку.
    with _Home() as home:
        S.Staging()
        fc._backup_dir()
        S._app_dir()
        assert not (home / "staging").exists()
        assert not (home / "backups").exists()
