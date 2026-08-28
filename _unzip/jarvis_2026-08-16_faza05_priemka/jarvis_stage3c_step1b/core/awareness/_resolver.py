# core/awareness/_resolver.py
# Referent resolver: turns a structured `kind` (chosen by Gemini from the user's
# words) into a concrete entity, using the world model (what the USER did) and
# dialogue_state (what JARVIS did). Deterministic and side-effect-free — the NL
# understanding lives in the live model, the state lookup lives here.
#
# Recency comes ONLY from our own event journals, never from filesystem access
# times (NTFS last-access is off by default). File contents are never read.

from __future__ import annotations

import os
import re
from pathlib import Path

from core.awareness import _world_model, _known_folders

# Editor/browser scratch files to skip when picking the "newest file in a folder".
_TEMP_SUFFIXES = (".tmp", ".crdownload", ".part", ".partial", ".swp", ".swx", ".bak")


def _is_temp_name(name: str) -> bool:
    n = name.lower()
    return n.startswith(("~$", ".~")) or n.endswith(_TEMP_SUFFIXES)


def _newest_file_in_folder(folder) -> str | None:
    """
    Newest real file directly in `folder` by modification time — the cold-start
    answer to "the last file in <folder>" when the event journal has nothing.
    Metadata only, one folder, no recursion; never uses last-access time.
    """
    if not folder:
        return None
    try:
        p = Path(folder)
        if not p.exists():
            return None
        newest = None
        newest_mtime = -1.0
        for entry in os.scandir(p):
            try:
                if not entry.is_file(follow_symlinks=False) or _is_temp_name(entry.name):
                    continue
                m = entry.stat().st_mtime
                if m > newest_mtime:
                    newest_mtime, newest = m, entry.path
            except OSError:
                continue
        return newest
    except OSError:
        return None

# A Windows path sitting inside a free-text summary (dialogue_state journal), e.g.
# "Created file: plan.txt (full path: C:/Users/x/Desktop/plan.txt)".
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'()]+")


def _dialogue_state_last_path() -> str | None:
    """Newest concrete path JARVIS itself touched, from its action journal."""
    try:
        import core.dialogue_state as ds
        journal = ds.get().get("action_journal") or []
    except Exception:
        return None
    for entry in reversed(journal):
        m = _PATH_RE.search(entry.get("summary", ""))
        if m:
            return m.group(0).replace("/", "\\")
    return None


def _norm(p: str) -> str:
    return str(p).replace("/", "\\").rstrip("\\").lower()


def _is_under(path: str, folder) -> bool:
    if folder is None:
        return False
    base = _norm(str(folder))
    p = _norm(path)
    return p == base or p.startswith(base + "\\")


def _files_newest_first():
    """Journalled FILE events (not directories), newest first."""
    return [e for e in _world_model.recent_events() if not e.get("is_dir")]


def _found(type_, path=None, title=None, reason=""):
    return {"found": True, "type": type_, "path": path, "title": title, "reason": reason}


def _not_found(reason):
    return {"found": False, "type": None, "path": None, "title": None, "reason": reason}


# Referents that ask "what is in front of me" without naming a category. Before
# Perception Core these had nowhere to go: the model had to guess between
# document / page / app BEFORE anything looked at the screen, and a wrong guess
# came back as a denial ("сейчас не документ") instead of an answer. They are all
# answered from one description of the foreground now.
FOREGROUND_KINDS = frozenset({
    "foreground", "active", "active_window", "current_window", "this_window",
    "what_is_active", "whats_active", "screen", "current", "anything",
})

# "нет, а happ" — a window addressed by NAME, read-only. Without this kind the
# model has only computer_control/focus_window, which is an action: asking a
# question would change what the user is looking at (and gets blocked).
NAMED_WINDOW_KINDS = frozenset({
    "named_window", "window_by_name", "window", "other_window", "find_window",
    "app_by_name", "named_app", "other_app",
})

# "что у меня вообще открыто"
ALL_WINDOWS_KINDS = frozenset({
    "all_windows", "open_windows", "windows", "everything_open",
})

SUBJECT_KINDS = FOREGROUND_KINDS | NAMED_WINDOW_KINDS | ALL_WINDOWS_KINDS


def _subject_result(subject: dict) -> dict:
    """Wrap a Subject so render() can speak it and callers can still act on it."""
    window = (subject or {}).get("window") or {}
    artifact = (subject or {}).get("artifact") or {}
    return {
        "found": bool((subject or {}).get("found")),
        "type": (subject or {}).get("surface") or "window",
        "path": artifact.get("path"),
        "title": window.get("title"),
        "reason": (subject or {}).get("reason") or "",
        "subject": subject,
    }


def describe_kind(kind: str, hint: str = "", text: str = "") -> dict:
    """
    Resolve one of SUBJECT_KINDS through Perception Core.

    `text` is the user's raw phrase. It is the recovery path for the case the
    model classified the question as "what is in front of me" while the user
    actually named another window ("а в проводнике что"). An explicit `hint`
    always wins over anything read out of the phrase.
    """
    from core.awareness import _perception
    kind = (kind or "").strip().lower()
    intent = {}
    if text:
        try:
            intent = _perception.interpret(text) or {}
        except Exception:
            intent = {}
    if not hint:
        hint = intent.get("hint", "") or ""
    focus = intent.get("focus", "") or ""

    if kind in ALL_WINDOWS_KINDS or (
        kind in FOREGROUND_KINDS and intent.get("target") == "all"
    ):
        target = "all"
    elif kind in NAMED_WINDOW_KINDS or hint:
        target = "window"
    else:
        target = "foreground"
    try:
        subject = _perception.describe(target, hint)
    except Exception as e:                      # a question must never fail
        return _not_found(f"Не удалось осмотреть окна: {e}")
    if focus and isinstance(subject, dict):
        subject["focus"] = focus
    return _subject_result(subject)


def _resolve_active_app():
    """
    Which application is in front.

    Goes through the SAME description of the foreground that system_context and
    every other question uses. That is the whole point: while there were two
    independent readers of the screen, one conversation could contain two
    contradictory answers about the very same window.
    """
    from core.awareness import _perception
    try:
        subject = _perception.describe("foreground")
    except Exception:
        subject = None
    if not subject or not subject.get("found"):
        active = _world_model.snapshot().get("active_window") or {}
        if not active.get("title"):
            return _not_found((subject or {}).get("reason") or "Не вижу активного окна.")
        return _found("app", title=active.get("title"), path=None)
    window = subject.get("window") or {}
    reason = ""
    if window.get("substituted"):
        try:
            from core.awareness import _inspectors
            reason = _inspectors.APP_SUBSTITUTED_NOTE
        except Exception:
            reason = ""
    return _found("app", title=window.get("title"), path=None, reason=reason)


# Referents that mean "the document open in front of me right now". Answered by
# _inspectors (issue 009), NOT by the event journal: the journal knows what the
# user last touched, which is a different question and was the old wrong answer.
DOCUMENT_KINDS = frozenset({
    "active_document", "open_document", "current_document",
    "this_document", "document", "active_file", "open_file",
})


def _resolve_active_document() -> dict:
    """
    Ask the document inspector. The whole DocResult is carried under "doc" so
    render() can speak the inspector own honest sentence (several candidates,
    unsaved document, browser page) instead of flattening it to a bare path.
    found is True only when a verified path exists.
    """
    from core.awareness import _inspectors
    try:
        doc = _inspectors.active_document()
    except Exception as e:  # a read-only lookup must never take a turn down
        return _not_found(f"Не удалось определить открытый документ: {e}")
    if not isinstance(doc, dict):
        return _not_found("Не удалось определить открытый документ.")
    # One diagnostic line: which level answered, how sure it is, how long it
    # took. Without it a wrong answer on a real machine cannot be traced back
    # to a level of the cascade.
    try:
        print(
            "[Doc] source=%s conf=%s name=%s path=%s %sms"
            % (doc.get("source"), doc.get("confidence"), doc.get("name"),
               doc.get("path"), doc.get("elapsed_ms"))
        )
    except Exception:
        pass
    # The cascade knows Office and a list of editors. When it comes back with
    # nothing usable, that is not proof that nothing is open — it is proof that
    # this window is outside its tables (an archive, a viewer, a player, any
    # program written after this list). Perception Core looks at the same window
    # without any application list; if IT can name what is open, that answer is
    # better than a denial. This is what used to make "у меня была активна zip
    # файл" come back as "сейчас на переднем плане не документ".
    if not doc.get("path") and not doc.get("from_memory"):
        try:
            from core.awareness import _perception
            subject = _perception.describe("foreground")
        except Exception:
            subject = None
        if subject and subject.get("found"):
            artifact = subject.get("artifact") or {}
            surface = subject.get("surface")
            better = bool(artifact.get("path") or artifact.get("name")) or surface in (
                "page", "folder", "terminal", "dialog", "own", "app")
            if better:
                return _subject_result(subject)

    return {
        "found": bool(doc.get("path")),
        "type": "document",
        "path": doc.get("path"),
        "title": doc.get("name"),
        "reason": doc.get("reason") or "",
        "doc": doc,
    }


# Referents that mean "the web page in front of me right now". Answered by
# reading browser window captions — NEVER by driving the browser: a question
# must not change what the user is looking at.
PAGE_KINDS = frozenset({
    "active_page", "open_page", "current_page", "browser_page",
    "active_tab", "open_tab", "current_tab", "browser_tab", "page", "tab",
})


def _resolve_active_page(hint: str = "") -> dict:
    from core.awareness import _inspectors
    try:
        page = _inspectors.active_page(hint or "")
    except Exception as e:  # a read-only lookup must never take a turn down
        return _not_found(f"Не удалось определить открытую страницу: {e}")
    if not isinstance(page, dict):
        return _not_found("Не удалось определить открытую страницу.")
    first = (page.get("pages") or [{}])[0]
    return {
        "found": bool(page.get("found")),
        "type": "page",
        "path": None,
        "title": first.get("title"),
        "reason": page.get("reason") or "",
        "page": page,
    }


def resolve(kind: str, hint: str = "", text: str = "") -> dict:
    """
    Resolve a referent `kind` to {found, type, path, title, reason}.
    Unknown/unsatisfiable kinds return found=False with a Russian reason.
    """
    kind = (kind or "").strip().lower()

    # Category-free questions first: they describe the foreground as it is
    # instead of testing it against one category and denying everything else.
    if kind in SUBJECT_KINDS:
        return describe_kind(kind, hint, text)

    if kind in ("active_app", "this_app", "app"):
        # "какое приложение активно" WITH a name is really "расскажи про это
        # окно" — answer about the window the user named, read-only.
        if hint:
            return describe_kind("named_window", hint, text)
        return _resolve_active_app()

    if kind in ("last_launched_app", "launched_app", "recently_launched_app"):
        e = _world_model.last_app_event("launched")
        if e:
            return _found("app", title=e["name"])
        return _not_found("Не заметил недавно запущенных приложений.")

    if kind in ("last_installed_app", "installed_app", "recently_installed_app"):
        e = _world_model.last_app_event("installed")
        if e:
            return _found("app", title=e["name"])
        return _not_found("Не заметил недавно установленных приложений.")

    if kind in ("downloaded_file", "downloaded", "download"):
        downloads = _known_folders.folder("downloads")
        for e in _files_newest_first():
            if _is_under(e["path"], downloads):
                return _found("file", path=e["path"])
        # Cold fallback: nothing in the session journal — look at the actual folder.
        newest = _newest_file_in_folder(downloads)
        if newest:
            return _found("file", path=newest)
        return _not_found("Не нашёл файлов в папке Загрузки.")

    if kind in ("newest_in_folder", "last_in_folder", "latest_in_folder", "last_file_in_folder"):
        folder = _known_folders.folder((hint or "downloads").strip().lower())
        newest = _newest_file_in_folder(folder)
        if newest:
            return _found("file", path=newest)
        return _not_found(f"В папке «{hint or 'downloads'}» не нашёл файлов.")

    if kind in ("recent_file", "last_document", "last_file", "opened_file", "this_file"):
        files = _files_newest_first()
        if files:
            return _found("file", path=files[0]["path"])
        ds_path = _dialogue_state_last_path()
        if ds_path:
            return _found("file", path=ds_path)
        return _not_found("Не нашёл недавнего файла.")

    if kind in ("new_folder", "added_folder", "folder"):
        for e in _world_model.recent_events():
            if e.get("is_dir") and e.get("kind") == "created":
                return _found("folder", path=e["path"])
        return _not_found("Не заметил недавно созданной папки.")

    if kind in ("open_folder", "active_folder", "current_folder", "opened_folder"):
        from core.awareness import _explorer
        folders = _explorer.open_folders()
        if not folders:
            return _not_found("Не вижу открытой папки Проводника.")
        fg = _explorer.foreground_hwnd()
        for f in folders:                       # the active Explorer window wins
            if f.get("hwnd") == fg:
                return _found("folder", path=f["path"])
        if len(folders) == 1:                   # only one open → unambiguous
            return _found("folder", path=folders[0]["path"])
        names = ", ".join(Path(f["path"]).name for f in folders[:5])
        return _not_found(
            f"Открыто несколько папок ({names}). Сделайте нужную активной или назовите её."
        )

    if kind in ("same_folder", "there", "same_place"):
        files = _files_newest_first()
        if files:
            return _found("folder", path=str(Path(files[0]["path"]).parent))
        return _not_found("Не знаю, куда «туда же» — не было недавних файлов.")

    if kind in DOCUMENT_KINDS:
        return _resolve_active_document()

    if kind in PAGE_KINDS:
        return _resolve_active_page(hint)

    if kind in ("by_extension", "extension", "by_ext"):
        ext = "." + (hint or "").strip().lstrip(".").lower()
        if ext == ".":
            return _not_found("Не указано расширение файла.")
        for e in _files_newest_first():
            if e["path"].lower().endswith(ext):
                return _found("file", path=e["path"])
        return _not_found(f"Не нашёл недавнего файла {ext}.")

    # An unrecognised kind used to end the turn with a refusal. The user asked
    # about their screen either way, so the honest fallback is to describe what
    # is in front of them and let the model phrase it.
    if hint:
        named = describe_kind("named_window", hint)
        if named.get("found"):
            return named
    described = describe_kind("foreground", "")
    if described.get("found"):
        return described
    return _not_found(f"Не удалось разрешить ссылку типа «{kind}».")


_TYPE_RU = {"file": "Файл", "folder": "Папка", "app": "Активное приложение"}


def render(result: dict) -> str:
    """
    Russian text for a resolve() result — what the resolve_reference tool speaks
    back so the model can act on it (open / save / name it).
    """
    # A document result speaks for itself: the inspector already phrases the
    # honest answer, including "several files with that name" and "no path".
    if isinstance(result, dict) and result.get("doc") is not None:
        from core.awareness import _inspectors
        return _inspectors.render(result["doc"])

    if isinstance(result, dict) and result.get("page") is not None:
        from core.awareness import _inspectors
        return _inspectors.render_page(result["page"])

    if isinstance(result, dict) and result.get("subject") is not None:
        from core.awareness import _perception
        subject = result["subject"] or {}
        return _perception.render_subject(subject, subject.get("focus", ""))

    if not result or not result.get("found"):
        reason = (result or {}).get("reason") or "Не удалось разрешить ссылку."
        return reason
    label = _TYPE_RU.get(result.get("type"), "Объект")
    target = result.get("path") or result.get("title") or ""
    text = f"{label}: {target}"
    reason = result.get("reason") or ""
    return f"{reason} {text}".strip() if reason else text
