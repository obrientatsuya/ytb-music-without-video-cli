"""search.py - YouTube search and playlist loading via yt-dlp."""

import json
import re
import subprocess
import sys

_PLAYLIST_RE = re.compile(r"[?&]list=", re.I)
_SEP = "|SPLIT|"

# Call yt-dlp via the same Python interpreter to avoid PATH issues
_YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]


def is_playlist(url: str) -> bool:
    return "youtube.com" in url and bool(_PLAYLIST_RE.search(url))


def load_playlist(url: str) -> list[dict]:
    """Load all tracks from a playlist without downloading anything."""
    print("📋 Loading playlist...", end="\r", flush=True)
    cmd = _YTDLP_CMD + [
        "--flat-playlist",
        "--no-warnings", "--quiet",
        "--yes-playlist",
        "--playlist-items", "1-500",
        "--print", f"%(id)j{_SEP}%(title)j{_SEP}%(duration)j",
        url,
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return []

    results = _parse(raw)
    print(f"📋 {len(results)} tracks loaded.          ")
    return results


def search_youtube(query: str, max_results: int = 8) -> list[dict]:
    """Search by text, returns list of tracks."""
    cmd = _YTDLP_CMD + [
        "--flat-playlist",
        "--no-warnings", "--quiet",
        "--print", f"%(id)j{_SEP}%(title)j{_SEP}%(duration)j",
        f"ytsearch{max_results}:{query}",
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return []
    return _parse(raw)


def get_stream_url(yt_url: str) -> str:
    """Resolve the direct opus/webm stream URL."""
    cmd = _YTDLP_CMD + [
        "--no-warnings", "--quiet",
        "--extractor-args", "youtube:skip=dash,hls",
        "-f", "bestaudio[acodec=opus]/bestaudio",
        "--get-url",
        yt_url,
    ]
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    return out.strip().splitlines()[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(raw: str) -> list[dict]:
    results = []
    for line in raw.strip().splitlines():
        parts = line.split(_SEP)
        if len(parts) < 3:
            continue
        try:
            vid_id   = json.loads(parts[0])
            title    = json.loads(parts[1])
            duration = json.loads(parts[2])
        except (json.JSONDecodeError, ValueError):
            continue
        if not vid_id:
            continue
        results.append({
            "title":    title or "?",
            "yt_url":   f"https://www.youtube.com/watch?v={vid_id}",
            "duration": _fmt(duration),
        })
    return results


def _fmt(secs) -> str:
    if not secs:
        return "?"
    s = int(secs)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
