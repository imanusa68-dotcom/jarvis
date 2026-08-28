"""Stage 3.0: the durability floor for JSON-backed state.

An audit before Stage 3 found two active data-loss bugs:

  1. Memory and the personality profile lived INSIDE the build folder, so
     every unpacked update started the assistant with a blank memory.
  2. Saves were a plain truncate-then-write, and loads silently returned an
     EMPTY object on a parse error - after which the next save overwrote the
     damaged file with that empty object. Silent, permanent, total amnesia.

These tests pin the fixes: durable location, atomic writes, snapshots,
quarantine-instead-of-overwrite, and a one-time non-destructive import.

Runner-style (pytest-free): module-level test_* + plain asserts.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from core import safe_json


# -- helpers -----------------------------------------------------------------

def _fresh_state_dir():
    """Point all durable state at a throwaway dir. Never touches real ~/.jarvis."""
    tmp = tempfile.mkdtemp(prefix="jarvis-stage30-")
    os.environ[safe_json.STATE_DIR_ENV] = tmp
    return Path(tmp)


def _memory_module(state_dir):
    """Import memory_manager and reset its one-time-import latch."""
    from memory import memory_manager as mm
    mm._migrated_for = None
    return mm


def _personality_module(state_dir):
    from memory import personality_engine as pe
    pe._migrated_for = None
    return pe


def _sample_memory(mm, name="Rustam"):
    mem = mm._empty_memory()
    mem["identity"]["name"] = {"value": name, "updated": "2026-07-25"}
    return mem


# -- 1. location: state must survive a build update --------------------------

def test_memory_no_longer_lives_in_the_build_folder():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    path = mm._memory_path()
    assert path.parent == state, "memory must live in the durable state dir"
    assert "long_term.json" == path.name
    # The whole point: it is NOT inside the unpacked project directory.
    assert mm.BASE_DIR not in path.parents, (
        "memory must not live in the build folder - that is what wiped it "
        "on every update"
    )


def test_state_dir_is_the_same_home_dir_the_database_uses():
    """Invariant 1: one owner, one location for durable state."""
    os.environ.pop(safe_json.STATE_DIR_ENV, None)
    try:
        from core import store
        assert safe_json.state_dir() == store.app_dir(), (
            "memory and jarvis.db must share one durable directory"
        )
    finally:
        _fresh_state_dir()


# -- 2. round trip -----------------------------------------------------------

def test_save_then_load_roundtrip_survives_a_new_process_view():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    mm.save_memory(_sample_memory(mm))
    mm._migrated_for = None  # simulate a fresh start against the same state dir

    loaded = mm.load_memory()
    assert loaded["identity"]["name"]["value"] == "Rustam"


def test_missing_file_is_empty_but_not_an_error():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    data, report = safe_json.load_json_report(
        state / "long_term.json", mm._empty_memory, label="Test"
    )
    assert report["source"] == "missing", "a never-created file is not corruption"
    assert data == mm._empty_memory()


# -- 3. atomicity ------------------------------------------------------------

def test_write_leaves_no_temp_files_behind():
    state = _fresh_state_dir()
    target = state / "thing.json"

    safe_json.atomic_write_json(target, {"a": 1})
    safe_json.atomic_write_json(target, {"a": 2})

    leftovers = [p.name for p in state.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files leaked: {leftovers}"
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}


def test_failed_save_leaves_the_previous_file_completely_intact():
    """The old code truncated first, so a failure destroyed the good copy."""
    state = _fresh_state_dir()
    target = state / "thing.json"
    safe_json.atomic_write_json(target, {"good": True})
    before = target.read_text(encoding="utf-8")

    class Unserialisable:
        pass

    try:
        safe_json.atomic_write_json(target, {"bad": Unserialisable()})
        raise AssertionError("expected the bad payload to be rejected")
    except TypeError:
        pass

    assert target.read_text(encoding="utf-8") == before, (
        "a failed save must not modify the file on disk"
    )
    leftovers = [p.name for p in state.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files leaked after failure: {leftovers}"


def test_hard_kill_mid_write_never_yields_a_half_written_file():
    """SIGKILL a child while it hammers the same file, exactly like the Stage 2
    crash-test. The file must always parse: either fully old or fully new."""
    state = _fresh_state_dir()
    target = state / "hammer.json"
    safe_json.atomic_write_json(target, {"n": 0})

    repo = str(Path(__file__).resolve().parent.parent)
    child = (
        "import sys, os; sys.path.insert(0, %r);"
        "os.environ[%r] = %r;"
        "from core.safe_json import atomic_write_json;"
        "from pathlib import Path;"
        "p = Path(%r);"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    atomic_write_json(p, {'n': i, 'pad': 'x' * 20000})\n"
        % (repo, safe_json.STATE_DIR_ENV, str(state), str(target))
    )
    proc = subprocess.Popen([sys.executable, "-c", child])
    try:
        import time
        time.sleep(0.6)
    finally:
        proc.kill()
        proc.wait(timeout=10)

    raw = target.read_text(encoding="utf-8")
    parsed = json.loads(raw)  # must not raise
    assert isinstance(parsed, dict) and "n" in parsed, (
        "file must be a complete JSON document after a hard kill"
    )
    leftovers = [p.name for p in state.iterdir() if p.name.endswith(".tmp")]
    assert len(leftovers) <= 1, (
        "at most the single in-flight temp file may remain: %s" % leftovers
    )


# -- 4. no silent loss -------------------------------------------------------

def test_corrupt_file_is_quarantined_and_never_overwritten():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    path = mm._memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"identity": {"name": {"value": "Rus', encoding="utf-8")

    data = mm.load_memory()
    assert data == mm._empty_memory(), "unreadable memory starts empty for this run"

    quarantined = list(state.glob("long_term.json.corrupt-*"))
    assert quarantined, "the damaged file must be preserved, not destroyed"
    assert "Rus" in quarantined[0].read_text(encoding="utf-8"), (
        "quarantined copy must keep the original bytes for recovery"
    )

    # And the killer scenario: the following save must not erase the evidence.
    mm.save_memory(_sample_memory(mm, "New"))
    assert quarantined[0].exists(), "a later save must not remove the quarantine"


def test_corrupt_file_recovers_from_the_last_good_snapshot():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    mm.save_memory(_sample_memory(mm, "Rustam"))   # creates the primary
    mm.save_memory(_sample_memory(mm, "Rustam2"))  # rotates a snapshot

    mm._memory_path().write_text("}{ broken", encoding="utf-8")

    recovered = mm.load_memory()
    assert recovered["identity"]["name"]["value"] == "Rustam", (
        "must fall back to the previous known-good version"
    )
    assert mm._memory_path().exists(), "the primary must be restored on disk"


def test_a_corrupt_file_never_becomes_a_snapshot():
    state = _fresh_state_dir()
    target = state / "thing.json"
    safe_json.atomic_write_json(target, {"good": 1})
    safe_json.atomic_write_json(target, {"good": 2})   # now .bak1 == {"good": 1}

    target.write_text("not json at all", encoding="utf-8")
    safe_json.atomic_write_json(target, {"good": 3})

    snap = safe_json.snapshot_path(target, 1)
    assert json.loads(snap.read_text(encoding="utf-8")) == {"good": 1}, (
        "the corrupt version must not have displaced the known-good snapshot"
    )
    for backup in state.glob("thing.json.bak*"):
        json.loads(backup.read_text(encoding="utf-8"))  # every snapshot must parse


def test_snapshots_rotate_and_are_bounded():
    state = _fresh_state_dir()
    target = state / "thing.json"
    for i in range(6):
        safe_json.atomic_write_json(target, {"n": i})

    kept = sorted(p.name for p in state.glob("thing.json.bak*"))
    assert len(kept) == safe_json.SNAPSHOT_COUNT, (
        "snapshot count must stay bounded, got %s" % kept
    )
    assert json.loads(safe_json.snapshot_path(target, 1).read_text("utf-8")) == {"n": 4}
    assert json.loads(safe_json.snapshot_path(target, 2).read_text("utf-8")) == {"n": 3}


# -- 5. one-time import from the build folder --------------------------------

def test_build_folder_memory_is_imported_once_and_original_kept():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    legacy_dir = Path(tempfile.mkdtemp(prefix="jarvis-oldbuild-"))
    legacy = legacy_dir / "long_term.json"
    legacy.write_text(json.dumps(_sample_memory(mm, "Legacy")), encoding="utf-8")
    mm._LEGACY_MEMORY_PATH = legacy

    loaded = mm.load_memory()
    assert loaded["identity"]["name"]["value"] == "Legacy", "old memory must carry over"
    assert legacy.exists(), "the user's original file must never be deleted"
    assert (legacy_dir / "long_term.json.imported").exists(), "import must be marked"


def test_import_is_idempotent_and_durable_copy_always_wins():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    legacy_dir = Path(tempfile.mkdtemp(prefix="jarvis-oldbuild-"))
    legacy = legacy_dir / "long_term.json"
    legacy.write_text(json.dumps(_sample_memory(mm, "Legacy")), encoding="utf-8")
    mm._LEGACY_MEMORY_PATH = legacy

    mm.load_memory()                      # first import
    mm.save_memory(_sample_memory(mm, "Current"))
    mm._migrated_for = None               # pretend we restarted

    loaded = mm.load_memory()
    assert loaded["identity"]["name"]["value"] == "Current", (
        "a second import must never clobber newer durable memory"
    )


def test_empty_or_broken_legacy_file_is_not_imported():
    state = _fresh_state_dir()
    mm = _memory_module(state)

    legacy_dir = Path(tempfile.mkdtemp(prefix="jarvis-oldbuild-"))
    legacy = legacy_dir / "long_term.json"
    legacy.write_text("{ broken", encoding="utf-8")

    assert safe_json.import_legacy_once(legacy, state / "long_term.json") is False
    assert not (state / "long_term.json").exists()


# -- 6. the personality profile gets the identical guarantees ----------------

def test_personality_profile_is_durable_and_crash_safe():
    state = _fresh_state_dir()
    pe = _personality_module(state)

    assert pe._profile_path().parent == state
    assert pe.BASE_DIR not in pe._profile_path().parents

    profile = pe._default_profile()
    profile["tone"] = "friendly"
    pe._save_profile(profile)

    pe._migrated_for = None
    assert pe.load_profile()["tone"] == "friendly"


def test_corrupt_profile_is_quarantined_not_silently_defaulted():
    state = _fresh_state_dir()
    pe = _personality_module(state)

    pe._save_profile(pe._default_profile())
    pe._profile_path().write_text("garbage", encoding="utf-8")

    pe.load_profile()
    assert list(state.glob("personality.json.corrupt-*")), (
        "a damaged profile must be preserved for inspection"
    )
