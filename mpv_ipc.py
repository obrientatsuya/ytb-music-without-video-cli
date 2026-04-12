"""mpv_ipc.py - Real-time mpv control via IPC socket (JSON commands)."""

import json
import os
import socket
import sys
import tempfile
import threading
import time


class MpvIPC:
    """Send commands to mpv via IPC socket without restarting the process."""

    def __init__(self, socket_path: str):
        self.path = socket_path
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._connect()

    def _connect(self, retries: int = 20) -> None:
        for _ in range(retries):
            try:
                if sys.platform == "win32":
                    # Windows uses named pipe; connect via AF_INET is not applicable
                    # mpv on Windows supports --input-ipc-server=\\.\pipe\name
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect(("127.0.0.1", int(self.path)))
                else:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self.path)
                self._sock = s
                return
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                time.sleep(0.1)

    def send(self, *args) -> None:
        if not self._sock:
            return
        cmd = json.dumps({"command": list(args)}) + "\n"
        with self._lock:
            try:
                self._sock.sendall(cmd.encode())
            except OSError:
                pass

    def set_volume(self, vol: int) -> None:
        """Set volume (0–130)."""
        self.send("set_property", "volume", max(0, min(130, vol)))

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass


def get_ipc_path() -> str:
    """Return a unique IPC socket path for this session."""
    if sys.platform == "win32":
        return "ytplay-ipc"  # used as named pipe name
    return os.path.join(tempfile.gettempdir(), f"ytplay-{os.getpid()}.sock")


def ipc_flag(path: str) -> str:
    """Return the correct mpv flag for IPC based on platform."""
    if sys.platform == "win32":
        return f"--input-ipc-server=\\\\.\\pipe\\{path}"
    return f"--input-ipc-server={path}"
