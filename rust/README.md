# ytplay (Rust)

Single-binary YouTube audio player. This directory contains the Rust rewrite.

## Design

- **yt-dlp** handles YouTube search, playlist metadata and stream extraction.
  A standalone binary is embedded at build time via `include_bytes!`.
- **No JS runtime at playback time.** The extractor uses the
  `android_music` / `ios_music` / `mediaconnect` clients, which return playable
  URLs without the JavaScript `n` challenge.
- **rodio + Symphonia** decode and play AAC (`m4a`) in-process.
- **HTTP Range source** streams the media and implements `Read + Seek`, so
  pause/seek work without downloading the full track.
- If streaming fails, a temporary AAC file is downloaded and deleted after
  playback.

## Build

```bash
# Linux
./scripts/fetch-yt-dlp.sh linux
cargo build --release

# Windows PowerShell
.\scripts\fetch-yt-dlp.ps1
cargo build --release
```

The embedded file is read by `build.rs` from `vendor/`. If `vendor/` is
missing, the build still succeeds and the runtime falls back to a `yt-dlp`
found in `PATH` (not a single-file build).

Output:

```text
target/release/ytplay       # Linux
target\release\ytplay.exe   # Windows
```

## Tests

```bash
cargo test --release
```

The test suite covers URL detection, JSON parsing, queue/shuffle behavior,
stream metadata parsing and HTTP Range read/seek against a local server.

## Runtime behavior

- Linux double-click: if stdin/stdout are not TTYs and there is no CLI
  argument, the binary re-executes itself in the first available terminal
  emulator (`$TERMINAL`, GNOME Terminal, Konsole, xfce4-terminal, kitty, foot,
  wezterm, xterm, etc.).
- Windows: built as a console executable, so double-click opens a console.
- Raw terminal mode is restored on normal exit, errors and panics.
- Embedded `yt-dlp` is extracted once to the user cache directory and reused.

## Packaging

The binary is the artifact. GitHub Actions downloads the target-specific
`yt-dlp` before building, so each artifact is self-contained:

```text
ytplay-linux-x86_64
ytplay-windows-x86_64.exe
```

Linux still needs the normal desktop ALSA library at runtime. The Windows build
is a console executable, so double-clicking opens a console window.

## Performance

Measured on this machine with a 5-minute AAC track and a paced ALSA loopback
device:

| Stage | RSS | CPU |
|---|---:|---:|
| Rust process during playback | ~6.5 MB | ~0.4% average |
| Peak while `yt-dlp` resolves | ~94 MB total | one-core burst for ~2–5 s |
| Search peak | ~82 MB total | one-core burst for ~2 s |

The `yt-dlp` child exits after resolving; playback stays in single-digit MB.
