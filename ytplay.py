#!/usr/bin/env python3
"""ytplay - Stream de áudio do YouTube no terminal. Sem download, sem GPU."""

import sys
from search import search_youtube, load_playlist, is_playlist
from player import play_queue
from queue import Queue
from ui import pick_result


def main() -> None:
    arg = " ".join(sys.argv[1:]).strip() or input("Buscar ou colar link: ").strip()
    if not arg:
        sys.exit("❌ Nenhuma entrada informada.")

    # --- Playlist ---
    if is_playlist(arg):
        tracks = load_playlist(arg)
        if not tracks:
            sys.exit("❌ Playlist vazia ou inválida.")
        print(f"▶  Tocando playlist com {len(tracks)} músicas")
        q = Queue(autoplay=True)
        q.set_tracks(tracks, start=0)
        play_queue(q)
        return

    # --- Busca por texto ---
    print(f"🔍 {arg}")
    results = search_youtube(arg)
    if not results:
        sys.exit("❌ Nenhum resultado encontrado.")

    chosen_idx = pick_result(results)
    if chosen_idx is None:
        sys.exit(0)

    q = Queue(autoplay=True)
    q.set_tracks(results, start=chosen_idx)
    play_queue(q)


if __name__ == "__main__":
    main()
