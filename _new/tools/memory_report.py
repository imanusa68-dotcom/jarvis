# tools/memory_report.py
"""
Stage 3B.1 - look inside Jarvis's memory with your own eyes.

WHY THIS EXISTS
---------------
3B.1 is deliberately inert: it builds the new memory, but nothing in the live
assistant reads from it yet. That is safe, but it leaves the user with nothing
to test - and "trust me, it works" is how the last three memory bugs survived
all the way to a live conversation.

This tool is the test surface. It never changes how Jarvis behaves; it only
shows what is stored and lets you interrogate the new search directly.

USAGE
  uv run python tools/memory_report.py              # what is in memory now
  uv run python tools/memory_report.py --import     # copy old memory across
  uv run python tools/memory_report.py --search "ночной график"
  uv run python tools/memory_report.py --all        # include hidden junk

Nothing here deletes anything. The old long_term.json is never modified.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import store                      # noqa: E402
from core.safe_json import state_dir        # noqa: E402


def _fts5_available() -> bool:
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()


def _backup_before_upgrade(path: Path):
    """Copy jarvis.db aside if this run is about to migrate it.

    A migration is the one moment a database can be left in a shape an older
    build refuses to open. A backup costs milliseconds; not having one costs
    the user everything Jarvis knows about them.
    """
    if not path.exists():
        return None
    probe = store.connect(path)
    try:
        current = store._user_version(probe)
    finally:
        probe.close()
    latest = max(m[0] for m in store.JARVIS_MIGRATIONS)
    if current >= latest:
        return None
    dest = path.with_name(
        f"{path.name}.pre-v{latest}-{datetime.now():%Y%m%d-%H%M%S}.bak")
    shutil.copy2(path, dest)
    return dest


def _show(facts: list, title: str) -> None:
    print(f"\n{title} ({len(facts)}):")
    if not facts:
        print("   - пусто -")
        return
    for fact in facts:
        flag = "⊙" if fact.get("pinned") else " "
        print(f"  {flag} [{fact['category']}/{fact['key']}] {fact['value']}")
        if fact.get("verbatim"):
            print(f"      твоими словами: «{fact['verbatim']}»")
        print(f"      источник: {fact['source']}  "
              f"уверенность: {float(fact['confidence']):.2f}  "
              f"обновлён: {fact['updated_at']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Jarvis memory v2")
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="copy memory v1 (long_term.json) into memory v2")
    parser.add_argument("--search", metavar="TEXT", help="try the new search")
    parser.add_argument("--all", action="store_true",
                        help="also show hidden low-confidence facts")
    args = parser.parse_args()

    print(f"python  : {sys.version.split()[0]}")
    print(f"sqlite  : {sqlite3.sqlite_version}")
    fts = _fts5_available()
    print(f"fts5    : {'OK' if fts else 'НЕДОСТУПЕН'}")
    if not fts:
        print("\n✗ Эта сборка Python без FTS5 - поиск по памяти работать не будет.")
        return 2

    path = store.db_path()
    print(f"база    : {path}")
    print(f"состояние: {state_dir()}")

    saved = _backup_before_upgrade(path)
    if saved:
        print(f"резерв  : {saved.name}")

    conn = store.open_store(path)
    from memory import fact_store as fs      # after migration, by design

    print(f"версия СХ: v{store._user_version(conn)}")
    print(f"индекс  : {'РАССИНХРОНИЗИРОВАН' if fs.fts_out_of_sync(conn) else 'OK'}")

    if args.do_import:
        from memory.memory_manager import load_memory
        report = fs.import_legacy_memory(conn, load_memory())
        print(f"\nСинхронизировано фактов: {report['imported']}, "
              f"из них скрыто как мусор: {report['hidden']}")
        if report["removed"]:
            print(f"Убрано (исчезло из старой памяти): {report['removed']}")
        print("Можно запускать сколько угодно раз - это зеркало, не добавление.")
        print("Старый long_term.json не изменён.")

    if args.search:
        hits = fs.search_facts(conn, args.search)
        print(f"\nПоиск: «{args.search}»")
        if not hits:
            print("   ничего не найдено")
        for hit in hits:
            print(f"  {hit['score']:6.2f}  [{hit['category']}/{hit['key']}] "
                  f"{hit['value']}")
        return 0

    _show(fs.list_facts(conn), "Факты, которые Джарвис считает годными")
    if args.all:
        visible = {f["id"] for f in fs.list_facts(conn)}
        hidden = [f for f in fs.list_facts(conn, include_hidden=True)
                  if f["id"] not in visible]
        _show(hidden, "Скрытое (хранится, но не используется)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
