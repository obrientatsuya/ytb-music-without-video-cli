"""search.py - YouTube search, URL handling and stream resolution via yt-dlp."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

_SEP = "|SPLIT|"
_SEARCH_TIMEOUT = 60
_PLAYLIST_TIMEOUT = 180
_STREAM_TIMEOUT = 45

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

_PRINT_FIELDS = f"%(id)j{_SEP}%(title)j{_SEP}%(duration)j"
_BARE_URL_RE = re.compile(
    r"^(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)(?:/|$)",
    re.IGNORECASE,
)


class YtDlpError(RuntimeError):
    """Raised when yt-dlp is missing, times out, or cannot resolve a URL."""


# ---------------------------------------------------------------------------
# yt-dlp process helpers
# ---------------------------------------------------------------------------

def _ytdlp_cmd() -> list[str]:
    """Prefer a yt-dlp executable, fall back to the current interpreter."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def _run(args: list[str], timeout: int) -> str:
    cmd = _ytdlp_cmd() + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        raise YtDlpError(
            "yt-dlp not found or could not be started. "
            "Install it with: pip install yt-dlp"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise YtDlpError(f"yt-dlp timed out after {timeout}s.") from exc

    # Playlists with --ignore-errors can exit non-zero while still producing
    # useful output, so only treat it as a failure when there is no output.
    if proc.returncode != 0 and not proc.stdout.strip():
        raise YtDlpError(_friendly_error(proc.stderr))
    return proc.stdout


def _friendly_error(stderr: str) -> str:
    text = (stderr or "").strip()
    if "No module named yt_dlp" in text or "No module named 'yt_dlp'" in text:
        return "yt-dlp is not installed. Install it with: pip install yt-dlp"
    if not text:
        return "yt-dlp failed without an error message."
    last_line = text.splitlines()[-1].strip()
    return f"yt-dlp failed: {last_line[:300]}"


# ---------------------------------------------------------------------------
# Public search / playback API
# ---------------------------------------------------------------------------

def search_youtube(query: str, max_results: int = 8) -> list[dict]:
    """Search by text and return a list of tracks."""
    if max_results < 1:
        raise ValueError("max_results must be at least 1")

    raw = _run(
        [
            "--flat-playlist",
            "--no-warnings",
            "--quiet",
            "--ignore-errors",
            "--print",
            _PRINT_FIELDS,
            f"ytsearch{max_results}:{query}",
        ],
        timeout=_SEARCH_TIMEOUT,
    )
    return _parse(raw)


def load_playlist(url: str, limit: int = 500) -> list[dict]:
    """Load tracks from a playlist without downloading anything."""
    args = [
        "--flat-playlist",
        "--no-warnings",
        "--quiet",
        "--ignore-errors",
        "--yes-playlist",
    ]
    if limit > 0:
        args += ["--playlist-items", f"1-{limit}"]
    args += ["--print", _PRINT_FIELDS, url]
    return _parse(_run(args, timeout=_PLAYLIST_TIMEOUT))


def load_video(url: str) -> dict:
    """Read metadata for a single video URL, without downloading it."""
    raw = _run(
        [
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--print",
            _PRINT_FIELDS,
            url,
        ],
        timeout=_STREAM_TIMEOUT,
    )
    tracks = _parse(raw)
    if tracks:
        return tracks[0]

    vid = video_id(url)
    if not vid:
        raise YtDlpError("Could not read video metadata from that URL.")
    return _track(vid, "YouTube video", None)


def get_stream_url(yt_url: str) -> str:
    """Resolve the direct audio-only stream URL."""
    raw = _run(
        [
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "-f",
            "bestaudio[acodec=opus]/bestaudio/best",
            "--get-url",
            yt_url,
        ],
        timeout=_STREAM_TIMEOUT,
    )
    urls = [line.strip() for line in raw.splitlines() if line.strip()]
    if not urls:
        raise YtDlpError("No playable audio stream found.")
    return urls[0]


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

def is_youtube_url(text: str) -> bool:
    """Return True for http(s) YouTube URLs, with or without a scheme."""
    parsed = _parse_url(text)
    return parsed is not None and (parsed.hostname or "").lower() in _YOUTUBE_HOSTS


def is_playlist(url: str) -> bool:
    """Return True when the URL points at a playlist or contains a list id."""
    parsed = _parse_url(url)
    if parsed is None or (parsed.hostname or "").lower() not in _YOUTUBE_HOSTS:
        return False
    if parse_qs(parsed.query).get("list"):
        return True
    return parsed.path.rstrip("/") == "/playlist"


def is_video(url: str) -> bool:
    """Return True when the URL identifies a single YouTube video."""
    return is_youtube_url(url) and video_id(url) is not None


def video_id(url: str) -> str | None:
    """Extract a video id from watch, youtu.be, shorts, live and embed URLs."""
    parsed = _parse_url(url)
    if parsed is None:
        return None

    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        return parsed.path.strip("/").split("/")[0] or None
    if parsed.path.rstrip("/") == "/watch":
        values = parse_qs(parsed.query).get("v")
        return values[0] if values else None

    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
        return parts[1] or None
    return None


def _parse_url(text: str):
    value = (text or "").strip()
    if not value:
        return None
    if "://" not in value and _BARE_URL_RE.match(value):
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse(raw: str) -> list[dict]:
    results: list[dict] = []
    for line in raw.splitlines():
        parts = line.split(_SEP)
        if len(parts) != 3:
            continue
        try:
            vid_id, title, duration = (json.loads(part) for part in parts)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if not isinstance(vid_id, str) or not vid_id or vid_id == "NA":
            continue
        results.append(_track(vid_id, title, duration))
    return results


def _track(vid: str, title, duration) -> dict:
    return {
        "title": _clean(title),
        "yt_url": f"https://www.youtube.com/watch?v={vid}",
        "duration": _fmt(duration),
    }


def _clean(text) -> str:
    """Strip control characters so remote titles cannot break the terminal."""
    printable = "".join(
        char if char.isprintable() else " " for char in str(text or "")
    )
    return " ".join(printable.split()) or "?"


def _fmt(secs) -> str:
    if secs is None or secs == "":
        return "?"
    try:
        total = int(float(secs))
    except (TypeError, ValueError):
        return "?"
    if total <= 0:
        return "?"
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
