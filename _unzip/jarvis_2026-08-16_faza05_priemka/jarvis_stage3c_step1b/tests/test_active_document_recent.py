"""
Issue 009 — step 5: restoring a path from Windows Recent and the Office MRU.

This is the level that answers "где лежит этот файл" for every application
that cannot be asked directly — Notepad, VS Code, PDF readers, archivers.

The two OS-touching parts are seams (_list_recent_lnks, _shortcut_target,
_office_mru_values), so everything here runs without Windows, without the
registry and without COM. What is pinned down: the MRU value format, name
matching, ordering (MRU before Recent, newest .lnk first), deduplication, the
cost caps, lock-file rejection, and the rule that this level never invents an
answer when nothing matches.

Run:  python -m pytest tests/test_active_document_recent.py -q
"""

from core.awareness import _inspectors as ins


_RECENT_SEAMS = ("_list_recent_lnks", "_shortcut_target", "_office_mru_values")


class Fakes:
    """Swap the three Recent/MRU seams and record what was asked of them."""

    def __init__(self, lnks=None, targets=None, mru=None):
        self.lnks = lnks or []          # [(lnk path, mtime)]
        self.targets = targets or {}    # lnk path -> target path
        self.mru = mru or []            # raw registry strings
        self.resolved = []              # which .lnk files were actually opened
        self._saved = {}

    def __enter__(self):
        ins.reset()   # a swapped seam invalidates every remembered shortcut
        for seam in _RECENT_SEAMS:
            self._saved[seam] = getattr(ins, seam)
        ins._list_recent_lnks = lambda limit=ins._RECENT_SCAN_MAX: self.lnks[:limit]
        ins._office_mru_values = lambda process="": list(self.mru)

        def resolve(lnk):
            self.resolved.append(lnk)
            return self.targets.get(lnk)

        ins._shortcut_target = resolve
        return self

    def __exit__(self, *_exc):
        for seam, original in self._saved.items():
            setattr(ins, seam, original)
        ins.reset()
        return False


def _lnk(name, mtime):
    return (rf"C:\Users\u\AppData\Roaming\Microsoft\Windows\Recent\{name}.lnk", mtime)


# ─────────────────────────────────────────────────────────────────────────────
# parse_mru_value — pure, no OS at all
# ─────────────────────────────────────────────────────────────────────────────

def test_mru_value_keeps_only_the_path():
    raw = r"[F00000000][T01DB1234567890AB][O00000000]*C:\Работа\Отчёт.docx"
    assert ins.parse_mru_value(raw) == r"C:\Работа\Отчёт.docx"


def test_mru_value_handles_a_bare_path_and_a_unc_share():
    assert ins.parse_mru_value(r"C:\a\b.txt") == r"C:\a\b.txt"
    raw = r"[F00000000][T01DB][O00000000]*\\server\share\План.xlsx"
    assert ins.parse_mru_value(raw) == r"\\server\share\План.xlsx"


def test_mru_value_rejects_junk_and_cloud_urls():
    # A OneDrive/SharePoint entry is not a path on this disk — issue 009 says
    # answer honestly rather than hand back something unopenable.
    for raw in [
        None, "", "   ",
        "[F00000000][T01DB][O00000000]*https://d.docs.live.net/1234/Отчёт.docx",
        "[F00000000][T01DB][O00000000]*",
        r"Отчёт.docx",              # relative — proves nothing about location
        r"..\Отчёт.docx",
    ]:
        assert ins.parse_mru_value(raw) is None, raw


# ─────────────────────────────────────────────────────────────────────────────
# recent_candidates — the Recent folder
# ─────────────────────────────────────────────────────────────────────────────

def test_notepad_file_is_found_through_the_recent_folder():
    # The exact case from the real session: Notepad has no COM, only Recent.
    lnk = _lnk("test.txt", 300.0)
    with Fakes(lnks=[lnk], targets={lnk[0]: r"C:\Users\rdrr\Desktop\test.txt"}):
        assert ins.recent_candidates("test.txt") == [r"C:\Users\rdrr\Desktop\test.txt"]


def test_a_moved_file_is_reported_at_its_new_location():
    # Windows rewrites the shortcut when the file moves; we must follow it and
    # never repeat the stale Desktop path the old Jarvis used to invent.
    lnk = _lnk("test.txt", 300.0)
    new = r"C:\Users\rdrr\Desktop\Новая папка (2)\test.txt"
    with Fakes(lnks=[lnk], targets={lnk[0]: new}):
        assert ins.recent_candidates("test.txt") == [new]


def test_unrelated_recent_entries_are_ignored():
    rows = [_lnk("бюджет.xlsx", 300.0), _lnk("котик.jpg", 299.0)]
    targets = {rows[0][0]: r"C:\a\бюджет.xlsx", rows[1][0]: r"C:\a\котик.jpg"}
    with Fakes(lnks=rows, targets=targets) as f:
        assert ins.recent_candidates("test.txt") == []
        # Nothing matched by name, so not a single shortcut was opened.
        assert f.resolved == []


def test_hidden_extension_still_matches_by_stem():
    # Windows hides extensions, so the caption may say "Отчёт" for "Отчёт.docx".
    lnk = _lnk("Отчёт.docx", 300.0)
    with Fakes(lnks=[lnk], targets={lnk[0]: r"C:\Работа\Отчёт.docx"}):
        assert ins.recent_candidates("Отчёт") == [r"C:\Работа\Отчёт.docx"]


def test_newest_shortcut_comes_first():
    # Two files with the same name: the one opened most recently is the better
    # guess, and the cascade shows the rest as candidates instead of guessing.
    old = _lnk("Отчёт.docx", 100.0)
    new = (r"C:\R\Отчёт.docx (2).lnk", 900.0)
    rows = sorted([old, new], key=lambda r: r[1], reverse=True)
    targets = {old[0]: r"C:\Старое\Отчёт.docx", new[0]: r"C:\Новое\Отчёт.docx"}
    with Fakes(lnks=rows, targets=targets):
        got = ins.recent_candidates("Отчёт.docx")
    assert got == [r"C:\Новое\Отчёт.docx", r"C:\Старое\Отчёт.docx"]


def test_broken_shortcuts_and_relative_targets_are_dropped():
    a = _lnk("Отчёт.docx", 300.0)
    b = (r"C:\R\Отчёт.docx (2).lnk", 200.0)
    c = (r"C:\R\Отчёт.docx (3).lnk", 100.0)
    targets = {a[0]: None, b[0]: "Отчёт.docx", c[0]: r"C:\ok\Отчёт.docx"}
    with Fakes(lnks=[a, b, c], targets=targets):
        assert ins.recent_candidates("Отчёт.docx") == [r"C:\ok\Отчёт.docx"]


def test_office_lock_files_are_never_offered():
    lnk = _lnk("~$Отчёт.docx", 300.0)
    with Fakes(lnks=[lnk], targets={lnk[0]: r"C:\Работа\~$Отчёт.docx"}):
        assert ins.recent_candidates("~$Отчёт.docx") == []


def test_the_same_file_seen_twice_is_returned_once():
    a = _lnk("Отчёт.docx", 300.0)
    b = (r"C:\R\Отчёт.docx (2).lnk", 200.0)
    same = r"C:\Работа\Отчёт.docx"
    with Fakes(lnks=[a, b], targets={a[0]: same, b[0]: same.upper()}):
        assert ins.recent_candidates("Отчёт.docx") == [same]


# ─────────────────────────────────────────────────────────────────────────────
# recent_candidates — Office MRU and the cost caps
# ─────────────────────────────────────────────────────────────────────────────

def test_office_mru_is_used_and_comes_before_the_recent_folder():
    lnk = _lnk("Отчёт.docx", 300.0)
    mru = [r"[F00000000][T01DB][O00000000]*C:\MRU\Отчёт.docx"]
    with Fakes(lnks=[lnk], targets={lnk[0]: r"C:\Recent\Отчёт.docx"}, mru=mru):
        got = ins.recent_candidates("Отчёт.docx", process="WINWORD.EXE")
    assert got == [r"C:\MRU\Отчёт.docx", r"C:\Recent\Отчёт.docx"]


def test_mru_entries_with_other_names_are_ignored():
    mru = [
        r"[F00000000][T01DB][O00000000]*C:\MRU\Другой.docx",
        r"[F00000000][T01DB][O00000000]*C:\MRU\Отчёт.docx",
    ]
    with Fakes(mru=mru):
        assert ins.recent_candidates("Отчёт.docx", process="WINWORD.EXE") == [
            r"C:\MRU\Отчёт.docx"
        ]


def test_only_matching_shortcuts_are_opened_and_no_more_than_the_cap():
    # Opening a .lnk is a COM call. With 300 unrelated entries in Recent none of
    # them may be opened, or the 800 ms budget is gone.
    rows = [_lnk(f"файл{i}.txt", 1000.0 - i) for i in range(300)]
    rows += [(rf"C:\R\Отчёт.docx ({i}).lnk", 500.0 - i) for i in range(30)]
    targets = {p: rf"C:\Папка{i}\Отчёт.docx" for i, (p, _m) in enumerate(rows)}
    with Fakes(lnks=rows, targets=targets) as f:
        got = ins.recent_candidates("Отчёт.docx", limit=50)
    assert len(f.resolved) <= ins._RECENT_RESOLVE_MAX
    assert all("Отчёт" in p for p in f.resolved)
    assert len(got) <= ins._RECENT_RESOLVE_MAX


def test_limit_is_respected():
    rows = [(rf"C:\R\Отчёт.docx ({i}).lnk", 500.0 - i) for i in range(5)]
    targets = {p: rf"C:\Папка{i}\Отчёт.docx" for i, (p, _m) in enumerate(rows)}
    with Fakes(lnks=rows, targets=targets):
        assert len(ins.recent_candidates("Отчёт.docx", limit=2)) == 2


# ─────────────────────────────────────────────────────────────────────────────
# The module must never raise, whatever the OS does
# ─────────────────────────────────────────────────────────────────────────────

def test_an_empty_or_missing_name_asks_the_os_nothing():
    with Fakes(lnks=[_lnk("a.txt", 1.0)]) as f:
        assert ins.recent_candidates("") == []
        assert ins.recent_candidates(None) == []
        assert f.resolved == []


def test_an_exploding_seam_degrades_to_an_empty_list():
    def boom(*_a, **_k):
        raise RuntimeError("registry on fire")

    saved = (ins._list_recent_lnks, ins._office_mru_values)
    try:
        ins._list_recent_lnks = boom
        ins._office_mru_values = boom
        assert ins.recent_candidates("Отчёт.docx", process="WINWORD.EXE") == []
    finally:
        ins._list_recent_lnks, ins._office_mru_values = saved


def test_a_non_office_process_does_not_touch_the_registry():
    assert ins._office_mru_values("notepad.exe") == []
    assert ins._office_mru_values("") == []


# ─────────────────────────────────────────────────────────────────────────────
# End to end through the cascade: Notepad now gets a real answer
# ─────────────────────────────────────────────────────────────────────────────

def test_notepad_question_now_returns_a_verified_path():
    moved = r"C:\Users\rdrr\Desktop\Новая папка (2)\test.txt"
    lnk = _lnk("test.txt", 300.0)
    saved_window, saved_exists = ins.active_window, ins.path_exists
    ins.reset()
    try:
        ins.active_window = lambda: {
            "title": "test.txt - Notepad", "process": "Notepad.exe", "hwnd": 7,
        }
        ins.path_exists = lambda p: p == moved
        with Fakes(lnks=[lnk], targets={lnk[0]: moved}):
            r = ins.active_document(use_cache=False)
    finally:
        ins.active_window, ins.path_exists = saved_window, saved_exists
        ins.reset()

    assert r["found"] is True
    assert r["path"] == moved
    assert r["source"] == ins.SOURCE_RECENT
    assert r["confidence"] == ins.CONF_PROBABLE
    assert r["kind"] == ins.KIND_LOCAL


def test_a_recent_hit_that_no_longer_exists_is_not_reported():
    # The whole point of issue 009: never name a path that is not on disk.
    lnk = _lnk("test.txt", 300.0)
    saved_window, saved_exists = ins.active_window, ins.path_exists
    ins.reset()
    try:
        ins.active_window = lambda: {
            "title": "test.txt - Notepad", "process": "Notepad.exe", "hwnd": 7,
        }
        ins.path_exists = lambda _p: False
        with Fakes(lnks=[lnk], targets={lnk[0]: r"C:\Users\rdrr\Desktop\test.txt"}):
            r = ins.active_document(use_cache=False)
    finally:
        ins.active_window, ins.path_exists = saved_window, saved_exists
        ins.reset()

    assert r["found"] is False
    assert r["path"] is None
    assert r["name"] == "test.txt"


def test_two_same_named_files_are_offered_as_a_question_not_a_guess():
    a = r"C:\Работа\test.txt"
    b = r"C:\Users\rdrr\Desktop\test.txt"
    rows = [(r"C:\R\test.txt.lnk", 900.0), (r"C:\R\test.txt (2).lnk", 100.0)]
    saved_window, saved_exists = ins.active_window, ins.path_exists
    ins.reset()
    try:
        ins.active_window = lambda: {
            "title": "test.txt - Notepad", "process": "Notepad.exe", "hwnd": 7,
        }
        ins.path_exists = lambda _p: True
        with Fakes(lnks=rows, targets={rows[0][0]: a, rows[1][0]: b}):
            r = ins.active_document(use_cache=False)
    finally:
        ins.active_window, ins.path_exists = saved_window, saved_exists
        ins.reset()

    assert r["path"] is None
    assert r["candidates"] == [a, b]
    assert "уточнить" in r["reason"]


def test_terminal_and_application_windows_never_reach_the_recent_scan():
    for title, process in [
        (r"C:\Windows\System32\cmd.exe - python x.py", "cmd.exe"),
        ("Happ 3.3.6 (591)", ""),
    ]:
        saved_window = ins.active_window
        ins.reset()
        try:
            ins.active_window = lambda t=title, p=process: {
                "title": t, "process": p, "hwnd": 1,
            }
            with Fakes(lnks=[_lnk("cmd.exe", 1.0)]) as f:
                r = ins.active_document(use_cache=False)
        finally:
            ins.active_window = saved_window
            ins.reset()
        assert r["found"] is False, title
        assert f.resolved == [], title


# ── Step 6k — a shortcut resolved once is not resolved again ───────────────────


def test_a_shortcut_is_opened_only_once_for_repeated_questions():
    # Opening a .lnk is a COM round-trip; asking twice within seconds must not
    # pay for it twice. This is what turned a 4-second answer into an instant
    # one when the same question is repeated.
    lnk = _lnk("test.txt", 300.0)
    with Fakes(lnks=[lnk], targets={lnk[0]: r"C:\Users\rdrr\Desktop\test.txt"}) as f:
        first = ins.recent_candidates("test.txt")
        second = ins.recent_candidates("test.txt")
    assert first == second == [r"C:\Users\rdrr\Desktop\test.txt"]
    assert f.resolved == [lnk[0]], f.resolved


def test_a_rewritten_shortcut_is_read_again():
    # Windows rewrites the .lnk when the file moves, which changes its mtime.
    # A remembered answer must never survive that — otherwise we would keep
    # naming the old location of a file the user has already moved.
    path = _lnk("test.txt", 300.0)[0]
    old_target = r"C:\Users\rdrr\Desktop\test.txt"
    new_target = r"C:\Users\rdrr\Desktop\iii\test.txt"
    with Fakes(lnks=[(path, 300.0)], targets={path: old_target}) as f:
        assert ins.recent_candidates("test.txt") == [old_target]
    with Fakes(lnks=[(path, 900.0)], targets={path: new_target}) as f2:
        assert ins.recent_candidates("test.txt") == [new_target]
        assert f2.resolved == [path]


def test_a_broken_shortcut_is_not_retried_every_time():
    # Broken links are the common case in a Recent folder. Remembering the
    # failure is what keeps a scan inside its time budget.
    lnk = _lnk("test.txt", 300.0)
    with Fakes(lnks=[lnk], targets={}) as f:
        assert ins.recent_candidates("test.txt") == []
        assert ins.recent_candidates("test.txt") == []
    assert f.resolved == [lnk[0]], f.resolved


def test_reset_forgets_every_remembered_shortcut():
    lnk = _lnk("test.txt", 300.0)
    target = r"C:\Users\rdrr\Desktop\test.txt"
    with Fakes(lnks=[lnk], targets={lnk[0]: target}) as f:
        ins.recent_candidates("test.txt")
        ins.reset()
        ins.recent_candidates("test.txt")
    assert f.resolved == [lnk[0], lnk[0]], f.resolved


def test_the_memory_of_shortcuts_cannot_grow_without_end():
    # A Recent folder can hold thousands of entries over a long session; the
    # cache is bounded so a machine left running for weeks cannot bloat.
    assert ins._LNK_CACHE_MAX >= 256
    ins.reset()
    for i in range(ins._LNK_CACHE_MAX + 50):
        ins._lnk_cache["lnk-%d" % i] = (0.0, None)
    real = ins._shortcut_target
    ins._shortcut_target = lambda p: None
    try:
        ins._shortcut_target_cached("overflow.lnk", 1.0)
    finally:
        ins._shortcut_target = real
    assert len(ins._lnk_cache) <= ins._LNK_CACHE_MAX
    ins.reset()
