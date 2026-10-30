use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=vendor");

    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let target_arch = env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
    let filename = match target_os.as_str() {
        "windows" => "yt-dlp.exe".to_owned(),
        "macos" => "yt-dlp_macos".to_owned(),
        _ if target_arch == "aarch64" => "yt-dlp_linux_aarch64".to_owned(),
        _ => "yt-dlp_linux".to_owned(),
    };

    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let candidate = env::var_os("YTPLAY_YTDLP")
        .map(PathBuf::from)
        .unwrap_or_else(|| manifest.join("vendor").join(filename));
    let version_file = manifest.join("vendor").join("yt-dlp.version");

    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let version = fs::read_to_string(&version_file)
        .unwrap_or_default()
        .trim()
        .to_owned();

    let declaration = if candidate.is_file() {
        format!(
            "pub static EMBEDDED_YTDLP: Option<&[u8]> = Some(include_bytes!({:?}) as &[u8]);",
            candidate
        )
    } else {
        "pub static EMBEDDED_YTDLP: Option<&[u8]> = None;".to_owned()
    };

    let generated = format!(
        "{declaration}\npub static EMBEDDED_VERSION: &str = {:?};\n",
        version
    );
    fs::write(out.join("embedded_ytdlp.rs"), generated).unwrap();

    println!("cargo:rerun-if-changed={}", candidate.display());
    println!("cargo:rerun-if-changed={}", version_file.display());
}
