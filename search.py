"""search.py - Busca YouTube e carrega playlists via yt-dlp."""

import json
import re
import subprocess
import sys

_PLAYLIST_RE = re.compile(
    r"(youtube\.com/(playlist|watch)\?.*list=|youtu\.be/.+\?.*list=)", re.I
)


def is_playlist(url: str) -> bool:
    return bool(_PLAYLIST_RE.search(url))


def load_playlist(url: str) -> list[dict]:
    """Carrega todos os tracks de uma playlist sem baixar nada."""
    print("📋 Carregando playlist...", end="\r", flush=True)
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--no-warnings", "--quiet",
        "--print", "%(id)j\t%(title)j\t%(duration)j",
        url,
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        sys.exit("❌ yt-dlp não encontrado: pip install yt-dlp")
    except subprocess.CalledProcessError:
        return []

    results = _parse(raw)
    print(f"📋 {len(results)} músicas carregadas.          ")
    return results


def search_youtube(query: str, max_results: int = 8) -> list[dict]:
    """Busca por texto, retorna lista de tracks."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--no-warnings", "--quiet",
        "--print", "%(id)j\t%(title)j\t%(duration)j",
        f"ytsearch{max_results}:{query}",
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        sys.exit("❌ yt-dlp não encontrado: pip install yt-dlp")
    except subprocess.CalledProcessError:
        return []
    return _parse(raw)


def get_stream_url(yt_url: str) -> str:
    """Resolve URL direta do stream opus/webm."""
    cmd = [
        "yt-dlp",
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
        parts = line.split("\t")
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
