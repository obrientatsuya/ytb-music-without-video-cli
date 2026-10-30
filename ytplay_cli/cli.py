"""cli.py - Argument parsing and the main ytplay flow."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .player import BackendNotFound, play_queue
from .playqueue import Queue
from .search import (
    YtDlpError,
    is_playlist,
    is_video,
    is_youtube_url,
    load_playlist,
    load_video,
    search_youtube,
)
from .ui import pick_result, show_playlist


def _int_range(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return number

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytplay",
        description=(
            "Stream YouTube audio from the terminal. Search, pick a result, "
            "or paste a video/playlist URL. No downloads, no video."
        ),
        epilog=(
            "Examples:\n"
            '  ytplay "daft punk instant crush"\n'
            "  ytplay https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  ytplay --no-autoplay --volume 80 https://youtu.be/VIDEO_ID\n"
            "  ytplay --shuffle --playlist-limit 100 PLAYLIST_URL\n"
            "  ytplay --single 'https://www.youtube.com/watch?v=ID&list=LIST'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="search terms or a YouTube video/playlist URL",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=_int_range(1, 50),
        default=8,
        metavar="N",
        help="number of search results to show (default: 8)",
    )
    parser.add_argument(
        "--playlist-limit",
        type=_int_range(1, 5000),
        default=500,
        metavar="N",
        help="maximum playlist tracks to load (default: 500)",
    )
    parser.add_argument(
        "--no-autoplay",
        action="store_true",
        help="stop after the current track instead of advancing",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="shuffle the queue, keeping the chosen track first",
    )
    parser.add_argument(
        "--volume",
        type=_int_range(0, 130),
        default=100,
        metavar="0-130",
        help="initial volume (default: 100)",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="for a URL that contains a playlist, play only its video",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        text = " ".join(args.query).strip()
        if not text:
            text = input("Search or paste a YouTube URL: ").strip()
        if not text:
            print("❌ No input provided.", file=sys.stderr)
            return 1

        queue = Queue(autoplay=not args.no_autoplay)
        if is_youtube_url(text):
            _run_url(text, args, queue)
        else:
            _run_search(text, args, queue)

        play_queue(queue, volume=args.volume)
        return 0

    except (YtDlpError, BackendNotFound) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    except EOFError:
        print()
        return 1


def _run_url(text: str, args: argparse.Namespace, queue: Queue) -> None:
    if is_playlist(text) and not args.single:
        print("📋 Playlist detected.")
        tracks = load_playlist(text, limit=args.playlist_limit)
        if not tracks:
            raise YtDlpError("Playlist is empty or unavailable.")
        print(f"📋 {len(tracks)} tracks loaded.")

        start = show_playlist(tracks)
        if start < 0:
            raise SystemExit(0)

        queue.set_tracks(tracks, start)
        if args.shuffle:
            queue.shuffle()
        return

    if is_video(text):
        print("🎬 Video URL detected.")
        track = load_video(text)
        queue.set_tracks([track], 0)
        return

    raise YtDlpError(
        "Unsupported YouTube URL. Use a video, a playlist, or search terms."
    )


def _run_search(text: str, args: argparse.Namespace, queue: Queue) -> None:
    print(f"🔍 {text}")
    results = search_youtube(text, max_results=args.limit)
    if not results:
        raise YtDlpError("No results found.")

    chosen = pick_result(results)
    if chosen is None:
        raise SystemExit(0)

    queue.set_tracks(results, chosen)
    if args.shuffle:
        queue.shuffle()
