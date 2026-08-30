# tests/test_perception.py
# Perception Core — three levels, tested separately on purpose.
#
#   1. sensors        — what the sources return (injected, never the real screen)
#   2. interpretation — caption + process → surface, as a table
#   3. dialogue       — the sentence the user actually hears
#
# Every fact these tests rely on is supplied by the test itself. Nothing here
# reads Windows, so the results are identical on the developer's machine, in CI
# and on the user's PC.

from core.awareness import _perception as pc
from core.awareness import _inspectors as ins


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

ARCHIVE = "Creative-Motion-Studio — копия (10) — BESTT — копия.7z"
ARCHIVE_PATH = r"C:\Users\rdrr\Downloads\Creative-Motion-Studio — копия (10) — BESTT — копия.7z"
WORD_PATH = r"C:\Users\rdrr\Desktop\Отчет.docx"


def _w(title, process="unknown.exe", hwnd=1000, **extra):
    window = {"title": title, "process": process, "hwnd": hwnd,
              "owner": 0, "toolwindow": False}
    window.update(extra)
    return window


class Sources:
    """
    Injects a whole desktop. Anything not supplied answers "nothing", so a test
    can never accidentally fall through to a live read.
    """

    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        pc.reset()
        defaults = {
            "active_window": lambda: None,
            "list_windows": lambda: [],
            "active_document": lambda deadline_s=None: ins.new_result(),
            "recent_candidates": lambda name, process="": [],
            "path_exists": lambda path: False,
            "is_own_window": lambda title, process="", hwnd=0: False,
            "snapshot": lambda: {},
        }
        defaults.update(self.overrides)
        pc.set_sources(**defaults)
        return pc

    def __exit__(self, *exc):
        pc.reset()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Level 1 — extensions and artifacts (pure)
# ─────────────────────────────────────────────────────────────────────────────

def test_extension_category_covers_the_obvious_families():
    assert pc.extension_category("7z") == "archive"
    assert pc.extension_category(".ZIP") == "archive"
    assert pc.extension_category("docx") == "document"
    assert pc.extension_category("py") == "code"
    assert pc.extension_category("mp4") == "media"
    assert pc.extension_category("png") == "image"


def test_unknown_extension_is_still_a_file_not_nothing():
    # The whole point of scoring instead of white-listing: a format nobody
    # anticipated must still produce "файл такой-то", not silence.
    assert pc.extension_category("qqq") == "file"
    assert pc.extension_category("") == ""
    assert pc.extension_category(None) == ""


def test_artifact_survives_separators_inside_the_file_name():
    # This exact caption used to be truncated at the first dash, so a 7z archive
    # was reported as "Creative-Motion-Studio".
    art = pc.extract_artifact(ARCHIVE, "7zfm")
    assert art is not None
    assert art["name"] == ARCHIVE
    assert art["extension"] == "7z"
    assert art["category"] == "archive"


def test_artifact_from_a_two_part_caption():
    art = pc.extract_artifact("Отчет.docx - Word", "WINWORD.EXE")
    assert art["name"] == "Отчет.docx"
    assert art["category"] == "document"


def test_artifact_marks_unsaved_changes():
    art = pc.extract_artifact("*Отчет.docx - Word", "WINWORD.EXE")
    assert art["dirty"] is True


def test_no_artifact_when_the_caption_names_no_file():
    assert pc.extract_artifact("Happ 3.3.6 (591)", "Happ.exe") is None
    assert pc.extract_artifact("", "") is None
    assert pc.extract_artifact("Загрузки", "explorer.exe") is None


def test_office_lock_file_is_never_an_artifact():
    assert pc.extract_artifact("~$Отчет.docx", "WINWORD.EXE") is None


# ─────────────────────────────────────────────────────────────────────────────
# Level 2 — interpretation table
# ─────────────────────────────────────────────────────────────────────────────

def _surface(title, process, **extra):
    with Sources():
        window = _w(title, process, **extra)
        return pc.score(window, pc.extract_artifact(title, process))["type"]


def test_interpretation_table():
    cases = [
        # caption, process, expected surface, why it matters
        (ARCHIVE, "7zFM.exe", "archive"),
        ("Отчет.docx - Word", "WINWORD.EXE", "document"),
        ("main.py - jarvis - Visual Studio Code", "Code.exe", "code"),
        ("Свадьба.mp4 - VLC media player", "vlc.exe", "media"),
        ("photo.png — Фотографии", "Photos.exe", "image"),
        ("Исследование | Notion - Google Chrome", "chrome.exe", "page"),
        (r"C:\Windows\System32\cmd.exe - python -m pytest -q", "cmd.exe", "terminal"),
        ("Загрузки", "explorer.exe", "folder"),
        ("Happ 3.3.6 (591)", "Happ.exe", "app"),
    ]
    for title, process, expected in cases:
        got = _surface(title, process)
        assert got == expected, f"{title!r} → {got}, ожидалось {expected}"


def test_a_file_decides_the_surface_even_for_an_unheard_of_program():
    # No table contains "SuperViewer". The file still answers the question.
    assert _surface("Смета.xlsx - SuperViewer 2031", "superviewer.exe") == "document"
    assert _surface("backup.tar.gz — ArchiveThing", "archivething.exe") == "archive"


def test_a_popup_is_a_dialog_whatever_it_is_called():
    # Owned window: the same rule Alt+Tab uses, so it works in every language
    # and for every program — no list of dialog titles required.
    assert _surface("Перевести эту страницу?", "chrome.exe", owner=11) == "dialog"
    assert _surface("Sokšš", "weird.exe", toolwindow=True) == "dialog"


def test_own_window_is_recognised_as_own():
    with Sources(is_own_window=lambda title, process="", hwnd=0: True):
        window = _w("J.A.R.V.I.S — MARK XXXV", "python3.12")
        assert pc.score(window, None)["type"] == "own"


def test_scoring_explains_itself():
    with Sources():
        verdict = pc.score(_w(ARCHIVE, "7zFM.exe"), pc.extract_artifact(ARCHIVE, "7zFM"))
    assert verdict["scores"].get("archive", 0) > 0
    assert verdict["reasons"]          # every decision carries its evidence


# ─────────────────────────────────────────────────────────────────────────────
# Level 2b — describe(): sources in, Subject out
# ─────────────────────────────────────────────────────────────────────────────

def test_archive_window_is_described_with_its_path():
    with Sources(
        active_window=lambda: _w(ARCHIVE, "7zFM.exe"),
        recent_candidates=lambda name, process="": [ARCHIVE_PATH],
        path_exists=lambda path: path == ARCHIVE_PATH,
    ) as p:
        subject = p.describe("foreground")
    assert subject["found"] is True
    assert subject["surface"] == "archive"
    assert subject["artifact"]["path"] == ARCHIVE_PATH
    assert "архив" in p.render_subject(subject)


def test_the_document_cascade_still_wins_when_it_has_an_exact_answer():
    exact = ins.new_result(found=True, path=WORD_PATH, name="Отчет.docx",
                           source=ins.SOURCE_COM, confidence=ins.CONF_EXACT)
    with Sources(
        active_window=lambda: _w("Отчет.docx - Word", "WINWORD.EXE"),
        active_document=lambda deadline_s=None: exact,
    ) as p:
        subject = p.describe("foreground")
    assert subject["artifact"]["path"] == WORD_PATH
    assert subject["artifact"]["confidence"] == ins.CONF_EXACT


def test_ambiguous_name_asks_instead_of_guessing():
    twins = [r"C:\a\Смета.xlsx", r"C:\b\Смета.xlsx"]
    with Sources(
        active_window=lambda: _w("Смета.xlsx - SomeEditor", "someeditor.exe"),
        recent_candidates=lambda name, process="": twins,
        path_exists=lambda path: path in twins,
    ) as p:
        subject = p.describe("foreground")
        text = p.render_subject(subject)
    assert subject["artifact"]["path"] is None
    assert "Уточни" in text


def test_no_foreground_falls_back_to_the_ingested_snapshot():
    # The honest source of truth when there is no live read — and the reason
    # the suite no longer reports the terminal it runs in.
    with Sources(snapshot=lambda: {"active_window": _w("Report - Word", "WINWORD.EXE")}) as p:
        subject = p.describe("foreground")
    assert subject["found"] is True
    assert "Word" in subject["window"]["title"]


def test_nothing_visible_is_said_plainly():
    with Sources() as p:
        subject = p.describe("foreground")
        text = p.render_subject(subject)
    assert subject["found"] is False
    assert "не вижу" in text.lower()


def test_a_window_can_be_asked_about_by_name():
    # "нет, а happ" — a question, so it must be answered without touching the
    # desktop. Before this existed the only route was focus_window, an action.
    windows = [
        _w("Исследование | Notion - Google Chrome", "chrome.exe", hwnd=1),
        _w("Happ 3.3.6 (591)", "Happ.exe", hwnd=2),
        _w("Отчет.docx - Word", "WINWORD.EXE", hwnd=3),
    ]
    with Sources(list_windows=lambda: windows) as p:
        subject = p.describe("window", "happ")
    assert subject["found"] is True
    assert subject["window"]["hwnd"] == 2


def test_name_lookup_is_case_and_form_insensitive():
    windows = [_w("Happ 3.3.6 (591)", "Happ.exe", hwnd=2)]
    for hint in ("HAPP", "happ", "Happ 3.3.6"):
        with Sources(list_windows=lambda: windows) as p:
            assert p.describe("window", hint)["found"] is True, hint


def test_unknown_name_says_so_without_pretending():
    with Sources(list_windows=lambda: [_w("Word", "WINWORD.EXE")]) as p:
        subject = p.describe("window", "telegram")
    assert subject["found"] is False
    assert "telegram" in subject["reason"]


def test_all_windows_lists_everything_except_jarvis_itself():
    windows = [_w("A", "a.exe", hwnd=1), _w("B", "b.exe", hwnd=2)]
    with Sources(
        list_windows=lambda: windows,
        is_own_window=lambda title, process="", hwnd=0: title == "A",
    ) as p:
        subject = p.describe("all")
        text = p.render_subject(subject)
    assert len(subject["alternatives"]) == 1
    assert "B" in text


def test_describe_never_raises_even_when_every_source_is_broken():
    def boom(*a, **kw):
        raise RuntimeError("COM упал")
    with Sources(active_window=boom, list_windows=boom, snapshot=boom,
                 active_document=boom, recent_candidates=boom,
                 path_exists=boom, is_own_window=boom) as p:
        for target in ("foreground", "window", "all", "previous"):
            subject = p.describe(target, "x")
            assert isinstance(subject, dict)
            assert p.render_subject(subject)


# ─────────────────────────────────────────────────────────────────────────────
# Level 3 — dialogue
# ─────────────────────────────────────────────────────────────────────────────

def test_repeating_the_question_does_not_repeat_the_answer():
    # "а щас?" five times used to produce the same sentence five times, which
    # reads as a broken assistant rather than as "nothing changed".
    pc.reset()
    first = pc.dedupe_answer("Сейчас открыт документ «Отчет.docx».")
    second = pc.dedupe_answer("Сейчас открыт документ «Отчет.docx».")
    third = pc.dedupe_answer("Сейчас открыт архив «Архив.7z».")
    assert first.startswith("Сейчас")
    assert second.startswith("С прошлого раза")
    assert third.startswith("Сейчас")
    pc.reset()


def test_subject_reports_whether_the_screen_changed():
    seen = {"n": 0}

    def window():
        seen["n"] += 1
        return _w("A.txt - Editor", "editor.exe") if seen["n"] < 3 \
            else _w("B.txt - Editor", "editor.exe")

    with Sources(active_window=window) as p:
        assert p.describe("foreground")["changed"] is None   # first answer ever
        assert p.describe("foreground")["changed"] is False  # same window
        assert p.describe("foreground")["changed"] is True   # different file


def test_previous_returns_the_last_answer_without_looking_again():
    with Sources(active_window=lambda: _w("A.txt - Editor", "editor.exe")) as p:
        p.describe("foreground")
        again = p.describe("previous")
    assert again["window"]["title"] == "A.txt - Editor"


def test_own_window_answer_explains_the_substitution_once():
    with Sources(active_window=lambda: dict(
            _w("Отчет.docx - Word", "WINWORD.EXE"), substituted=True)) as p:
        text = p.render_subject(p.describe("foreground"))
    assert "Своё окно" in text
    assert "Отчет.docx" in text


def test_an_answer_never_denies_a_category_it_was_not_asked_about():
    # The old layer answered "сейчас не окно документа" to the question "что
    # активно". A description must state what IS there.
    with Sources(active_window=lambda: _w("Happ 3.3.6 (591)", "Happ.exe")) as p:
        text = p.render_subject(p.describe("foreground"))
    assert "Happ" in text
    assert "не документ" not in text.lower()


def test_trace_contains_the_evidence_behind_the_answer():
    with Sources(active_window=lambda: _w(ARCHIVE, "7zFM.exe")) as p:
        dump = p.trace(p.describe("foreground"))
    assert "surface" in dump and "evidence" in dump


# ─────────────────────────────────────────────────────────────────────────────
# Wiring — the resolver must route through the same description
# ─────────────────────────────────────────────────────────────────────────────

def test_resolver_routes_category_free_kinds_to_perception():
    from core.awareness import _resolver
    with Sources(active_window=lambda: _w(ARCHIVE, "7zFM.exe")):
        for kind in ("active", "foreground", "what_is_active", "current_window"):
            result = _resolver.resolve(kind)
            assert result["found"] is True, kind
            assert result["subject"]["surface"] == "archive"
            assert "архив" in _resolver.render(result)


def test_resolver_answers_about_a_named_window_read_only():
    from core.awareness import _resolver
    windows = [_w("Happ 3.3.6 (591)", "Happ.exe", hwnd=7)]
    with Sources(list_windows=lambda: windows):
        result = _resolver.resolve("named_window", "happ")
    assert result["found"] is True
    assert "Happ" in (result["title"] or "")


def test_active_app_and_the_foreground_agree():
    # Two readers of one screen was the reason a single conversation could
    # contain two different answers about the same window.
    from core.awareness import _resolver
    with Sources(active_window=lambda: _w("Happ 3.3.6 (591)", "Happ.exe")):
        app = _resolver.resolve("active_app")
        described = _resolver.resolve("active")
    assert app["title"] == described["subject"]["window"]["title"]


def test_document_question_about_an_archive_answers_with_the_archive():
    # "какой файл открыт" while a 7z window is in front: the cascade knows no
    # archiver, so it used to deny. The description takes over.
    from core.awareness import _resolver
    with Sources(
        active_window=lambda: _w(ARCHIVE, "7zFM.exe"),
        active_document=lambda deadline_s=None: ins.new_result(
            reason="Не удалось определить документ"),
        recent_candidates=lambda name, process="": [ARCHIVE_PATH],
        path_exists=lambda path: path == ARCHIVE_PATH,
    ):
        result = _resolver.resolve("active_document")
        text = _resolver.render(result)
    assert result["found"] is True
    assert ARCHIVE_PATH in text


def test_an_unknown_kind_describes_instead_of_refusing():
    from core.awareness import _resolver
    with Sources(active_window=lambda: _w("Happ 3.3.6 (591)", "Happ.exe")):
        result = _resolver.resolve("whatever_the_model_invented")
    assert result["found"] is True
    assert "Happ" in (result["title"] or "")
