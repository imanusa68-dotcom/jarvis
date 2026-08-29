import re

try:
    import requests
    from bs4 import BeautifulSoup
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_video_id(url: str) -> str | None:
    patterns = [r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _is_valid_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url or ""))


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()

        url = simpledialog.askstring("J.A.R.V.I.S", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as e:
        print(f"[YouTube] ⚠️ URL dialog failed: {e}")
        return None


def _scrape_video_info(video_id: str) -> dict:
    if not _REQUESTS_OK:
        return {}

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text
        info = {}

        title_match = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"', html)
        if title_match:
            info["title"] = title_match.group(1)

        channel_match = re.search(r'"ownerChannelName":"([^"]+)"', html)
        if channel_match:
            info["channel"] = channel_match.group(1)

        views_match = re.search(r'"viewCount":"(\d+)"', html)
        if views_match:
            views = int(views_match.group(1))
            info["views"] = f"{views:,}"

        duration_match = re.search(r'"lengthSeconds":"(\d+)"', html)
        if duration_match:
            secs = int(duration_match.group(1))
            info["duration"] = f"{secs // 60}:{secs % 60:02d}"

        likes_match = re.search(r'"label":"([0-9,]+ likes)"', html)
        if likes_match:
            info["likes"] = likes_match.group(1)

        return info

    except Exception as e:
        print(f"[YouTube] ⚠️ Info scrape failed: {e}")
        return {}


def _scrape_trending(region: str = "TR", max_results: int = 8) -> list[dict]:
    if not _REQUESTS_OK:
        return []

    url = f"https://www.youtube.com/feed/trending?gl={region.upper()}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text

        titles   = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', html)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)

        results = []
        seen    = set()
        for i, title in enumerate(titles):
            if title in seen or len(title) < 5:
                continue
            seen.add(title)
            channel = channels[i] if i < len(channels) else "Unknown"
            results.append({"rank": len(results) + 1, "title": title, "channel": channel})
            if len(results) >= max_results:
                break

        return results

    except Exception as e:
        print(f"[YouTube] ⚠️ Trending scrape failed: {e}")
        return []

def _handle_get_info(parameters: dict, player, speak) -> str:
    url = parameters.get("url", "").strip()
    if not url:
        url = _ask_for_url("Please paste the YouTube video URL:")
    if not url or not _is_valid_youtube_url(url):
        return "Please provide a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID, sir."

    if player:
        player.write_log(f"[YouTube] Getting info: {url}")

    info = _scrape_video_info(video_id)
    if not info:
        return "Could not retrieve video information, sir."

    lines = []
    for key in ("title", "channel", "views", "duration", "likes"):
        if key in info:
            lines.append(f"{key.capitalize()}: {info[key]}")

    result = "\n".join(lines)
    if speak:
        speak(f"Here's the video info, sir. {result.replace(chr(10), '. ')}")

    return result


def _handle_trending(parameters: dict, player, speak) -> str:
    region = parameters.get("region", "TR").upper()

    if player:
        player.write_log(f"[YouTube] Trending: {region}")

    trending = _scrape_trending(region=region, max_results=8)
    if not trending:
        return f"Could not fetch trending videos for region {region}, sir."

    lines = [f"Top trending videos in {region}:"]
    for item in trending:
        lines.append(f"{item['rank']}. {item['title']} — {item['channel']}")

    result = "\n".join(lines)

    if speak:
        top3   = trending[:3]
        spoken = "Here are the top trending videos, sir. " + ". ".join(
            f"Number {v['rank']}: {v['title']} by {v['channel']}" for v in top3
        )
        speak(spoken)

    return result


_ACTION_MAP = {
    "get_info":  _handle_get_info,
    "trending":  _handle_trending,
}


def youtube_video(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    SECURED: YouTube control is limited to read-only operations.
    
    ALLOWED actions (read-only, no automation):
        get_info  : Get video information via URL
        trending  : Get trending videos list
    
    BLOCKED actions (use keyboard/mouse automation or save files):
        play, summarize
    """
    params = parameters or {}
    action = params.get("action", "play").lower().strip()

    # SECURITY: Block dangerous actions
    BLOCKED_ACTIONS = {"play", "summarize"}
    SAFE_ACTIONS = {"get_info", "trending"}

    if action in BLOCKED_ACTIONS:
        print(f"[YouTube] BLOCKED: Action '{action}' disabled for security")
        return (
            f"SECURITY: YouTube action '{action}' is blocked for safety. "
            "Browser automation and file saving are disabled. "
            "Only 'get_info' and 'trending' actions are allowed."
        )

    if action not in SAFE_ACTIONS:
        return f"SECURITY: Unknown YouTube action: '{action}'. Allowed: get_info, trending."

    if player:
        player.write_log(f"[YouTube] (safe) Action: {action}")

    print(f"[YouTube] (safe) Action: {action}  Params: {params}")

    try:
        handler = _ACTION_MAP.get(action)
        if handler:
            return handler(params, player, speak) or "Done."
        return "Action not available."
    except Exception as e:
        print(f"[YouTube] Error in {action}: {e}")
        return f"YouTube {action} failed, sir: {e}"
