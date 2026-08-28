"""
Issue 007 — fast file search: Everything (es.exe) primary, bounded fallback when
no index. Tests stub the es.exe seam (no real process) and run the fallback on a
real temp tree (no whole-disk walk, deterministic).

Run:  python -m pytest tests/test_search.py -q
"""

from core.awareness import _search
from core.awareness import _known_folders as kf


def test_search_uses_everything_when_available(monkeypatch, tmp_path):
    f = tmp_path / "quarterly report.pdf"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: True)
    monkeypatch.setattr(_search, "_run_es", lambda query, limit: str(f) + "\n")
    res = _search.search(ext="pdf", limit=10)
    assert any(r["path"].endswith("quarterly report.pdf") for r in res)


def test_bounded_fallback_finds_in_roots(monkeypatch, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "invoice.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "note.txt").write_text("y", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    res = _search.search(ext="pdf", limit=10)
    paths = [r["path"] for r in res]
    assert any(p.endswith("invoice.pdf") for p in paths)
    assert all(not p.endswith("note.txt") for p in paths)


def test_bounded_fallback_respects_limit(monkeypatch, tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.log").write_text("x", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    res = _search.search(ext="log", limit=5)
    assert len(res) == 5


def test_bounded_fallback_skips_noise_dirs(monkeypatch, tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "buried.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "real.pdf").write_text("y", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    paths = [r["path"] for r in _search.search(ext="pdf", limit=10)]
    assert any(p.endswith("real.pdf") for p in paths)
    assert all("node_modules" not in p for p in paths)


def test_bounded_fallback_matches_name_substring(monkeypatch, tmp_path):
    (tmp_path / "monthly_report_2026.txt").write_text("x", encoding="utf-8")
    (tmp_path / "other.txt").write_text("y", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    paths = [r["path"] for r in _search.search(name="report", limit=10)]
    assert any(p.endswith("monthly_report_2026.txt") for p in paths)
    assert all(not p.endswith("other.txt") for p in paths)


def test_bounded_fallback_never_raises_with_tiny_timeout(monkeypatch, tmp_path):
    for i in range(30):
        (tmp_path / f"f{i}.log").write_text("x", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    res = _search.search(ext="log", limit=100, timeout_s=0.0)  # deadline already passed
    assert isinstance(res, list)                                # returns, doesn't hang/raise


# ── integration: file_controller.find_files / get_largest_files go via _search ──

def test_find_files_uses_search_backend(monkeypatch, tmp_path):
    import actions.file_controller as fc
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "target_report.pdf").write_text("x", encoding="utf-8")
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    out = fc.find_files(extension=".pdf", path="home")
    assert "target_report.pdf" in out


def test_largest_files_is_bounded_via_search(monkeypatch, tmp_path):
    import actions.file_controller as fc
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    (tmp_path / "small.bin").write_bytes(b"x" * 10)
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    monkeypatch.setattr(kf, "watch_roots", lambda: [tmp_path])
    out = fc.get_largest_files(path="home", count=1)
    assert "big.bin" in out and "small.bin" not in out


def test_largest_returns_biggest_first(monkeypatch, tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "b.bin").write_bytes(b"x" * 9000)
    monkeypatch.setattr(_search, "_es_available", lambda: False)
    res = _search.largest(scope=tmp_path, count=5)
    assert res and res[0]["path"].endswith("b.bin")
