"""controls.py - Background keyboard listener for playback controls (Windows/Linux)."""

import sys
import threading

# Actions
NEXT            = "next"
PREV            = "prev"
QUIT            = "quit"
TOGGLE_AUTOPLAY = "toggle_autoplay"
VOL_UP          = "vol_up"
VOL_DOWN        = "vol_down"

_action: list[str] = []
_lock = threading.Lock()


def get_action() -> str | None:
    with _lock:
        return _action.pop(0) if _action else None


def _push(action: str) -> None:
    with _lock:
        _action.clear()
        _action.append(action)


def _listen_windows() -> None:
    import msvcrt
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b'\xe0', b'\x00'):    # special key (arrow)
                ch2 = msvcrt.getch()
                if   ch2 == b'M': _push(NEXT)      # →
                elif ch2 == b'K': _push(PREV)      # ←
                elif ch2 == b'H': _push(VOL_UP)    # ↑
                elif ch2 == b'P': _push(VOL_DOWN)  # ↓
            elif ch == b'n': _push(NEXT)
            elif ch == b'p': _push(PREV)
            elif ch == b'+': _push(VOL_UP)
            elif ch == b'-': _push(VOL_DOWN)
            elif ch == b'a': _push(TOGGLE_AUTOPLAY)
            elif ch in (b'q', b'\x03'): _push(QUIT)


def _listen_unix() -> None:
    import tty, termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                ch2 = sys.stdin.read(2)
                if   ch2 == '[C': _push(NEXT)      # →
                elif ch2 == '[D': _push(PREV)      # ←
                elif ch2 == '[A': _push(VOL_UP)    # ↑
                elif ch2 == '[B': _push(VOL_DOWN)  # ↓
            elif ch == 'n': _push(NEXT)
            elif ch == 'p': _push(PREV)
            elif ch == '+': _push(VOL_UP)
            elif ch == '-': _push(VOL_DOWN)
            elif ch == 'a': _push(TOGGLE_AUTOPLAY)
            elif ch in ('q', '\x03'): _push(QUIT)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def start_listener() -> None:
    """Start background keyboard listener thread."""
    fn = _listen_windows if sys.platform == "win32" else _listen_unix
    t = threading.Thread(target=fn, daemon=True)
    t.start()
