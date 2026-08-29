# core/awareness/_perception.py
# Perception Core — ONE description of what is in front of the user.
#
# Why this module exists
# ----------------------
# Before it, three separate entry points (active_document / active_page /
# active_app) each answered about their own category, the model had to pick the
# category BEFORE anything looked at the screen, and "not my category" came out
# as a denial ("сейчас не документ") even when the honest answer was obvious.
# On top of that the world model and the inspectors were two independent readers
# of the same screen with different rules, so one conversation could contain two
# contradictory answers.
#
# The rules this module is built on
# ---------------------------------
# 1. Nothing here decides a category by application name. Application-name
#    tables may only ADD WEIGHT; they may never be the gate. A window of an
#    application nobody ever heard of is described from its measurable signals.
# 2. Every OS read goes through an injectable source. Production injects the
#    live reads, tests inject fixtures. No function here reads Windows directly,
#    so no test can depend on the machine it runs on.
# 3. Unknown is phrased as "what I could establish", never as a denial.
# 4. Every answer carries the evidence it was built from, so a wrong answer on a
#    real machine can be traced without guessing.
#
# Invariant: nothing in this module raises. Every failure degrades to a Subject
# with found=False and an honest Russian `reason`.

from __future__ import annotations

import threading
import time

# ─────────────────────────────────────────────────────────────────────────────
# Sources — the ONLY place that touches the operating system.
#
# A source is a plain callable. The defaults forward to _inspectors (which owns
# the low-level Windows work); tests replace them wholesale. Because the source
# table is data, "where does the truth come from" is an explicit, inspectable
# decision instead of something buried in a function body.
# ─────────────────────────────────────────────────────────────────────────────

def _src_active_window():
    from core.awareness import _inspectors
    return _inspectors.active_window()


def _src_list_windows():
    from core.awareness import _inspectors
    return _inspectors._list_windows()


def _src_active_document(deadline_s=None):
    from core.awareness import _inspectors
    return _inspectors.active_document(deadline_s=deadline_s)


def _src_recent_candidates(name, process=""):
    from core.awareness import _inspectors
    return _inspectors.recent_candidates(name, process=process)


def _src_path_exists(path):
    from core.awareness import _inspectors
    return _inspectors.path_exists(path)


def _src_is_own_window(title, process="", hwnd=0):
    from core.awareness import _inspectors
    return _inspectors.is_own_window(title, process, hwnd)


def _src_snapshot():
    from core.awareness import _world_model
    return _world_model.snapshot()


_DEFAULT_SOURCES = {
    "active_window": _src_active_window,
    "list_windows": _src_list_windows,
    "active_document": _src_active_document,
    "recent_candidates": _src_recent_candidates,
    "path_exists": _src_path_exists,
    "is_own_window": _src_is_own_window,
    "snapshot": _src_snapshot,
}

_sources: dict = dict(_DEFAULT_SOURCES)
_lock = threading.Lock()


def set_sources(**overrides) -> None:
    """Replace one or more sources (tests, replay mode). Unknown keys are ignored."""
    with _lock:
        for key, fn in overrides.items():
            if key in _DEFAULT_SOURCES and callable(fn):
                _sources[key] = fn


def reset_sources() -> None:
    """Back to the live reads."""
    with _lock:
        _sources.clear()
        _sources.update(_DEFAULT_SOURCES)


def _read(name: str, *args, **kwargs):
    """Call a source. A broken source degrades the answer, never the turn."""
    fn = _sources.get(name)
    if fn is None:
        return None
    started = time.monotonic()
    try:
        value = fn(*args, **kwargs)
        ok = True
    except Exception:
        value, ok = None, False
    cost = int((time.monotonic() - started) * 1000)
    return {"value": value, "ok": ok, "cost_ms": cost, "source": name}


# ─────────────────────────────────────────────────────────────────────────────
# Extension knowledge.
#
# This is a table about FILE EXTENSIONS, not about applications — a `.7z` is an
# archive whichever program opened it, on any machine, in any locale. That is
# why it is allowed to be a table while application-name lists are not: adding
# a program must never be required, and an unknown extension still yields a
# generic "file" answer rather than silence.
# ─────────────────────────────────────────────────────────────────────────────

_EXT_CATEGORY = {
    # archives
    "zip": "archive", "7z": "archive", "rar": "archive", "tar": "archive",
    "gz": "archive", "bz2": "archive", "xz": "archive", "iso": "archive",
    "cab": "archive", "tgz": "archive", "zst": "archive",
    # text documents
    "doc": "document", "docx": "document", "odt": "document", "rtf": "document",
    "txt": "document", "md": "document", "pdf": "document", "djvu": "document",
    "epub": "document", "fb2": "document", "tex": "document", "one": "document",
    "pages": "document",
    # spreadsheets / presentations — still documents to a human
    "xls": "document", "xlsx": "document", "ods": "document", "csv": "document",
    "ppt": "document", "pptx": "document", "odp": "document", "vsdx": "document",
    # code and configuration
    "py": "code", "js": "code", "ts": "code", "tsx": "code", "jsx": "code",
    "java": "code", "kt": "code", "c": "code", "h": "code", "cpp": "code",
    "cs": "code", "go": "code", "rs": "code", "rb": "code", "php": "code",
    "sql": "code", "sh": "code", "ps1": "code", "json": "code", "yaml": "code",
    "yml": "code", "toml": "code", "ini": "code", "xml": "code", "html": "code",
    "css": "code", "ipynb": "code",
    # images
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "bmp": "image", "webp": "image", "svg": "image", "tiff": "image",
    "heic": "image", "psd": "image", "ai": "image", "cdr": "image",
    # audio / video
    "mp3": "media", "wav": "media", "flac": "media", "ogg": "media",
    "m4a": "media", "aac": "media", "mp4": "media", "mkv": "media",
    "avi": "media", "mov": "media", "webm": "media", "wmv": "media",
    # executables and installers
    "exe": "program", "msi": "program", "bat": "program", "cmd": "program",
    "apk": "program", "appx": "program",
}

# What a human calls each surface, and how it is spoken about.
SURFACE_RU = {
    "page": "страница в браузере",
    "document": "документ",
    "code": "файл с кодом",
    "image": "изображение",
    "media": "медиафайл",
    "archive": "архив",
    "program": "программа",
    "file": "файл",
    "folder": "папка",
    "terminal": "терминал",
    "dialog": "служебное окно",
    "app": "окно приложения",
    "own": "окно самого Джарвиса",
    "unknown": "окно",
}

# Surfaces that mean "a file is open in there". Used by the document view.
FILE_SURFACES = frozenset({"document", "code", "image", "media", "archive", "file"})


# ── How the user addresses a window ───────────────────────────────────────────────
# People name a window three different ways: by the program ("happ"), by its
# ROLE ("в проводнике", "в браузере"), or by what is inside it ("тот json").
# Only the first used to work — and on Russian Windows only by luck, because
# Explorer captions happen to end with "— проводник". Roles are derived below
# from the same generic predicates the scorer already uses, so no application
# is named anywhere; the tables hold words of a LANGUAGE, not a list of apps.
ROLE_WORDS = {
    "browser": ("браузер", "браузере", "браузера", "браузеру", "browser",
                "хром", "хроме", "хрома", "chrome", "edge", "firefox",
                "яндекс", "яндексе", "opera", "опера", "опере", "safari"),
    "explorer": ("проводник", "проводнике", "проводника", "проводнику",
                 "explorer", "файловый", "файловом"),
    "terminal": ("терминал", "терминале", "терминала", "консоль", "консоли",
                 "командная", "командной", "terminal", "console", "cmd",
                 "powershell"),
    "editor": ("редактор", "редакторе", "редактора", "editor", "ide"),
    "office": ("офис", "офисе", "офисный", "office"),
}

# Words about the CONTENT rather than the program.
SURFACE_WORDS = {
    "page": ("страница", "странице", "страницу", "страницы", "вкладка",
             "вкладке", "вкладку", "сайт", "сайте", "page", "tab"),
    "folder": ("папка", "папке", "папку", "папки", "директория",
               "директории", "folder", "directory"),
    "archive": ("архив", "архиве", "архива", "зип", "zip", "rar", "archive"),
    "document": ("документ", "документе", "документа", "document"),
    "code": ("код", "коде", "json", "скрипт", "скрипте"),
    "image": ("картинка", "картинке", "изображение", "изображении",
              "фото", "image"),
    "media": ("видео", "медиа", "музыка", "аудио", "video", "media"),
    "program": ("программа", "программе", "программу", "приложение",
                "приложении", "приложения", "app", "application"),
}

_ROLE_BY_WORD = {w: role for role, words in ROLE_WORDS.items() for w in words}
_SURFACE_BY_WORD = {w: s for s, words in SURFACE_WORDS.items() for w in words}


def role_of_word(word: str) -> str:
    """"в проводнике" → "explorer". Empty string when the word names no role."""
    return _ROLE_BY_WORD.get((word or "").strip().lower(), "")


def surface_of_word(word: str) -> str:
    """"архив" → "archive". Empty string when the word names no content kind."""
    return _SURFACE_BY_WORD.get((word or "").strip().lower(), "")


def window_roles(window: dict) -> frozenset:
    """Roles one window can answer to. Derived, never hardcoded per program."""
    ins = _ins()
    process = ins.normalize_process((window or {}).get("process"))
    roles = set()
    try:
        if ins.is_browser(process):
            roles.add("browser")
        if process in ins._TERMINAL_PROCESSES:
            roles.add("terminal")
        if process in ins._EXPLORER_PROCESSES:
            roles.add("explorer")
        if process in ins._DOCUMENT_APPS:
            roles.add("editor")
            roles.add("office")
    except Exception:
        pass
    return frozenset(roles)


def noun_for(name_or_path: str | None) -> str:
    """
    What to CALL a file in a sentence: "документ", "архив", "файл с кодом"…

    Both renderers call this, so one file can no longer be a "документ" in one
    answer and a "файл с кодом" in the next — which is exactly what happened to
    new_file.json two questions apart.
    """
    text = (name_or_path or "").strip().replace("\\", "/")
    base = text.rsplit("/", 1)[-1]
    if "." not in base:
        return SURFACE_RU["file"]
    category = extension_category(base.rsplit(".", 1)[-1])
    return SURFACE_RU.get(category, SURFACE_RU["file"])


def extension_category(extension: str | None) -> str:
    """'7z' -> 'archive'. An unknown extension is still a file, never nothing."""
    ext = (extension or "").strip().lstrip(".").lower()
    if not ext:
        return ""
    return _EXT_CATEGORY.get(ext, "file")


# ─────────────────────────────────────────────────────────────────────────────
# Signals and scoring.
#
# Nothing below asks "is this process in my list of document applications".
# It asks measurable questions about the window, and the answer with the most
# supporting evidence wins. A new application changes nothing here.
# ─────────────────────────────────────────────────────────────────────────────

def _ins():
    from core.awareness import _inspectors
    return _inspectors


def split_segments(title: str) -> list[str]:
    """Caption split on the separators every windowing system uses. Pure."""
    ins = _ins()
    body = ins._clean(title)
    return [s for s in (p.strip() for p in ins._SEGMENT_RE.split(body)) if s]


def extract_artifact(title: str | None, process: str = "") -> dict | None:
    """
    The file this caption is about, from the caption alone.

    Generic on purpose: every candidate the caption can offer (the whole
    caption, each segment, the basename of each) is tested against the same
    two questions — does it look like a file name, and does it carry an
    extension. The first that answers yes wins. No application is named.

    Returns {name, extension, category, dirty} or None.
    """
    ins = _ins()
    raw = ins._clean(title)
    if not raw:
        return None
    body, dirty = ins._strip_dirty_marker(raw)
    body = ins._strip_decorations(body)

    candidates: list[str] = []
    segments = split_segments(body)
    # Whole caption first: a file name may itself contain the separators
    # ("Creative-Motion-Studio — копия (10) — BESTT — копия.7z").
    candidates.append(body)
    candidates.extend(segments)
    candidates.extend(ins._basename(s) for s in ([body] + segments))

    for candidate in candidates:
        text = ins._strip_decorations((candidate or "").strip())
        text, inner_dirty = ins._strip_dirty_marker(text)
        text = text.strip()
        if not text or not ins._looks_like_name(text):
            continue
        if not ins.has_extension(text):
            continue
        if ins.is_lock_name(text):
            continue
        extension = text.rsplit(".", 1)[-1].lower()
        return {
            "name": text,
            "extension": extension,
            "category": extension_category(extension),
            "dirty": bool(dirty or inner_dirty) or None,
        }
    return None


def collect_signals(window: dict, artifact: dict | None = None) -> dict:
    """
    Everything measurable about one window. Booleans only — the interpretation
    happens in score(), so both can be tested independently.
    """
    ins = _ins()
    title = (window or {}).get("title") or ""
    process = ins.normalize_process((window or {}).get("process"))
    segments = split_segments(title)

    own = False
    read = _read("is_own_window", title, process, int((window or {}).get("hwnd") or 0))
    if read and read["ok"]:
        own = bool(read["value"])

    page_name = None
    try:
        if ins._BROWSER_SUFFIX_RE.search(ins._clean(title)):
            page_name = ins.page_title(title)
    except Exception:
        page_name = None

    return {
        "has_title": bool(title.strip()),
        "is_own": own,
        "is_real_window": ins.is_real_window(window) if isinstance(window, dict) else True,
        "single_segment": len(segments) <= 1,
        "has_artifact": artifact is not None,
        "artifact_category": (artifact or {}).get("category") or "",
        "browser_suffix": page_name is not None,
        "browser_process": ins.is_browser(process),
        "terminal_process": process in ins._TERMINAL_PROCESSES,
        "explorer_process": process in ins._EXPLORER_PROCESSES,
        "document_process": process in ins._DOCUMENT_APPS,
        "title_is_path": ins.is_absolute_path(title.strip()),
        "page_name": page_name,
        "process": process,
        "segments": segments,
    }


# Weights. Positive = evidence for, negative = evidence against. Process-name
# knowledge appears ONLY here, and only as a nudge: a browser process with a
# file open still resolves to that file, and an unknown process with a file in
# its caption still resolves to a file.
_WEIGHTS = (
    # (signal, surface, weight, human explanation)
    ("is_own", "own", 100, "окно принадлежит самому Джарвису"),
    ("is_real_window", "dialog", -40, "это настоящее окно верхнего уровня"),
    ("browser_suffix", "page", 60, "в заголовке суффикс браузера"),
    ("browser_process", "page", 25, "процесс показывает веб-содержимое"),
    ("terminal_process", "terminal", 50, "процесс — командная оболочка"),
    ("explorer_process", "folder", 50, "процесс — файловый менеджер"),
    ("title_is_path", "folder", 20, "в заголовке абсолютный путь"),
    ("document_process", "document", 15, "процесс обычно показывает документы"),
)


def score(window: dict, artifact: dict | None = None) -> dict:
    """
    {type, confidence, scores, signals} for one window.

    The winner is whichever surface collected the most evidence. When nothing
    collects any, the answer is "app" — a window of a program — which is still
    a true statement, unlike "не документ".
    """
    signals = collect_signals(window, artifact)
    scores: dict[str, int] = {}
    reasons: list[str] = []

    def add(surface: str, weight: int, why: str):
        if not surface:
            return
        scores[surface] = scores.get(surface, 0) + weight
        if weight:
            reasons.append(f"{why} ({surface}{'+' if weight > 0 else ''}{weight})")

    for name, surface, weight, why in _WEIGHTS:
        if signals.get(name):
            add(surface, weight, why)

    # A file named in the caption is the strongest ordinary evidence there is,
    # and it decides the surface by the FILE's category — which is how an
    # archive, a video or an unknown extension all get a correct answer without
    # anybody adding the program that opened them.
    if signals["has_artifact"]:
        category = signals["artifact_category"] or "file"
        extension = (artifact or {}).get("extension", "")
        if category == "program":
            # An executable in a caption names the PROGRAM that owns the window
            # ("cmd.exe - python -m pytest", "Setup.exe"), not a file the user
            # opened. Weighting it like an open document is what made the test
            # runner's own console look like "открыта программа cmd.exe"
            # instead of "активен терминал".
            add("program", 20, f"в заголовке имя программы .{extension}")
        else:
            add(category, 70, f"в заголовке имя файла .{extension}")

    # A popup, bubble or modal (owned window / tool window) is a dialog whatever
    # it is called, in any language — the same rule Alt+Tab uses.
    if not signals["is_real_window"]:
        add("dialog", 80, "окно является всплывающим или модальным")

    # One lonely caption segment with no file in it is a program window.
    if signals["single_segment"] and not signals["has_artifact"]:
        add("app", 30, "в заголовке один сегмент и нет имени файла")

    if not signals["has_title"]:
        add("unknown", 10, "у окна нет заголовка")

    if not scores:
        return {"type": "app", "confidence": 0, "scores": {}, "signals": signals,
                "reasons": ["нет ни одного признака"]}

    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] <= 0:
        return {"type": "app", "confidence": 0, "scores": scores, "signals": signals,
                "reasons": reasons}
    return {"type": best[0], "confidence": best[1], "scores": scores,
            "signals": signals, "reasons": reasons}


# ─────────────────────────────────────────────────────────────────────────────
# Subject — the single record every question is answered from.
# ─────────────────────────────────────────────────────────────────────────────

def new_subject(**over) -> dict:
    subject = {
        "target": "foreground",
        "found": False,
        "window": None,       # {title, process, hwnd, substituted}
        "app": None,          # {process, display_name}
        "artifact": None,     # {name, path, extension, category, exists, dirty, confidence}
        "surface": "unknown",
        "alternatives": [],
        "behind": None,       # служебное окно, за которым нашлось содержимое
        "evidence": [],
        "reason": "",
        "changed": None,      # vs the previous answer about the same target
        "elapsed_ms": 0,
    }
    subject.update(over)
    return subject


def _app_of(window: dict) -> dict:
    ins = _ins()
    process = ins.normalize_process((window or {}).get("process"))
    segments = split_segments((window or {}).get("title") or "")
    # A human-facing name, preferring what the caption itself says over the
    # process image name, and never requiring a lookup table.
    display = segments[-1] if len(segments) > 1 else (segments[0] if segments else "")
    if not display:
        display = process
    return {"process": process, "display_name": display}


def _resolve_artifact_path(artifact: dict, process: str, evidence: list) -> dict:
    """
    Turn a file NAME into a verified path, using the same Recent/MRU index the
    document cascade uses. A path is only ever reported when it exists on disk.
    """
    out = dict(artifact)
    out.setdefault("path", None)
    out.setdefault("exists", None)
    out.setdefault("confidence", "name_only")
    read = _read("recent_candidates", artifact.get("name") or "", process)
    if not read:
        return out
    evidence.append({"kind": "recent_candidates", "source": "recent",
                     "value": len(read["value"] or []) if read["ok"] else "error",
                     "cost_ms": read["cost_ms"]})
    candidates = [c for c in (read["value"] or []) if c]
    verified = []
    for candidate in candidates[:10]:
        exists = _read("path_exists", candidate)
        if exists and exists["ok"] and exists["value"]:
            verified.append(candidate)
    if len(verified) == 1:
        out.update({"path": verified[0], "exists": True, "confidence": "probable"})
    elif len(verified) > 1:
        out.update({"candidates": verified[:5], "confidence": "name_only"})
    return out


def _subject_from_window(window: dict, target: str, deep: bool = False,
                         deadline_s=None) -> dict:
    """Build a Subject from one window. `deep` also asks the document cascade."""
    ins = _ins()
    evidence: list = []
    title = (window or {}).get("title") or ""
    process = ins.normalize_process((window or {}).get("process"))

    artifact = extract_artifact(title, process)
    verdict = score(window, artifact)
    evidence.append({"kind": "window", "source": "windows",
                     "value": {"title": title, "process": process},
                     "cost_ms": 0})
    evidence.append({"kind": "surface", "source": "scoring",
                     "value": verdict["type"], "cost_ms": 0,
                     "why": verdict["reasons"], "scores": verdict["scores"]})

    surface = verdict["type"]

    # The precise path. The existing cascade (COM for Office, Recent/MRU, UIA)
    # is the most accurate source there is, so it is asked FIRST for the
    # foreground; when it declines — which is exactly what used to happen for
    # every application outside its tables — the generic name→path lookup runs
    # instead, so an answer is still produced.
    if artifact is not None:
        if deep:
            read = _read("active_document", deadline_s)
            doc = (read or {}).get("value") if read else None
            if isinstance(doc, dict):
                evidence.append({"kind": "document_cascade", "source": doc.get("source"),
                                 "value": doc.get("path") or doc.get("name"),
                                 "confidence": doc.get("confidence"),
                                 "cost_ms": doc.get("elapsed_ms", read["cost_ms"])})
                if doc.get("path") and not doc.get("from_memory"):
                    artifact = dict(artifact)
                    artifact.update({
                        "name": doc.get("name") or artifact.get("name"),
                        "path": doc.get("path"),
                        "exists": True,
                        "confidence": doc.get("confidence") or "probable",
                        "dirty": doc.get("dirty", artifact.get("dirty")),
                    })
        if not artifact.get("path"):
            artifact = _resolve_artifact_path(artifact, process, evidence)

    subject = new_subject(
        target=target,
        found=bool(title.strip()),
        window={
            "title": title,
            "process": process,
            "hwnd": int((window or {}).get("hwnd") or 0),
            "substituted": bool((window or {}).get("substituted")),
        },
        app=_app_of(window),
        artifact=artifact,
        surface=surface,
        evidence=evidence,
    )
    return subject


# Surfaces that carry no content of their own. A window like this in front of
# the user almost always belongs to a program whose real window is right behind
# it: a "resume reading" bubble over a document, a progress box over an
# archiver, a login prompt over a mail client. Answering "ничего не вижу" in
# that situation is technically true and practically useless.
_EMPTY_SURFACES = frozenset({"app", "dialog", "unknown", "program"})


def _sibling_with_content(subject: dict) -> dict | None:
    """
    Another window of the SAME process that does have content.

    Generic by construction: the link is the process id/name, which every
    windowing system provides, so it works for a program nobody listed anywhere.
    Only runs when the foreground itself yielded nothing, so it costs nothing in
    the normal case.
    """
    window = subject.get("window") or {}
    process = (window.get("process") or "").strip()
    if not process:
        return None
    read = _read("list_windows")
    windows = [w for w in ((read or {}).get("value") or [])
               if isinstance(w, dict) and (w.get("title") or "").strip()]
    ins = _ins()
    best = None
    for candidate in windows:
        if int(candidate.get("hwnd") or 0) == int(window.get("hwnd") or 0):
            continue
        if ins.normalize_process(candidate.get("process")) != process:
            continue
        sibling = _subject_from_window(candidate, subject.get("target"), deep=False)
        if sibling.get("surface") in _EMPTY_SURFACES:
            continue
        if not ((sibling.get("artifact") or {}).get("name")
                or sibling.get("surface") == "page"):
            continue
        best = sibling
        break
    if best is None:
        return None
    best["behind"] = window.get("title")
    return best


def _match_window(window: dict, hint: str) -> int:
    """How well one window answers to a name the user said. 0 = not at all."""
    ins = _ins()
    hint = (hint or "").strip().lower()
    if not hint:
        return 0
    title = ((window or {}).get("title") or "").lower()
    process = ins.normalize_process((window or {}).get("process"))
    segments = [s.lower() for s in split_segments((window or {}).get("title") or "")]
    if process == hint:
        return 100
    if any(s == hint for s in segments):
        return 90
    if hint in process or process in hint:
        return 70
    if any(s.startswith(hint) for s in segments):
        return 60
    if hint in title:
        return 40

    # Addressed by ROLE or by CONTENT instead of by name: "в проводнике",
    # "в браузере", "а архив?". Deliberately scored BELOW every name tier,
    # so a window literally called "Проводник Pro" still wins over the role.
    role = role_of_word(hint)
    if role and role in window_roles(window):
        return 50
    surface = surface_of_word(hint)
    if surface:
        artifact = extract_artifact((window or {}).get("title") or "", process)
        if artifact and artifact.get("category") == surface:
            return 45
        try:
            if _subject_from_window(window, "window", deep=False).get("surface") == surface:
                return 45
        except Exception:
            pass
    return 0


# Words that are part of the question, never the name of a window.
_QUESTION_WORDS = frozenset({
    "что", "че", "чего", "где", "какой", "какая", "какое", "какие", "сейчас",
    "щас", "теперь", "открыто", "открыт", "открыта", "активно", "активен",
    "активна", "меня", "мне", "есть", "вообще", "там", "это", "этот", "эта",
    "тот", "скажи", "покажи", "назови", "окно", "окне", "окна", "файл",
    "файле", "файла", "моем", "моём", "нет", "да", "ну", "ага",
})

_ALL_WINDOW_PHRASES = (
    "все окна", "всех окон", "сколько окон", "перечисли окна",
    "какие окна", "вообще открыто", "что там висит", "all windows",
)


def _tokens(text: str) -> list:
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else " " for ch in text)
    return [t for t in cleaned.split() if t]


def interpret(text: str) -> dict:
    """
    Which window the user's own words are about — decided HERE, in code, not by
    the model's choice of `kind`. The model can classify wrongly or drop the
    word entirely ("а в проводнике что" arrived as a bare "what is active"),
    and then nothing downstream could recover it. With the raw phrase passed
    through, the layer has a second, deterministic chance.

    Returns {"target", "hint", "focus"}. An empty hint means "the foreground",
    which is the safe default: never answer about a window nobody mentioned.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return {"target": "foreground", "hint": "", "focus": ""}
    for phrase in _ALL_WINDOW_PHRASES:
        if phrase in raw:
            return {"target": "all", "hint": "", "focus": ""}

    tokens = _tokens(raw)
    for token in tokens:                                  # role wins first
        role = role_of_word(token)
        if role:
            return {"target": "window", "hint": token,
                    "focus": "page" if role == "browser" else ""}
    for token in tokens:                                  # then content
        surface = surface_of_word(token)
        if surface:
            return {"target": "window", "hint": token,
                    "focus": "page" if surface == "page" else ""}

    # Nothing generic matched — maybe the user named a window that is open right
    # now. Asking the screen instead of a fixed list means this also works for
    # programs nobody has ever heard of.
    read = _read("list_windows")
    windows = [w for w in ((read or {}).get("value") or []) if isinstance(w, dict)]
    for token in tokens:
        if len(token) < 3 or token in _QUESTION_WORDS:
            continue
        for window in windows:
            if _match_window(window, token) >= 60:
                return {"target": "window", "hint": token, "focus": ""}
    return {"target": "foreground", "hint": "", "focus": ""}


def describe(target: str = "foreground", name_hint: str = "",
             deadline_s=None) -> dict:
    """
    The one primitive. Every question about "what is in front of me" is a view
    over the record this returns.

      foreground — the window the user is looking at (Jarvis's own window is
                   substituted for the one behind it, as before)
      window     — a window addressed by name ("нет, а happ")
      app        — same, phrased about the application
      all        — every real window, cheaply
      previous   — the last subject answered for this target
    """
    started = time.monotonic()
    target = (target or "foreground").strip().lower()

    def finish(subject: dict) -> dict:
        subject["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        subject["changed"] = _changed_since_last(subject)
        _remember(subject)
        return subject

    if target == "previous":
        last = last_subject("foreground")
        if last:
            return last
        return finish(new_subject(target=target,
                                  reason="Я ещё ничего не отвечал про активное окно."))

    if target in ("all", "windows", "open_windows"):
        read = _read("list_windows")
        windows = [w for w in ((read or {}).get("value") or [])
                   if isinstance(w, dict) and (w.get("title") or "").strip()]
        subjects = []
        for window in windows:
            sub = _subject_from_window(window, "all", deep=False)
            if sub["surface"] == "own":
                continue
            subjects.append(sub)
        head = new_subject(target="all", found=bool(subjects),
                           alternatives=subjects,
                           reason="" if subjects else "Не вижу открытых окон.")
        return finish(head)

    if target in ("window", "named_window", "app", "named_app"):
        read = _read("list_windows")
        windows = [w for w in ((read or {}).get("value") or [])
                   if isinstance(w, dict) and (w.get("title") or "").strip()]
        ranked = sorted(
            ((_match_window(w, name_hint), w) for w in windows),
            key=lambda pair: pair[0], reverse=True,
        )
        best = ranked[0] if ranked else (0, None)
        if not best[0]:
            return finish(new_subject(
                target=target,
                reason=(f"Не вижу окна с названием «{name_hint}». "
                        "Скажи иначе или назови приложение.") if name_hint else
                       "Не назвали, про какое окно спросить.",
            ))
        subject = _subject_from_window(best[1], target, deep=False)
        subject["alternatives"] = [
            _subject_from_window(w, target, deep=False)
            for sc, w in ranked[1:4] if sc > 0
        ]
        return finish(subject)

    # ── foreground ───────────────────────────────────────────────────────────
    read = _read("active_window")
    window = (read or {}).get("value")
    if not isinstance(window, dict) or not (window.get("title") or "").strip():
        snap = _read("snapshot")
        active = ((snap or {}).get("value") or {}).get("active_window") or {}
        if (active.get("title") or "").strip():
            window = dict(active)
        else:
            return finish(new_subject(
                target="foreground",
                reason="Сейчас я не вижу ни одного окна на переднем плане.",
            ))
    subject = _subject_from_window(window, "foreground", deep=True,
                                   deadline_s=deadline_s)
    if subject.get("surface") in _EMPTY_SURFACES and not (
            subject.get("artifact") or {}).get("name"):
        sibling = _sibling_with_content(subject)
        if sibling is not None:
            return finish(sibling)
    return finish(subject)


# ─────────────────────────────────────────────────────────────────────────────
# Answer journal — what was already said, and what changed since.
#
# Without it "а щас?" cannot be answered at all: the layer has no idea what the
# previous answer was, so it repeats it word for word. With it the same question
# becomes a comparison.
# ─────────────────────────────────────────────────────────────────────────────

_answers: dict = {}
_last_text: dict = {"text": "", "ts": 0.0}
# Which substitution has already been explained to the user. The explanation is
# useful the first time and noise from the second time on: while the user TYPES,
# Jarvis is in front at every single turn, so the note appeared in every answer.
_note_said: dict = {"key": None}
_SAME_TTL_S = 600.0


def _fingerprint(subject: dict) -> tuple:
    window = subject.get("window") or {}
    artifact = subject.get("artifact") or {}
    return (window.get("hwnd"), window.get("title"), subject.get("surface"),
            artifact.get("path") or artifact.get("name"))


def _changed_since_last(subject: dict):
    previous = _answers.get(subject.get("target"))
    if not previous:
        return None
    return _fingerprint(previous) != _fingerprint(subject)


def _remember(subject: dict) -> None:
    with _lock:
        _answers[subject.get("target")] = subject


def last_subject(target: str = "foreground") -> dict | None:
    return _answers.get(target)


def dedupe_answer(text: str) -> str:
    """
    Say something new, or say plainly that nothing changed.

    Repeating an identical sentence three times in a row (which is what the
    layer used to do) reads as a malfunction. The user asked a NEW question, so
    the honest new information is "it is the same as a moment ago".
    """
    text = (text or "").strip()
    if not text:
        return text
    with _lock:
        same = (text == _last_text["text"]
                and (time.monotonic() - _last_text["ts"]) < _SAME_TTL_S)
        _last_text.update({"text": text, "ts": time.monotonic()})
    if same:
        return "С прошлого раза ничего не изменилось. " + text
    return text


def _should_say_substitution_note(window: dict) -> bool:
    """True once per substituted window, then silent until it changes."""
    key = (window or {}).get("title")
    with _lock:
        if _note_said["key"] == key:
            return False
        _note_said["key"] = key
    return True


def reset() -> None:
    """Forget the conversation and go back to live sources (tests, new session)."""
    with _lock:
        _answers.clear()
        _last_text.update({"text": "", "ts": 0.0})
        _note_said["key"] = None
    reset_sources()


# ─────────────────────────────────────────────────────────────────────────────
# Rendering — one sentence built from what was ESTABLISHED.
# ─────────────────────────────────────────────────────────────────────────────

def _safe(text, limit=160) -> str:
    return _ins()._safe(text, limit)


def render_subject(subject: dict, focus: str = "") -> str:
    """
    Russian sentence for a Subject.

    `focus` narrows the phrasing to what the user asked about (document / page /
    app / window) WITHOUT turning a mismatch into a denial: if the user asked
    about a document and a video is open, the answer names the video.
    """
    if not isinstance(subject, dict):
        return "Не удалось понять, что сейчас открыто."

    if subject.get("target") == "all":
        others = subject.get("alternatives") or []
        if not others:
            return subject.get("reason") or "Не вижу открытых окон."
        listed = "; ".join(
            f"«{_safe((s.get('window') or {}).get('title'), 80)}»" for s in others[:8]
        )
        tail = f" и ещё {len(others) - 8}" if len(others) > 8 else ""
        return f"Сейчас открыто окон: {len(others)}. {listed}{tail}."

    if not subject.get("found"):
        return subject.get("reason") or "Не удалось определить, что сейчас открыто."

    window = subject.get("window") or {}
    app = subject.get("app") or {}
    artifact = subject.get("artifact") or None
    surface = subject.get("surface") or "unknown"
    title = _safe(window.get("title"), 120)
    app_name = _safe(app.get("display_name") or app.get("process"), 60)

    lead = ""
    if window.get("substituted") and _should_say_substitution_note(window):
        lead = ("Своё окно я за ответ не считаю, поэтому отвечаю про то, "
                "что было активно до него. ")
    if subject.get("behind"):
        lead += (f"На переднем плане служебное окно "
                 f"«{_safe(subject['behind'], 80)}», а содержимое — в соседнем окне "
                 f"того же приложения. ")

    if surface == "own":
        return (lead + "Сейчас на переднем плане окно самого Джарвиса, "
                       "а до него я не запомнил другого окна.").strip()

    if surface == "program":
        name = _safe((artifact or {}).get("name") or title, 120)
        return (lead + f"Сейчас активно окно программы «{name}».").strip()

    # A file was established — say which one, whatever program shows it. The
    # surface check keeps a terminal or a folder from being announced as an
    # "open file" just because its caption happens to contain a path.
    if surface in FILE_SURFACES and artifact and (
            artifact.get("path") or artifact.get("name")):
        kind = SURFACE_RU.get(surface, SURFACE_RU["file"])
        where = artifact.get("path")
        name = _safe(artifact.get("name"), 120)
        dirty = " Есть несохранённые изменения." if artifact.get("dirty") is True else ""
        if where:
            body = f"Сейчас открыт {kind} «{name}» — {_safe(where)}."
        elif artifact.get("candidates"):
            listed = "; ".join(_safe(c) for c in artifact["candidates"])
            body = (f"Сейчас открыт {kind} «{name}», но файлов с таким именем "
                    f"несколько: {listed}. Уточни, какой нужен.")
        else:
            body = (f"Сейчас открыт {kind} «{name}», в окне приложения "
                    f"«{app_name}». Где он лежит, определить не удалось.")
        return (lead + body + dirty).strip()

    if surface == "page":
        name = _safe(
            (score(window).get("signals") or {}).get("page_name") or title, 120)
        return (lead + f"Сейчас открыта страница «{name}» в браузере.").strip()

    if surface == "folder":
        return (lead + f"Сейчас активна папка: «{title}».").strip()

    if surface == "terminal":
        return (lead + f"Сейчас активен терминал: «{title}».").strip()

    if surface == "dialog":
        return (lead + f"Сейчас на переднем плане служебное окно «{title}» "
                       f"приложения «{app_name}» — не сам документ.").strip()

    return (lead + f"Сейчас активно окно «{title}» "
                   f"(приложение «{app_name}»). Открытого файла в нём я не вижу.").strip()


def trace(subject: dict) -> str:
    """
    Everything the answer was built from, as text.

    This exists because the machine that misbehaves is not the machine this code
    is written on. One trace replaces three rounds of guessing.
    """
    if not isinstance(subject, dict):
        return "нет данных"
    lines = [
        f"target      : {subject.get('target')}",
        f"found       : {subject.get('found')}",
        f"surface     : {subject.get('surface')}",
        f"changed     : {subject.get('changed')}",
        f"elapsed_ms  : {subject.get('elapsed_ms')}",
        f"window      : {subject.get('window')}",
        f"app         : {subject.get('app')}",
        f"artifact    : {subject.get('artifact')}",
    ]
    for item in subject.get("evidence") or []:
        lines.append(f"evidence    : {item}")
    for other in subject.get("alternatives") or []:
        lines.append(f"alternative : {(other.get('window') or {}).get('title')}")
    return "\n".join(lines)
