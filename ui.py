"""ui.py - Terminal result picker. Returns 0-based index."""


def pick_result(results: list[dict]) -> int | None:
    """Show numbered list, ask user to pick one. Returns 0-based index or None."""
    print()
    for i, r in enumerate(results, 1):
        print(f"  [{i:>3}] {r['title']}  ({r['duration']})")
    print()
    while True:
        raw = input(f"Choose [1-{len(results)}] or q to quit: ").strip().lower()
        if raw == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(results):
            return int(raw) - 1
        print("    Invalid choice, try again.")


def show_playlist(tracks: list[dict]) -> int:
    """Show all playlist tracks and ask where to start. Returns 0-based index."""
    print()
    for i, r in enumerate(tracks, 1):
        print(f"  [{i:>3}] {r['title']}  ({r['duration']})")
    print()
    while True:
        raw = input(
            f"Start from [1-{len(tracks)}], press Enter for 1, or q to quit: "
        ).strip().lower()
        if raw == "q":
            return -1
        if raw == "":
            return 0
        if raw.isdigit() and 1 <= int(raw) <= len(tracks):
            return int(raw) - 1
        print("    Invalid choice, try again.")
