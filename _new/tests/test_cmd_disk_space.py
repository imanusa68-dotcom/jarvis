# -*- coding: utf-8 -*-
"""
Regression: the WMIC `/format:` OUTPUT switch must NOT trip the `format`
disk-wipe blocker.

Surfaced during Stage 1 acceptance: "check disk space" resolves to
`wmic logicaldisk get caption,freespace,size /format:list`, which the gate
correctly ALLOWED, but cmd_control's internal `_is_safe` wrongly matched the
substring 'format' inside '/format:list' and blocked a safe read-only command.

Run:  python -m pytest tests/test_cmd_disk_space.py -q
or:   python tests/test_cmd_disk_space.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from actions.cmd_control import _is_safe, _find_hardcoded


def test_disk_space_resolved_command_is_allowed():
    cmd = _find_hardcoded("check disk space")
    assert cmd, "disk-space request should resolve to a hardcoded command"
    assert "/format:" in cmd.lower(), f"command really does carry the switch: {cmd!r}"
    safe, reason = _is_safe(cmd)
    assert safe, f"safe read-only disk command wrongly blocked: {reason}"


def test_wmic_format_switch_is_allowed():
    safe, _ = _is_safe("wmic product get name,version /format:table")
    assert safe


def test_real_format_command_still_blocked():
    for bad in ("format c:", "format", "format /q d:", "FORMAT D:"):
        safe, reason = _is_safe(bad)
        assert not safe, f"dangerous command slipped through: {bad!r}"


def test_other_blockers_intact():
    for bad in ("diskpart", "fdisk", "shutdown /s /t 0", "taskkill /im x.exe",
                "rm -rf /", "reg delete HKLM"):
        safe, _ = _is_safe(bad)
        assert not safe, f"blocker regressed for: {bad!r}"


def _run():
    fns = [
        test_disk_space_resolved_command_is_allowed,
        test_wmic_format_switch_is_allowed,
        test_real_format_command_still_blocked,
        test_other_blockers_intact,
    ]
    for fn in fns:
        fn()
        print("OK  ", fn.__name__)
    print("\nRESULT: ALL PASS")


if __name__ == "__main__":
    _run()
