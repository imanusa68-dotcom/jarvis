"""
Issue 013 — backup before overwrite + undo ("верни как было").

Safe roots AND the backup dir are redirected to a temp dir, so the real home is
never touched. Backup fires only on true overwrite (mode "w"), never on create or
append — so existing file tests are unaffected.

Run:  python -m pytest tests/test_undo_overwrite.py -q
"""

import tempfile
from pathlib import Path

import actions.file_controller as fc


class _Env:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "Desktop"
        self.root.mkdir(parents=True, exist_ok=True)
        self.bdir = base / "backups"
        self._orig_roots = fc._safe_roots
        self._orig_bdir = fc._backup_dir
        fc._safe_roots = lambda: [self.root]        # type: ignore
        fc._backup_dir = lambda: self.bdir          # type: ignore
        return self

    def __exit__(self, *exc):
        fc._safe_roots = self._orig_roots           # type: ignore
        fc._backup_dir = self._orig_bdir            # type: ignore
        self._tmp.cleanup()


def test_overwrite_then_undo_restores_previous_content():
    with _Env() as env:
        f = env.root / "note.txt"
        fc.create_file(str(f), content="ORIGINAL")
        fc.write_file(str(f), content="OVERWRITTEN")      # true overwrite → backup
        assert f.read_text(encoding="utf-8") == "OVERWRITTEN"
        r = fc.restore_file(str(f))                        # "верни как было"
        assert f.read_text(encoding="utf-8") == "ORIGINAL"
        assert "восстанов" in r.lower()


def test_create_and_append_do_not_backup():
    with _Env() as env:
        new = env.root / "fresh.txt"
        fc.create_file(str(new), content="a")              # create → no backup
        fc.write_file(str(new), content="b\n", append=True)  # append → no backup
        assert not env.bdir.exists() or not any(env.bdir.iterdir())


def test_undo_without_backup_is_honest():
    with _Env() as env:
        f = env.root / "never_overwritten.txt"
        fc.create_file(str(f), content="x")
        r = fc.restore_file(str(f))
        assert "нет" in r.lower()                           # honest, not a crash
        assert f.read_text(encoding="utf-8") == "x"         # untouched


def test_backup_failure_does_not_block_write(monkeypatch):
    with _Env() as env:
        f = env.root / "note.txt"
        fc.create_file(str(f), content="ORIGINAL")
        monkeypatch.setattr(fc, "_save_backup",
                            lambda t: (_ for _ in ()).throw(OSError("disk full")))
        r = fc.write_file(str(f), content="NEW")            # backup blows up…
        assert f.read_text(encoding="utf-8") == "NEW"       # …write still succeeds
        assert "overwrote" in r.lower()


def test_undo_action_via_dispatch_and_policy():
    from core.security import check_tool_call
    d = check_tool_call("file_controller", {"action": "undo"})
    assert d.allowed is True
    with _Env() as env:
        f = env.root / "d.txt"
        fc.create_file(str(f), content="AAA")
        fc.file_controller({"action": "write", "path": str(f), "content": "BBB"})
        out = fc.file_controller({"action": "undo", "path": str(f)})
        assert f.read_text(encoding="utf-8") == "AAA"
        assert "восстанов" in out.lower()
