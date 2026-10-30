#!/usr/bin/env bash
# Download the standalone yt-dlp binary embedded into release builds.
# Usage: ./scripts/fetch-yt-dlp.sh [linux|linux-arm64|windows|macos|all]
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p vendor
target="${1:-all}"

download() {
    local name="$1"
    local url="https://github.com/yt-dlp/yt-dlp/releases/latest/download/$name"
    echo "Fetching $name"
    curl -fL --retry 3 --progress-bar -o "vendor/$name" "$url"
    chmod +x "vendor/$name" 2>/dev/null || true
}

case "$target" in
    linux) download yt-dlp_linux ;;
    linux-arm64) download yt-dlp_linux_aarch64 ;;
    windows) download yt-dlp.exe ;;
    macos) download yt-dlp_macos ;;
    all)
        download yt-dlp_linux
        download yt-dlp_linux_aarch64
        download yt-dlp.exe
        download yt-dlp_macos
        ;;
    *)
        echo "usage: $0 [linux|linux-arm64|windows|macos|all]" >&2
        exit 2
        ;;
esac

if [[ "$target" == "all" || "$target" == "linux" ]]; then
    version="$(vendor/yt-dlp_linux --version 2>/dev/null || true)"
    if [[ -n "$version" ]]; then
        printf '%s\n' "$version" > vendor/yt-dlp.version
    fi
fi
