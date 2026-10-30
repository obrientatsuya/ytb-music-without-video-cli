//! ytplay - a single-binary YouTube audio player.
//!
//! The binary embeds a standalone yt-dlp at build time (when available),
//! resolves an AAC stream, and decodes/plays it in-process with rodio.
//! No Python, no mpv, no browser and no video decoding.

use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::{self, File};
use std::io::{self, IsTerminal, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::terminal;
use rodio::{Decoder, DeviceSinkBuilder, Player, Source};
use url::Url;

mod embedded {
    include!(concat!(env!("OUT_DIR"), "/embedded_ytdlp.rs"));
}

const SEEK_STEP: u64 = 5;
const VOL_MIN: i32 = 0;
const VOL_MAX: i32 = 130;
const VOL_STEP: i32 = 5;
const HTTP_BUFFER: usize = 32 * 1024;
const DEFAULT_UA: &str = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 \
    (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Debug)]
struct Cli {
    query: Vec<String>,
    limit: usize,
    playlist_limit: usize,
    no_autoplay: bool,
    shuffle: bool,
    volume: i32,
    single: bool,
    play_file: Option<PathBuf>,
}

fn main() {
    std::panic::set_hook(Box::new(|info| {
        let message = info.to_string();
        if message.contains("Broken pipe") {
            std::process::exit(0);
        }
        eprintln!("{message}");
    }));

    let args: Vec<OsString> = env::args_os().skip(1).collect();
    if args.iter().any(|arg| arg == "-h" || arg == "--help") {
        print_help();
        return;
    }
    if args.iter().any(|arg| arg == "-V" || arg == "--version") {
        println!("ytplay {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if try_reexec_in_terminal() {
        return;
    }

    let cli = match parse_args(args) {
        Ok(cli) => cli,
        Err(err) => {
            eprintln!("❌ {err:#}");
            std::process::exit(2);
        }
    };
    if let Err(err) = run(cli) {
        eprintln!("❌ {err:#}");
        if env::args_os().len() == 1 && io::stdin().is_terminal() {
            eprintln!("\nPress Enter to close...");
            let mut dummy = String::new();
            let _ = io::stdin().read_line(&mut dummy);
        }
        std::process::exit(1);
    }
}

fn print_help() {
    println!(
        r#"ytplay {} - stream YouTube audio in the terminal

Usage: ytplay [OPTIONS] [QUERY]...

Options:
  -n N   search results (1-50, default 8)
  -p N   playlist tracks (default 500)
  -N     stop after the current track
  -s     shuffle, keeping the chosen track first
  -v N   volume (0-130, default 100)
  -S     for a playlist URL, play only its video
  -f F   play a local audio file (debug)
  -h     this help
  -V     version

Examples:
  ytplay "yung buda"
  ytplay -N -v 80 https://youtu.be/VIDEO_ID
  ytplay -s -p 100 PLAYLIST_URL"#,
        env!("CARGO_PKG_VERSION")
    );
}

fn parse_args<I>(args: I) -> Result<Cli>
where
    I: IntoIterator<Item = OsString>,
{
    let mut cli = Cli {
        query: Vec::new(),
        limit: 8,
        playlist_limit: 500,
        no_autoplay: false,
        shuffle: false,
        volume: 100,
        single: false,
        play_file: None,
    };
    let mut args = args.into_iter();
    let mut options = true;

    while let Some(argument) = args.next() {
        let text = argument.to_string_lossy().into_owned();
        if options && text == "--" {
            options = false;
            continue;
        }
        if !options {
            cli.query.push(text);
            continue;
        }

        match text.as_str() {
            "-N" | "--no-autoplay" => cli.no_autoplay = true,
            "-s" | "--shuffle" => cli.shuffle = true,
            "-S" | "--single" => cli.single = true,
            "-n" | "--limit" => {
                let value = args
                    .next()
                    .with_context(|| format!("{text} needs a value"))?;
                cli.limit = parse_number(&value, 1, 50, "limit")? as usize;
            }
            "-p" | "--playlist-limit" => {
                let value = args
                    .next()
                    .with_context(|| format!("{text} needs a value"))?;
                cli.playlist_limit = parse_number(&value, 1, 5000, "playlist limit")? as usize;
            }
            "-v" | "--volume" => {
                let value = args
                    .next()
                    .with_context(|| format!("{text} needs a value"))?;
                cli.volume = parse_number(&value, 0, 130, "volume")? as i32;
            }
            "-f" | "--play-file" => {
                cli.play_file = Some(PathBuf::from(
                    args.next()
                        .with_context(|| format!("{text} needs a value"))?,
                ));
            }
            _ => {
                if let Some(value) = text.strip_prefix("--limit=") {
                    cli.limit = parse_text_number(value, 1, 50, "limit")? as usize;
                } else if let Some(value) = text.strip_prefix("--playlist-limit=") {
                    cli.playlist_limit =
                        parse_text_number(value, 1, 5000, "playlist limit")? as usize;
                } else if let Some(value) = text.strip_prefix("--volume=") {
                    cli.volume = parse_text_number(value, 0, 130, "volume")? as i32;
                } else if let Some(value) = text.strip_prefix("-n").filter(|v| !v.is_empty()) {
                    cli.limit = parse_text_number(value, 1, 50, "limit")? as usize;
                } else if let Some(value) = text.strip_prefix("-p").filter(|v| !v.is_empty()) {
                    cli.playlist_limit =
                        parse_text_number(value, 1, 5000, "playlist limit")? as usize;
                } else if let Some(value) = text.strip_prefix("-v").filter(|v| !v.is_empty()) {
                    cli.volume = parse_text_number(value, 0, 130, "volume")? as i32;
                } else if let Some(value) = text.strip_prefix("-f").filter(|v| !v.is_empty()) {
                    cli.play_file = Some(PathBuf::from(value));
                } else if text.starts_with('-') {
                    bail!("unknown option: {text}");
                } else {
                    cli.query.push(text);
                }
            }
        }
    }
    Ok(cli)
}

fn parse_number(value: &OsStr, min: u32, max: u32, name: &str) -> Result<u32> {
    let text = value.to_string_lossy();
    parse_text_number(&text, min, max, name)
}

fn parse_text_number(value: &str, min: u32, max: u32, name: &str) -> Result<u32> {
    let number: u32 = value
        .parse()
        .with_context(|| format!("{name} must be a number"))?;
    if !(min..=max).contains(&number) {
        bail!("{name} must be between {min} and {max}");
    }
    Ok(number)
}

fn run(cli: Cli) -> Result<()> {
    let mut ytdlp: Option<YtDlp> = None;
    let mut query = cli.query.join(" ").trim().to_owned();

    if query.is_empty() && cli.play_file.is_none() {
        print!("Search or paste a YouTube URL: ");
        io::stdout().flush()?;
        let mut line = String::new();
        if io::stdin().read_line(&mut line)? == 0 {
            bail!("No input provided.");
        }
        query = line.trim().to_owned();
    }
    if query.is_empty() && cli.play_file.is_none() {
        bail!("No input provided.");
    }

    let mut queue = Queue::new(!cli.no_autoplay);

    if let Some(path) = cli.play_file {
        let title = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("local audio")
            .to_owned();
        queue.set_tracks(
            vec![Track {
                title: clean_title(&title),
                source: TrackSource::Local(path),
                duration: None,
            }],
            0,
        );
    } else if let Some(url) = parse_youtube_url(&query) {
        let yt = ytdlp_ref(&mut ytdlp)?;
        if is_playlist(&url) && !cli.single {
            println!("📋 Playlist detected.");
            let tracks = yt.playlist(url.as_str(), cli.playlist_limit)?;
            if tracks.is_empty() {
                bail!("Playlist is empty or unavailable.");
            }
            println!("📋 {} tracks loaded.", tracks.len());
            let Some(start) = show_playlist(&tracks)? else {
                return Ok(());
            };
            queue.set_tracks(tracks, start);
        } else if is_video(&url) {
            println!("🎬 Video URL detected.");
            queue.set_tracks(
                vec![Track {
                    title: "YouTube video".to_owned(),
                    source: TrackSource::YouTube(url.to_string()),
                    duration: None,
                }],
                0,
            );
        } else {
            bail!("Unsupported YouTube URL. Use a video, a playlist, or search terms.");
        }
    } else {
        println!("🔍 {query}");
        let yt = ytdlp_ref(&mut ytdlp)?;
        let tracks = yt.search(&query, cli.limit)?;
        if tracks.is_empty() {
            bail!("No results found.");
        }
        let Some(chosen) = pick_result(&tracks)? else {
            return Ok(());
        };
        queue.set_tracks(tracks, chosen);
    }

    if cli.shuffle {
        queue.shuffle();
    }

    play_queue(&mut queue, &mut ytdlp, cli.volume)
}

fn ytdlp_ref(slot: &mut Option<YtDlp>) -> Result<&YtDlp> {
    if slot.is_none() {
        *slot = Some(YtDlp::discover()?);
    }
    Ok(slot.as_ref().expect("yt-dlp was just initialized"))
}

// ---------------------------------------------------------------------------
// URL detection
// ---------------------------------------------------------------------------

const YOUTUBE_HOSTS: &[&str] = &[
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
];

fn parse_youtube_url(text: &str) -> Option<Url> {
    let value = text.trim();
    if value.is_empty() {
        return None;
    }
    let candidate = if value.contains("://") {
        value.to_owned()
    } else if value.starts_with("youtube.com")
        || value.starts_with("www.youtube.com")
        || value.starts_with("m.youtube.com")
        || value.starts_with("music.youtube.com")
        || value.starts_with("youtu.be")
        || value.starts_with("www.youtu.be")
    {
        format!("https://{value}")
    } else {
        return None;
    };
    let url = Url::parse(&candidate).ok()?;
    let host = url.host_str()?.to_ascii_lowercase();
    YOUTUBE_HOSTS.contains(&host.as_str()).then_some(url)
}

fn is_playlist(url: &Url) -> bool {
    url.query_pairs().any(|(key, _)| key == "list")
        || url.path().trim_end_matches('/') == "/playlist"
}

fn video_id(url: &Url) -> Option<String> {
    let host = url.host_str()?.to_ascii_lowercase();
    if host == "youtu.be" || host == "www.youtu.be" {
        return url
            .path_segments()
            .and_then(|mut segments| segments.next())
            .filter(|id| !id.is_empty())
            .map(str::to_owned);
    }
    match url.path().trim_end_matches('/') {
        "/watch" => url
            .query_pairs()
            .find_map(|(key, value)| (key == "v" && !value.is_empty()).then(|| value.into_owned())),
        path if path.starts_with("/shorts/")
            || path.starts_with("/live/")
            || path.starts_with("/embed/") =>
        {
            path.split('/')
                .nth(2)
                .filter(|id| !id.is_empty())
                .map(str::to_owned)
        }
        _ => None,
    }
}

fn is_video(url: &Url) -> bool {
    video_id(url).is_some()
}

// ---------------------------------------------------------------------------
// yt-dlp
// ---------------------------------------------------------------------------

struct YtDlp {
    path: PathBuf,
}

struct DownloadedAudio {
    _directory: TempDirectory,
    path: PathBuf,
    len: u64,
    title: Option<String>,
    duration: Option<u64>,
}

struct TempDirectory {
    path: PathBuf,
}

impl TempDirectory {
    fn new() -> Result<Self> {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = env::temp_dir().join(format!("ytplay-{}-{nanos}", std::process::id()));
        fs::create_dir(&path).with_context(|| format!("could not create {}", path.display()))?;
        Ok(Self { path })
    }

    fn path(&self) -> &std::path::Path {
        &self.path
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

struct StreamInfo {
    url: String,
    len: Option<u64>,
    title: Option<String>,
    duration: Option<u64>,
}

impl YtDlp {
    fn discover() -> Result<Self> {
        let path = if let Some(bytes) = embedded::EMBEDDED_YTDLP {
            extract_embedded(
                bytes,
                "yt-dlp",
                embedded::EMBEDDED_VERSION,
                true,
                cfg!(windows),
            )?
        } else if let Some(path) = find_in_path(&exe_name("yt-dlp")) {
            path
        } else {
            bail!("yt-dlp not found. Build with an embedded yt-dlp or install yt-dlp in PATH.");
        };
        Ok(Self { path })
    }

    fn run<S: AsRef<OsStr>>(&self, args: &[S]) -> Result<String> {
        let mut command = Command::new(&self.path);
        command
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        hide_child_console(&mut command);

        let output = command
            .output()
            .with_context(|| format!("could not run {}", self.path.display()))?;
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        if !output.status.success() && stdout.trim().is_empty() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let last = stderr.lines().last().unwrap_or("unknown yt-dlp error");
            bail!("yt-dlp failed: {last}");
        }
        Ok(stdout)
    }

    fn search(&self, query: &str, limit: usize) -> Result<Vec<Track>> {
        let target = format!("ytsearch{limit}:{query}");
        let args = vec![
            "--flat-playlist".to_owned(),
            "--no-warnings".to_owned(),
            "--quiet".to_owned(),
            "--ignore-errors".to_owned(),
            "--no-js-runtimes".to_owned(),
            "--print".to_owned(),
            "%(id)j\t%(title)j\t%(duration)j".to_owned(),
            target,
        ];
        Ok(parse_track_lines(&self.run(&args)?))
    }

    fn playlist(&self, url: &str, limit: usize) -> Result<Vec<Track>> {
        let mut args = vec![
            "--flat-playlist".to_owned(),
            "--no-warnings".to_owned(),
            "--quiet".to_owned(),
            "--ignore-errors".to_owned(),
            "--no-js-runtimes".to_owned(),
            "--yes-playlist".to_owned(),
            "--print".to_owned(),
            "%(id)j\t%(title)j\t%(duration)j".to_owned(),
        ];
        if limit > 0 {
            args.push("--playlist-items".to_owned());
            args.push(format!("1-{limit}"));
        }
        args.push(url.to_owned());
        Ok(parse_track_lines(&self.run(&args)?))
    }

    fn stream(&self, url: &str) -> Result<StreamInfo> {
        let mut last_error = None;
        for client in ["android_music", "ios_music", "mediaconnect"] {
            let extractor = format!("youtube:player_client={client}");
            let args = vec![
                "--no-playlist".to_owned(),
                "--no-warnings".to_owned(),
                "--quiet".to_owned(),
                "--no-js-runtimes".to_owned(),
                "--extractor-args".to_owned(),
                extractor,
                "-f".to_owned(),
                "bestaudio[ext=m4a]".to_owned(),
                "--get-url".to_owned(),
                "--print".to_owned(),
                "%(title)j\t%(duration)j".to_owned(),
                url.to_owned(),
            ];
            match self.run(&args) {
                Ok(output) => {
                    if let Some(info) = parse_stream_output(&output) {
                        return Ok(info);
                    }
                }
                Err(error) => last_error = Some(error),
            }
        }
        if let Some(error) = last_error {
            return Err(error.context("all streaming clients failed"));
        }
        bail!("no playable audio stream available for this video")
    }

    fn download(&self, url: &str) -> Result<DownloadedAudio> {
        let directory = TempDirectory::new()?;
        let path = directory.path().join("audio.m4a");
        let args = vec![
            "--no-playlist".to_owned(),
            "--no-warnings".to_owned(),
            "--quiet".to_owned(),
            "--no-js-runtimes".to_owned(),
            "--extractor-args".to_owned(),
            "youtube:player_client=android_music".to_owned(),
            "-f".to_owned(),
            "140/bestaudio[ext=m4a]".to_owned(),
            "--no-part".to_owned(),
            "--force-overwrites".to_owned(),
            "--no-simulate".to_owned(),
            "--print".to_owned(),
            "%(title)j\t%(duration)j".to_owned(),
            "-o".to_owned(),
            path.to_string_lossy().into_owned(),
            url.to_owned(),
        ];
        let output = self.run(&args)?;
        let (title, duration) = parse_printed_metadata(&output);
        let len = fs::metadata(&path)
            .with_context(|| format!("yt-dlp did not create {}", path.display()))?
            .len();
        if len == 0 {
            bail!("yt-dlp produced an empty audio file");
        }
        Ok(DownloadedAudio {
            _directory: directory,
            path,
            len,
            title,
            duration,
        })
    }
}

fn parse_printed_metadata(output: &str) -> (Option<String>, Option<u64>) {
    for line in output.lines() {
        let (title, duration) = parse_metadata_line(line);
        if title.is_some() || duration.is_some() {
            return (title, duration);
        }
    }
    (None, None)
}

fn parse_track_lines(output: &str) -> Vec<Track> {
    output.lines().filter_map(parse_track_line).collect()
}

fn parse_track_line(line: &str) -> Option<Track> {
    let mut parts = line.splitn(3, '\t');
    let id = serde_json::from_str::<Option<String>>(parts.next()?)
        .ok()
        .flatten()?;
    if id.is_empty() || id == "NA" {
        return None;
    }
    let title = serde_json::from_str::<Option<String>>(parts.next()?)
        .ok()
        .flatten()
        .unwrap_or_else(|| "YouTube video".to_owned());
    let duration = serde_json::from_str::<Option<f64>>(parts.next()?)
        .ok()
        .flatten()
        .map(|value| value.max(0.0) as u64);
    Some(Track {
        title: clean_title(&title),
        source: TrackSource::YouTube(format!("https://www.youtube.com/watch?v={id}")),
        duration,
    })
}

fn parse_metadata_line(line: &str) -> (Option<String>, Option<u64>) {
    let Some((title, duration)) = line.split_once('\t') else {
        return (None, None);
    };
    let title = serde_json::from_str::<String>(title)
        .ok()
        .map(|value| clean_title(&value));
    let duration = serde_json::from_str::<f64>(duration)
        .ok()
        .map(|value| value.max(0.0) as u64);
    (title, duration)
}

fn parse_stream_output(output: &str) -> Option<StreamInfo> {
    let mut stream_url = None;
    let mut title = None;
    let mut duration = None;
    for line in output.lines() {
        if line.starts_with("http://") || line.starts_with("https://") {
            stream_url = Some(line.trim().to_owned());
        } else {
            let (line_title, line_duration) = parse_metadata_line(line);
            if line_title.is_some() {
                title = line_title;
            }
            if line_duration.is_some() {
                duration = line_duration;
            }
        }
    }
    let url = stream_url?;
    Some(StreamInfo {
        len: stream_len(&url),
        url,
        title,
        duration,
    })
}

fn stream_len(url: &str) -> Option<u64> {
    Url::parse(url)
        .ok()?
        .query_pairs()
        .find(|(key, _)| key == "clen")?
        .1
        .parse()
        .ok()
}

fn clean_title(value: &str) -> String {
    let printable: String = value
        .chars()
        .map(|ch| if ch.is_control() { ' ' } else { ch })
        .collect();
    let cleaned = printable.split_whitespace().collect::<Vec<_>>().join(" ");
    if cleaned.is_empty() {
        "?".to_owned()
    } else {
        cleaned
    }
}

fn exe_name(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    }
}

fn cache_directory() -> PathBuf {
    #[cfg(windows)]
    if let Some(local_app_data) = env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local_app_data).join("ytplay");
    }
    #[cfg(target_os = "macos")]
    if let Some(home) = env::var_os("HOME") {
        return PathBuf::from(home).join("Library/Caches/ytplay");
    }
    if let Some(cache) = env::var_os("XDG_CACHE_HOME").filter(|value| !value.is_empty()) {
        return PathBuf::from(cache).join("ytplay");
    }
    if let Some(home) = env::var_os("HOME") {
        return PathBuf::from(home).join(".cache/ytplay");
    }
    env::temp_dir().join("ytplay")
}

fn find_in_path(name: &str) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .map(|directory| directory.join(name))
        .find(|candidate| candidate.is_file())
}

fn extract_embedded(
    bytes: &[u8],
    stem: &str,
    version: &str,
    versioned: bool,
    windows: bool,
) -> Result<PathBuf> {
    let base = cache_directory().join("bin");
    fs::create_dir_all(&base).with_context(|| format!("could not create {}", base.display()))?;

    let version = if version.is_empty() {
        bytes.len().to_string()
    } else {
        version.replace(['/', '\\'], "_")
    };
    let filename = if versioned {
        format!("{stem}-{version}{}", if windows { ".exe" } else { "" })
    } else {
        format!("{stem}{}", if windows { ".exe" } else { "" })
    };
    let path = base.join(filename);
    let already_ok = fs::metadata(&path)
        .map(|metadata| metadata.len() == bytes.len() as u64)
        .unwrap_or(false);
    if already_ok {
        return Ok(path);
    }

    let temporary = path.with_extension("tmp");
    fs::write(&temporary, bytes)
        .with_context(|| format!("could not write {}", temporary.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o755))?;
    }
    if path.exists() {
        fs::remove_file(&path).ok();
    }
    fs::rename(&temporary, &path)
        .with_context(|| format!("could not install embedded yt-dlp at {}", path.display()))?;
    Ok(path)
}

#[cfg(windows)]
fn hide_child_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_child_console(_command: &mut Command) {}

// ---------------------------------------------------------------------------
// Queue and UI
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
struct Track {
    title: String,
    source: TrackSource,
    duration: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
enum TrackSource {
    YouTube(String),
    Local(PathBuf),
}

impl Track {
    fn duration_text(&self) -> String {
        self.duration
            .map(format_duration)
            .unwrap_or_else(|| "?".to_owned())
    }
}

struct Queue {
    tracks: Vec<Track>,
    index: usize,
    autoplay: bool,
}

impl Queue {
    fn new(autoplay: bool) -> Self {
        Self {
            tracks: Vec::new(),
            index: 0,
            autoplay,
        }
    }

    fn set_tracks(&mut self, tracks: Vec<Track>, index: usize) {
        self.tracks = tracks;
        self.index = index.min(self.tracks.len().saturating_sub(1));
    }

    fn current(&self) -> Option<&Track> {
        self.tracks.get(self.index)
    }

    fn has_next(&self) -> bool {
        self.index + 1 < self.tracks.len()
    }

    fn shuffle(&mut self) {
        if self.tracks.is_empty() {
            return;
        }
        let current = self.current().cloned();
        let mut seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64
            ^ u64::from(std::process::id());
        for index in (1..self.tracks.len()).rev() {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let swap = (seed as usize) % (index + 1);
            self.tracks.swap(index, swap);
        }
        if let Some(current) = current
            && let Some(position) = self.tracks.iter().position(|track| track == &current)
        {
            let track = self.tracks.remove(position);
            self.tracks.insert(0, track);
        }
        self.index = 0;
    }
}

fn pick_result(tracks: &[Track]) -> Result<Option<usize>> {
    println!();
    print_tracks(tracks, 0, tracks.len());
    println!();
    loop {
        print!("Choose [1-{}] or q to quit: ", tracks.len());
        io::stdout().flush()?;
        let Some(raw) = read_choice()? else {
            return Ok(None);
        };
        if raw == "q" {
            return Ok(None);
        }
        if let Ok(value) = raw.parse::<usize>()
            && (1..=tracks.len()).contains(&value)
        {
            return Ok(Some(value - 1));
        }
        println!("    Invalid choice, try again.");
    }
}

fn show_playlist(tracks: &[Track]) -> Result<Option<usize>> {
    println!();
    if tracks.len() <= 26 {
        print_tracks(tracks, 0, tracks.len());
    } else {
        print_tracks(tracks, 0, 20);
        println!(
            "  ... {} more tracks (type l to list all) ...",
            tracks.len() - 25
        );
        print_tracks(tracks, tracks.len() - 5, tracks.len());
    }
    println!();
    loop {
        print!(
            "Start from [1-{}], Enter for 1, l to list all, or q to quit: ",
            tracks.len()
        );
        io::stdout().flush()?;
        let Some(raw) = read_choice()? else {
            return Ok(None);
        };
        if raw == "q" {
            return Ok(None);
        }
        if raw.is_empty() {
            return Ok(Some(0));
        }
        if raw == "l" {
            println!();
            print_tracks(tracks, 0, tracks.len());
            println!();
            continue;
        }
        if let Ok(value) = raw.parse::<usize>()
            && (1..=tracks.len()).contains(&value)
        {
            return Ok(Some(value - 1));
        }
        println!("    Invalid choice, try again.");
    }
}

fn read_choice() -> Result<Option<String>> {
    let mut line = String::new();
    if io::stdin().read_line(&mut line)? == 0 {
        return Ok(None);
    }
    Ok(Some(line.trim().to_ascii_lowercase()))
}

fn print_tracks(tracks: &[Track], start: usize, end: usize) {
    let width = terminal_width();
    for (offset, track) in tracks[start..end].iter().enumerate() {
        let index = start + offset + 1;
        let prefix = format!("  [{index:>3}] ");
        let suffix = format!("  ({})", track.duration_text());
        let available = width.saturating_sub(prefix.len() + suffix.len()).max(1);
        let title = truncate(&track.title, available);
        println!("{prefix}{title}{suffix}");
    }
}

fn terminal_width() -> usize {
    terminal::size()
        .map(|(width, _)| width as usize)
        .unwrap_or(100)
        .max(40)
}

fn truncate(value: &str, width: usize) -> String {
    if value.chars().count() <= width {
        return value.to_owned();
    }
    let mut result: String = value.chars().take(width.saturating_sub(1)).collect();
    result.push('…');
    result
}

fn format_duration(seconds: u64) -> String {
    let hours = seconds / 3600;
    let minutes = (seconds % 3600) / 60;
    let seconds = seconds % 60;
    if hours > 0 {
        format!("{hours}:{minutes:02}:{seconds:02}")
    } else {
        format!("{minutes}:{seconds:02}")
    }
}

// ---------------------------------------------------------------------------
// HTTP range source
// ---------------------------------------------------------------------------

struct HttpSource {
    inner: Mutex<HttpInner>,
}

struct HttpInner {
    client: ureq::Agent,
    url: String,
    pos: u64,
    len: Option<u64>,
    response: Option<ureq::BodyReader<'static>>,
    buffer: Vec<u8>,
    buffer_pos: usize,
}

impl HttpSource {
    fn new(url: String, len: Option<u64>) -> Result<Self> {
        let client = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .timeout_connect(Some(Duration::from_secs(10)))
            .timeout_per_read(Some(Duration::from_secs(30)))
            .build()
            .into();
        Ok(Self {
            inner: Mutex::new(HttpInner {
                client,
                url,
                pos: 0,
                len,
                response: None,
                buffer: Vec::new(),
                buffer_pos: 0,
            }),
        })
    }

    fn len(&self) -> Option<u64> {
        lock(&self.inner).len
    }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

impl HttpInner {
    fn fill(&mut self) -> io::Result<bool> {
        let range = format!("bytes={}-", self.pos);
        let response = self
            .client
            .get(&self.url)
            .header("Range", range)
            .header("User-Agent", DEFAULT_UA)
            .call()
            .map_err(|error| io::Error::other(format!("HTTP request failed: {error}")))?;
        let status = response.status().as_u16();
        if status == 416 {
            self.response = None;
            return Ok(false);
        }
        if status != 206 && !(status == 200 && self.pos == 0) {
            return Err(io::Error::other(format!(
                "audio server returned HTTP {status}"
            )));
        }
        if self.len.is_none() {
            self.len = response
                .headers()
                .get("content-range")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.rsplit('/').next()?.trim().parse().ok())
                .or_else(|| {
                    (status == 200)
                        .then(|| {
                            response
                                .headers()
                                .get("content-length")
                                .and_then(|value| value.to_str().ok())
                                .and_then(|value| value.parse().ok())
                        })
                        .flatten()
                });
        }
        self.buffer.resize(HTTP_BUFFER, 0);
        self.buffer_pos = 0;
        self.response = Some(response.into_body().into_reader());
        Ok(true)
    }

    fn read_chunk(&mut self) -> io::Result<usize> {
        loop {
            if let Some(response) = self.response.as_mut() {
                match response.read(&mut self.buffer) {
                    Ok(0) => {
                        self.response = None;
                        return Ok(0);
                    }
                    Ok(read) => {
                        self.buffer.truncate(read);
                        self.buffer_pos = 0;
                        return Ok(read);
                    }
                    Err(error) => {
                        self.response = None;
                        return Err(error);
                    }
                }
            }
            if !self.fill()? {
                return Ok(0);
            }
        }
    }
}

impl Read for HttpSource {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        let mut inner = lock(&self.inner);
        let mut written = 0;
        while written < output.len() {
            if inner.buffer_pos < inner.buffer.len() {
                let available = inner.buffer.len() - inner.buffer_pos;
                let count = available.min(output.len() - written);
                output[written..written + count]
                    .copy_from_slice(&inner.buffer[inner.buffer_pos..inner.buffer_pos + count]);
                inner.buffer_pos += count;
                inner.pos += count as u64;
                written += count;
                continue;
            }
            if inner.read_chunk()? == 0 {
                break;
            }
        }
        Ok(written)
    }
}

impl Seek for HttpSource {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        let mut inner = lock(&self.inner);
        let current = i128::from(inner.pos);
        let target = match position {
            SeekFrom::Start(value) => i128::from(value),
            SeekFrom::Current(value) => current + i128::from(value),
            SeekFrom::End(value) => {
                let length = inner.len.ok_or_else(|| {
                    io::Error::new(io::ErrorKind::Unsupported, "unknown stream length")
                })?;
                i128::from(length) + i128::from(value)
            }
        };
        if target < 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "negative seek position",
            ));
        }
        inner.pos = u64::try_from(target).unwrap_or(u64::MAX);
        inner.response = None;
        inner.buffer.clear();
        inner.buffer_pos = 0;
        Ok(inner.pos)
    }
}

enum AudioInput {
    Http(Box<HttpSource>),
    File(File),
}

impl Read for AudioInput {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        match self {
            Self::Http(source) => source.read(output),
            Self::File(file) => file.read(output),
        }
    }
}

impl Seek for AudioInput {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        match self {
            Self::Http(source) => source.seek(position),
            Self::File(file) => file.seek(position),
        }
    }
}

struct RawMode;

impl RawMode {
    fn enable() -> Result<Self> {
        terminal::enable_raw_mode().context("could not enable raw terminal mode")?;
        Ok(Self)
    }
}

impl Drop for RawMode {
    fn drop(&mut self) {
        let _ = terminal::disable_raw_mode();
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Outcome {
    Finished,
    Next,
    Prev,
    Quit,
}

fn play_queue(queue: &mut Queue, ytdlp: &mut Option<YtDlp>, volume: i32) -> Result<()> {
    let mut settings = Settings {
        volume: volume.clamp(VOL_MIN, VOL_MAX),
    };

    while let Some(track) = queue.current().cloned() {
        let outcome = play_one(queue, &track, ytdlp, &mut settings);
        match outcome {
            Ok(Outcome::Quit) => return Ok(()),
            Ok(Outcome::Finished) => {
                if queue.autoplay && queue.has_next() {
                    queue.index += 1;
                } else {
                    break;
                }
            }
            Ok(Outcome::Next) => {
                if queue.has_next() {
                    queue.index += 1;
                } else {
                    break;
                }
            }
            Ok(Outcome::Prev) => {
                queue.index = queue.index.saturating_sub(1);
            }
            Err(err) => {
                eprintln!("❌ Could not play \"{}\": {err:#}", track.title);
                if queue.has_next() {
                    queue.index += 1;
                } else {
                    break;
                }
            }
        }
    }
    Ok(())
}

struct Settings {
    volume: i32,
}

fn play_one(
    queue: &mut Queue,
    track: &Track,
    ytdlp: &mut Option<YtDlp>,
    settings: &mut Settings,
) -> Result<Outcome> {
    let mut display_track = track.clone();
    let (decoder, _download) = match &track.source {
        TrackSource::Local(path) => {
            let file =
                File::open(path).with_context(|| format!("could not open {}", path.display()))?;
            let len = file.metadata().ok().map(|metadata| metadata.len());
            let hint = path
                .extension()
                .and_then(|extension| extension.to_str())
                .unwrap_or("m4a");
            let decoder = build_decoder(AudioInput::File(file), len, hint)?;
            (decoder, None)
        }
        TrackSource::YouTube(url) => {
            println!("⏳ Resolving stream...");
            let yt = ytdlp_ref(ytdlp)?;
            let streamed = yt.stream(url).and_then(|info| {
                let source = HttpSource::new(info.url.clone(), info.len)?;
                let len = source.len();
                let decoder = build_decoder(AudioInput::Http(Box::new(source)), len, "m4a")?;
                Ok((decoder, info))
            });

            match streamed {
                Ok((decoder, info)) => {
                    if let Some(title) = info.title {
                        display_track.title = title;
                    }
                    if info.duration.is_some() {
                        display_track.duration = info.duration;
                    }
                    (decoder, None)
                }
                Err(error) => {
                    println!("⚠ Streaming failed ({error}); downloading instead...");
                    let audio = yt.download(url)?;
                    if let Some(title) = &audio.title {
                        display_track.title = title.clone();
                    }
                    if audio.duration.is_some() {
                        display_track.duration = audio.duration;
                    }
                    let file = File::open(&audio.path)
                        .with_context(|| format!("could not open {}", audio.path.display()))?;
                    let decoder = build_decoder(AudioInput::File(file), Some(audio.len), "m4a")?;
                    (decoder, Some(audio))
                }
            }
        }
    };

    let duration = display_track
        .duration
        .or_else(|| decoder.total_duration().map(|value| value.as_secs()));

    let mut sink = DeviceSinkBuilder::open_default_sink()
        .context("no audio output device; check PipeWire/PulseAudio/ALSA")?;
    sink.log_on_drop(false);
    let player = Player::connect_new(sink.mixer());
    player.set_volume(settings.volume as f32 / 100.0);
    player.append(decoder);

    let controls = io::stdin().is_terminal();
    let _raw = if controls {
        Some(RawMode::enable()?)
    } else {
        None
    };

    println!();
    println!(
        "🎵 [{} / {}] {}  ({})",
        queue.index + 1,
        queue.tracks.len(),
        display_track.title,
        duration
            .map(format_duration)
            .unwrap_or_else(|| "?".to_owned())
    );
    println!(
        "   ↑/+ vol  ↓/- vol  space pause  ,/. seek {}s  ←/p  →/n  a auto  q quit",
        SEEK_STEP
    );

    let mut last_draw = Instant::now() - Duration::from_secs(1);
    let mut paused = false;

    loop {
        if player.empty() {
            print!("\r\n");
            io::stdout().flush().ok();
            return Ok(Outcome::Finished);
        }

        if controls && event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }
                match handle_key(key, &player, queue, settings, &mut paused, duration)? {
                    Some(outcome) => {
                        print!("\r\n");
                        io::stdout().flush().ok();
                        return Ok(outcome);
                    }
                    None => last_draw = Instant::now() - Duration::from_secs(1),
                }
            }
        } else if !controls {
            std::thread::sleep(Duration::from_millis(100));
        }

        if last_draw.elapsed() >= Duration::from_millis(500) {
            draw_status(
                &player,
                queue,
                &display_track,
                settings.volume,
                duration,
                paused,
            );
            last_draw = Instant::now();
        }
    }
}

fn build_decoder(input: AudioInput, len: Option<u64>, hint: &str) -> Result<Decoder<AudioInput>> {
    let mut builder = Decoder::builder()
        .with_data(input)
        .with_hint(hint)
        .with_seekable(len.is_some())
        .with_gapless(true);
    if let Some(len) = len {
        builder = builder.with_byte_len(len);
    }
    builder
        .build()
        .with_context(|| format!("could not decode {hint} audio"))
}

fn handle_key(
    key: KeyEvent,
    player: &Player,
    queue: &mut Queue,
    settings: &mut Settings,
    paused: &mut bool,
    duration: Option<u64>,
) -> Result<Option<Outcome>> {
    if key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL) {
        return Ok(Some(Outcome::Quit));
    }
    match key.code {
        KeyCode::Char('q') | KeyCode::Esc => Ok(Some(Outcome::Quit)),
        KeyCode::Char('n') | KeyCode::Right => Ok(Some(Outcome::Next)),
        KeyCode::Char('p') | KeyCode::Left => Ok(Some(Outcome::Prev)),
        KeyCode::Char(' ') => {
            if player.is_paused() {
                player.play();
                *paused = false;
            } else {
                player.pause();
                *paused = true;
            }
            Ok(None)
        }
        KeyCode::Char('a') => {
            queue.autoplay = !queue.autoplay;
            Ok(None)
        }
        KeyCode::Char('+') | KeyCode::Char('=') | KeyCode::Up => {
            settings.volume = (settings.volume + VOL_STEP).min(VOL_MAX);
            player.set_volume(settings.volume as f32 / 100.0);
            Ok(None)
        }
        KeyCode::Char('-') | KeyCode::Char('_') | KeyCode::Down => {
            settings.volume = (settings.volume - VOL_STEP).max(VOL_MIN);
            player.set_volume(settings.volume as f32 / 100.0);
            Ok(None)
        }
        KeyCode::Char(',') | KeyCode::Char('<') => {
            let position = player
                .get_pos()
                .saturating_sub(Duration::from_secs(SEEK_STEP));
            let _ = player.try_seek(position);
            Ok(None)
        }
        KeyCode::Char('.') | KeyCode::Char('>') => {
            let mut position = player.get_pos() + Duration::from_secs(SEEK_STEP);
            if let Some(duration) = duration {
                position = position.min(Duration::from_secs(duration));
            }
            let _ = player.try_seek(position);
            Ok(None)
        }
        _ => Ok(None),
    }
}

fn draw_status(
    player: &Player,
    queue: &Queue,
    track: &Track,
    volume: i32,
    duration: Option<u64>,
    paused: bool,
) {
    let position = player.get_pos().as_secs();
    let total = duration.unwrap_or(0);
    let width = terminal_width();
    let state = if paused { "⏸" } else { "▶" };
    let prefix = format!(
        "{state} [{}/{}] {}  {}/{}  vol {}%  {}",
        queue.index + 1,
        queue.tracks.len(),
        track.title,
        format_duration(position),
        if total == 0 {
            "?".to_owned()
        } else {
            format_duration(total)
        },
        volume,
        if queue.autoplay { "auto" } else { "manual" }
    );
    let line = truncate(&prefix, width.saturating_sub(1));
    print!("\r{line:<width$}", width = width.saturating_sub(1));
    io::stdout().flush().ok();
}

// ---------------------------------------------------------------------------
// Terminal relaunch (double-click on Linux)
// ---------------------------------------------------------------------------

enum TerminalStyle {
    Execute,
    DashDash,
    Direct,
}

fn try_reexec_in_terminal() -> bool {
    if env::var_os("YTPLAY_IN_TERMINAL").is_some() || env::var_os("YTPLAY_NO_TERMINAL").is_some() {
        return false;
    }
    if io::stdin().is_terminal() || io::stdout().is_terminal() {
        return false;
    }
    // Only auto-open a terminal for the no-argument double-click case. Piped
    // invocations such as `ytplay "query" | tee` must stay in the pipeline.
    if env::args_os().len() != 1 {
        return false;
    }

    if env::var_os("DISPLAY").is_none() && env::var_os("WAYLAND_DISPLAY").is_none() {
        return false;
    }

    let Ok(executable) = env::current_exe() else {
        return false;
    };
    let arguments: Vec<OsString> = env::args_os().skip(1).collect();
    let mut terminals: Vec<(String, TerminalStyle)> = Vec::new();
    if let Some(terminal) = env::var_os("TERMINAL")
        && let Some(terminal) = terminal.to_str()
    {
        terminals.push((terminal.to_owned(), TerminalStyle::Execute));
    }
    terminals.extend([
        ("x-terminal-emulator".to_owned(), TerminalStyle::Execute),
        ("gnome-terminal".to_owned(), TerminalStyle::DashDash),
        ("kgx".to_owned(), TerminalStyle::DashDash),
        ("ptyxis".to_owned(), TerminalStyle::DashDash),
        ("konsole".to_owned(), TerminalStyle::Execute),
        ("xfce4-terminal".to_owned(), TerminalStyle::Execute),
        ("terminator".to_owned(), TerminalStyle::Execute),
        ("tilix".to_owned(), TerminalStyle::Execute),
        ("alacritty".to_owned(), TerminalStyle::Execute),
        ("kitty".to_owned(), TerminalStyle::Direct),
        ("foot".to_owned(), TerminalStyle::Direct),
        ("wezterm".to_owned(), TerminalStyle::Direct),
        ("xterm".to_owned(), TerminalStyle::Execute),
        ("urxvt".to_owned(), TerminalStyle::Execute),
        ("st".to_owned(), TerminalStyle::Execute),
    ]);

    for (terminal, style) in terminals {
        let mut command = Command::new(&terminal);
        match style {
            TerminalStyle::Execute => {
                command.arg("-e");
            }
            TerminalStyle::DashDash => {
                command.arg("--");
            }
            TerminalStyle::Direct => {
                if terminal == "wezterm" {
                    command.args(["start", "--"]);
                }
            }
        }
        command
            .arg(&executable)
            .args(&arguments)
            .env("YTPLAY_IN_TERMINAL", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if command.spawn().is_ok() {
            return true;
        }
    }
    eprintln!("No terminal emulator found. Run ytplay from a terminal or set TERMINAL.");
    false
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn url(value: &str) -> Url {
        parse_youtube_url(value).expect("valid YouTube URL")
    }

    #[test]
    fn parses_one_letter_flags() {
        let cli = parse_args(
            [
                "-n", "3", "-p", "100", "-N", "-s", "-v", "80", "-S", "query",
            ]
            .into_iter()
            .map(OsString::from),
        )
        .unwrap();
        assert_eq!(cli.limit, 3);
        assert_eq!(cli.playlist_limit, 100);
        assert_eq!(cli.volume, 80);
        assert!(cli.no_autoplay);
        assert!(cli.shuffle);
        assert!(cli.single);
        assert_eq!(cli.query, ["query"]);
    }

    #[test]
    fn rejects_invalid_volume() {
        assert!(parse_args(["-v", "131"].into_iter().map(OsString::from)).is_err());
    }

    #[test]
    fn detects_video_and_playlist_urls() {
        assert!(is_video(&url("https://www.youtube.com/watch?v=abc")));
        assert!(is_video(&url("https://youtu.be/abc?t=10")));
        assert!(is_video(&url("https://www.youtube.com/shorts/abc")));
        assert!(!is_video(&url("https://www.youtube.com/playlist?list=PL1")));
        assert!(is_playlist(&url(
            "https://www.youtube.com/watch?v=abc&list=PL1"
        )));
        assert!(!is_playlist(&url("https://youtu.be/abc")));
        assert_eq!(
            video_id(&url("https://www.youtube.com/watch?v=abc&list=PL1")),
            Some("abc".to_owned())
        );
    }

    #[test]
    fn accepts_bare_youtube_urls() {
        assert!(parse_youtube_url("youtu.be/abc").is_some());
        assert!(parse_youtube_url("music.youtube.com/watch?v=abc").is_some());
        assert!(parse_youtube_url("https://example.com/watch?v=abc").is_none());
        assert!(parse_youtube_url("just search terms").is_none());
    }

    #[test]
    fn formats_duration() {
        assert_eq!(format_duration(5), "0:05");
        assert_eq!(format_duration(65), "1:05");
        assert_eq!(format_duration(3661), "1:01:01");
    }

    #[test]
    fn parses_flat_entries() {
        let tracks = parse_track_lines(
            "\"abc\"\t\"Hello\"\t65\n\"NA\"\t\"bad\"\tnull\n\"def\"\t\"World\"\tnull",
        );
        assert_eq!(tracks.len(), 2);
        assert_eq!(tracks[0].title, "Hello");
        assert_eq!(tracks[0].duration, Some(65));
        assert_eq!(tracks[1].title, "World");
        assert_eq!(tracks[1].duration, None);
    }

    #[test]
    fn cleans_control_characters() {
        assert_eq!(clean_title("bad\x1b[31mtitle\nline"), "bad [31mtitle line");
    }

    #[test]
    fn http_source_reads_and_seeks_with_ranges() {
        use std::net::TcpListener;
        use std::thread;

        let data: Vec<u8> = (0..10_000).map(|index| (index % 251) as u8).collect();
        let expected = data.clone();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            for _ in 0..2 {
                let (mut socket, _) = listener.accept().unwrap();
                let mut request = [0u8; 2048];
                let size = socket.read(&mut request).unwrap();
                let request = String::from_utf8_lossy(&request[..size]);
                let range = request
                    .lines()
                    .find(|line| line.to_ascii_lowercase().starts_with("range:"))
                    .and_then(|line| line.split_once('=').map(|(_, value)| value))
                    .unwrap()
                    .trim();
                let start: usize = range.split('-').next().unwrap().parse().unwrap();
                let body = &data[start..];
                let header = format!(
                    "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\n\
                     Content-Range: bytes {}-{}/{}\r\nConnection: close\r\n\r\n",
                    body.len(),
                    start,
                    data.len() - 1,
                    data.len()
                );
                socket.write_all(header.as_bytes()).unwrap();
                socket.write_all(body).unwrap();
            }
        });

        let mut source = HttpSource::new(
            format!("http://{address}/audio"),
            Some(expected.len() as u64),
        )
        .unwrap();
        let mut first = [0u8; 100];
        source.read_exact(&mut first).unwrap();
        assert_eq!(first, expected[..100]);

        source.seek(SeekFrom::Start(5_000)).unwrap();
        let mut second = [0u8; 100];
        source.read_exact(&mut second).unwrap();
        assert_eq!(second, expected[5_000..5_100]);
        server.join().unwrap();
    }

    #[test]
    fn parses_stream_output_and_content_length() {
        let output = "\"Title\"\t65\nhttps://example.test/audio?clen=1234&x=1\n";
        let info = parse_stream_output(output).unwrap();
        assert_eq!(info.title.as_deref(), Some("Title"));
        assert_eq!(info.duration, Some(65));
        assert_eq!(info.len, Some(1234));
    }

    #[test]
    fn queue_keeps_current_first_when_shuffling() {
        let tracks: Vec<Track> = (0..10)
            .map(|index| Track {
                title: index.to_string(),
                source: TrackSource::YouTube("x".to_owned()),
                duration: None,
            })
            .collect();
        let mut queue = Queue::new(true);
        queue.set_tracks(tracks, 5);
        let current = queue.current().cloned();
        queue.shuffle();
        assert_eq!(queue.index, 0);
        assert_eq!(queue.current().cloned(), current);
        assert_eq!(queue.tracks.len(), 10);
    }
}
