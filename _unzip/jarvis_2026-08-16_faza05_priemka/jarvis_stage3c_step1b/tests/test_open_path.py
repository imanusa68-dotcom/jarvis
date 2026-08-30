"""
Issue 005 — open_path: open a resolved file/folder in its default app.

Exercises actions/open_path directly. The real launcher (os.startfile) is patched
via the module's `_launch` seam so tests never actually open anything, and safe
roots are pinned to a temp dir so the boundary check is deterministic. Mirrors the
seam style used across the awareness tests (fake state, no OS side effects).

Run:  python -m pytest tests/test_open_path.py -q
"""

import importlib
import os
import shutil
import tempfile
from pathlib import Path

import actions.open_path as op


class _Env:
    """Pin safe roots to a temp dir and capture launches instead of opening."""
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.launched = []
        self.apps = []
        self._orig_roots = op._safe_roots
        self._orig_launch = op._launch
        op._safe_roots = lambda: [self.root]          # type: ignore
        op._launch = lambda path, app=None: (self.launched.append(path), self.apps.append(app))  # type: ignore
        return self

    def __exit__(self, *exc):
        op._safe_roots = self._orig_roots             # type: ignore
        op._launch = self._orig_launch                # type: ignore
        self._tmp.cleanup()


def test_opens_a_file_inside_safe_roots():
    with _Env() as env:
        f = env.root / "report.pdf"
        f.write_text("x", encoding="utf-8")
        r = op.open_path(parameters={"path": str(f)})
        assert env.launched == [str(f)]
        assert "report.pdf" in r


def test_refuses_executable_and_script_extensions():
    with _Env() as env:
        for ext in (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".lnk", ".scr"):
            f = env.root / ("tool" + ext)
            f.write_text("x", encoding="utf-8")
            r = op.open_path(parameters={"path": str(f)})
            assert "SECURITY" in r, ext
        assert env.launched == []


def test_refuses_path_outside_safe_roots():
    with _Env() as env:
        outside = Path(tempfile.gettempdir()) / "not_in_roots.txt"
        r = op.open_path(parameters={"path": str(outside)})
        assert "SECURITY" in r
        assert env.launched == []


def test_refuses_dotdot_escape():
    with _Env() as env:
        tricky = env.root / ".." / "escape.txt"
        r = op.open_path(parameters={"path": str(tricky)})
        assert "SECURITY" in r
        assert env.launched == []


def test_opens_a_folder_inside_roots():
    with _Env() as env:
        d = env.root / "proj"
        d.mkdir()
        r = op.open_path(parameters={"path": str(d)})
        assert env.launched == [str(d)]
        assert "proj" in r


def test_missing_path_is_refused_not_launched():
    with _Env() as env:
        r = op.open_path(parameters={"path": str(env.root / "nope.pdf")})
        assert env.launched == []
        assert "не найден" in r.lower()


def test_open_path_policy_is_allowed_medium_risk():
    from core.security import check_tool_call
    d = check_tool_call("open_path", {"path": "x"})
    assert d.allowed is True
    assert d.risk == "medium"


def test_open_path_not_reachable_from_agent_executor():
    from agent.executor import _call_tool
    out = _call_tool("open_path", {"path": "x"}, None)
    assert "SECURITY" in out or "Unknown tool" in out


# ── issue 011: open in a SPECIFIC app ──────────────────────────────────────────

def test_open_path_with_app_passes_app_to_launcher(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(op, "_launch", lambda path, app=None: calls.append((path, app)))
    monkeypatch.setattr(op, "_safe_roots", lambda: [tmp_path])
    f = tmp_path / "data.json"
    f.write_text("x", encoding="utf-8")
    r = op.open_path(parameters={"path": str(f), "app": "notepad"})
    assert calls == [(str(f), "notepad")]
    assert "notepad" in r.lower()


def test_open_path_without_app_uses_default(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(op, "_launch", lambda path, app=None: calls.append((path, app)))
    monkeypatch.setattr(op, "_safe_roots", lambda: [tmp_path])
    f = tmp_path / "data.json"
    f.write_text("x", encoding="utf-8")
    op.open_path(parameters={"path": str(f)})
    assert calls == [(str(f), None)]


def test_resolve_launcher_maps_notepad():
    assert op._resolve_launcher("блокнот") == "notepad.exe"
    assert op._resolve_launcher("notepad") == "notepad.exe"


def test_open_path_app_launch_failure_is_honest(monkeypatch, tmp_path):
    def boom(path, app=None):
        raise OSError("nope")
    monkeypatch.setattr(op, "_launch", boom)
    monkeypatch.setattr(op, "_safe_roots", lambda: [tmp_path])
    f = tmp_path / "data.json"
    f.write_text("x", encoding="utf-8")
    r = op.open_path(parameters={"path": str(f), "app": "weirdapp"})
    assert "не удалось" in r.lower() and "weirdapp" in r.lower()


def test_app_does_not_bypass_extension_block(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(op, "_launch", lambda path, app=None: calls.append((path, app)))
    monkeypatch.setattr(op, "_safe_roots", lambda: [tmp_path])
    f = tmp_path / "tool.exe"
    f.write_text("x", encoding="utf-8")
    r = op.open_path(parameters={"path": str(f), "app": "notepad"})
    assert "SECURITY" in r
    assert calls == []


def test_resolve_downloaded_then_open_end_to_end(monkeypatch):
    # The headline scenario: "открой файл, который я только что скачал".
    # scratch dir under the project (NOT %TEMP%, which the noise filter drops).
    import core.awareness as aw
    from core.awareness import _known_folders as kf
    importlib.reload(aw)
    aw.reset()

    d = Path(tempfile.mkdtemp(dir=os.getcwd()))
    launched = []
    try:
        monkeypatch.setattr(kf, "folder",
                            lambda n: d if (n or "").lower() == "downloads" else None)
        monkeypatch.setattr(op, "_safe_roots", lambda: [d])
        monkeypatch.setattr(op, "_launch", lambda p, app=None: launched.append(p))

        f = d / "invoice.pdf"
        f.write_text("x", encoding="utf-8")
        aw.ingest_file_event(str(f), "created")          # user downloaded it

        ref = aw.resolve("downloaded_file")              # Jarvis resolves the referent
        assert ref["found"] and ref["path"].endswith("invoice.pdf")

        r = op.open_path(parameters={"path": ref["path"]})   # …and opens it
        assert launched == [ref["path"]]
        assert "invoice.pdf" in r
    finally:
        shutil.rmtree(d, ignore_errors=True)
