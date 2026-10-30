"""playqueue.py - Track queue management with navigation and autoplay."""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Queue:
    tracks: list[dict] = field(default_factory=list)
    index: int = 0
    autoplay: bool = True

    # --- navigation ---

    def current(self) -> dict | None:
        if 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    def next(self) -> dict | None:
        if self.index + 1 < len(self.tracks):
            self.index += 1
            return self.current()
        return None

    def prev(self) -> dict | None:
        if self.index > 0:
            self.index -= 1
            return self.current()
        return None

    def has_next(self) -> bool:
        return self.index + 1 < len(self.tracks)

    # --- mutation ---

    def set_tracks(self, tracks: list[dict], start: int = 0) -> None:
        self.tracks = tracks
        self.index = start

    def toggle_autoplay(self) -> bool:
        self.autoplay = not self.autoplay
        return self.autoplay

    def shuffle(self, keep_current: bool = True) -> None:
        """Shuffle the queue, optionally leaving the current track first."""
        current = self.current()
        random.shuffle(self.tracks)
        if keep_current and current is not None:
            try:
                self.tracks.remove(current)
            except ValueError:
                pass
            else:
                self.tracks.insert(0, current)
        self.index = 0
