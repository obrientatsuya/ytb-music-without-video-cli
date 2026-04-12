"""player.py - Reproduz stream com fila, navegação, autoplay e controle de volume."""

import os
import subprocess
import shutil
import sys
import threading
import time

from search import get_stream_url
from controls import start_listener, get_action, NEXT, PREV, QUIT, TOGGLE_AUTOPLAY, VOL_UP, VOL_DOWN
from mpv_ipc import MpvIPC, get_ipc_path, ipc_flag

_WINDOWS_MPV_PATHS = [
    r"C:\Program Files\MPV Player\mpv.exe",
    r"C:\Program Files\mpv\mpv.exe",
    r"C:\mpv\mpv.exe",
]

VOL_STEP = 5   # % por tecla
VOL_MIN  = 0
VOL_MAX  = 130


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def play_queue(queue) -> None:
    _, exe = _detect_backend()
    start_listener()
    volume = [100]  # mutável via closure

    while True:
        track = queue.current()
        if not track:
            break

        _print_status(queue, volume[0])
        action = _play_track(track, exe, queue, volume)

        if action == QUIT:
            print("\n⏹  Parado.")
            break
        elif action == NEXT:
            if not queue.next():
                print("\n✅ Fim da fila.")
                break
        elif action == PREV:
            queue.prev()
        else:
            if queue.autoplay and queue.has_next():
                queue.next()
            else:
                print("\n✅ Fim da fila.")
                break


# ---------------------------------------------------------------------------
# Reprodução de uma faixa
# ---------------------------------------------------------------------------

def _play_track(track: dict, exe: str, queue, volume: list) -> str | None:
    stream_url = _resolve(track["yt_url"])
    if not stream_url:
        print("❌ Falha ao resolver stream, pulando...")
        return NEXT

    ipc_path = get_ipc_path()
    cmd = _build_cmd(exe, stream_url, ipc_path, volume[0])
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ipc = MpvIPC(ipc_path)

    try:
        while proc.poll() is None:
            action = get_action()

            if action == TOGGLE_AUTOPLAY:
                queue.autoplay = not queue.autoplay
                state = "ON" if queue.autoplay else "OFF"
                print(f"\r🔁 Autoplay {state}          ", flush=True)

            elif action == VOL_UP:
                volume[0] = min(VOL_MAX, volume[0] + VOL_STEP)
                ipc.set_volume(volume[0])
                print(f"\r🔊 Volume: {volume[0]}%    ", flush=True)

            elif action == VOL_DOWN:
                volume[0] = max(VOL_MIN, volume[0] - VOL_STEP)
                ipc.set_volume(volume[0])
                print(f"\r🔉 Volume: {volume[0]}%    ", flush=True)

            elif action in (NEXT, PREV, QUIT):
                proc.terminate()
                ipc.close()
                return action

            time.sleep(0.05)

    except KeyboardInterrupt:
        proc.terminate()
        ipc.close()
        return QUIT

    ipc.close()
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(yt_url: str) -> str | None:
    result: dict = {}
    def _r():
        try:
            result["url"] = get_stream_url(yt_url)
        except Exception:
            pass
    t = threading.Thread(target=_r, daemon=True)
    t.start()
    print("⏳ Resolvendo stream...", end="\r", flush=True)
    t.join()
    return result.get("url")


def _print_status(queue, volume: int) -> None:
    track = queue.current()
    idx   = queue.index + 1
    total = len(queue.tracks)
    ap    = "ON" if queue.autoplay else "OFF"
    print(f"\n🎵 [{idx}/{total}] {track['title']}  ({track['duration']})")
    print(f"   ↑/+ volume up  ↓/- volume down  (atual: {volume}%)")
    print(f"   ←/p anterior   →/n próxima   a autoplay({ap})   q sair\n")


def _build_cmd(exe: str, url: str, ipc_path: str, volume: int) -> list[str]:
    return [
        exe,
        "--no-video", "--vo=null", "--hwdec=no",
        "--audio-display=no", "--cache=yes",
        "--demuxer-max-bytes=5MiB",
        "--demuxer-readahead-secs=5",
        "--really-quiet",
        f"--volume={volume}",
        ipc_flag(ipc_path),
        url,
    ]


def _detect_backend() -> tuple[str, str]:
    for b in ("mpv", "ffplay", "aplay"):
        exe = shutil.which(b)
        if exe:
            return b, exe
    for p in _WINDOWS_MPV_PATHS:
        if os.path.isfile(p):
            return "mpv", p
    if shutil.which("powershell"):
        return "powershell", "powershell"
    sys.exit("❌ Instale mpv ou ffmpeg.")
