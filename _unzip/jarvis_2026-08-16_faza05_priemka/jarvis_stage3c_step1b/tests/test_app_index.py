"""
Issue 014 — launch app by (possibly Russian / imprecise) name.

The shortcut list is stubbed, so the fuzzy matcher is tested deterministically
without touching the real Start Menu. No LLM, no screen vision.

Run:  python -m pytest tests/test_app_index.py -q
"""

from core.awareness import _app_index as ai

_FAKE = [
    {"name": "Telegram Desktop", "path": r"C:\Start\Telegram.lnk"},
    {"name": "Adobe Photoshop 2024", "path": r"C:\Start\Photoshop.lnk"},
    {"name": "Discord", "path": r"C:\Start\Discord.lnk"},
    {"name": "Google Chrome", "path": r"C:\Start\Chrome.lnk"},
    {"name": "Blender", "path": r"C:\Start\Blender.lnk"},
]


def _fake(monkeypatch):
    monkeypatch.setattr(ai, "list_shortcuts", lambda: list(_FAKE))


def test_exact_name_matches(monkeypatch):
    _fake(monkeypatch)
    assert ai.best_match("Telegram")["name"] == "Telegram Desktop"


def test_russian_translit_photoshop(monkeypatch):
    _fake(monkeypatch)
    m = ai.best_match("фотошоп")
    assert m and "Photoshop" in m["name"]


def test_russian_translit_telegram_and_discord(monkeypatch):
    _fake(monkeypatch)
    assert ai.best_match("телеграм")["name"] == "Telegram Desktop"
    assert ai.best_match("дискорд")["name"] == "Discord"


def test_partial_substring(monkeypatch):
    _fake(monkeypatch)
    assert "Photoshop" in ai.best_match("photoshop")["name"]


def test_gibberish_no_match_but_suggests(monkeypatch):
    _fake(monkeypatch)
    assert ai.best_match("zzzqqqxyz") is None
    s = ai.suggestions("телеграм", 3)          # ranked suggestions for imprecise input
    assert s and "Telegram" in s[0]


def test_open_app_fuzzy_fallback_launches_shortcut(monkeypatch):
    import actions.open_app as oa
    _fake(monkeypatch)
    launched = []
    monkeypatch.setattr(oa, "_launch_shortcut", lambda path: launched.append(path))
    r = oa.open_app(parameters={"app_name": "фотошоп"})       # Russian, not in allowlist
    assert launched and launched[0].endswith("Photoshop.lnk")
    assert "photoshop" in r.lower()


def test_open_app_unknown_name_suggests_not_vision(monkeypatch):
    import actions.open_app as oa
    _fake(monkeypatch)
    launched = []
    monkeypatch.setattr(oa, "_launch_shortcut", lambda path: launched.append(path))
    r = oa.open_app(parameters={"app_name": "zzzqqqxyz"})
    assert launched == []                                     # nothing launched
    assert "не нашёл" in r.lower()                            # honest text, not a screen click


def test_open_app_allowlist_path_unchanged(monkeypatch):
    # An allowlisted app must NOT go through the fuzzy fallback.
    import actions.open_app as oa
    _fake(monkeypatch)
    called = {"fuzzy": False}
    monkeypatch.setattr(oa, "_open_installed_by_name",
                        lambda name, player: called.__setitem__("fuzzy", True) or "x")
    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {oa.platform.system(): lambda n: True})
    oa.open_app(parameters={"app_name": "chrome"})
    assert called["fuzzy"] is False
