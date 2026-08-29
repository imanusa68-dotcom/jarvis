# tests/test_active_document_dialogue.py
# Issue 009, step 6 — the wiring between the dialogue and the document inspector.
#
# Steps 1-5 proved the inspector answers correctly. These tests prove the answer
# actually reaches the user through resolve_reference, that it stays honest on
# the way (no invented paths, no crashes), and that the two integration mistakes
# that would be invisible at runtime cannot come back:
#   * resolving the document synchronously on the asyncio loop (freezes voice),
#   * losing hwnd on the way from the window watcher to the inspector.

from __future__ import annotations

import io
import os

import core.awareness as aw
from core.awareness import _inspectors as ins
from core.awareness import _resolver, _world_model

_MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


class FakeDoc:
    """Replaces the whole inspector for one test. Records how often it ran."""

    def __init__(self, result=None, boom=False):
        self.result = result
        self.boom = boom
        self.calls = 0
        self._real = None

    def __enter__(self):
        self._real = ins.active_document

        def fake(*a, **kw):
            self.calls += 1
            if self.boom:
                raise RuntimeError("COM exploded")
            return self.result

        ins.active_document = fake
        return self

    def __exit__(self, *exc):
        ins.active_document = self._real
        return False


def _doc(**over):
    base = ins.new_result()
    base.update(over)
    return base


# ── which words reach the inspector ──────────────────────────────────────────

def test_document_referents_are_recognised():
    for kind in ("active_document", "open_document", "current_document",
                 "this_document", "document", "active_file", "open_file"):
        assert aw.is_document_kind(kind), kind
        assert aw.is_document_kind("  " + kind.upper() + " "), kind


def test_other_referents_are_not_document_referents():
    # recent_file is the trap: it answers "the last file you touched", which is
    # NOT "the file in front of you". Routing it here would reintroduce the bug
    # where Jarvis named a file the user had already closed.
    for kind in ("recent_file", "downloaded_file", "open_folder", "same_folder",
                 "active_app", "by_extension", "", "nonsense"):
        assert not aw.is_document_kind(kind), kind


# ── the answer that comes back ───────────────────────────────────────────────

def test_a_verified_path_is_reported_as_found():
    path = r"C:\Users\rdrr\Desktop\iii\test.txt"
    with FakeDoc(_doc(found=True, path=path, name="test.txt",
                      source=ins.SOURCE_RECENT, confidence=ins.CONF_PROBABLE,
                      kind=ins.KIND_LOCAL)) as fake:
        r = _resolver.resolve("active_document")
    assert fake.calls == 1
    assert r["found"] is True
    assert r["type"] == "document"
    assert r["path"] == path
    assert path in _resolver.render(r)


def test_a_name_without_a_path_is_not_reported_as_found():
    with FakeDoc(_doc(found=True, name="test.txt", source=ins.SOURCE_TITLE,
                      confidence=ins.CONF_NAME_ONLY, kind=ins.KIND_LOCAL,
                      reason="\u0418\u043c\u044f \u0432\u0437\u044f\u0442\u043e \u0438\u0437 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430 \u043e\u043a\u043d\u0430.")):
        r = _resolver.resolve("active_document")
    # "found" in this layer means "I have a concrete target", so a bare name is
    # not found — otherwise a caller could try to open the string "test.txt".
    assert r["found"] is False
    assert r["path"] is None
    assert r["title"] == "test.txt"
    text = _resolver.render(r)
    assert "test.txt" in text
    assert "C:\\" not in text          # nothing invented


def test_several_candidates_are_spoken_as_a_question():
    a = r"C:\A\\\u043e\u0442\u0447\u0451\u0442.docx"
    b = r"C:\B\\\u043e\u0442\u0447\u0451\u0442.docx"
    with FakeDoc(_doc(found=True, name="\u043e\u0442\u0447\u0451\u0442.docx", candidates=[a, b],
                      source=ins.SOURCE_RECENT, confidence=ins.CONF_NAME_ONLY)):
        r = _resolver.resolve("active_document")
    assert r["found"] is False
    text = _resolver.render(r)
    assert a in text and b in text
    assert "\u0443\u0442\u043e\u0447\u043d\u0438" in text.lower()


def test_a_browser_page_is_explained_not_guessed():
    with FakeDoc(_doc(found=True, name="Gmail", kind=ins.KIND_WEB,
                      source=ins.SOURCE_TITLE, confidence=ins.CONF_NAME_ONLY)):
        r = _resolver.resolve("active_document")
    text = _resolver.render(r)
    assert r["found"] is False
    assert "\u0431\u0440\u0430\u0443\u0437\u0435\u0440" in text.lower()


def test_an_exploding_inspector_never_breaks_the_turn():
    with FakeDoc(boom=True):
        r = _resolver.resolve("active_document")
    assert r["found"] is False
    assert _resolver.render(r)          # still says something in Russian


def test_the_inspector_is_asked_exactly_once_per_turn():
    # main.py resolves on a worker thread and then renders the SAME result, so a
    # single user question must never cost two COM round-trips.
    with FakeDoc(_doc(found=True, path=r"C:\x\a.txt", name="a.txt")) as fake:
        r = aw.resolve("active_document")
        aw.render_resolved(r)
    assert fake.calls == 1


def test_resolving_a_document_writes_nothing_anywhere():
    import core.dialogue_state as ds
    before = list(ds.get().get("action_journal") or [])
    with FakeDoc(_doc(found=True, path=r"C:\x\a.txt", name="a.txt")):
        _resolver.resolve("active_document")
    assert list(ds.get().get("action_journal") or []) == before


def test_other_referents_still_work_untouched():
    # The ten existing kinds must be unaffected by the new branch.
    with FakeDoc(_doc(found=True, path=r"C:\x\a.txt"), ) as fake:
        r = _resolver.resolve("nonsense_kind")
    assert fake.calls == 0
    assert r["found"] is False
    assert "doc" not in r


# ── the window handle survives the trip ──────────────────────────────────────────

class NoLiveRead:
    """
    Silence the live foreground read for the duration of a test.

    active_window() asks Windows which window is in front and only falls back to
    the world model when that fails. These two tests are about the FALLBACK path
    — that the watcher's hwnd survives the trip through the world model — so the
    live read has to be out of the picture. Without this, the tests pass on Linux
    (no pywin32) and fail on the real machine, where the live read correctly
    returns the handle of the terminal running pytest.
    """

    def __enter__(self):
        self._saved = ins._live_foreground
        ins._live_foreground = lambda: None
        return self

    def __exit__(self, *exc):
        ins._live_foreground = self._saved
        return False


def test_the_window_handle_reaches_the_inspector():
    # The watcher collects hwnd; the world model used to drop it, which silently
    # disabled the COM level (it needs the handle to match the right document).
    _world_model.reset()
    try:
        _world_model.ingest_windows([
            {"title": "\u041e\u0442\u0447\u0451\u0442.docx - Word", "process": "WINWORD.EXE",
             "foreground": True, "visible": True, "hwnd": 4242, "pid": 77},
        ])
        with NoLiveRead():
            w = ins.active_window()
        assert w is not None
        assert w["hwnd"] == 4242
        assert w["process"] == "WINWORD.EXE"
    finally:
        _world_model.reset()


def test_a_missing_handle_is_zero_not_a_crash():
    _world_model.reset()
    try:
        _world_model.ingest_windows([
            {"title": "test.txt - Notepad", "process": "notepad.exe",
             "foreground": True, "visible": True},
        ])
        with NoLiveRead():
            assert ins.active_window()["hwnd"] == 0
    finally:
        _world_model.reset()


def test_the_live_read_is_what_answers_on_a_real_machine():
    # The mirror image of the two tests above: when the live read works, its
    # window wins over the watcher snapshot, however stale that snapshot is.
    _world_model.reset()
    saved = ins._live_foreground
    try:
        _world_model.ingest_windows([
            {"title": "stale.txt - Notepad", "process": "notepad.exe",
             "foreground": True, "visible": True, "hwnd": 1},
        ])
        ins._live_foreground = lambda: {"title": "fresh.txt - Notepad",
                                        "process": "notepad.exe", "hwnd": 2}
        w = ins.active_window()
        assert w["title"] == "fresh.txt - Notepad"
        assert w["hwnd"] == 2
    finally:
        ins._live_foreground = saved
        _world_model.reset()


# ── the two integration mistakes that runtime would hide ─────────────────────

def _main_source():
    return io.open(_MAIN, encoding="utf-8").read()


def test_the_document_branch_runs_off_the_event_loop():
    # A document lookup can spend up to DEADLINE_S talking to Office. Doing that
    # inline would freeze the voice session. If someone ever "simplifies" this
    # into a direct call, this test fails instead of the microphone.
    src = _main_source()
    start = src.index('elif name == "resolve_reference":')
    branch = src[start:src.index('elif name == "open_path":', start)]
    assert "is_document_kind" in branch
    assert "run_in_executor" in branch
    doc_part = branch[branch.index("is_document_kind"):branch.index("else:", branch.index("is_document_kind"))]
    assert "run_in_executor" in doc_part


def test_the_layer_off_answer_still_comes_first():
    src = _main_source()
    start = src.index('elif name == "resolve_reference":')
    branch = src[start:src.index('elif name == "open_path":', start)]
    off = branch.index("is_running")
    assert off < branch.index("is_document_kind")


def test_no_new_tool_was_smuggled_in():
    # Step 6 deliberately extends resolve_reference instead of adding a tool, so
    # the security policy and the executor allow-list stay exactly as audited.
    from core.security import SECURITY_POLICY
    assert "resolve_reference" in SECURITY_POLICY
    assert SECURITY_POLICY["resolve_reference"].status == "allowed"
    assert "active_document" not in SECURITY_POLICY


def test_the_model_is_told_when_to_use_the_new_referent():
    src = _main_source()
    decl_start = src.index('"name": "resolve_reference"')
    decl = src[decl_start:decl_start + 4000]
    assert "active_document" in decl
    assert "\u043a\u0430\u043a\u043e\u0439 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u0443 \u043c\u0435\u043d\u044f \u0441\u0435\u0439\u0447\u0430\u0441 \u043e\u0442\u043a\u0440\u044b\u0442?" in decl


# ── Step 6i — "which app is active" obeys the live read and the Jarvis rule ────


class FakeWindow:
    """Pins what the live window read returns for one test."""

    def __init__(self, window):
        self.window = window
        self._real = None

    def __enter__(self):
        self._real = ins.active_window
        ins.active_window = lambda: self.window
        return self

    def __exit__(self, *exc):
        ins.active_window = self._real
        return False


def test_the_active_app_is_never_jarvis_itself():
    with FakeWindow({"title": "Отчёт.docx - Word",
                     "process": "WINWORD.EXE", "hwnd": 7, "substituted": True}):
        r = _resolver.resolve("active_app")
    assert r["found"] is True
    assert "Word" in (r["title"] or "")
    assert r["reason"] == ins.APP_SUBSTITUTED_NOTE
    assert ins.APP_SUBSTITUTED_NOTE in _resolver.render(r)


def test_a_normal_app_answer_carries_no_note():
    with FakeWindow({"title": "Happ 3.3.6 (591)", "process": "Happ.exe", "hwnd": 9}):
        r = _resolver.resolve("active_app")
    assert r["reason"] == ""
    assert _resolver.render(r).endswith("Happ 3.3.6 (591)")


def test_the_app_answer_falls_back_to_the_snapshot():
    real = _world_model.snapshot
    _world_model.snapshot = lambda: {"active_window": {"title": "Блокнот",
                                                       "process": "notepad.exe"}}
    try:
        with FakeWindow(None):
            r = _resolver.resolve("active_app")
    finally:
        _world_model.snapshot = real
    assert r["found"] is True
    assert r["title"] == "Блокнот"


def test_a_broken_live_read_does_not_break_the_app_answer():
    def boom():
        raise RuntimeError("live read exploded")

    real_window, real_snapshot = ins.active_window, _world_model.snapshot
    ins.active_window = boom
    _world_model.snapshot = lambda: {"active_window": {"title": "Блокнот"}}
    try:
        r = _resolver.resolve("active_app")
    finally:
        ins.active_window = real_window
        _world_model.snapshot = real_snapshot
    assert r["found"] is True


def test_with_nothing_visible_the_app_answer_is_honest():
    real = _world_model.snapshot
    _world_model.snapshot = lambda: {"active_window": None}
    try:
        with FakeWindow(None):
            r = _resolver.resolve("active_app")
    finally:
        _world_model.snapshot = real
    assert r["found"] is False


# ── Step 6j — our own window is recognised by HANDLE, not only by caption ────


class OwnHandles:
    """Pins which handles belong to Jarvis for one test."""

    def __init__(self, *handles):
        self.handles = frozenset(int(h) for h in handles)
        self._real = None

    def __enter__(self):
        ins.reset()
        self._real = ins.own_window_handles
        ins.own_window_handles = lambda: self.handles
        return self

    def __exit__(self, *exc):
        ins.own_window_handles = self._real
        ins.reset()
        return False


def test_a_window_of_ours_is_ours_whatever_the_caption_says():
    with OwnHandles(555):
        assert ins.is_own_window("C:\\Windows\\System32\\cmd.exe - python -m pytest",
                                 "cmd.exe", 555) is True


def test_a_stranger_window_is_not_ours():
    with OwnHandles(555):
        assert ins.is_own_window("Отчёт.docx - Word", "WINWORD.EXE", 777) is False


def test_the_caption_rule_still_works_without_a_handle():
    with OwnHandles(555):
        assert ins.is_own_window("J.A.R.V.I.S — MARK XXXV", "python.exe") is True


def test_a_project_folder_named_jarvis_is_still_not_our_window():
    with OwnHandles(555):
        assert ins.is_own_window("main.c - jarvis - Visual Studio Code",
                                 "Code.exe", 777) is False


def test_the_handle_lookup_is_cached():
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return frozenset({555})

    real = ins.own_window_handles
    ins.own_window_handles = counted
    try:
        ins.reset()
        for _ in range(5):
            ins.is_own_handle(555)
    finally:
        ins.own_window_handles = real
        ins.reset()
    assert calls["n"] == 1, calls


def test_a_broken_handle_lookup_never_raises():
    def boom():
        raise RuntimeError("EnumWindows exploded")

    real = ins.own_window_handles
    ins.own_window_handles = boom
    try:
        ins.reset()
        try:
            ins.is_own_handle(555)
            crashed = False
        except Exception:
            crashed = True
    finally:
        ins.own_window_handles = real
        ins.reset()
    assert crashed is False


def test_zero_and_nonsense_handles_are_never_ours():
    with OwnHandles(555):
        assert ins.is_own_handle(0) is False
        assert ins.is_own_handle(None) is False
        assert ins.is_own_handle("не число") is False


# ── Step 6k — one broken door must not close the other ───────────────────────


class OwnDoors:
    """Pins the two OS sources own_window_handles() is built from."""

    def __init__(self, console=None, process=None):
        self.console = console
        self.process = process
        self._saved = {}

    def __enter__(self):
        ins.reset()
        for seam in ("console_window", "process_window_handles"):
            self._saved[seam] = getattr(ins, seam)
        if self.console is not None:
            ins.console_window = self.console
        if self.process is not None:
            ins.process_window_handles = self.process
        return self

    def __exit__(self, *exc):
        for seam, original in self._saved.items():
            setattr(ins, seam, original)
        ins.reset()
        return False


def test_the_console_window_counts_as_ours():
    with OwnDoors(console=lambda: 77, process=lambda: frozenset()):
        assert 77 in ins.own_window_handles()


def test_our_own_windows_count_as_ours():
    with OwnDoors(console=lambda: 0, process=lambda: frozenset({88, 99})):
        assert ins.own_window_handles() == frozenset({88, 99})


def test_a_missing_console_door_does_not_hide_our_windows():
    # The exact release-breaking bug: the console lookup raised, and with it
    # the whole set came back empty, so Jarvis stopped recognising itself.
    def boom():
        raise AttributeError("module has no GetConsoleWindow")

    with OwnDoors(console=boom, process=lambda: frozenset({88})):
        assert ins.own_window_handles() == frozenset({88})


def test_a_broken_window_scan_does_not_hide_the_console():
    def boom():
        raise OSError("EnumWindows failed")

    with OwnDoors(console=lambda: 77, process=boom):
        assert ins.own_window_handles() == frozenset({77})


def test_owning_no_windows_at_all_is_remembered_too():
    # A run with neither console nor window must not re-ask Windows on every
    # single question just because the answer happens to be empty.
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return frozenset()

    with OwnDoors(console=lambda: 0, process=lambda: frozenset()):
        real = ins.own_window_handles
        ins.own_window_handles = counted
        try:
            for _ in range(4):
                ins.is_own_handle(123)
        finally:
            ins.own_window_handles = real
    assert calls["n"] == 1, calls
