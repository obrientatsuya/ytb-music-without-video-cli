"""ui.py - Terminal result picker. Returns 0-based index."""

import sys


def pick_result(results: list[dict]) -> int | None:
    print()
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['title']}  ({r['duration']})")
    print()
    while True:
        raw = input(f"Choose [1-{len(results)}] or q to quit: ").strip().lower()
        if raw == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(results):
            return int(raw) - 1
        print("    Invalid choice, try again.")
