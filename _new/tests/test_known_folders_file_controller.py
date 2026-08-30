"""
Issue 006 — file_controller resolves folder shortcuts via the SAME OneDrive-aware
known-folders source that the awareness layer (watcher/resolver/open_path) uses, so
a file created/moved to "documents" lands where Explorer shows it — not in a naive
home-relative folder the user never opens.

Deterministic: the known-folder lookup is stubbed, so these don't depend on the
test machine's real redirection.

Run:  python -m pytest tests/test_known_folders_file_controller.py -q
"""

from pathlib import Path

import actions.file_controller as fc
from core.awareness import _known_folders as kf


def test_resolve_path_documents_uses_known_folders(monkeypatch):
    redirected = Path(r"C:\Users\u\OneDrive\Документы")
    monkeypatch.setattr(kf, "folder",
                        lambda n: redirected if (n or "").lower() == "documents" else None)
    assert fc._resolve_path("documents") == redirected


def test_safe_roots_come_from_known_folders(monkeypatch):
    redirected = Path(r"C:\Users\u\OneDrive\Документы")
    monkeypatch.setattr(kf, "folder",
                        lambda n: redirected if (n or "").lower() == "documents"
                        else Path(r"C:\Users\u") / (n or "").capitalize())
    roots = fc._safe_roots()
    assert redirected in roots                       # redirected Documents is a safe root
    assert Path(r"C:\Users\u\Desktop") in roots      # others come from the same source


def test_resolve_path_passes_through_full_paths():
    full = Path(r"C:\Users\u\Desktop\project\file.txt")
    assert fc._resolve_path(str(full)) == full


def test_home_shortcut_still_resolves_to_home():
    assert fc._resolve_path("home") == Path.home()
