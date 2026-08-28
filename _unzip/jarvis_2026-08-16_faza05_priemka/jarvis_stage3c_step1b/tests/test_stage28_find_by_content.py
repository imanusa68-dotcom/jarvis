# -*- coding: utf-8 -*-
"""Stage 2.8 - a file must be findable by what is INSIDE it, not only by name.

Regression for the real dialogue: проба.txt whose CONTENT is привет. When the
user referred to it as 'the file привет', Jarvis denied it existed. It must
instead resolve the file by content. Also guards the huge-file safety rule:
content search must never read a giant file.
"""
import tempfile
from pathlib import Path

from core.awareness import _search
from actions.file_controller import find_files, _suggest_files


def _fresh():
    d = Path(tempfile.mkdtemp(prefix="jv_28_"))
    return d


def test_search_content_finds_a_file_by_its_text():
    w = _fresh()
    (w / "проба.txt").write_text("привет", encoding="utf-8")
    (w / "other.txt").write_text("nothing here", encoding="utf-8")
    hits = _search.search_content("привет", scope=w, limit=10)
    names = [Path(h["path"]).name for h in hits]
    assert "проба.txt" in names, names
    assert "other.txt" not in names, names


def test_search_content_is_case_insensitive():
    w = _fresh()
    (w / "a.txt").write_text("Hello WORLD", encoding="utf-8")
    hits = _search.search_content("world", scope=w, limit=10)
    assert [Path(h["path"]).name for h in hits] == ["a.txt"]


def test_content_search_never_reads_a_huge_file():
    """A multi-MB file that CONTAINS the term is skipped - content search must
    stay huge-file-safe and never pull a giant file into memory."""
    w = _fresh()
    big = w / "giant.txt"
    # 1 MB > _CONTENT_MAX_BYTES (512 KB); the needle is really inside it.
    big.write_text("x" * (1024 * 1024) + "привет", encoding="utf-8")
    hits = _search.search_content("привет", scope=w, limit=10)
    assert hits == [], "huge file must be skipped, not read"


def test_content_search_ignores_binary_extensions():
    w = _fresh()
    (w / "pic.png").write_bytes(b"\x89PNG\r\n" + "привет".encode("utf-8"))
    hits = _search.search_content("привет", scope=w, limit=10)
    assert hits == [], "non-text extension must not be content-scanned"


def test_find_falls_back_from_name_to_content():
    """'find привет' with no file NAMED привет must still surface проба.txt
    because that file CONTAINS привет."""
    w = _fresh()
    (w / "проба.txt").write_text("привет", encoding="utf-8")
    out = find_files(name="привет", path=str(w))
    assert "проба.txt" in out, out
    assert "содержимому" in out, "must flag that the match was by content"


def test_find_by_explicit_content_arg():
    w = _fresh()
    (w / "notes.txt").write_text("secret plan", encoding="utf-8")
    out = find_files(content="secret", path=str(w))
    assert "notes.txt" in out, out


def test_suggest_files_offers_content_matches_on_a_miss():
    """The 'did you mean' helper must offer проба.txt when asked about привет."""
    w = _fresh()
    (w / "проба.txt").write_text("привет", encoding="utf-8")
    hint = _suggest_files("привет", folder=str(w))
    assert "проба.txt" in hint, hint
    assert "Возможно" in hint


def test_find_still_matches_by_name():
    w = _fresh()
    (w / "report.txt").write_text("anything", encoding="utf-8")
    out = find_files(name="report", path=str(w))
    assert "report.txt" in out, out
    # A plain name match must NOT be mislabelled as a content match.
    assert "содержимому" not in out


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"test_stage28_find_by_content: {passed} passed, {failed} failed")


if __name__ == "__main__":
    _run()
