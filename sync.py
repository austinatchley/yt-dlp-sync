#!/usr/bin/env python3
import argparse
import logging
import logging.handlers
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

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


def get_playlists(config: dict[str, Any]) -> list[dict[str, str | None]]:
    """Return playlists as dicts with ``name`` (optional) and ``url``.

    Supports three config shapes:

    - ``[[playlists]]`` tables with ``name`` and ``url``
    - ``playlist_urls`` list of URLs (names derived from playlist titles)
    - ``playlist_url`` single URL (legacy)
    """
    playlists = config.get("playlists")
    if playlists:
        return [{"name": p.get("name"), "url": p["url"]} for p in playlists]

    urls = config.get("playlist_urls")
    if urls is not None:
        return [{"name": None, "url": u} for u in urls]

    return [{"name": None, "url": config["playlist_url"]}]


def sanitize_dir_name(name: str) -> str:
    """Make a playlist title safe for use as a directory name."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = name.strip().strip(".")
    return name or "playlist"


def playlist_url_id(url: str) -> str:
    """Extract the playlist id from a URL, if present."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    ids = query.get("list")
    return ids[0] if ids else ""


def resolve_playlist_name(ytdlp: str, url: str) -> str:
    """Resolve the playlist title, falling back to its id."""
    result = subprocess.run(
        [
            ytdlp,
            "--flat-playlist",
            "--playlist-items", "1",
            "--print", "%(playlist_title)s",
            url,
        ],
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if lines:
        return sanitize_dir_name(lines[0])
    return sanitize_dir_name(playlist_url_id(url) or "playlist")


def playlist_reachable(ytdlp: str, url: str) -> bool:
    """Check that the playlist itself loads. Individual unavailable tracks do
    not affect this; a deleted/private/invalid playlist returns non-zero."""
    result = subprocess.run(
        [ytdlp, "--flat-playlist", "--ignore-errors", "--print", "%(id)s", url],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def resolve_playlists(ytdlp: str, config: dict[str, Any]) -> list[tuple[str, str]]:
    """Resolve each playlist to a unique (directory_name, url) pair."""
    resolved: list[tuple[str, str]] = []
    used: set[str] = set()

    for playlist in get_playlists(config):
        url = playlist["url"]
        name = playlist.get("name") or resolve_playlist_name(ytdlp, url)
        name = sanitize_dir_name(name)

        if name in used:
            suffix = playlist_url_id(url) or str(len(resolved) + 1)
            name = f"{name}-{suffix}"
        used.add(name)
        resolved.append((name, url))

    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync YouTube playlists to FLAC.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved playlists and exit without downloading.",
    )
    args = parser.parse_args()

    config = load_config()

    output_dir = Path(config["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / "log.txt"

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

    ytdlp = str(Path(config["ytdlp_path"]).expanduser())
    playlists = resolve_playlists(ytdlp, config)

    if args.dry_run:
        for name, url in playlists:
            print(f"{name}\t{url}")
        return 0

    logging.info("=== sync started ===")

    base_cmd: list[str] = [
        ytdlp,
        "--extract-audio",
        "--audio-format", "flac",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--add-metadata",
        "--yes-playlist",
        # skip unavailable/private/age-gated tracks instead of aborting the playlist
        "--ignore-errors",
        "-N", "4",
        "--quiet",
        "--no-warnings",
        "--no-write-thumbnail",
        # only prints for newly downloaded files (after move to final location)
        "--print", "after_move:Downloaded: %(title)s - %(artist,uploader)s",
    ]

    # Optional: pass browser cookies to reach age-restricted/members-only tracks.
    # Example: cookies_from_browser = "chrome"
    cookies = config.get("cookies_from_browser")
    if cookies:
        base_cmd.extend(["--cookies-from-browser", str(cookies)])

    failed: list[str] = []
    for name, url in playlists:
        playlist_dir = output_dir / name
        playlist_dir.mkdir(parents=True, exist_ok=True)
        archive_path = playlist_dir / "archive.txt"

        logging.info(f"=== syncing {url} -> {playlist_dir} ===")

        # A reachable playlist may still contain unavailable tracks, which makes
        # yt-dlp exit non-zero. Only a playlist that cannot load at all is fatal.
        if not playlist_reachable(ytdlp, url):
            failed.append(url)
            logging.error(f"Playlist could not be loaded: {url}")
            continue

        cmd = base_cmd + [
            "--download-archive", str(archive_path),
            "-o", str(playlist_dir / "%(title)s - %(artist,uploader)s.%(ext)s"),
            url,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        downloaded = 0
        for line in result.stdout.strip().splitlines():
            logging.info(line)
            if line.startswith("Downloaded:"):
                downloaded += 1

        error_lines = result.stderr.strip().splitlines()
        for line in error_lines:
            logging.error(line)

        logging.info(
            f"=== finished {name} "
            f"({downloaded} new track(s), {len(error_lines)} error line(s)) ==="
        )

        if result.returncode != 0:
            logging.warning(
                f"yt-dlp exited with code {result.returncode} for {name}; "
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
