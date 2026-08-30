"""
Stage 3 slice 1 — create/write files inside safe folders, behind the boundary
guard and (for write) the confirm-gate.

SAFETY: every test that touches the filesystem points the safe-roots at a
temporary directory, so the real Desktop/Documents are never modified. No test
creates, writes, or deletes anything under the real user home.

Run:  python -m pytest tests/test_stage3_files.py -q
or:   python tests/test_stage3_files.py
"""

import os
import tempfile
from pathlib import Path

import actions.file_controller as fc
import core.security as sec
from core.security import get_policy, needs_confirmation, check_tool_call


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — redirect the safe roots to a throwaway temp dir for the test body.
# ─────────────────────────────────────────────────────────────────────────────

class _TempRoots:
    def __init__(self):
        self._tmp = None
        self._orig = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "Desktop"
        root.mkdir(parents=True, exist_ok=True)
        self._orig = fc._safe_roots
        fc._safe_roots = lambda: [root]  # type: ignore
        return root

    def __exit__(self, *exc):
        fc._safe_roots = self._orig  # type: ignore
        self._tmp.cleanup()


# ─────────────────────────────────────────────────────────────────────────────
# Policy layer — the two locks must agree (anti-desync invariant)
# ─────────────────────────────────────────────────────────────────────────────

def test_create_and_write_are_now_unblocked():
    assert get_policy("file_controller", {"action": "create_file"}) == "auto"
    assert get_policy("file_controller", {"action": "create_folder"}) == "auto"
    assert get_policy("file_controller", {"action": "write"}) == "confirm"


def test_delete_confirms_and_organize_desktop_still_blocked():
    # Stage 2.6 unblocked delete ONLY because it became reversible (staged undo
    # + Recycle Bin) - and only behind a confirmation. organize_desktop has no
    # such compensation yet, so it stays blocked.
    d = check_tool_call("file_controller", {"action": "delete", "path": "desktop"})
    assert d.allowed, "delete is now reachable"
    need, _ = needs_confirmation("file_controller", {"action": "delete"})
    assert need is True, "delete must never run without confirmation"

    d = check_tool_call("file_controller", {"action": "organize_desktop",
                                           "path": "desktop"})
    assert not d.allowed, "organize_desktop should still be blocked"


def test_move_rename_allowed_but_confirm():
    for action in ("move", "rename"):
        d = check_tool_call("file_controller", {"action": action, "path": "desktop"})
        assert d.allowed, f"{action} should now be allowed"
        need, _ = needs_confirmation("file_controller", {"action": action})
        assert need is True, f"{action} should require confirmation"


def test_write_requires_confirmation_create_does_not():
    need_w, _ = needs_confirmation("file_controller", {"action": "write"})
    assert need_w is True
    need_c, _ = needs_confirmation("file_controller", {"action": "create_file"})
    assert need_c is False
    # confirmed=true clears the write confirmation
    need_wc, _ = needs_confirmation("file_controller", {"action": "write", "confirmed": True})
    assert need_wc is False


def test_anti_desync_unblocked_actions_have_real_impl():
    """If the policy says an action is not 'forbid', the module must not return
    a 'disabled' stub for it. Catches lock-1/lock-2 drift."""
    with _TempRoots() as root:
        # create_file
        r = fc.create_file(str(root / "a.txt"), content="hi")
        assert "disabled" not in r.lower(), r
        # create_folder
        r = fc.create_folder(str(root / "sub"))
        assert "disabled" not in r.lower(), r
        # write_file
        r = fc.write_file(str(root / "b.txt"), content="x")
        assert "disabled" not in r.lower(), r
        # move + rename (unblocked behind confirm) must have real impls too
        fc.create_file(str(root / "m.txt"), content="x")
        r = fc.move_file(str(root / "m.txt"), str(root / "sub"))
        assert "disabled" not in r.lower(), r
        fc.create_file(str(root / "r.txt"), content="x")
        r = fc.rename_file(str(root / "r.txt"), "r2.txt")
        assert "disabled" not in r.lower(), r


# ─────────────────────────────────────────────────────────────────────────────
# Real filesystem behaviour (temp dir only)
# ─────────────────────────────────────────────────────────────────────────────

def test_create_file_actually_creates():
    with _TempRoots() as root:
        target = root / "note.txt"
        r = fc.create_file(str(target), content="hello")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello"
        assert "created" in r.lower()


def test_write_file_writes_and_appends():
    with _TempRoots() as root:
        target = root / "log.txt"
        fc.write_file(str(target), content="line1\n")
        fc.write_file(str(target), content="line2\n", append=True)
        assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_create_folder_actually_creates():
    with _TempRoots() as root:
        target = root / "newdir"
        fc.create_folder(str(target))
        assert target.is_dir()


# ─────────────────────────────────────────────────────────────────────────────
# Regression: "folder only, no file name" must NOT raise Permission denied.
# Bug was: create_file("desktop") wrote to the directory → PermissionError.
# Fix: a default file name is dropped inside the folder instead.
# ─────────────────────────────────────────────────────────────────────────────

def test_create_file_folder_only_uses_default_name():
    with _TempRoots() as root:
        # Pass the FOLDER itself (no file name) — the real-world failing case.
        r = fc.create_file(str(root), content="hi")
        assert "permission denied" not in r.lower(), r
        created = root / "new_file.txt"
        assert created.exists() and created.read_text(encoding="utf-8") == "hi"


def test_write_folder_only_uses_default_name():
    with _TempRoots() as root:
        r = fc.write_file(str(root), content="data")
        assert "permission denied" not in r.lower(), r
        assert (root / "new_file.txt").exists()


def test_create_folder_parent_only_uses_default_name():
    with _TempRoots() as root:
        r = fc.create_folder(str(root))
        assert "permission denied" not in r.lower(), r
        assert (root / "new_folder").is_dir()


def test_create_file_with_explicit_name_still_works():
    with _TempRoots() as root:
        target = root / "explicit.txt"
        fc.create_file(str(target), content="z")
        assert target.exists()
        # The default-name path must not have been triggered.
        assert not (root / "new_file.txt").exists()


# ─────────────────────────────────────────────────────────────────────────────
# Boundary guard — the safety-critical part
# ─────────────────────────────────────────────────────────────────────────────

def test_refuses_write_outside_safe_roots():
    with _TempRoots():
        # A path clearly outside the (temp) safe root.
        outside = Path(tempfile.gettempdir()) / "_evil_outside.txt"
        r = fc.write_file(str(outside), content="x")
        assert "SECURITY" in r
        assert not outside.exists(), "must not have written outside safe roots"


def test_refuses_traversal_escape():
    with _TempRoots() as root:
        # ../../ escape attempt from inside the safe root.
        escape = root / ".." / ".." / "_escape.txt"
        r = fc.create_file(str(escape), content="x")
        assert "SECURITY" in r
        assert not escape.resolve().exists()


def test_refuses_system_path():
    with _TempRoots():
        sys_path = "C:/Windows/_should_not_write.txt" if os.name == "nt" else "/etc/_should_not_write"
        r = fc.create_file(sys_path, content="x")
        assert "SECURITY" in r


def test_read_only_actions_unaffected_by_boundary():
    # Reading is not boundary-restricted; list of a real existing dir still works.
    r = fc.list_files("home")
    assert "SECURITY" not in r


if __name__ == "__main__":
    import sys
    tests = [
        test_create_and_write_are_now_unblocked,
        test_delete_still_blocked,
        test_move_rename_allowed_but_confirm,
        test_write_requires_confirmation_create_does_not,
        test_anti_desync_unblocked_actions_have_real_impl,
        test_create_file_actually_creates,
        test_write_file_writes_and_appends,
        test_create_folder_actually_creates,
        test_create_file_folder_only_uses_default_name,
        test_write_folder_only_uses_default_name,
        test_create_folder_parent_only_uses_default_name,
        test_create_file_with_explicit_name_still_works,
        test_refuses_write_outside_safe_roots,
        test_refuses_traversal_escape,
        test_refuses_system_path,
        test_read_only_actions_unaffected_by_boundary,
    ]
    fails = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK   {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            fails += 1
            print(f"  ERR  {fn.__name__}: {e!r}")
    print("\nRESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)
