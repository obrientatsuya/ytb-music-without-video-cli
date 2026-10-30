"""controls.py - Background keyboard listener for playback controls."""

from __future__ import annotations

import atexit
import os
import select
import sys
import threading
import time
from collections import deque

# Actions
NEXT = "next"
PREV = "prev"
QUIT = "quit"
TOGGLE_AUTOPLAY = "toggle_autoplay"
VOL_UP = "vol_up"
VOL_DOWN = "vol_down"
PAUSE = "pause"
SEEK_FWD = "seek_fwd"
SEEK_BACK = "seek_back"

_ACTIONS: deque[str] = deque(maxlen=32)
_ACTIONS_LOCK = threading.Lock()

_KEY_ACTIONS = {
    b"n": NEXT,
    b"N": NEXT,
    b"p": PREV,
    b"P": PREV,
    b"+": VOL_UP,
    b"=": VOL_UP,
    b"-": VOL_DOWN,
    b"_": VOL_DOWN,
    b"a": TOGGLE_AUTOPLAY,
    b"A": TOGGLE_AUTOPLAY,
    b" ": PAUSE,
    b".": SEEK_FWD,
    b">": SEEK_FWD,
    b",": SEEK_BACK,
    b"<": SEEK_BACK,
    b"q": QUIT,
    b"Q": QUIT,
    b"\x03": QUIT,  # Ctrl+C, in case ISIG is disabled
}

_ARROW_ACTIONS = {
    b"A": VOL_UP,
    b"B": VOL_DOWN,
    b"C": NEXT,
    b"D": PREV,
}


def get_action() -> str | None:
    with _ACTIONS_LOCK:
        return _ACTIONS.popleft() if _ACTIONS else None


def _push(action: str) -> None:
    with _ACTIONS_LOCK:
        _ACTIONS.append(action)


class _KeyboardListener:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._fd: int | None = None
        self._saved_termios = None

    def start(self) -> bool:
        if not sys.stdin.isatty():
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop.clear()

        target = self._listen_windows if sys.platform == "win32" else self._listen_unix
        self._thread = threading.Thread(
            target=target, name="ytplay-controls", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if (
            thread
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
        self._restore()

    def _restore(self) -> None:
        with self._lock:
            fd = self._fd
            saved = self._saved_termios
            self._fd = None
            self._saved_termios = None
        if fd is None or saved is None:
            return
        try:
            import termios

            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except Exception:
            pass

    # --- Unix / macOS ---

    def _listen_unix(self) -> None:
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
        except Exception:
            return

        with self._lock:
            self._fd = fd
            self._saved_termios = saved
        atexit.register(self._restore)

        try:
            # cbreak keeps output post-processing enabled, so newlines still
            # behave normally while keys arrive without pressing Enter.
            tty.setcbreak(fd)
            while not self._stop.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    ch = os.read(fd, 1)
                except OSError:
                    break
                if not ch:
                    break
                if ch == b"\x1b":
                    self._read_escape(fd)
                else:
                    action = _KEY_ACTIONS.get(ch)
                    if action:
                        _push(action)
        finally:
            self._restore()

    def _read_escape(self, fd: int) -> None:
        """Read an arrow-key sequence after ESC (CSI or SS3)."""
        seq = b""
        while len(seq) < 2:
            try:
                ready, _, _ = select.select([fd], [], [], 0.02)
            except (OSError, ValueError):
                return
            if not ready:
                return
            try:
                seq += os.read(fd, 1)
            except OSError:
                return
        if len(seq) == 2 and seq[0:1] in (b"[", b"O"):
            action = _ARROW_ACTIONS.get(seq[1:2])
            if action:
                _push(action)

    # --- Windows ---

    def _listen_windows(self) -> None:
        try:
            import msvcrt
        except ImportError:
            return

        try:
            while not self._stop.is_set():
                if not msvcrt.kbhit():
                    time.sleep(0.03)
                    continue
                ch = msvcrt.getch()
                if ch in (b"\x00", b"\xe0"):
                    if not msvcrt.kbhit():
                        continue
                    ch2 = msvcrt.getch()
                    action = _ARROW_ACTIONS.get(ch2)
                    if action:
                        _push(action)
                    continue
                action = _KEY_ACTIONS.get(ch)
                if action:
                    _push(action)
        except Exception:
            return


_LISTENER = _KeyboardListener()


def start_listener() -> bool:
    """Start the background keyboard listener. Returns False if unavailable."""
    return _LISTENER.start()


def stop_listener() -> None:
    """Stop the listener and restore the terminal state."""
    _LISTENER.stop()
