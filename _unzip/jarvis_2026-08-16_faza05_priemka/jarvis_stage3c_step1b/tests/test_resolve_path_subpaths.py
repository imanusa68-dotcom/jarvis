"""
Issue 018 — _resolve_path must resolve shortcut+subpath under the REAL known
folder and must never resolve a relative path against the app's working dir
(which silently buried files inside the project tree).

The known-folder lookup is stubbed, so these are deterministic.

Run:  python -m pytest tests/test_resolve_path_subpaths.py -q
"""

import os
from pathlib import Path

import actions.file_controller as fc

_DESK = Path(r"C:\Users\u\Desktop")
_DOCS = Path(r"C:\Users\u\OneDrive\Документы")


def _folders(monkeypatch):
    def fake(name):
        return {"desktop": _DESK, "documents": _DOCS}.get((name or "").lower(),
                                                          Path(r"C:\Users\u") / (name or "").capitalize())
    monkeypatch.setattr(fc, "_known_folder", fake)


def test_shortcut_plus_subpath_resolves_under_known_folder(monkeypatch):
    _folders(monkeypatch)
    assert fc._resolve_path("documents/Мои сочинения") == _DOCS / "Мои сочинения"
    assert fc._resolve_path("documents\\Мои сочинения") == _DOCS / "Мои сочинения"
    assert fc._resolve_path("desktop/proj/sub") == _DESK / "proj" / "sub"


def test_bare_relative_anchors_to_desktop_not_cwd(monkeypatch):
    _folders(monkeypatch)
    r = fc._resolve_path("Мои сочинения")
    assert r == _DESK / "Мои сочинения"
    assert r.is_absolute()
    assert fc._resolve_path("JV") == _DESK / "JV"


def test_relative_never_resolves_under_project_cwd(monkeypatch):
    _folders(monkeypatch)
    for raw in ("Мои сочинения", "JV", "documents/Мои сочинения"):
        r = str(fc._resolve_path(raw))
        assert str(Path(os.getcwd())).lower() not in r.lower(), raw


def test_bare_shortcut_and_absolute_unchanged(monkeypatch):
    _folders(monkeypatch)
    assert fc._resolve_path("desktop") == _DESK
    ab = r"C:\some\abs\file.txt"
    assert fc._resolve_path(ab) == Path(ab)
    assert fc._resolve_path("home") == Path.home()
