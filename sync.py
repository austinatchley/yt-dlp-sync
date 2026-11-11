#!/usr/bin/env python3
import logging
import logging.handlers
import subprocess
import sys
import urllib.request
from typing import Any
from pathlib import Path
import tomllib

CONFIG_PATH = Path(__file__).parent / "config.toml"


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def send_ntfy(topic: str, title: str, message: str, priority: str = "default") -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode(),
        method="POST",
        headers={"Title": title, "Priority": priority},
    )
    urllib.request.urlopen(req, timeout=10)


def get_playlist_urls(config: dict[str, Any]) -> list[str]:
    urls = config.get("playlist_urls")
    if urls is not None:
        return list(urls)
    return [config["playlist_url"]]


def playlist_reachable(ytdlp: str, playlist_url: str) -> bool:
    """Check that the playlist itself loads. Individual unavailable tracks do
    not affect this; a deleted/private/invalid playlist returns non-zero."""
    result = subprocess.run(
        [ytdlp, "--flat-playlist", "--ignore-errors", "--print", "%(id)s", playlist_url],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    config = load_config()

    output_dir = Path(config["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = str(output_dir / "log.txt")
    archive_path = str(output_dir / "archive.txt")

    playlist_urls = get_playlist_urls(config)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_path, maxBytes=1_000_000, backupCount=5
            ),
            logging.StreamHandler(),
        ],
    )

    logging.info("=== sync started ===")

    ytdlp = str(Path(config["ytdlp_path"]).expanduser())

    base_cmd: list[str] = [
        ytdlp,
        "--extract-audio",
        "--audio-format", "flac",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--add-metadata",
        "--yes-playlist",
        "--download-archive", archive_path,
        # skip unavailable/private/age-gated tracks instead of aborting the playlist
        "--ignore-errors",
        "-N", "4",
        "--quiet",
        "--no-warnings",
        "--no-write-thumbnail",
        # only prints for newly downloaded files (after move to final location)
        "--print", "after_move:Downloaded: %(title)s - %(artist,uploader)s",
        "-o", str(output_dir / "%(title)s - %(artist,uploader)s.%(ext)s"),
    ]

    # Optional: pass browser cookies to reach age-restricted/members-only tracks.
    # Example: cookies_from_browser = "chrome"
    cookies = config.get("cookies_from_browser")
    if cookies:
        base_cmd.extend(["--cookies-from-browser", str(cookies)])

    failed: list[str] = []
    for playlist_url in playlist_urls:
        logging.info(f"=== syncing {playlist_url} ===")

        # A reachable playlist may still contain unavailable tracks, which makes
        # yt-dlp exit non-zero. Only a playlist that cannot load at all is fatal.
        if not playlist_reachable(ytdlp, playlist_url):
            failed.append(playlist_url)
            logging.error(f"Playlist could not be loaded: {playlist_url}")
            continue

        result = subprocess.run(base_cmd + [playlist_url], capture_output=True, text=True)

        downloaded = 0
        for line in result.stdout.strip().splitlines():
            logging.info(line)
            if line.startswith("Downloaded:"):
                downloaded += 1

        error_lines = result.stderr.strip().splitlines()
        for line in error_lines:
            logging.error(line)

        logging.info(
            f"=== finished {playlist_url} "
            f"({downloaded} new track(s), {len(error_lines)} error line(s)) ==="
        )

        if result.returncode != 0:
            logging.warning(
                f"yt-dlp exited with code {result.returncode} for {playlist_url}; "
                "some tracks may be unavailable (see errors above)"
            )

    if failed:
        try:
            send_ntfy(
                config["ntfy_topic"],
                title="yt-dlp-sync failed",
                message=(
                    f"{len(failed)} playlist(s) failed: {', '.join(failed)}. "
                    f"Check {log_path} for details."
                ),
                priority="high",
            )
        except Exception as exc:
            logging.error(f"Failed to send ntfy notification: {exc}")

    logging.info(f"=== sync finished ({len(failed)} failed) ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
