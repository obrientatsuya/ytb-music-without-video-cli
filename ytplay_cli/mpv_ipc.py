"""mpv_ipc.py - Real-time mpv control via IPC (Unix socket / Windows pipe)."""

from __future__ import annotations

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

    def __init__(self, path: str, timeout: float = 3.0):
        self.path = path
        self._lock = threading.Lock()
        self._handle = None
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._connect(timeout)
        if self._handle is not None:
            self._reader = threading.Thread(
                target=self._drain_responses, name="ytplay-mpv-ipc", daemon=True
            )
            self._reader.start()

    @property
    def connected(self) -> bool:
        return self._handle is not None

    def _connect(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if sys.platform == "win32":
                    self._handle = self._open_pipe()
                else:
                    import socket

                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    try:
                        sock.connect(self.path)
                    except Exception:
                        sock.close()
                        raise
                    sock.settimeout(None)
                    self._handle = sock
                return
            except Exception:
                time.sleep(0.05)

    def _open_pipe(self):
        import ctypes
        import ctypes.wintypes as wt

        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.restype = wt.HANDLE
        create_file.argtypes = [
            wt.LPCWSTR,
            wt.DWORD,
            wt.DWORD,
            wt.LPVOID,
            wt.DWORD,
            wt.DWORD,
            wt.HANDLE,
        ]
        handle = create_file(
            self.path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if not handle or handle == wt.HANDLE(-1).value:
            raise OSError("Pipe not available")
        return handle

    def _drain_responses(self) -> None:
        """Discard mpv replies so its write buffer never fills up."""
        while not self._stop.is_set():
            try:
                if sys.platform == "win32":
                    if not self._read_pipe():
                        break
                else:
                    data = self._handle.recv(4096)
                    if not data:
                        break
            except Exception:
                break

    def _read_pipe(self) -> bool:
        import ctypes
        import ctypes.wintypes as wt

        read_file = ctypes.windll.kernel32.ReadFile
        read_file.restype = wt.BOOL
        read_file.argtypes = [
            wt.HANDLE,
            wt.LPVOID,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
            wt.LPVOID,
        ]

        buf = ctypes.create_string_buffer(4096)
        read = wt.DWORD(0)
        ok = read_file(
            self._handle, buf, len(buf), ctypes.byref(read), None
        )
        if not ok:
            return False
        return read.value > 0

    def send(self, *args) -> bool:
        if self._handle is None:
            return False
        data = (json.dumps({"command": list(args)}) + "\n").encode()
        with self._lock:
            try:
                if sys.platform == "win32":
                    import ctypes
                    import ctypes.wintypes as wt

                    write_file = ctypes.windll.kernel32.WriteFile
                    write_file.restype = wt.BOOL
                    write_file.argtypes = [
                        wt.HANDLE,
                        wt.LPCVOID,
                        wt.DWORD,
                        ctypes.POINTER(wt.DWORD),
                        wt.LPVOID,
                    ]

                    buf = ctypes.create_string_buffer(data, len(data))
                    written = wt.DWORD(0)
                    ok = write_file(
                        self._handle,
                        buf,
                        len(data),
                        ctypes.byref(written),
                        None,
                    )
                    return bool(ok)
                self._handle.sendall(data)
                return True
            except Exception:
                return False

    def set_volume(self, vol: int) -> bool:
        """Set volume (0-130)."""
        return self.send("set_property", "volume", max(0, min(130, vol)))

    def toggle_pause(self) -> bool:
        return self.send("cycle", "pause")

    def seek(self, seconds: float) -> bool:
        return self.send("seek", seconds, "relative")

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is not None:
            try:
                if sys.platform == "win32":
                    import ctypes
                    import ctypes.wintypes as wt

                    close_handle = ctypes.windll.kernel32.CloseHandle
                    close_handle.restype = wt.BOOL
                    close_handle.argtypes = [wt.HANDLE]
                    close_handle(handle)
                else:
                    handle.close()
            except Exception:
                pass
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=0.5)
        if sys.platform != "win32":
            try:
                os.unlink(self.path)
            except OSError:
                pass
