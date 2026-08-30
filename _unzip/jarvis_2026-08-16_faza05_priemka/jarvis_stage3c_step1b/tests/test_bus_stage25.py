# -*- coding: utf-8 -*-
"""
Stage 2.5 - the event bus: facts, not commands.

These tests lock the three hard rules:
  1. FACTS ONLY - every catalog name is past tense; no command could ride the
     bus and become an un-gated execution path.
  2. FROZEN CATALOG - unknown events / unknown / missing fields are rejected,
     so two parts of Jarvis can never drift on the meaning of a fact.
  3. NEVER BREAK THE PUBLISHER - a buggy subscriber must not fail a real write.

Run:  PYTHONPATH=.:/data/shims python tests/test_bus_stage25.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from core import store
from core.journal import Journal
from core.staging import Staging
from core.fileops import FileOps
from core.bus import (
    EVENTS, Bus, EventContractError, attach_console_subscriber,
    generate_events_md, get_bus, reset_bus,
)


def _raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    raise AssertionError(f"expected {exc.__name__} to be raised")


def _fo(wire: Path) -> FileOps:
    conn = store.open_store(str(wire / "jarvis.db"))
    return FileOps(journal=Journal(conn), staging=Staging(root=wire / "staging"),
                   safe_roots=[str(wire)])


def _fresh(prefix="jv_bus_"):
    """Clean global bus + a private wire dir for one test."""
    reset_bus()
    return Path(tempfile.mkdtemp(prefix=prefix))


# -- rule 2: the frozen catalog -------------------------------------------

def test_unknown_event_is_rejected():
    bus = Bus()
    _raises(lambda: bus.publish("file.exploded", path="x"), EventContractError)


def test_unknown_field_is_rejected():
    bus = Bus()
    _raises(lambda: bus.publish("file.created", path="a.txt", colour="red"),
            EventContractError)


def test_missing_required_field_is_rejected():
    bus = Bus()
    _raises(lambda: bus.publish("file.created"), EventContractError)


def test_every_catalog_event_is_a_past_tense_fact_not_a_command():
    banned = ("do_", "run_", "execute", "delete_file", "write_", "create_file",
              "please", "cmd", "command")
    for name in EVENTS:
        assert "." in name, f"{name} must be namespaced"
        assert not any(b in name for b in banned), f"{name} looks like a command"
        verb = name.split(".", 1)[1]
        # past tense / past participle: created, moved, failed, overwritten
        assert verb.endswith(("ed", "en")), (
            f"{name} must be past tense (a fact that already happened)")


def test_events_md_is_generated_from_the_catalog():
    md = generate_events_md()
    for name in EVENTS:
        assert f"`{name}`" in md
    assert f"**{len(EVENTS)} events**" in md


# -- rule 3: never break the publisher ------------------------------------

def test_broken_subscriber_cannot_break_the_publisher():
    bus = Bus()
    seen = []

    def boom(_event):
        raise RuntimeError("subscriber is buggy")

    bus.subscribe("*", boom, name="boom")
    bus.subscribe("*", lambda e: seen.append(e.name), name="good")

    event = bus.publish("file.created", path="a.txt")  # must NOT raise
    assert event is not None
    assert seen == ["file.created"], "a broken listener must not starve others"
    assert bus.errors() and bus.errors()[0][0] == "boom"


def test_a_broken_subscriber_cannot_fail_a_real_write():
    wire = _fresh()

    def boom(_e):
        raise RuntimeError("nope")

    get_bus().subscribe("*", boom)
    fo = _fo(wire)
    f = wire / "a.txt"
    res = fo.replace_file(f, "hello")
    assert res["ok"] and f.read_text(encoding="utf-8") == "hello"


def test_events_are_immutable():
    bus = Bus()
    got = []
    bus.subscribe("*", got.append)
    bus.publish("file.created", path="a.txt")

    def _mutate():
        got[0].name = "file.moved"

    _raises(_mutate)


# -- subscribing -----------------------------------------------------------

def test_prefix_and_exact_subscriptions_and_unsubscribe():
    bus = Bus()
    files, undos = [], []
    bus.subscribe("file.*", lambda e: files.append(e.name))
    stop = bus.subscribe("undo.performed", lambda e: undos.append(e.name))

    bus.publish("file.created", path="a.txt")
    bus.publish("undo.performed", ok=True)
    assert files == ["file.created"] and undos == ["undo.performed"]

    stop()
    bus.publish("undo.performed", ok=True)
    assert undos == ["undo.performed"], "unsubscribe must stop delivery"


def test_flight_recorder_is_bounded():
    bus = Bus(max_recent=5)
    for i in range(20):
        bus.publish("file.created", path=f"f{i}.txt")
    recent = bus.recent(limit=50)
    assert len(recent) == 5, "ring buffer must bound memory during a storm"
    assert recent[-1]["path"] == "f19.txt"
    assert recent[-1].seq == 20


def test_console_subscriber_prints_facts():
    bus = Bus()
    lines = []
    attach_console_subscriber(bus, printer=lines.append)
    bus.publish("file.created", path="a.txt")
    assert lines and "file.created" in lines[0] and "a.txt" in lines[0]


# -- real facts from real operations ---------------------------------------

def test_fileops_publishes_create_overwrite_rename_move():
    wire = _fresh()
    seen = []
    get_bus().subscribe("*", lambda e: seen.append((e.name, dict(e.payload))))
    fo = _fo(wire)

    f = wire / "a.txt"
    fo.replace_file(f, "one")
    fo.replace_file(f, "two")
    fo.rename(f, "b.txt")
    fo.move(wire / "b.txt", wire / "sub" / "b.txt")

    names = [n for n, _ in seen]
    for expected in ("file.created", "file.overwritten", "file.renamed",
                     "file.moved"):
        assert expected in names, f"missing fact {expected}; got {names}"

    created = next(p for n, p in seen if n == "file.created")
    assert created["path"] == str(f)
    moved = next(p for n, p in seen if n == "file.moved")
    assert moved["dst"].endswith("b.txt")


def test_fileops_publishes_undo_and_redo_facts():
    wire = _fresh()
    fo = _fo(wire)
    f = wire / "a.txt"
    fo.replace_file(f, "one")
    fo.replace_file(f, "two")

    seen = []
    get_bus().subscribe("undo.*", seen.append)
    get_bus().subscribe("redo.*", seen.append)

    fo.undo_last(str(f))
    fo.redo_last(str(f))

    names = [e.name for e in seen]
    assert names == ["undo.performed", "redo.performed"], names
    assert seen[0]["ok"] is True
    assert seen[0]["path"] == str(f)


def test_session_started_fact_on_new_session():
    wire = _fresh()
    fo = _fo(wire)
    seen = []
    get_bus().subscribe("session.started", seen.append)
    fo.new_session()
    assert len(seen) == 1 and seen[0]["session_id"]


def test_op_failed_fact_uses_the_frozen_shape():
    wire = _fresh()
    fo = _fo(wire)
    seen = []
    get_bus().subscribe("file.op_failed", seen.append)
    _raises(lambda: fo.move(wire / "missing.txt", wire / "x.txt"))
    for e in seen:
        assert e["op"] and e["error"]


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print("OK  ", fn.__name__)
    print(f"\nRESULT: ALL PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run()
