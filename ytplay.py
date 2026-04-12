#!/usr/bin/env python3
"""ytplay - YouTube audio streamer for the terminal. No downloads, no GPU."""

import sys
from search import search_youtube, load_playlist, is_playlist
from player import play_queue
from queue import Queue
from ui import pick_result


def main() -> None:
    arg = " ".join(sys.argv[1:]).strip() or input("Search or paste link: ").strip()
    if not arg:
        sys.exit("❌ No input provided.")

    # --- Playlist ---
    if is_playlist(arg):
        print(f"📋 Playlist detected.")
        tracks = load_playlist(arg)
        if not tracks:
            sys.exit("❌ Playlist is empty or invalid.")
        print(f"▶  Playing playlist with {len(tracks)} tracks")
        q = Queue(autoplay=True)
        q.set_tracks(tracks, start=0)
        play_queue(q)
        return

    # --- Text search ---
    print(f"🔍 {arg}")
    results = search_youtube(arg)
    if not results:
        sys.exit("❌ No results found.")

    chosen_idx = pick_result(results)
    if chosen_idx is None:
        sys.exit(0)

    q = Queue(autoplay=True)
    q.set_tracks(results, start=chosen_idx)
    play_queue(q)


if __name__ == "__main__":
    main()
