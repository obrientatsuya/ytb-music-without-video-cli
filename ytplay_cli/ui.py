"""ui.py - Terminal result picker and playlist viewer."""

from __future__ import annotations

import shutil

_PREVIEW_HEAD = 20
_PREVIEW_TAIL = 5


def _width() -> int:
    return max(40, shutil.get_terminal_size((100, 24)).columns)


def _shorten(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _print_tracks(tracks: list[dict]) -> None:
    width = _width()
    for i, track in enumerate(tracks, 1):
        prefix = f"  [{i:>3}] "
        suffix = f"  ({track['duration']})"
        available = max(1, width - len(prefix) - len(suffix))
        title = _shorten(track["title"], available)
        print(f"{prefix}{title}{suffix}")


def _print_preview(tracks: list[dict]) -> None:
    """Keep huge playlists readable: show the head and tail only."""
    if len(tracks) <= _PREVIEW_HEAD + _PREVIEW_TAIL + 1:
        _print_tracks(tracks)
        return

    _print_tracks(tracks[:_PREVIEW_HEAD])
    hidden = len(tracks) - _PREVIEW_HEAD - _PREVIEW_TAIL
    print(f"  ... {hidden} more tracks (type l to list all) ...")
    for offset, track in enumerate(
        tracks[-_PREVIEW_TAIL:], len(tracks) - _PREVIEW_TAIL + 1
    ):
        prefix = f"  [{offset:>3}] "
        suffix = f"  ({track['duration']})"
        available = max(1, _width() - len(prefix) - len(suffix))
        print(f"{prefix}{_shorten(track['title'], available)}{suffix}")


def _read_choice(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


def pick_result(results: list[dict]) -> int | None:
    """Show numbered list, ask user to pick one. Returns 0-based index or None."""
    print()
    _print_tracks(results)
    print()
    while True:
        raw = _read_choice(f"Choose [1-{len(results)}] or q to quit: ")
        if raw == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(results):
            return int(raw) - 1
        print("    Invalid choice, try again.")


def show_playlist(tracks: list[dict]) -> int:
    """Show playlist tracks and ask where to start. Returns 0-based index."""
    print()
    _print_preview(tracks)
    print()
    while True:
        raw = _read_choice(
            f"Start from [1-{len(tracks)}], Enter for 1, l to list all, "
            "or q to quit: "
        )
        if raw == "q":
            return -1
        if raw == "":
            return 0
        if raw == "l":
            print()
            _print_tracks(tracks)
            print()
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(tracks):
            return int(raw) - 1
        print("    Invalid choice, try again.")
