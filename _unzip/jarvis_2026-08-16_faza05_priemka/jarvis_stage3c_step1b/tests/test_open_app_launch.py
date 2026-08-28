# -*- coding: utf-8 -*-
"""
Регрессионные тесты пост-смоук фиксов open_app / app_index (2026-07-19):

1. Русские алиасы в _normalize («блокнот» шёл в слепой GUI-поиск и открывал
   Edge с веб-поиском).
2. score(): однобуквенные токены имени («V» из Hyper-V, «K» из K-Lite) давали
   0.85 любому запросу — «блокнот» матчился на «Uninstall K-Lite Codec Pack».
3. Ярлыки-деинсталляторы исключены из кандидатов резолвера.
4. Прямой запуск не нашёл exe → фолбэк в индекс меню «Пуск», а не GUI-поиск.

Запуск: uv run pytest tests/test_open_app_launch.py -q
"""

import pytest

import actions.open_app as oa
from core.awareness import _app_index as ai


# ── 1. Русские алиасы ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("блокнот", "notepad.exe"),
    ("Блокнот", "notepad.exe"),
    ("калькулятор", "calc.exe"),
    ("проводник", "explorer.exe"),
    ("настройки", "ms-settings:"),
    ("vs code", "code"),
    ("Notepad", "notepad.exe"),
])
def test_normalize_russian_aliases(raw, expected):
    assert oa._normalize(raw) == expected


# ── 2. Скоринг: однобуквенные токены не дают substring-бонус ─────────────────

def test_score_single_letter_token_no_bonus():
    # «V» из «Hyper-V Manager» содержится в «vs code» — раньше 0.85
    assert ai.score("vs code", "Hyper-V Manager") < 0.7
    # «K» из «K-Lite» содержится в транслите «bloknot» — раньше 0.85
    assert ai.score("блокнот", "Uninstall K-Lite Codec Pack") < 0.6
    # Легитимный substring-матч жив
    assert ai.score("vs code", "Visual Studio Code") >= 0.85
    assert ai.score("photoshop", "Adobe Photoshop 2024") >= 0.85


# ── 3. Деинсталляторы вне кандидатов ─────────────────────────────────────────

def test_uninstallers_filtered_from_ranking(monkeypatch):
    shortcuts = [
        {"name": "Uninstall K-Lite Codec Pack", "path": "C:/u.lnk"},
        {"name": "CapCut Setup", "path": "C:/s.lnk"},
        {"name": "Telegram Desktop", "path": "C:/t.lnk"},
    ]
    monkeypatch.setattr(ai, "list_shortcuts", lambda: shortcuts)
    names = [sc["name"] for _, sc in ai._ranked("telegram")]
    assert names == ["Telegram Desktop"]
    # даже точный запрос не запускает деинсталлятор голосом
    assert ai.best_match("uninstall k-lite codec pack") is None


# ── 4. Фолбэк: прямой запуск не нашёл → индекс Пуска, не GUI ─────────────────

def test_direct_launch_fallback_to_start_menu_index(monkeypatch):
    calls = {}

    def fake_startfile(name):
        raise OSError("not found")

    def fake_index(name, player=None):
        calls["index"] = name
        return "Открываю X."

    monkeypatch.setattr(oa.os, "startfile", fake_startfile, raising=False)
    monkeypatch.setattr(oa, "_open_installed_by_name", fake_index)
    r = oa.open_app(parameters={"app_name": "блокнот"})
    assert calls.get("index") == "блокнот"
    assert "Открываю" in r


def test_direct_launch_success_no_fallback(monkeypatch):
    launched = {}
    monkeypatch.setattr(oa.os, "startfile",
                        lambda name: launched.setdefault("name", name), raising=False)
    monkeypatch.setattr(oa, "_open_installed_by_name",
                        lambda *a, **k: pytest.fail("индекс не должен вызываться при успехе"))
    r = oa.open_app(parameters={"app_name": "блокнот"})
    assert launched["name"] == "notepad.exe"
    assert "Opened" in r
