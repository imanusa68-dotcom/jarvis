# tests/test_home_guard_step35.py
"""Шаг 35.3 — сторож настоящего дома и дом на каждый тест.

Сторож снимает слепок ~/.jarvis до прогона и сверяет после. Если хоть
один файл или папка изменились — красным становится весь прогон, потому
что виноват может быть любой тест из тысячи, и назвать его заранее
нельзя. Требования к сторожу: только чтение, дом не создавать, при
отсутствии дома молчать.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import conftest as C  # noqa: E402
from core import store  # noqa: E402


def _tmp():
    return Path(tempfile.mkdtemp(prefix="jv-guard-35-")).resolve()


def test_the_guard_sees_a_new_file():
    d = _tmp()
    before = C.home_fingerprint(d)
    (d / "jarvis.db").write_bytes(b"x")
    assert C._diff_fingerprints(before, C.home_fingerprint(d)) == ["jarvis.db"]


def test_the_guard_sees_a_touched_folder():
    # Именно так вскрылась папка staging: изменилось время папки.
    d = _tmp()
    (d / "staging").mkdir()
    before = C.home_fingerprint(d)
    later = int(time.time()) + 60
    os.utime(d / "staging", (later, later))
    assert C._diff_fingerprints(before, C.home_fingerprint(d)) == ["staging"]


def test_the_guard_stays_quiet_when_nothing_changed():
    d = _tmp()
    (d / "jarvis.db").write_bytes(b"x")
    (d / "logs").mkdir()
    before = C.home_fingerprint(d)
    assert C._diff_fingerprints(before, C.home_fingerprint(d)) == []


def test_a_missing_home_is_not_an_error_and_is_not_created():
    ghost = _tmp() / "net-takoy-papki"
    assert C.home_fingerprint(ghost) == {}
    assert not ghost.exists(), "сторож создал дом, который сторожит"


def test_this_very_test_lives_in_a_temporary_home():
    # Доказательство, что подмена дома на тест действительно работает.
    seam = os.environ.get("JARVIS_STATE_DIR", "").strip()
    assert seam, "дом на тест не выставлен — фикстура шага 35 не работает"
    real = (Path.home() / ".jarvis").resolve()
    assert Path(seam).resolve() != real
    assert Path(store.app_dir()).resolve() != real
