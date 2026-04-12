# ytplay 🎵

High-performance YouTube audio streamer for the terminal — no downloads, no video, no GPU.

---

## Installation — Windows

### 1. Python 3.11
Download from: https://www.python.org/downloads/release/python-3119/
- Choose **Windows installer (64-bit)**
- On the first screen, check **"Add python.exe to PATH"** 

### 2. yt-dlp
Open the terminal (cmd) and run:
```cmd
py -3.11 -m pip install yt-dlp
```

### 3. mpv
```cmd
winget install shinchiro.mpv
```
Or download from: https://mpv.io/installation/ → Windows builds  
Extract and place `mpv.exe` in `C:\Program Files\MPV Player\`

### 4. Add mpv to PATH (optional but recommended)
```cmd
setx PATH "%PATH%;C:\Program Files\MPV Player"
```
Close and reopen the terminal after this.

---

## Installation — Linux

```bash
# Ubuntu/Debian
sudo apt install python3 mpv

# Arch
sudo pacman -S python mpv

# yt-dlp (all distros)
pip install yt-dlp
```

---

## Installation — macOS

```bash
brew install python mpv yt-dlp
```

---

## Usage

```bash
# Windows
py -3.11 ytplay.py song name here

# Linux / macOS
python3 ytplay.py song name here

# No arguments (interactive prompt)
py -3.11 ytplay.py

# Full playlist
py -3.11 ytplay.py https://www.youtube.com/playlist?list=PLxxxx
```

## Playback Controls

| Key | Action |
|---|---|
| `↑` or `+` | Volume up (+5%) |
| `↓` or `-` | Volume down (-5%) |
| `→` or `n` | Next track |
| `←` or `p` | Previous track |
| `a` | Toggle autoplay |
| `q` or Ctrl+C | Stop |

---

## Audio Quality

| Format | Quality | Notes |
|---|---|---|
| opus (default) | ~128kbps | Perceptually equivalent to MP3 320k |
| aac | ~128kbps | Automatic fallback |

ytplay always selects the best available audio-only stream.  
Opus at 128kbps is YouTube's standard and delivers excellent quality.

---

## File Structure

```
ytplay.py     ← entry point
search.py     ← search and stream resolution via yt-dlp
player.py     ← playback, queue, volume
queue.py      ← queue state management
controls.py   ← background keyboard listener
mpv_ipc.py   ← real-time volume control via mpv IPC socket
ui.py         ← terminal result picker
```

---

## Troubleshooting

**`yt-dlp not found`**
```cmd
py -3.11 -m pip install yt-dlp
```

**`mpv not found`**  
Make sure `mpv.exe` is in `C:\Program Files\MPV Player\` or add it to PATH.

**`python not recognized`**  
Reinstall Python and check "Add to PATH", or use `py -3.11` instead of `python`.

**Volume not working**  
Volume control uses mpv's IPC socket. Make sure mpv is the active player backend.
