"""mpv_ipc.py - Real-time mpv volume control via IPC (Unix socket / Windows named pipe)."""

import json
import os
import sys
import tempfile
import threading
import time


def get_ipc_path() -> str:
    """Return a unique IPC path for this session."""
    if sys.platform == "win32":
        return rf"\\.\pipe\ytplay-{os.getpid()}"
    return os.path.join(tempfile.gettempdir(), f"ytplay-{os.getpid()}.sock")


def ipc_flag(path: str) -> str:
    """Return the correct mpv --input-ipc-server flag."""
    return f"--input-ipc-server={path}"


class MpvIPC:
    """Send JSON commands to mpv via named pipe (Windows) or Unix socket."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._handle = None
        self._connect()

    def _connect(self, retries: int = 30) -> None:
        for _ in range(retries):
            try:
                if sys.platform == "win32":
                    self._handle = self._open_pipe()
                else:
                    import socket
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self.path)
                    self._handle = s
                return
            except Exception:
                time.sleep(0.1)

    def _open_pipe(self):
        import ctypes, ctypes.wintypes
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        handle = ctypes.windll.kernel32.CreateFileW(
            self.path, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if handle == ctypes.wintypes.HANDLE(-1).value:
            raise OSError("Pipe not available")
        return handle

    def send(self, *args) -> None:
        if self._handle is None:
            return
        data = (json.dumps({"command": list(args)}) + "\n").encode()
        with self._lock:
            try:
                if sys.platform == "win32":
                    import ctypes
                    written = ctypes.c_ulong(0)
                    ctypes.windll.kernel32.WriteFile(
                        self._handle, data, len(data),
                        ctypes.byref(written), None
                    )
                else:
                    self._handle.sendall(data)
            except Exception:
                pass

    def set_volume(self, vol: int) -> None:
        """Set volume (0–130)."""
        self.send("set_property", "volume", max(0, min(130, vol)))

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._handle)
            else:
                self._handle.close()
        except Exception:
            pass
        self._handle = None
