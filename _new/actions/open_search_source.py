# actions/open_search_source.py
# MARK XXXV — Open a Source from the Most Recent Search / Research Result
#
# This tool lets the user say "Open source 2 in Yandex" or
# "Open the Forbes source in Chrome" right after a search/research response.
#
# It does NOT touch the search pipeline.
# It reads from the session source registry and delegates to browser_control,
# which already handles "prefer existing browser + new tab" natively.

from __future__ import annotations
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ═══════════════════════════════════════════════════════════════════════════════

_MSG_NO_SOURCES    = "I don't have any recent search sources to open, sir."
_MSG_NOT_FOUND     = "I couldn't find that source in the most recent search results, sir."
_MSG_NO_URL        = "That source doesn't have a URL I can open, sir."


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_source(
    source_index: Optional[int],
    source_match: Optional[str],
) -> Optional[dict]:
    """
    Look up a source in the session registry by index or text match.
    Returns the source dict or None.
    """
    from core.search_source_registry import (
        get_last_sources,
        get_source_by_index,
        find_source,
    )

    if source_index is not None:
        return get_source_by_index(int(source_index))

    if source_match:
        return find_source(source_match)

    # Neither index nor match given → return the first (most relevant) source
    state = get_last_sources()
    if state and state.get("sources"):
        return state["sources"][0]

    return None


def _open_url(
    url: str,
    browser: Optional[str],
    prefer_existing_browser: bool,
    prefer_new_tab: bool,
) -> str:
    """
    Open *url* using the project's browser layer.

    Default preference order:
      1. Existing running browser → new tab  (via native_browser)
      2. Existing running browser → same tab (fallback)
      3. browser_control (Playwright fallback for the same browser)
      4. browser_control without browser hint (system default)
    """
    from actions.browser_control import browser_control

    # ── Preferred path: native browser already running ──────────────────────
    if prefer_existing_browser and browser:
        try:
            from actions.native_browser import is_browser_running, navigate_to_url

            if is_browser_running(browser):
                print(f"[OpenSearchSource] {browser} is running — using native new-tab path")
                success = navigate_to_url(browser, url, same_tab=not prefer_new_tab)
                if success:
                    tab_desc = "a new tab" if prefer_new_tab else "the current tab"
                    return f"Opened the source in {tab_desc} of {browser.capitalize()}, sir."

                print(f"[OpenSearchSource] native navigate_to_url failed, falling back to browser_control")

        except Exception as e:
            print(f"[OpenSearchSource] native browser check failed: {e}")

    # ── Standard fallback: browser_control handles the rest ─────────────────
    params: dict = {
        "action":   "go_to",
        "url":      url,
        "same_tab": not prefer_new_tab,   # default: new tab
    }
    if browser:
        params["browser"] = browser

    result = browser_control(params)
    return result or f"Opened {url} in the browser, sir."


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def open_search_source(
    parameters: dict = None,
    player=None,
    session_memory=None,
    **kwargs,
) -> str:
    """
    Open one of the sources from the most recent web_search / deep_research result.

    Parameters (all optional — at least one of source_index / source_match is
    recommended for disambiguation):

        source_index:             int   — 1-based position shown to the user
        source_match:             str   — title/domain/URL substring to search by
        browser:                  str   — "yandex" | "chrome" (preferred browser)
        prefer_existing_browser:  bool  — prefer an already running browser
                                          (default: True)
        prefer_new_tab:           bool  — prefer opening in a new tab
                                          (default: True)

    Returns:
        Human-readable status string.
    """
    params = parameters or kwargs

    source_index            = params.get("source_index", None)
    source_match            = params.get("source_match", None)
    browser                 = (params.get("browser") or "").strip().lower() or None
    prefer_existing_browser = bool(params.get("prefer_existing_browser", True))
    prefer_new_tab          = bool(params.get("prefer_new_tab", True))

    if player:
        player.write_log(f"[OpenSource] index={source_index} match={source_match} browser={browser}")

    print(
        f"[OpenSearchSource] index={source_index}  match={source_match!r}  "
        f"browser={browser}  prefer_existing={prefer_existing_browser}  "
        f"prefer_new_tab={prefer_new_tab}"
    )

    # ── Step 1: Check that there are any stored sources ─────────────────────
    try:
        from core.search_source_registry import get_last_sources
    except ImportError as e:
        print(f"[OpenSearchSource] registry import failed: {e}")
        return _MSG_NO_SOURCES

    state = get_last_sources()
    if not state or not state.get("sources"):
        return _MSG_NO_SOURCES

    # ── Step 2: Find the requested source ───────────────────────────────────
    source = _resolve_source(source_index, source_match)
    if not source:
        return _MSG_NOT_FOUND

    url = source.get("url", "").strip()
    if not url:
        return _MSG_NO_URL

    title  = source.get("title",  "")
    domain = source.get("domain", "")
    idx    = source.get("index",  "?")

    label  = f'"{title}"' if title else f"source {idx}"
    if domain:
        label += f" ({domain})"

    print(f"[OpenSearchSource] Opening: {label} → {url}")

    # ── Step 3: Open through browser layer ──────────────────────────────────
    try:
        result = _open_url(url, browser, prefer_existing_browser, prefer_new_tab)
        print(f"[OpenSearchSource] Browser result: {result!r:.100}")
        return result
    except Exception as e:
        print(f"[OpenSearchSource] Error opening URL: {e}")
        import traceback
        traceback.print_exc()
        return f"I found the source but couldn't open it, sir. URL: {url}"
