# Download the standalone yt-dlp binary embedded into the Windows build.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path vendor | Out-Null

Write-Host "Fetching yt-dlp.exe"
Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile "vendor/yt-dlp.exe"

$info = Invoke-RestMethod -Uri "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
$info.tag_name | Set-Content "vendor/yt-dlp.version"
