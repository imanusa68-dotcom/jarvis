"""
Issue 015 — `_full_path` must not double the filename when the model passes the
full path in `path` AND repeats it in `name` (which crashed writes with WinError
183). Exercised through the file_controller dispatcher, like the real tool call.

Run:  python -m pytest tests/test_write_double_path.py -q
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
        self._or, self._orp, self._ob = fc._safe_roots, fc._resolve_path, fc._backup_dir
        fc._safe_roots = lambda: [self.root]                                   # type: ignore
        fc._resolve_path = lambda p: self.root if p == "desktop" else Path(p).expanduser()  # type: ignore
        fc._backup_dir = lambda: base / "backups"                              # type: ignore
        return self

    def __exit__(self, *exc):
        fc._safe_roots, fc._resolve_path, fc._backup_dir = self._or, self._orp, self._ob  # type: ignore
        self._tmp.cleanup()


def test_write_with_full_path_and_repeated_name_does_not_double():
    with _Env() as env:
        full = str(env.root / "t.txt")
        fc.file_controller({"action": "create_file", "path": "desktop", "name": "t.txt"})
        r = fc.file_controller({"action": "write", "path": full, "name": "t.txt",
                                "content": "СОЧИНЕНИЕ"})
        assert "winerror" not in r.lower() and "could not" not in r.lower()
        assert (env.root / "t.txt").read_text(encoding="utf-8") == "СОЧИНЕНИЕ"
        assert not (env.root / "t.txt" / "t.txt").exists()      # never doubled


def test_overwrite_via_full_path_still_backs_up_for_undo():
    with _Env() as env:
        full = str(env.root / "note.txt")
        fc.file_controller({"action": "create_file", "path": "desktop", "name": "note.txt"})
        fc.write_file(full, content="ORIGINAL")
        fc.file_controller({"action": "write", "path": full, "name": "note.txt",
                            "content": "NEW"})                  # overwrite via full path+name
        assert (env.root / "note.txt").read_text(encoding="utf-8") == "NEW"
        fc.file_controller({"action": "undo", "path": full})
        assert (env.root / "note.txt").read_text(encoding="utf-8") == "ORIGINAL"


def test_plain_folder_plus_name_still_creates_inside_folder():
    with _Env() as env:
        r = fc.file_controller({"action": "create_file", "path": "desktop",
                                "name": "report.txt", "content": "x"})
        assert (env.root / "report.txt").exists()
        assert "created" in r.lower()
