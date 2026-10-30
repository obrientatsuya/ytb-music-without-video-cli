"""player.py - Audio playback with queue, navigation and live mpv controls."""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from .controls import (
    NEXT,
    PAUSE,
    PREV,
    QUIT,
    SEEK_BACK,
    SEEK_FWD,
    TOGGLE_AUTOPLAY,
    VOL_DOWN,
    VOL_UP,
    get_action,
    start_listener,
    stop_listener,
)
from .mpv_ipc import MpvIPC, get_ipc_path, ipc_flag
from .search import get_stream_url

VOL_STEP = 5
VOL_MIN = 0
VOL_MAX = 130
SEEK_STEP = 5

_WINDOWS_MPV_PATHS = [
    r"C:\Program Files\MPV Player\mpv.exe",
    r"C:\Program Files\mpv\mpv.exe",
    r"C:\mpv\mpv.exe",
]

# Global reference so atexit / signal handlers can kill mpv
_current_proc: subprocess.Popen | None = None
_windows_ctrl_handler = None


class BackendNotFound(RuntimeError):
    """Raised when no supported audio player is installed."""


@dataclass(frozen=True)
class Backend:
    name: str
    exe: str

    @property
    def supports_ipc(self) -> bool:
        return self.name == "mpv"


def _kill_current() -> None:
    """Kill the active player process, called on exit or crash."""
    global _current_proc
    proc = _current_proc
    _current_proc = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
    except Exception:
        pass


atexit.register(_kill_current)


def _signal_exit(signum, frame) -> None:
    _kill_current()
    raise SystemExit(0)


def _console_ctrl(event) -> bool:
    _kill_current()
    return False  # let Windows proceed with default handling


def _install_signal_handlers() -> None:
    """Install cleanup handlers without touching process state at import time."""
    if sys.platform != "win32":
        for sig in (signal.SIGHUP, signal.SIGTERM):
            try:
                signal.signal(sig, _signal_exit)
            except (ValueError, OSError):
                pass
        return

    global _windows_ctrl_handler
    if _windows_ctrl_handler is not None:
        return
    try:
        import ctypes
        import ctypes.wintypes

        _windows_ctrl_handler = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL, ctypes.wintypes.DWORD
        )(_console_ctrl)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_windows_ctrl_handler, True)
    except Exception:
        _windows_ctrl_handler = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def play_queue(queue, volume: int = 100) -> None:
    """Play the entire queue respecting navigation and autoplay."""
    backend = _detect_backend()
    state = {
        "volume": max(VOL_MIN, min(VOL_MAX, int(volume))),
        "paused": False,
        "warned": False,
    }

    _install_signal_handlers()
    controls = start_listener()
    if not controls:
        print("ℹ Keyboard controls disabled (stdin is not a TTY). Use Ctrl+C to stop.")
    if not backend.supports_ipc:
        print(
            "ℹ mpv not found; using ffplay (playback only - live volume, "
            "pause and seek need mpv)."
        )

    try:
        while True:
            track = queue.current()
            if not track:
                break

            _print_status(queue, state["volume"])
            action = _play_track(track, backend, queue, state)

            if action == QUIT:
                print("\n⏹  Stopped.")
                break
            if action == NEXT:
                if not queue.next():
                    print("\n✅ End of queue.")
                    break
            elif action == PREV:
                queue.prev()
            else:
                if queue.autoplay and queue.has_next():
                    queue.next()
                else:
                    print("\n✅ End of queue.")
                    break
    except KeyboardInterrupt:
        print("\n⏹  Stopped.")
    finally:
        stop_listener()
        _kill_current()


# ---------------------------------------------------------------------------
# Single track playback
# ---------------------------------------------------------------------------

def _play_track(track: dict, backend: Backend, queue, state: dict) -> str | None:
    global _current_proc

    stream_url = _resolve(track["yt_url"])
    if not stream_url:
        return NEXT

    state["paused"] = False
    ipc = None
    err_file = tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="replace"
    )

    if backend.supports_ipc:
        cmd = _build_mpv_cmd(
            backend.exe, stream_url, get_ipc_path(), state["volume"]
        )
    else:
        cmd = _build_ffplay_cmd(backend.exe, stream_url, state["volume"])

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_file,
        )
    except OSError as exc:
        err_file.close()
        print(f"❌ Could not start {backend.name}: {exc}")
        return NEXT

    _current_proc = proc

    if backend.supports_ipc:
        ipc = MpvIPC(get_ipc_path())
        if not ipc.connected:
            _warn_live_controls(backend, state)

    try:
        while proc.poll() is None:
            action = get_action()
            if action is None:
                time.sleep(0.05)
                continue

            if action == TOGGLE_AUTOPLAY:
                queue.autoplay = not queue.autoplay
                label = "ON" if queue.autoplay else "OFF"
                print(f"\r🔁 Autoplay {label}          ", flush=True)

            elif action == PAUSE:
                if _ipc_ready(ipc):
                    state["paused"] = not state["paused"]
                    ipc.toggle_pause()
                    label = "⏸  Paused " if state["paused"] else "▶  Playing"
                    print(f"\r{label}          ", flush=True)
                else:
                    _warn_live_controls(backend, state)

            elif action in (SEEK_FWD, SEEK_BACK):
                if _ipc_ready(ipc):
                    seconds = SEEK_STEP if action == SEEK_FWD else -SEEK_STEP
                    ipc.seek(seconds)
                    arrow = "⏩" if seconds > 0 else "⏪"
                    print(f"\r{arrow} {seconds:+d}s          ", flush=True)
                else:
                    _warn_live_controls(backend, state)

            elif action in (VOL_UP, VOL_DOWN):
                if _ipc_ready(ipc):
                    delta = VOL_STEP if action == VOL_UP else -VOL_STEP
                    state["volume"] = max(
                        VOL_MIN, min(VOL_MAX, state["volume"] + delta)
                    )
                    ipc.set_volume(state["volume"])
                    icon = "🔊" if delta > 0 else "🔉"
                    print(
                        f"\r{icon} Volume: {state['volume']}%    ", flush=True
                    )
                else:
                    _warn_live_controls(backend, state)

            elif action in (NEXT, PREV, QUIT):
                _kill_current()
                return action

        rc = proc.wait()
        if rc != 0:
            detail = _read_error(err_file)
            suffix = f" {detail}" if detail else ""
            print(f"⚠ Playback failed (exit {rc}).{suffix}")
            return NEXT
        return None

    except KeyboardInterrupt:
        _kill_current()
        return QUIT

    finally:
        if _current_proc is proc:
            if proc.poll() is None:
                _kill_current()
            else:
                _current_proc = None
        if ipc is not None:
            ipc.close()
        err_file.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ipc_ready(ipc: MpvIPC | None) -> bool:
    return ipc is not None and ipc.connected


def _warn_live_controls(backend: Backend, state: dict) -> None:
    if state["warned"]:
        return
    if backend.supports_ipc:
        message = "mpv IPC unavailable; live controls disabled."
    else:
        message = "Live volume, pause and seek require mpv."
    print(f"\rℹ {message}          ", flush=True)
    state["warned"] = True


def _resolve(yt_url: str) -> str | None:
    print("⏳ Resolving stream...", end="\r", flush=True)
    try:
        return get_stream_url(yt_url)
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        print(f"❌ Could not resolve stream: {message[:300]}")
        return None


def _print_status(queue, volume: int) -> None:
    track = queue.current()
    idx = queue.index + 1
    total = len(queue.tracks)
    autoplay = "ON" if queue.autoplay else "OFF"
    print(f"\n🎵 [{idx}/{total}] {track['title']}  ({track['duration']})")
    print(
        f"   ↑/+ volume ({volume}%)   ↓/- volume   space pause   ,/. seek {SEEK_STEP}s"
    )
    print(
        f"   ←/p previous   →/n next   a autoplay({autoplay})   q quit\n"
    )


def _build_mpv_cmd(exe: str, url: str, ipc_path: str, volume: int) -> list[str]:
    return [
        exe,
        "--no-video",
        "--vo=null",
        "--hwdec=no",
        "--audio-display=no",
        "--cache=yes",
        "--demuxer-max-bytes=8MiB",
        "--demuxer-readahead-secs=10",
        "--network-timeout=15",
        "--input-terminal=no",
        "--really-quiet",
        f"--volume={volume}",
        ipc_flag(ipc_path),
        "--",
        url,
    ]


def _build_ffplay_cmd(exe: str, url: str, volume: int) -> list[str]:
    return [
        exe,
        "-nodisp",
        "-autoexit",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-volume",
        str(max(0, min(100, volume))),
        "-i",
        url,
    ]


def _read_error(stream) -> str:
    try:
        stream.seek(0)
        text = stream.read().strip()
    except Exception:
        return ""
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[:300]
    return ""


def _detect_backend() -> Backend:
    for name in ("mpv", "ffplay"):
        exe = shutil.which(name)
        if exe:
            return Backend(name, exe)
    for path in _WINDOWS_MPV_PATHS:
        if os.path.isfile(path):
            return Backend("mpv", path)
    raise BackendNotFound(
        "No audio backend found. Install mpv (recommended) or ffmpeg/ffplay."
    )
