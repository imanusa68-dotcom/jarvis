# core/awareness/_inspectors.py
# Issue 009 — "active document" inspectors: WHICH document is open right now.
#
# Called STRICTLY on command (a user question / a referent like "this document"),
# NEVER from the background daemon (_watchers.run_loop). Nothing here is polled,
# cached long-term, or pushed into the system prompt.
#
# Step 1 of the cascade lives in this file and is PURE: parse_title() turns a
# window title + process name into a partial DocResult using nothing but string
# rules — no OS calls, no COM, no imports beyond the stdlib. Everything that can
# fail (COM, Recent, search) is added in later steps behind seam functions, so
# this layer stays 100% testable without Windows.
#
# Contract (DocResult), see docs/issues/009-active-document-inspectors.md:
#   found       bool        — do we have a usable, verified answer?
#   path        str|None    — absolute LOCAL path, only if isabs() and exists()
#   name        str|None    — file name; present even when path is None
#   source      str         — "title" | "com" | "recent" | "uia" | "none"
#   confidence  str         — "exact" | "probable" | "name_only" | "none"
#   dirty       bool|None   — unsaved changes; None means "unknown" (callers such
#                             as issue 010 MUST treat None as if it were True)
#   candidates  list[str]   — >1 means ambiguous: ask the user, never guess
#   kind        str         — "local_file" | "cloud_document" | "unsaved"
#                             | "web_page" | "unknown"
#   app         str|None    — application label taken from the title
#   context     str|None    — middle title segment (VS Code project, Word mode…)
#   reason      str         — short human-readable Russian explanation
#   elapsed_ms  int         — observability; filled by the orchestrator
#
# Invariant: this module NEVER raises. Every unknown input degrades into a
# DocResult with confidence "none"/"name_only" and an honest `reason`.

from __future__ import annotations

import re

# ─────────────────────────────────────────────────────────────────────────────
# Contract
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_TITLE = "title"
SOURCE_COM = "com"
SOURCE_RECENT = "recent"
SOURCE_UIA = "uia"
SOURCE_NONE = "none"

CONF_EXACT = "exact"
CONF_PROBABLE = "probable"
CONF_NAME_ONLY = "name_only"
CONF_NONE = "none"

KIND_LOCAL = "local_file"
KIND_CLOUD = "cloud_document"
KIND_UNSAVED = "unsaved"
KIND_WEB = "web_page"
KIND_UNKNOWN = "unknown"

# Ranking used by the orchestrator to decide whether to try the next, more
# expensive cascade level. Higher is better; "exact" stops the cascade.
_CONF_RANK = {CONF_NONE: 0, CONF_NAME_ONLY: 1, CONF_PROBABLE: 2, CONF_EXACT: 3}


def confidence_rank(confidence: str) -> int:
    """Numeric rank of a confidence level (unknown values rank lowest)."""
    return _CONF_RANK.get(confidence, 0)


def new_result(**over) -> dict:
    """An empty DocResult. Single place where the contract's shape is defined."""
    result = {
        "found": False,
        "path": None,
        "name": None,
        "source": SOURCE_NONE,
        "confidence": CONF_NONE,
        "dirty": None,
        "candidates": [],
        "kind": KIND_UNKNOWN,
        "app": None,
        "context": None,
        "reason": "",
        "elapsed_ms": 0,
    }
    result.update(over)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Title parsing tables (data, not branches — new apps are one row, no new logic)
# ─────────────────────────────────────────────────────────────────────────────

# Where the document name sits inside the title, per process (lowercase, no .exe):
#   "before_app" — everything before the trailing application segment
#                  ("My - notes.docx - Word"        -> "My - notes.docx")
#   "first"      — the first segment only
#                  ("main.py - jarvis - VS Code"    -> "main.py")
_DOC_POSITION = {
    "winword": "before_app",
    "excel": "before_app",
    "powerpnt": "before_app",
    "notepad": "before_app",
    "wordpad": "before_app",
    "code": "first",
    "code - insiders": "first",
    "cursor": "first",
    "windsurf": "first",
    "sublime_text": "first",
    "notepad++": "first",
    "pycharm64": "first",
    "idea64": "first",
}
_DOC_POSITION_DEFAULT = "first"

# Processes whose titles are web pages, never local files. Stops the cascade at
# level 1: "Отчёт - Google Документы - Chrome" must NOT become a file path.
_BROWSER_PROCESSES = {
    "chrome", "msedge", "firefox", "opera", "opera_gx", "brave", "vivaldi",
    "iexplore", "safari", "browser", "yandex", "chromium", "waterfox", "tor",
}

# Shells and file managers. Their captions look file-like ("C:\Windows\System32\
# cmd.exe - python ...", a folder name in Explorer), so without this list the
# parser happily reports "cmd.exe" as the open document. Found by a real run.
_TERMINAL_PROCESSES = {
    "cmd", "powershell", "pwsh", "windowsterminal", "wt", "conhost",
    "mintty", "bash", "sh", "alacritty", "wezterm", "hyper", "putty",
}
_EXPLORER_PROCESSES = {"explorer", "totalcmd", "totalcmd64", "far", "doublecmd"}

# Leading markers meaning "unsaved changes" (Office/editors: *, VS Code: ●/•).
_DIRTY_MARKERS = ("*", "\u25cf", "\u2022", "\u25cb", "\u2219")

# Trailing decorations to drop: "[Режим совместимости]", "(Read-Only)", …
_DECORATION_RE = re.compile(
    r"\s*[\[\(](?:[^\[\]\(\)]{0,60})[\]\)]\s*$"
)

# Segment separator: hyphen, en dash or em dash surrounded by whitespace.
_SEGMENT_RE = re.compile(r"\s+[-\u2013\u2014]\s+")

# Typical names of documents that were never saved to disk. Matching one of
# these means we must answer honestly instead of resolving a same-named file.
_UNSAVED_RE = re.compile(
    r"^(?:"
    r"документ\s*\d*|book\s*\d*|книга\s*\d*|document\s*\d*|"
    r"презентация\s*\d*|presentation\s*\d*|"
    r"безымянный|без\s+имени|untitled|new\s+file|новый\s+файл|"
    r"новый\s+текстовый\s+документ"
    r")$",
    re.IGNORECASE,
)

# A plausible file extension: 1..8 letters/digits after the last dot, and at
# least one letter. The letter requirement rejects version numbers: without it
# the caption "Happ 3.3.6" ends in ".6" and gets treated as a file. Real run.
_EXT_RE = re.compile(r"\.(?=[A-Za-z0-9]{1,8}$)[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*$")

# A dotted acronym used as a window caption: "J.A.R.V.I.S", "S.T.A.L.K.E.R".
_ACRONYM_RE = re.compile(r"(?:^|[\s(\[\u2014\u2013-])(?:[A-Za-z]\.){2,}[A-Za-z]$")

# The only single-letter extensions worth honouring (C/Fortran/R/Objective-C).
_SHORT_EXTENSIONS = {"c", "h", "r", "m", "f"}

# Jarvis's own window. It runs under a bare "python" process, so no process list
# can identify it; the caption can. Reporting it as a document is always wrong.
# Deliberately anchored to the START of the caption and to the dotted brand: a
# loose "jarvis" anywhere would also swallow "main.c - jarvis - Visual Studio
# Code", where jarvis is just the project folder and the document is real.
_OWN_WINDOW_RE = re.compile(
    r"^j\s*\.\s*a\s*\.\s*r\s*\.\s*v\s*\.\s*i\s*\.\s*s\b", re.IGNORECASE
)
_OWN_PROCESSES = ("python", "pythonw")


# Recognising our own window by CAPTION only works while the caption says so.
# Run under pytest, under uv, from cmd, from a shortcut or renamed by the user,
# and the caption says something else entirely — yet the window still belongs to
# us, and answering "the active app is cmd.exe running pytest" is never what was
# asked. The handle knows for certain: our own console window, plus every
# top-level window owned by our own process id.
_OWN_HANDLE_TTL_S = 5.0
_own_handles: dict = {"set": frozenset(), "ts": 0.0}


def console_window() -> int:
    """OS seam: handle of our own console window, 0 when we have none.

    Deliberately tries several doors. win32gui has no GetConsoleWindow at all
    (that cost us a whole release), win32console is not always importable, and
    kernel32 is always there. One missing door must never close the others.
    """
    try:
        import win32console  # type: ignore
        return int(win32console.GetConsoleWindow() or 0)
    except Exception:
        pass
    try:
        import ctypes  # stdlib, present everywhere
        return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)  # type: ignore
    except Exception:
        return 0


def process_window_handles() -> frozenset:
    """OS seam: every top-level window owned by our own process id."""
    handles: set = set()
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return frozenset()

    pid = os.getpid()

    def collect(hwnd, _unused):
        try:
            _tid, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if int(window_pid or 0) == pid:
                handles.add(int(hwnd))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        pass
    return frozenset(handles)


def own_window_handles() -> frozenset:
    """Handles that belong to Jarvis: our console plus our own windows."""
    handles: set = set()
    try:
        console = int(console_window() or 0)
        if console:
            handles.add(console)
    except Exception:
        pass
    try:
        handles.update(process_window_handles())
    except Exception:
        pass
    return frozenset(handles)


def is_own_handle(hwnd) -> bool:
    """True when this handle belongs to Jarvis. Cached: the watcher asks often."""
    try:
        handle = int(hwnd or 0)
    except Exception:
        return False
    if not handle:
        return False
    now = time.monotonic()
    with _state_lock:
        fresh = (now - _own_handles["ts"]) < _OWN_HANDLE_TTL_S and _own_handles["ts"] > 0.0
        known = _own_handles["set"] if fresh else None
    if known is None:
        try:
            known = own_window_handles()
        except Exception:
            # The layer never breaks an answer because a Windows call misfired.
            return False
        with _state_lock:
            _own_handles.update({"set": known, "ts": now})
    return handle in known


def is_own_window(title: str, process: str = "", hwnd=0) -> bool:
    """True for Jarvis's own console/UI window — by handle first, caption second."""
    if hwnd and is_own_handle(hwnd):
        return True
    text = (title or "").strip()
    if _OWN_WINDOW_RE.search(text):
        return True
    proc = normalize_process(process)
    return proc.startswith(_OWN_PROCESSES) and text.lower().startswith("jarvis")

# Applications that really do show an open document in their caption. Used only
# when the caption has NO extension: "Отчёт - Word" is a document, but
# "Happ 3.3.6 (591)" from an unknown app is just a program window and must not
# be announced as an open document.
_DOCUMENT_APPS = {
    "winword", "word", "excel", "powerpnt", "powerpoint", "visio", "mspub",
    "onenote", "notepad", "блокнот", "wordpad", "notepad++", "code",
    "code - insiders", "visual studio code", "cursor", "windsurf",
    "sublime_text", "sublime text", "pycharm64", "pycharm", "idea64",
    "intellij idea", "acrord32", "adobe acrobat reader", "sumatrapdf",
    "foxitreader", "libreoffice", "soffice", "writer", "calc", "impress",
}

# Characters that cannot appear in a Windows file name — if the candidate has
# them it is a window caption, not a file, so we refuse to call it a name.
_ILLEGAL_NAME_CHARS = set('<>:"/|?*')

# Office lock/scratch files must never be presented as the answer (see R11).
_LOCK_PREFIXES = ("~$", ".~")


def normalize_process(process: str | None) -> str:
    """'WINWORD.EXE' -> 'winword'. Empty string when unknown."""
    if not process:
        return ""
    text = str(process).strip().lower()
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def is_browser(process: str | None) -> bool:
    """True when the process only ever shows web content."""
    return normalize_process(process) in _BROWSER_PROCESSES


def has_extension(name: str | None) -> bool:
    """True when `name` ends with something that looks like a file extension."""
    if not name:
        return False
    m = _EXT_RE.search(name)
    if not m:
        return False
    # "J.A.R.V.I.S" ends in ".S" and used to pass as a file with extension "S",
    # so Jarvis announced its own window as an open document. A dotted acronym
    # is a name, not a file. Found by a real run.
    if _ACRONYM_RE.search(name.strip()):
        return False
    # One-letter extensions are almost always punctuation in a caption; only the
    # handful of real source-file ones are accepted.
    ext = m.group(0)[1:].lower()
    if len(ext) == 1 and ext not in _SHORT_EXTENSIONS:
        return False
    return True


def is_lock_name(name: str | None) -> bool:
    """Office/editor lock or scratch file ('~$report.docx')."""
    return bool(name) and name.lstrip().lower().startswith(_LOCK_PREFIXES)


def _clean(text: str | None) -> str:
    """Collapse whitespace and drop control characters. Never raises."""
    if not text:
        return ""
    return re.sub(r"[\s\u00a0\u200b]+", " ", str(text).replace("\x00", " ")).strip()


def _strip_dirty_marker(text: str) -> tuple[str, bool]:
    """('*Отчёт.docx', True) — leading unsaved-changes marker."""
    stripped = text
    dirty = False
    while stripped[:1] in _DIRTY_MARKERS:
        stripped = stripped[1:].lstrip()
        dirty = True
    return stripped, dirty


def _strip_decorations(text: str) -> str:
    """Drop trailing '[Режим совместимости]' / '(Read-Only)' style suffixes."""
    previous = None
    current = text
    while current != previous:
        previous = current
        current = _DECORATION_RE.sub("", current).strip()
    return current


def _basename(text: str) -> str:
    """Last path component, for editors that show a full path in the caption."""
    for sep in ("\\", "/"):
        if sep in text:
            text = text.rsplit(sep, 1)[-1]
    return text.strip()


def _looks_like_name(candidate: str) -> bool:
    """Reject captions that cannot be a file name at all."""
    if not candidate or len(candidate) > 255:
        return False
    return not any(ch in _ILLEGAL_NAME_CHARS for ch in candidate)


def parse_title(title: str | None, process: str | None = "") -> dict:
    """
    Cascade level 1 — the cheapest source. Pure: no OS calls, no exceptions.

    Reads the foreground window caption that the world model already keeps in
    memory (so this level costs ~0 ms and zero new OS work) and returns a
    partial DocResult. `path` is always None here: a title can never prove where
    a file lives — that is levels 2 and 3.
    """
    raw = _clean(title)
    if not raw:
        return new_result(
            source=SOURCE_TITLE,
            reason="Не удалось определить активное окно.",
        )

    proc = normalize_process(process)
    body, dirty = _strip_dirty_marker(raw)
    body = _strip_decorations(body)
    segments = [s for s in (p.strip() for p in _SEGMENT_RE.split(body)) if s]

    app = segments[-1] if len(segments) > 1 else None
    context = segments[1] if len(segments) > 2 else None

    # Web content never resolves to a local file, no matter how file-like the
    # tab title looks. Stop the cascade here (R8).
    if is_browser(proc):
        return new_result(
            source=SOURCE_TITLE,
            kind=KIND_WEB,
            name=None,
            app=app or proc,
            context=segments[0] if segments else None,
            dirty=dirty or None,
            confidence=CONF_NONE,
            reason="Активно окно браузера — это веб-страница, а не файл на диске.",
        )

    # A shell or a file manager is not a document editor: its caption is a
    # command line or a folder name, never a file the user is "looking at".
    if proc in _TERMINAL_PROCESSES:
        return new_result(
            source=SOURCE_TITLE,
            app=app or proc,
            dirty=None,
            confidence=CONF_NONE,
            reason="Активно окно терминала — это командная строка, а не документ.",
        )
    if proc in _EXPLORER_PROCESSES:
        return new_result(
            source=SOURCE_TITLE,
            app=app or proc,
            context=segments[0] if segments else None,
            dirty=None,
            confidence=CONF_NONE,
            reason="Активно окно проводника — это папка, а не открытый документ.",
        )

    # Jarvis's own window is not a document the user is working on. Without this
    # branch the very first question of a session ("what document is open?")
    # gets answered with Jarvis itself, because its window is in the foreground.
    if is_own_window(body, proc):
        return new_result(
            source=SOURCE_TITLE,
            app=app or proc,
            context=segments[0] if segments else None,
            dirty=None,
            confidence=CONF_NONE,
            reason="Активно окно самого Джарвиса — это не документ.",
        )

    # Some captions ARE one long file name containing dashes:
    # "Creative-Motion-Studio — копия (10) — BESTT — копия.7z". Splitting them
    # into segments truncates the name, so when only the LAST segment carries a
    # real extension the whole caption is the name. Found by a real run.
    position = _DOC_POSITION.get(proc, _DOC_POSITION_DEFAULT)
    whole_title_is_a_name = (
        len(segments) > 1
        and proc not in _DOC_POSITION
        and has_extension(segments[-1])
        and not any(has_extension(s) for s in segments[:-1])
    )
    if whole_title_is_a_name:
        candidate = body
        app = None
        context = None
    elif len(segments) <= 1:
        candidate = segments[0] if segments else ""
    elif position == "before_app":
        candidate = " - ".join(segments[:-1])
    else:
        candidate = segments[0]

    candidate = _basename(_strip_decorations(candidate.strip()))
    candidate, dirty_inner = _strip_dirty_marker(candidate)
    dirty = dirty or dirty_inner

    if not _looks_like_name(candidate):
        return new_result(
            source=SOURCE_TITLE,
            app=app,
            context=context,
            dirty=dirty or None,
            reason="Заголовок окна не содержит имени документа.",
        )

    # A never-saved document has no path anywhere — say so instead of resolving
    # some same-named file from Recent (R11).
    if _UNSAVED_RE.match(candidate):
        return new_result(
            source=SOURCE_TITLE,
            kind=KIND_UNSAVED,
            name=candidate,
            app=app,
            context=context,
            dirty=dirty or None,
            confidence=CONF_NONE,
            reason="Документ ещё не сохранён на диск, у него нет пути.",
        )

    # Lock/scratch artefacts are never the document the user means.
    if is_lock_name(candidate):
        return new_result(
            source=SOURCE_TITLE,
            app=app,
            context=context,
            dirty=dirty or None,
            reason="В заголовке служебный файл блокировки, а не документ.",
        )

    # No extension and no application known to display documents: this is a
    # program window ("Happ 3.3.6"), not an open file. Claiming a document here
    # would send the later cascade levels searching for a file that cannot
    # exist, and would make Jarvis say "открыт документ Happ".
    ext_hidden = not has_extension(candidate)
    known_app = (
        proc in _DOCUMENT_APPS
        or proc in _DOC_POSITION
        or normalize_process(app) in _DOCUMENT_APPS
    )
    # A real document window ALWAYS names the application after the file
    # ("test.txt - Блокнот", "Отчёт - Word"). A caption that is one lonely
    # segment with no extension coming from a document application is therefore
    # never the document itself: it is a dialog ("Открытие", "Сохранение как",
    # "Печать", "Свойства"), a start screen or a tool panel. This rule is
    # language- and application-independent on purpose — a hard-coded list of
    # dialog names can never cover every application and every locale.
    single_segment = len(segments) <= 1
    if ext_hidden and (not known_app or single_segment):
        return new_result(
            source=SOURCE_TITLE,
            app=app or (segments[0] if segments else None),
            context=context,
            dirty=dirty or None,
            confidence=CONF_NONE,
            reason=(
                "Активно служебное окно приложения (диалог или панель), "
                "а не сам документ."
                if known_app else
                "Активно окно приложения — имени открытого файла в заголовке нет."
            ),
        )

    return new_result(
        source=SOURCE_TITLE,
        kind=KIND_LOCAL,
        name=candidate,
        app=app,
        context=context,
        dirty=dirty or None,
        confidence=CONF_NAME_ONLY,
        reason=(
            "Имя взято из заголовка окна; расширение скрыто настройками Windows."
            if ext_hidden else
            "Имя взято из заголовка окна; путь пока не определён."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Steps 2-4: cascade orchestrator, timeout/single-flight machinery, sources.
#
# Every OS-touching operation is a module-level SEAM function that returns a
# neutral value on any failure (None / [] / False) and never raises. Tests
# monkeypatch the seams, so the whole cascade is verifiable without Windows,
# COM or Office.
# ─────────────────────────────────────────────────────────────────────────────

import os
import threading
import time

# One budget for the WHOLE cascade, not per level: the voice loop must get an
# answer back fast even when Office is wedged (target: <500 ms typical).
DEADLINE_S = 0.8

# Very short cache: the same question asked twice in a row must not re-enter COM,
# but switching document must be noticed immediately. Keyed by (hwnd, title).
CACHE_TTL_S = 2.0

# COM may spend at most this share of the total budget. Without the cap a wedged
# Office would eat the whole deadline and the cheap Recent/MRU level would never
# run — turning a recoverable miss into a dead end.
COM_BUDGET_SHARE = 0.7

# Circuit breaker: after this many abandoned COM threads, level 2 is switched
# off for the rest of the process (a wedged Office would otherwise leak a thread
# per question, since Python cannot kill a running thread).
HUNG_COM_LIMIT = 2

# Level 4 (UI Automation) is expensive and fragile: opt-in only.
UIA_ENV = "JARVIS_INSPECTOR_UIA"

# process (lowercase, no .exe) -> (COM ProgID, attribute path to the full name).
# DATA, not branches: supporting Visio/Publisher/AutoCAD is one more row, and on
# stage 8 this table moves into a plugin manifest without touching the logic.
_COM_ADAPTERS = {
    "winword": ("Word.Application", "ActiveDocument.FullName"),
    "excel": ("Excel.Application", "ActiveWorkbook.FullName"),
    "powerpnt": ("PowerPoint.Application", "ActivePresentation.FullName"),
    "visio": ("Visio.Application", "ActiveDocument.FullName"),
    "mspub": ("Publisher.Application", "ActiveDocument.FullName"),
}

# An Office application has ONE "active document" but MANY windows. Asking the
# application gives whichever document it considers active, which is not always
# the window in front of the user — that is how "Документ Microsoft Word.docx"
# was reported while a different Word window was on screen. These adapters walk
# the window collection instead and take the document of the matching window.
# name -> (progid, window collection attribute, document attribute on a window)
_COM_WINDOW_ADAPTERS = {
    "winword": ("Word.Application", "Windows", "Document"),
    "excel": ("Excel.Application", "Windows", "Parent"),
    "powerpnt": ("PowerPoint.Application", "Windows", "Presentation"),
}

_COM_WINDOW_LIMIT = 24     # a human never has more open; bounds the COM walk
COM_NO_MATCH = "\x00no-window-match"   # enumerated fine, no window matched

_state_lock = threading.Lock()
_cache: dict = {"key": None, "result": None, "ts": 0.0}

# The last document this process ever resolved to a real path. Used ONLY when
# the live read can say nothing at all (Jarvis's own window in front and the
# window behind it is a dialog, a panel or the desktop). Answering "I saw this
# one a moment ago", clearly marked as memory, beats "I do not know" — and it is
# never confused with a live reading because it carries its own reason string.
_LAST_DOC_TTL_S = 900.0
_last_doc: dict = {"path": None, "name": None, "ts": 0.0}
MEMORY_NOTE = (
    "Сейчас на переднем плане не документ; "
    "последний документ, который я видел"
)
_com_busy = False          # single-flight: one COM call at a time, process-wide
_hung_com = 0              # abandoned COM threads (never resurrected)


def reset() -> None:
    """Drop cached state and counters (tests, and after a Jarvis session ends)."""
    global _com_busy, _hung_com
    with _state_lock:
        _cache.update({"key": None, "result": None, "ts": 0.0})
        _last_doc.update({"path": None, "name": None, "ts": 0.0})
        _own_handles.update({"set": frozenset(), "ts": 0.0})
        _lnk_cache.clear()
        _com_busy = False
        _hung_com = 0


def stats() -> dict:
    """Observability snapshot: is COM wedged, is the breaker open?"""
    with _state_lock:
        return {
            "com_busy": _com_busy,
            "hung_com_threads": _hung_com,
            "com_disabled": _hung_com >= HUNG_COM_LIMIT,
            # Will level 2 even be attempted on the next question? False both
            # when the breaker has latched and while a call is still wedged.
            "level2_available": not (_hung_com >= HUNG_COM_LIMIT or _com_busy),
            "cached": _cache["key"] is not None,
        }


# ── Seams (the only places that touch the OS) ─────────────────────────────

SUBSTITUTED_NOTE = (
    "Окно Джарвиса "
    "на переднем плане "
    "— отвечаю про окно, "
    "которое было "
    "активно до него."
)


APP_SUBSTITUTED_NOTE = (
    "Окно Джарвиса "
    "на переднем плане "
    "— отвечаю про приложение, "
    "которое было "
    "активно до него."
)


def _measure(hwnd, title: str, process: str) -> dict:
    """Window dict plus the two flags that tell a real window from a popup."""
    owner = 0
    toolwindow = False
    try:
        import win32gui  # type: ignore

        owner = int(win32gui.GetWindow(hwnd, 4) or 0)  # 4 = GW_OWNER
        toolwindow = bool(int(win32gui.GetWindowLong(hwnd, -20) or 0) & 0x00000080)
    except Exception:
        pass
    return {
        "title": title,
        "process": process,
        "hwnd": int(hwnd),
        "owner": owner,
        "toolwindow": toolwindow,
    }


def _live_foreground() -> dict | None:
    """
    Ask Windows which window is in front RIGHT NOW.

    The watcher only polls every POLL_SECONDS (2 s), so its snapshot can be two
    seconds stale — enough to answer about the previous window when the question
    comes straight after a click. This read costs ~1 ms and happens only on
    command, never in a loop. Returns None off Windows or on any failure, and
    the world-model snapshot is used instead.
    """
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
        import psutil  # type: ignore

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindowVisible(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        if not title.strip():
            return None
        process = ""
        try:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid:
                process = psutil.Process(pid).name() or ""
        except Exception:
            process = ""
        def describe(handle):
            try:
                if not handle or not win32gui.IsWindowVisible(handle):
                    return None
                text = win32gui.GetWindowText(handle) or ""
                if not text.strip():
                    return None
                name = ""
                try:
                    _t, owner_pid = win32process.GetWindowThreadProcessId(handle)
                    if owner_pid:
                        name = psutil.Process(owner_pid).name() or ""
                except Exception:
                    name = ""
                return _measure(handle, text, name)
            except Exception:
                return None

        window = _measure(hwnd, title, process)
        # If a dialog or popup is in front, answer about the window it belongs to.
        return pick_real_window(window, describe) or window
    except Exception:
        return None


def _watcher_windows() -> tuple[dict | None, dict | None]:
    """(foreground, last non-Jarvis foreground) as last seen by the watcher."""
    def shape(raw) -> dict | None:
        raw = raw or {}
        title = raw.get("title") or ""
        if not title:
            return None
        return {
            "title": title,
            "process": raw.get("process") or "",
            "hwnd": int(raw.get("hwnd") or 0),
        }

    try:
        from core.awareness import _world_model
        snap = _world_model.snapshot() or {}
        return shape(snap.get("active_window")), shape(snap.get("last_user_window"))
    except Exception:
        return None, None


def active_window() -> dict | None:
    """
    {title, process, hwnd} of the window the question is about, or None.

    Live foreground read first, watcher snapshot as the fallback. When Jarvis's
    OWN console is in front — which is always the case while the user TYPES into
    the chat instead of speaking — the honest subject of the question is the
    window that was active just before it. That one is returned with
    substituted=True so the spoken answer can say where it came from.
    """
    live = _live_foreground()
    watched, last_user = _watcher_windows()
    window = live or watched
    if not window:
        # Neither source can name the foreground window right now (a splash
        # screen, an empty caption, a switch in progress). Saying "не удалось
        # определить активное окно" is useless while we still remember the
        # window the user actually worked in — answer about that one, marked.
        if last_user:
            remembered = dict(last_user)
            remembered["substituted"] = True
            return remembered
        return None
    if is_own_window(window["title"], window["process"], window.get("hwnd", 0)):
        if last_user and not is_own_window(last_user["title"], last_user["process"],
                                           last_user.get("hwnd", 0)):
            substituted = dict(last_user)
            substituted["substituted"] = True
            return substituted
    return window


# Windows drive path (C:\...), UNC share (\\server\share\...) or a POSIX root.
# Deliberately NOT os.path.isabs: that answers for the CURRENT platform, so a
# real Windows path would be judged "relative" when the tests run on Linux CI,
# and a bare Windows name would be judged "absolute" on none of them. The rule
# here is about the string itself, so it behaves identically everywhere.
_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/])")


def is_absolute_path(text: str | None) -> bool:
    """True only for a fully qualified path — the first half of the path invariant."""
    if not text:
        return False
    s = str(text).strip()
    return bool(_ABS_RE.match(s)) or s.startswith("/")


def path_exists(path: str) -> bool:
    """Existence straight from the filesystem — never from our own memory."""
    try:
        return bool(path) and os.path.exists(path)
    except OSError:
        return False


_CAPTION_INDEX_RE = re.compile(r":\d+$")


def window_matches(name: str | None, caption: str | None, full_name: str = "") -> bool:
    """
    Is this Office window the one whose caption we read off the screen?

    Word appends ":2" to the caption of a second view of the same document, and
    some apps caption a window with the file name while others use the full
    path, so both the caption and the file name are compared.
    """
    if not name:
        return False
    cap = _CAPTION_INDEX_RE.sub("", (caption or "").strip())
    if cap and names_match(name, cap):
        return True
    if cap and names_match(name, _basename(cap)):
        return True
    if full_name and names_match(name, _basename(full_name)):
        return True
    return False


def pick_window_full_name(entries, name: str | None) -> str:
    """
    Choose the document path of the matching window from [(caption, fullname)].

    Returns the path when exactly one window matches, COM_NO_MATCH when the
    windows were read but none of them is the one on screen (the caller then
    says so instead of naming the wrong file), and "" when there is nothing to
    choose from.
    """
    matches = []
    for caption, full in entries or []:
        full = (full or "").strip()
        if not full:
            continue
        if window_matches(name, caption, full):
            matches.append(full)
    if len(matches) == 1:
        return matches[0]
    if matches:
        # Two windows of the same document: same file, so either answer is right.
        first = matches[0]
        if all(m == first for m in matches):
            return first
        return COM_NO_MATCH
    if entries:
        return COM_NO_MATCH
    return ""


def com_window_entries(progid: str, windows_attr: str, doc_attr: str):
    """
    [(caption, document full name)] for every window of a running Office app.

    Read-only: opens nothing, changes nothing, activates nothing. Returns []
    when the app is not running, pywin32 is missing, or COM refuses.
    """
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return []

    initialised = False
    entries = []
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialised = True
        except Exception:
            try:
                pythoncom.CoInitialize()
                initialised = True
            except Exception:
                pass
        app = win32com.client.GetActiveObject(progid)
        collection = getattr(app, windows_attr, None)
        if collection is None:
            return []
        try:
            count = int(collection.Count)
        except Exception:
            count = 0
        for index in range(1, min(count, _COM_WINDOW_LIMIT) + 1):
            try:
                win = collection.Item(index)
                caption = str(getattr(win, "Caption", "") or "")
                doc = getattr(win, doc_attr, None)
                full = str(getattr(doc, "FullName", "") or "") if doc is not None else ""
                entries.append((caption, full))
            except Exception:
                continue
        return entries
    except Exception:
        return []
    finally:
        if initialised:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def com_window_fullname(progid: str, windows_attr: str, doc_attr: str, name: str) -> str:
    """Document path of the Office window whose caption matches `name`. Seam."""
    return pick_window_full_name(com_window_entries(progid, windows_attr, doc_attr), name)


def com_fullname(progid: str, attr_path: str) -> str | None:
    """
    Raw `.FullName` from a running Office app via the Running Object Table.

    Runs on whatever thread the caller provides, so it does its own
    CoInitializeEx/CoUninitialize pair (issue 009 AC / US-25). Returns None on
    every failure, including pythoncom.com_error and a missing pywin32.
    """
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return None

    initialised = False
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialised = True
        except Exception:
            try:
                pythoncom.CoInitialize()
                initialised = True
            except Exception:
                pass
        obj = win32com.client.GetActiveObject(progid)
        for part in attr_path.split("."):
            obj = getattr(obj, part)
        text = str(obj).strip() if obj is not None else ""
        return text or None
    except Exception:
        return None
    finally:
        if initialised:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def activate_window(hwnd: int) -> bool:
    """
    Nudge a window to the foreground — the documented workaround for the ROT not
    yet publishing an Office instance. Only used for ONE retry, never in a loop.
    """
    try:
        import win32gui
        win32gui.SetForegroundWindow(int(hwnd))
        return True
    except Exception:
        return False


# ── Level 3 sources: Windows Recent + Office MRU (step 5) ────────────────
#
# Needed for every app that cannot be asked directly: Notepad, VS Code, PDF
# readers, archivers. Windows already records what was opened, in two places:
#   1. %APPDATA%\Microsoft\Windows\Recent — one .lnk shortcut per opened file;
#   2. HKCU\...\Office\16.0\<App>\User MRU — Office's own recent list.
# Both are READ-ONLY here. Nothing is written, nothing is polled: this runs only
# when the user asks a question and levels 1-2 could not produce a path.
#
# Cost control (the whole cascade has an 800 ms budget):
#   • the folder listing is capped at _RECENT_SCAN_MAX entries;
#   • a .lnk is only OPENED when its own file name already matches the document
#     name, and at most _RECENT_RESOLVE_MAX of them — resolving a shortcut is
#     a COM call, so doing it for the whole folder would blow the budget.

_RECENT_SCAN_MAX = 400      # newest shortcuts examined per question
_RECENT_RESOLVE_MAX = 10    # shortcuts actually opened (COM) per question

# Recent stores one shortcut per name, so a second file with the same name
# arrives as "Отчёт.docx (2).lnk". The suffix belongs to the shortcut, not to
# the document, and must be ignored when matching names.
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d{1,3}\)$")

# "[F00000000][T01DB...][O00000000]*C:\path\file.docx" — Office MRU value.
_MRU_PREFIX_RE = re.compile(r"^(?:\[[^\]]*\])+\*")

# Office MRU lives under one subkey per application.
_OFFICE_MRU_APPS = {
    "winword": "Word",
    "excel": "Excel",
    "powerpnt": "PowerPoint",
    "visio": "Visio",
    "mspub": "Publisher",
}
_OFFICE_VERSIONS = ("16.0", "15.0", "14.0")


def parse_mru_value(raw: str | None) -> str | None:
    """
    Pure: strip the Office MRU bookkeeping prefix, keep the path.

    '[F00000000][T01DB...][O00000000]*C:\\Работа\\Отчёт.docx' -> the path.
    Returns None for anything that is not an absolute local path (cloud URLs in
    the MRU are deliberately dropped — they are not paths on this disk).
    """
    if not raw:
        return None
    text = _MRU_PREFIX_RE.sub("", str(raw).strip())
    text = text.strip().strip('"')
    if not text or not is_absolute_path(text):
        return None
    return text


def _recent_dir() -> str:
    """%APPDATA%\\Microsoft\\Windows\\Recent, or '' when unavailable."""
    try:
        appdata = os.environ.get("APPDATA") or ""
        if not appdata:
            return ""
        return os.path.join(appdata, "Microsoft", "Windows", "Recent")
    except Exception:
        return ""


def _list_recent_lnks(limit: int = _RECENT_SCAN_MAX) -> list[tuple[str, float]]:
    """
    SEAM. [(shortcut path, its own mtime)] newest first. [] on any failure.

    The mtime of the .lnk is when the file was last OPENED — that is what makes
    the newest entry the best guess when several files share a name.
    """
    folder = _recent_dir()
    if not folder:
        return []
    rows: list[tuple[str, float]] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.name.lower().endswith(".lnk"):
                        continue
                    rows.append((entry.path, entry.stat().st_mtime))
                except OSError:
                    continue
    except Exception:
        return []
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows[:max(0, int(limit))]


# Resolving a shortcut means a COM round-trip through the shell, and a Recent
# folder holds hundreds of them. The folder barely changes between two questions
# asked seconds apart, so every resolution is remembered against the shortcut's
# own mtime: a shortcut that has not been rewritten cannot point somewhere else.
# This is also what rescues a scan that ran out of time — the thread keeps going
# after the deadline, fills the cache, and the next question is instant.
_LNK_CACHE_MAX = 2048
_lnk_cache: dict = {}


def _shortcut_target_cached(lnk_path: str, mtime: float) -> str | None:
    """_shortcut_target with memory. Failures are remembered too (as None)."""
    key = str(lnk_path).lower()
    with _state_lock:
        hit = _lnk_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    target = _shortcut_target(lnk_path)
    with _state_lock:
        if len(_lnk_cache) >= _LNK_CACHE_MAX:
            _lnk_cache.clear()
        _lnk_cache[key] = (mtime, target)
    return target


def _shortcut_target(lnk_path: str) -> str | None:
    """SEAM. Where a .lnk points. None on any failure (no pywin32, broken link)."""
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return None
    initialised = False
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialised = True
        except Exception:
            pass
        shell = win32com.client.Dispatch("WScript.Shell")
        target = shell.CreateShortCut(lnk_path).Targetpath
        text = str(target).strip() if target else ""
        return text or None
    except Exception:
        return None
    finally:
        if initialised:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _office_mru_values(process: str = "") -> list[str]:
    """SEAM. Raw Office MRU strings for `process`'s app. [] on any failure."""
    app = _OFFICE_MRU_APPS.get(normalize_process(process))
    if not app:
        return []
    try:
        import winreg
    except Exception:
        return []
    values: list[str] = []
    for version in _OFFICE_VERSIONS:
        root = rf"Software\Microsoft\Office\{version}\{app}\User MRU"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as base:
                index = 0
                while True:
                    try:
                        profile = winreg.EnumKey(base, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(base, profile + r"\File MRU") as mru:
                            slot = 0
                            while True:
                                try:
                                    _n, data, _t = winreg.EnumValue(mru, slot)
                                except OSError:
                                    break
                                slot += 1
                                if isinstance(data, str):
                                    values.append(data)
                    except OSError:
                        continue
        except OSError:
            continue
        except Exception:
            continue
    return values


def recent_candidates(name: str, limit: int = 20, process: str = "") -> list[str]:
    """
    Absolute paths that could be the document called `name`, newest first.

    Order matters: Office MRU first (an exact per-application record), then the
    Recent folder (system-wide, covers Notepad/VS Code/readers). Existence is
    NOT checked here — the cascade filters with path_exists(), keeping the
    "never name a path that is not on disk" rule in exactly one place.

    Never raises. Returns [] when nothing matches.
    """
    wanted = _clean(name)
    if not wanted:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def take(path: str | None) -> None:
        if not path or len(out) >= max(1, int(limit)):
            return
        text = str(path).strip().strip('"')
        if not is_absolute_path(text):
            return
        base = _basename(text.replace("/", "\\"))
        if is_lock_name(base) or not names_match(wanted, base):
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(text)

    try:
        for raw in _office_mru_values(process):
            take(parse_mru_value(raw))
    except Exception:
        pass

    try:
        resolved = 0
        for lnk, _mtime in _list_recent_lnks():
            if len(out) >= max(1, int(limit)) or resolved >= _RECENT_RESOLVE_MAX:
                break
            # Cheap pre-filter on the shortcut's own name: 'Отчёт.docx.lnk'.
            shortcut = _basename(lnk)
            stem = shortcut[:-4] if shortcut.lower().endswith(".lnk") else shortcut
            stem = _COPY_SUFFIX_RE.sub("", stem).strip()
            if not names_match(wanted, stem):
                continue
            resolved += 1
            take(_shortcut_target_cached(lnk, _mtime))
    except Exception:
        pass

    return out


def uia_document_path(hwnd: int) -> str | None:
    """UI Automation fallback (level 4, opt-in, not implemented yet)."""
    return None


# ── Timeout + single-flight ────────────────────

def _run_with_timeout(fn, timeout_s: float, label: str) -> tuple[bool, object]:
    """
    Run `fn` on a dedicated daemon thread and wait at most `timeout_s`.

    A dedicated thread (never an executor pool thread) is required: COM must be
    initialised and uninitialised on the same thread, and a wedged call would
    otherwise poison a shared pool thread forever.

    Returns (ok, value). On timeout the thread is ABANDONED — Python cannot kill
    it — which is exactly why the single-flight guard and the circuit breaker
    below exist.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException:
            box["value"] = None
        finally:
            box["done"] = True

    thread = threading.Thread(target=target, daemon=True, name=f"inspector-{label}")
    thread.start()
    thread.join(max(0.0, float(timeout_s)))
    if not box.get("done"):
        return False, None
    return True, box.get("value")


def _guarded_com(work_fn, timeout_s: float) -> tuple[bool, str | None]:
    """Run one COM read under single-flight, timeout and the circuit breaker."""
    global _com_busy, _hung_com
    with _state_lock:
        if _hung_com >= HUNG_COM_LIMIT:
            return False, None            # breaker open: never try again
        if _com_busy:
            return False, None            # a previous call is still wedged
        _com_busy = True

    def work():
        try:
            return work_fn()
        finally:
            global _com_busy
            with _state_lock:
                _com_busy = False

    ok, value = _run_with_timeout(work, timeout_s, "com")
    if not ok:
        with _state_lock:
            _hung_com += 1
        return False, None
    return True, value if isinstance(value, str) else None


def _call_com_window(proc: str, name: str, timeout_s: float) -> tuple[bool, str | None]:
    """Ask the app for the document of the window that is actually on screen."""
    adapter = _COM_WINDOW_ADAPTERS.get(proc)
    if not adapter or not name:
        return False, None
    progid, windows_attr, doc_attr = adapter
    return _guarded_com(
        lambda: com_window_fullname(progid, windows_attr, doc_attr, name), timeout_s
    )


def _call_com(progid: str, attr_path: str, timeout_s: float) -> tuple[bool, str | None]:
    """COM call guarded by single-flight, timeout and the circuit breaker."""
    return _guarded_com(lambda: com_fullname(progid, attr_path), timeout_s)


# ── Path classification ──────────────────────────────────────────────

def classify_path(raw: str | None) -> dict:
    """
    Decide whether a raw string from COM/Recent may be published as `path`.

    THE core safety rule of issue 009: a path is only ever returned when it is
    absolute AND actually exists on disk. Everything else degrades honestly.
    Kills five bugs at once: unsaved 'Document1', OneDrive URLs, stale .lnk
    targets, relative names, lock files.
    """
    text = _clean(raw)
    if not text:
        return {"path": None, "kind": KIND_UNKNOWN, "reason": "Источник не вернул пути."}

    low = text.lower()
    if low.startswith(("http://", "https://")):
        return {
            "path": None,
            "kind": KIND_CLOUD,
            "name": _basename(text.replace("/", "\\")),
            "reason": "Документ лежит в облаке (OneDrive/SharePoint), локального пути нет.",
        }
    if not is_absolute_path(text):
        return {
            "path": None,
            "kind": KIND_UNSAVED,
            "name": _basename(text),
            "reason": "Документ ещё не сохранён на диск, у него нет пути.",
        }
    name = _basename(text.replace("/", "\\"))
    if is_lock_name(name):
        return {"path": None, "kind": KIND_UNKNOWN,
                "reason": "Получен служебный файл блокировки, а не документ."}
    if not path_exists(text):
        return {"path": None, "kind": KIND_UNKNOWN, "name": name,
                "reason": "Путь больше не существует на диске."}
    return {"path": text, "kind": KIND_LOCAL, "name": name, "reason": ""}


def _stem(name: str | None) -> str:
    if not name:
        return ""
    return _EXT_RE.sub("", name).strip().lower()


def names_match(title_name: str | None, path_name: str | None) -> bool:
    """
    Does the document COM reported match the window the user is looking at?

    `ActiveDocument` is the app's active document, NOT the foreground window's
    — with two Word windows open they can differ. Comparison is by stem so a
    hidden extension in the caption does not cause a false mismatch.
    """
    if not title_name or not path_name:
        return False
    a, b = title_name.strip().lower(), path_name.strip().lower()
    return a == b or _stem(a) == _stem(b)


# ── The cascade ─────────────────────────────────────────────────────

def _finish(result: dict, started: float, note: str = "") -> dict:
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    result["found"] = bool(result.get("path"))
    if note:
        reason = (result.get("reason") or "").strip()
        result["reason"] = (note + " " + reason).strip()
    return result


def active_document(deadline_s: float | None = None, use_cache: bool = True) -> dict:
    """
    Which document is open RIGHT NOW — the on-command entry point for issue 009.

    Cheap-to-precise cascade, stopping as soon as an answer is good enough:
      1. window title      ~0 ms      (already in memory)
      2. Office COM        50-400 ms  (only for the foreground process)
      3. Recent / MRU      5-50 ms    (only when a name exists but no path)
      4. UI Automation                (opt-in via JARVIS_INSPECTOR_UIA=1)

    Never raises, never blocks past the deadline, never returns an unverified
    path. MUST be called from a worker thread (main.py wraps it in
    run_in_executor) and MUST NOT be called from the background watcher.
    """
    started = time.monotonic()
    budget = DEADLINE_S if deadline_s is None else max(0.05, float(deadline_s))

    window = active_window()
    if not window:
        return _finish(new_result(reason="Не удалось определить активное окно."), started)

    # When the question is about the window BEHIND Jarvis's own console, every
    # answer below must say so — otherwise a correct path looks like a claim
    # about the window actually in front.
    note = SUBSTITUTED_NOTE if window.get("substituted") else ""

    cache_key = (window.get("hwnd", 0), window.get("title", ""))
    if use_cache:
        with _state_lock:
            fresh = (
                _cache["key"] == cache_key
                and _cache["result"] is not None
                and (time.monotonic() - _cache["ts"]) < CACHE_TTL_S
            )
            if fresh:
                return dict(_cache["result"])

    # ── Level 1: the caption we already have ───────────────────────────────
    proc = normalize_process(window.get("process"))
    result = parse_title(window.get("title"), proc)

    # Web content and never-saved documents have no path anywhere: stop here
    # instead of resolving some unrelated file with the same name.
    if result["kind"] in (KIND_WEB, KIND_UNSAVED):
        return _cache_and_finish(cache_key, result, started, use_cache, note)

    def remaining() -> float:
        return budget - (time.monotonic() - started)

    # ── Level 2: COM, only for the FOREGROUND process's own adapter ──────────
    adapter = _COM_ADAPTERS.get(proc)
    if adapter and remaining() > 0.05:
        progid, attr_path = adapter
        com_budget = max(0.05, min(remaining() - 0.05, budget * COM_BUDGET_SHARE))

        # Ask about the window on screen first; only if the app cannot list its
        # windows do we fall back to its notion of "the active document".
        ok, raw = _call_com_window(proc, result["name"], com_budget)
        if ok and raw == COM_NO_MATCH:
            result["reason"] = (
                "Приложение не нашло среди своих окон то, что показано на экране — "
                "точный путь не подтверждён."
            )
            ok, raw = False, None
        elif not (ok and raw):
            ok, raw = _call_com(progid, attr_path, com_budget)
        if not ok and remaining() > 0.15 and window.get("hwnd"):
            # ROT may not have published the instance yet: ONE retry after
            # nudging the window, never a retry loop.
            activate_window(window["hwnd"])
            ok, raw = _call_com(progid, attr_path, remaining())
        if ok and raw:
            verdict = classify_path(raw)
            if verdict["path"] and names_match(result["name"], verdict.get("name")):
                result.update({
                    "path": verdict["path"],
                    "name": verdict["name"],
                    "kind": KIND_LOCAL,
                    "source": SOURCE_COM,
                    "confidence": CONF_EXACT,
                    "reason": "Путь получен напрямую от приложения и проверен на диске.",
                })
                return _cache_and_finish(cache_key, result, started, use_cache, note)
            if verdict["path"]:
                # Right app, wrong window: degrade instead of naming the wrong file.
                result["reason"] = (
                    "Приложение сообщило другой документ, чем показан в активном окне — "
                    "точный путь не подтверждён."
                )
            elif verdict["kind"] in (KIND_CLOUD, KIND_UNSAVED):
                result["kind"] = verdict["kind"]
                result["name"] = verdict.get("name") or result["name"]
                result["confidence"] = CONF_NONE
                result["source"] = SOURCE_COM
                result["reason"] = verdict["reason"]
                return _cache_and_finish(cache_key, result, started, use_cache, note)

    # ── Level 3: Recent / Office MRU, only when we know a name but no path ────
    if not result["path"] and result["name"] and remaining() > 0.05:
        # The Recent scan resolves shell shortcuts, and a shortcut pointing at a
        # sleeping network drive or an app holding the COM apartment can block for
        # SECONDS. Off the deadline it once cost 4.4 s for a 0.8 s budget, so it
        # runs on its own thread with whatever time is left and is abandoned if it
        # overruns — same rule as COM, no exceptions for "cheap" levels.
        recent_budget = max(0.05, remaining() - 0.05)
        ok_recent, raw_recent = _run_with_timeout(
            lambda: recent_candidates(result["name"], process=proc),
            recent_budget, "recent")
        if not ok_recent:
            result["reason"] = (
                "Поиск по недавним документам не уложился в отведённое время — "
                "точный путь не подтверждён."
            )
        try:
            found = [p for p in ((raw_recent if ok_recent else None) or [])
                     if p and is_absolute_path(p) and path_exists(p)
                     and not is_lock_name(_basename(p.replace("/", "\\")))]
        except Exception:
            found = []
        unique: list[str] = []
        for p in found:
            if p.lower() not in [u.lower() for u in unique]:
                unique.append(p)
        if len(unique) == 1:
            result.update({
                "path": unique[0],
                "name": _basename(unique[0].replace("/", "\\")),
                "kind": KIND_LOCAL,
                "source": SOURCE_RECENT,
                "confidence": CONF_PROBABLE,
                "reason": "Путь восстановлен по недавним документам и проверен на диске.",
            })
            return _cache_and_finish(cache_key, result, started, use_cache, note)
        if len(unique) > 1:
            result.update({
                "candidates": unique[:5],
                "source": SOURCE_RECENT,
                "confidence": CONF_NAME_ONLY,
                "reason": "Найдено несколько файлов с таким именем — нужно уточнить, какой именно.",
            })
            return _cache_and_finish(cache_key, result, started, use_cache, note)

    # ── Level 4: UI Automation, opt-in ───────────────────────────────────
    if (not result["path"] and uia_enabled() and remaining() > 0.2):
        ok, raw = _run_with_timeout(
            lambda: uia_document_path(window.get("hwnd", 0)), remaining(), "uia")
        if ok and raw:
            verdict = classify_path(raw if isinstance(raw, str) else None)
            if verdict["path"]:
                result.update({
                    "path": verdict["path"],
                    "name": verdict["name"],
                    "kind": KIND_LOCAL,
                    "source": SOURCE_UIA,
                    "confidence": CONF_PROBABLE,
                    "reason": "Путь получен через интерфейс доступности и проверен на диске.",
                })

    # Nothing readable in front and nothing to name: rather than a dead end, say
    # what was open a moment ago — marked as memory, never as a live reading, and
    # never cached (the cache is for readings).
    if not result["path"] and not result["name"]:
        memory = last_known_document()
        if memory:
            result.update({
                "path": memory["path"],
                "name": memory["name"],
                "kind": KIND_LOCAL,
                "source": SOURCE_RECENT,
                "confidence": CONF_PROBABLE,
                "reason": MEMORY_NOTE + ".",
                "from_memory": True,
            })
            return _finish(result, started, note)

    return _cache_and_finish(cache_key, result, started, use_cache, note)


def uia_enabled() -> bool:
    """Level 4 is off unless JARVIS_INSPECTOR_UIA is explicitly switched on."""
    return str(os.environ.get(UIA_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def remember_document(path: str | None, name: str | None) -> None:
    """Keep the last VERIFIED document path, so a blank moment still has an answer."""
    if not path:
        return
    with _state_lock:
        _last_doc.update({"path": path, "name": name, "ts": time.monotonic()})


def last_known_document() -> dict | None:
    """The remembered document, or None when there is none or it went stale."""
    with _state_lock:
        if not _last_doc["path"]:
            return None
        if (time.monotonic() - _last_doc["ts"]) > _LAST_DOC_TTL_S:
            return None
        return {"path": _last_doc["path"], "name": _last_doc["name"]}


def _cache_and_finish(key, result: dict, started: float, use_cache: bool,
                      note: str = "") -> dict:
    remember_document(result.get("path"), result.get("name"))
    out = _finish(result, started, note)
    if use_cache:
        with _state_lock:
            _cache.update({"key": key, "result": dict(out), "ts": time.monotonic()})
    return out


# ── Rendering (untrusted text -> what Jarvis says) ─────────────────────────

def _safe(text: str | None, limit: int = 160) -> str:
    """
    Neutralise a file name/path before it reaches the model or the user.

    A file name is attacker-controlled text: it can carry newlines and fence
    markers. Reuses the world model's sanitiser so both paths behave the same.
    """
    if not text:
        return ""
    try:
        from core.awareness._world_model import _sanitize
        return _sanitize(str(text), limit)
    except Exception:
        return " ".join(str(text).split()).replace("[", "(").replace("]", ")")[:limit]


def render(result: dict) -> str:
    """The Russian sentence the tool speaks back. Always honest, never invents."""
    if not isinstance(result, dict):
        return "Не удалось определить открытый документ."
    dirty_note = " Есть несохранённые изменения." if result.get("dirty") is True else ""
    # The "answering about the window behind me" note lives in `reason`; the
    # found-path branch never speaks `reason`, so lift it to the front.
    lead = (SUBSTITUTED_NOTE + " "
            if (result.get("reason") or "").startswith(SUBSTITUTED_NOTE) else "")

    # One vocabulary for the whole assistant: the noun comes from the same
    # place Perception Core takes it from, so a .json is a "файл с кодом" in
    # every answer instead of a "документ" in one and something else in the
    # next.
    def _noun(value):
        try:
            from core.awareness import _perception
            return _perception.noun_for(value)
        except Exception:
            return "файл"

    if result.get("path"):
        noun = _noun(result.get("path") or result.get("name"))
        label = f"Открыт {noun}"
        if result.get("confidence") == CONF_PROBABLE:
            label = f"Скорее всего, открыт {noun}"
        if result.get("from_memory"):
            label = MEMORY_NOTE
            lead = ""
        return f"{lead}{label}: {_safe(result['path'])}{dirty_note}"

    candidates = result.get("candidates") or []
    if candidates:
        listed = "; ".join(_safe(c) for c in candidates)
        return (lead + "Нашёл несколько файлов с таким именем, уточни, какой нужен: "
                f"{listed}.{dirty_note}")

    if result.get("kind") == KIND_WEB:
        return "На переднем плане страница в браузере, а не файл на диске."

    name = _safe(result.get("name"))
    reason = _safe(result.get("reason"), 200)
    if name:
        return (f"Открыт {_noun(result.get('name'))} «{name}», но полный путь "
                f"определить не удалось. {reason}").strip()
    return f"Не удалось определить открытый документ. {reason}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Browser pages — the read-only answer to "какая страница сейчас открыта?"
#
# Without this the model has only browser_control, which NAVIGATES: asking
# "what page is open" replaced the user's page with about:blank. Window
# captions already carry the title of the active tab of every browser window,
# so the question is answerable with zero side effects and zero automation.
# The address is deliberately NOT invented: a caption never contains the URL.
# ─────────────────────────────────────────────────────────────────────────────

_BROWSER_NAMES = {
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "browser": "Яндекс.Браузер",
    "opera": "Opera",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "safari": "Safari",
}

_BROWSER_SUFFIX_RE = re.compile(
    r"\s+[-–—]\s+(?:google\s+chrome|chromium|chrome|mozilla\s+firefox|firefox|"
    r"microsoft\s+​edge|microsoft\s+edge|edge|Яндекс\.?\s?Браузер|yandex|opera|brave|vivaldi|safari)\s*$",
    re.IGNORECASE,
)
_TAB_COUNTER_RE = re.compile(r"^\(\d+\)\s*")


def page_title(title: str | None) -> str | None:
    """The tab name inside a browser window caption, or None. Pure."""
    text = _clean(title)
    if not text:
        return None
    text = _BROWSER_SUFFIX_RE.sub("", text).strip()
    text = _TAB_COUNTER_RE.sub("", text).strip()
    return text or None


def is_real_window(win: dict | None) -> bool:
    """
    True for a window a human would call "a window".

    Windows marks every popup, bubble, tooltip and modal dialog either as owned
    by the window it belongs to (GW_OWNER != 0) or as a tool window
    (WS_EX_TOOLWINDOW). Real application windows have neither. This is the same
    rule the taskbar and Alt+Tab use, so it holds for every application in every
    language without listing a single caption: Chrome's "Перевести эту
    страницу?" bubble, Word's "Открытие", a download prompt, a settings popup.
    Missing keys mean "not measured" and are treated as a real window.
    """
    if not isinstance(win, dict):
        return False
    if int(win.get("owner") or 0):
        return False
    if win.get("toolwindow"):
        return False
    return True


def pick_real_window(win: dict | None, owner_lookup) -> dict | None:
    """
    Walk from a popup or dialog up to the window it belongs to.

    When a Save-As dialog is in front, the window the user is actually working
    in is its owner. `owner_lookup(owner_hwnd)` returns that owner window or None.
    The walk is bounded (a dialog can own a dialog) and stops on a cycle.
    """
    current = win
    seen: set[int] = set()
    for _ in range(4):
        if not isinstance(current, dict):
            return None
        if is_real_window(current):
            return current
        hwnd = int(current.get("hwnd") or 0)
        owner_hwnd = int(current.get("owner") or 0)
        if not owner_hwnd or hwnd in seen:
            return current
        seen.add(hwnd)
        try:
            owner = owner_lookup(owner_hwnd)
        except Exception:
            owner = None
        if not isinstance(owner, dict) or not (owner.get("title") or "").strip():
            return current
        current = owner
    return current


def _list_windows() -> list[dict]:
    """Every visible titled top-level window. Seam: patched in tests, [] w/o pywin32."""
    try:
        import psutil
        import win32gui
        import win32process
    except Exception:
        return []

    found: list[dict] = []

    def collect(hwnd, _unused):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return True
            process = ""
            try:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    process = psutil.Process(pid).name() or ""
            except Exception:
                process = ""
            owner = 0
            toolwindow = False
            try:
                owner = int(win32gui.GetWindow(hwnd, 4) or 0)  # 4 = GW_OWNER
            except Exception:
                owner = 0
            try:
                # WS_EX_TOOLWINDOW (0x80) marks palettes and floating panels.
                ex_style = int(win32gui.GetWindowLong(hwnd, -20) or 0)
                toolwindow = bool(ex_style & 0x00000080)
            except Exception:
                toolwindow = False
            found.append(
                {
                    "title": title,
                    "process": process,
                    "hwnd": int(hwnd),
                    "owner": owner,
                    "toolwindow": toolwindow,
                }
            )
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        pass
    return found


def active_page(browser_hint: str = "") -> dict:
    """
    {found, pages:[{title, app, hwnd}], active, reason, elapsed_ms}.

    Read-only: reads window captions, never drives the browser. `browser_hint`
    narrows to one browser ("chrome", "firefox", ...) when the user named it.
    """
    started = time.monotonic()
    result = {"found": False, "pages": [], "active": None, "reason": "", "elapsed_ms": 0}

    def finish(res):
        res["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return res

    hint = normalize_process(browser_hint)
    try:
        windows = _list_windows()
    except Exception:
        windows = []

    try:
        front = active_window()
    except Exception:
        front = None
    if not windows and front:
        windows = [front]

    pages: list[dict] = []
    seen = set()
    for win in windows:
        proc = normalize_process((win or {}).get("process"))
        if not is_browser(proc):
            continue
        # A translate bubble, a download prompt or a profile popup carries a
        # caption too, but it is not a page.
        if not is_real_window(win):
            continue
        if hint and hint not in proc and proc not in hint:
            continue
        name = page_title((win or {}).get("title"))
        if not name:
            continue
        key = (name, proc)
        if key in seen:
            continue
        seen.add(key)
        pages.append({"title": name, "app": proc, "hwnd": int((win or {}).get("hwnd") or 0)})

    if not pages:
        named = f" «{_safe(browser_hint, 30)}»" if browser_hint else ""
        result["reason"] = f"Не вижу открытых окон браузера{named}."
        return finish(result)

    # The window in front (or the one the user worked in before Jarvis) is the
    # page being asked about; the rest are context, not the answer.
    if front and not is_real_window(front):
        by_hwnd = {int((w or {}).get("hwnd") or 0): w for w in windows}
        front = pick_real_window(front, lambda h: by_hwnd.get(h))
    if front and is_browser(normalize_process(front.get("process"))):
        front_title = page_title(front.get("title"))
        for page in pages:
            if page["title"] == front_title:
                pages.remove(page)
                pages.insert(0, page)
                result["active"] = page
                break

    result["found"] = True
    result["pages"] = pages
    result["reason"] = "Название вкладки прочитано из заголовка окна; адреса в заголовке нет."
    return finish(result)


def render_page(result: dict) -> str:
    """Russian sentence for an active_page() result."""
    if not isinstance(result, dict) or not result.get("found"):
        return (result or {}).get("reason") or "Не удалось определить открытую страницу."

    def label(page):
        app = _BROWSER_NAMES.get(page.get("app"), page.get("app") or "браузер")
        return f"«{_safe(page.get('title'), 120)}» ({app})"

    pages = result.get("pages") or []
    head = ("Открыта страница: " if result.get("active") else "Похоже, открыта страница: ")
    text = head + label(pages[0])
    rest = pages[1:]
    if rest:
        shown = ", ".join(label(p) for p in rest[:4])
        text += ". Другие окна браузера: " + shown
        if len(rest) > 4:
            text += f" и ещё {len(rest) - 4}"
    return text + "."
