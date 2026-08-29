# core/awareness/_file_watcher.py
# Event-driven file watcher over the user's known folders. On Windows watchdog
# uses ReadDirectoryChangesW (native, not polling), so idle cost is ~zero.
#
# watchdog is a SOFT dependency and imported at module top on purpose: the facade
# imports this module inside a try/except, so if watchdog is missing only the
# file half is disabled — the window watcher (issue 001) keeps working.

from __future__ import annotations

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class _Handler(FileSystemEventHandler):
    """Normalises watchdog events into the world model's ingest calls."""

    def __init__(self, sink):
        self._sink = sink

    def _emit(self, path, kind, is_dir):
        try:
            self._sink(path, kind, bool(is_dir))
        except Exception:
            pass  # one bad event must never break the observer thread

    def on_created(self, event):
        self._emit(event.src_path, "created", event.is_directory)

    def on_modified(self, event):
        self._emit(event.src_path, "modified", event.is_directory)

    def on_deleted(self, event):
        self._emit(event.src_path, "deleted", event.is_directory)

    def on_moved(self, event):
        # A move/rename is best described by where the file ended up.
        self._emit(getattr(event, "dest_path", event.src_path), "moved", event.is_directory)


def start(sink):
    """
    Begin watching the user's known folders recursively, pushing events to `sink`
    (awareness.ingest_file_event). Returns the running Observer, or None if there
    are no folders to watch. Never raises for a single unwatchable root.
    """
    from core.awareness import _known_folders

    roots = _known_folders.watch_roots()
    if not roots:
        return None

    observer = Observer()
    handler = _Handler(sink)
    watched = 0
    for root in roots:
        try:
            observer.schedule(handler, str(root), recursive=True)
            watched += 1
        except Exception as e:
            print(f"[Awareness] cannot watch {root}: {e}")

    if watched == 0:
        return None

    observer.daemon = True
    observer.start()
    print(f"[Awareness] file watcher on: {watched} folders")
    return observer
