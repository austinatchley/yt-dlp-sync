#!/usr/bin/env python3
import argparse
import logging
import logging.handlers
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import tomllib

CONFIG_PATH = Path(__file__).parent / "config.toml"

RETRY_ATTEMPTS_DEFAULT = 3
TRANSIENT_ERROR_MARKERS = (
    "unable to download webpage",
    "unable to download api page",
    "http error 5",
    "http error 429",
    "timed out",
    "timeout",
    "temporary failure",
    "connection reset",
    "connection aborted",
    "connection error",
    "network is unreachable",
    "read timed out",
    "too many requests",
)


class ConfigError(Exception):
    """Raised when config.toml is missing required or well-typed values."""


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def get_retries(config: dict[str, Any]) -> int:
    value = config.get("retries", RETRY_ATTEMPTS_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return RETRY_ATTEMPTS_DEFAULT
    return value


def validate_config(config: dict[str, Any], *, needs_ntfy: bool = True) -> None:
    """Raise :class:`ConfigError` listing every problem found in the config."""
    errors: list[str] = []

    for key in ("ytdlp_path", "output_dir"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key!r} must be a non-empty string")

    if needs_ntfy:
        value = config.get("ntfy_topic")
        if not isinstance(value, str) or not value.strip():
            errors.append("'ntfy_topic' must be a non-empty string")

    ytdlp_path = config.get("ytdlp_path")
    if isinstance(ytdlp_path, str) and ("/" in ytdlp_path or ytdlp_path.startswith("~")):
        if not Path(ytdlp_path).expanduser().exists():
            errors.append(f"'ytdlp_path' not found: {ytdlp_path}")

    playlists = config.get("playlists")
    playlist_urls = config.get("playlist_urls")
    playlist_url = config.get("playlist_url")

    if playlists is not None:
        if not isinstance(playlists, list) or not playlists:
            errors.append("'playlists' must be a non-empty list of tables")
        else:
            for index, entry in enumerate(playlists):
                if not isinstance(entry, dict):
                    errors.append(f"'playlists[{index}]' must be a table with a 'url'")
                    continue
                if not isinstance(entry.get("url"), str) or not entry["url"].strip():
                    errors.append(f"'playlists[{index}].url' must be a non-empty string")
                if "name" in entry and not isinstance(entry["name"], str):
                    errors.append(f"'playlists[{index}].name' must be a string")
    elif playlist_urls is not None:
        if not isinstance(playlist_urls, list) or not playlist_urls:
            errors.append("'playlist_urls' must be a non-empty list of URLs")
        else:
            for index, url in enumerate(playlist_urls):
                if not isinstance(url, str) or not url.strip():
                    errors.append(f"'playlist_urls[{index}]' must be a non-empty string")
    elif playlist_url is not None:
        if not isinstance(playlist_url, str) or not playlist_url.strip():
            errors.append("'playlist_url' must be a non-empty string")
    else:
        errors.append(
            "no playlists configured: set 'playlists', 'playlist_urls', or 'playlist_url'"
        )

    cookies = config.get("cookies_from_browser")
    if cookies is not None and not isinstance(cookies, str):
        errors.append("'cookies_from_browser' must be a string")

    retries = config.get("retries", RETRY_ATTEMPTS_DEFAULT)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        errors.append("'retries' must be a positive integer")

    if errors:
        raise ConfigError("Invalid config.toml:\n  - " + "\n  - ".join(errors))


def run_capture(
    cmd: list[str],
    attempts: int = RETRY_ATTEMPTS_DEFAULT,
    delay: float = 2.0,
) -> subprocess.CompletedProcess[str]:
    """Run a command, retrying on a non-zero exit with linear backoff."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    for attempt in range(1, attempts):
        if result.returncode == 0:
            return result
        logging.warning(
            "command failed (exit %s); retry %s/%s in %.0fs",
            result.returncode,
            attempt,
            attempts - 1,
            delay,
        )
        time.sleep(delay)
        result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def looks_transient(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in TRANSIENT_ERROR_MARKERS)


def run_download(
    cmd: list[str],
    attempts: int = RETRY_ATTEMPTS_DEFAULT,
    delay: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    """Run a yt-dlp download, retrying only transient (network) failures."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    for attempt in range(1, attempts):
        if result.returncode == 0 or not looks_transient(result.stderr):
            return result
        logging.warning(
            "yt-dlp failed with a transient error; retry %s/%s in %.0fs",
            attempt,
            attempts - 1,
            delay,
        )
        time.sleep(delay)
        result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    attempts: int = RETRY_ATTEMPTS_DEFAULT,
) -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode(),
        method="POST",
        headers={"Title": title, "Priority": priority},
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
            return
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)



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


def resolve_playlist_name(
    ytdlp: str, url: str, attempts: int = RETRY_ATTEMPTS_DEFAULT
) -> str:
    """Resolve the playlist title, falling back to its id."""
    result = run_capture(
        [
            ytdlp,
            "--flat-playlist",
            "--playlist-items", "1",
            "--print", "%(playlist_title)s",
            url,
        ],
        attempts=attempts,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if lines:
        return sanitize_dir_name(lines[0])
    return sanitize_dir_name(playlist_url_id(url) or "playlist")


def playlist_reachable(
    ytdlp: str, url: str, attempts: int = RETRY_ATTEMPTS_DEFAULT
) -> bool:
    """Check that the playlist itself loads. Individual unavailable tracks do
    not affect this; a deleted/private/invalid playlist returns non-zero."""
    result = run_capture(
        [ytdlp, "--flat-playlist", "--ignore-errors", "--print", "%(id)s", url],
        attempts=attempts,
    )
    return result.returncode == 0


def resolve_playlists(
    ytdlp: str, config: dict[str, Any], attempts: int = RETRY_ATTEMPTS_DEFAULT
) -> list[tuple[str, str]]:
    """Resolve each playlist to a unique (directory_name, url) pair."""
    resolved: list[tuple[str, str]] = []
    used: set[str] = set()

    for playlist in get_playlists(config):
        url = playlist["url"]
        name = playlist.get("name") or resolve_playlist_name(ytdlp, url, attempts)
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

    try:
        config = load_config()
        validate_config(config)
    except FileNotFoundError:
        print(f"Config not found: {CONFIG_PATH}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    retries = get_retries(config)

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
    playlists = resolve_playlists(ytdlp, config, retries)

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
        if not playlist_reachable(ytdlp, url, retries):
            failed.append(url)
            logging.error(f"Playlist could not be loaded: {url}")
            continue

        cmd = base_cmd + [
            "--download-archive", str(archive_path),
            "-o", str(playlist_dir / "%(title)s - %(artist,uploader)s.%(ext)s"),
            url,
        ]

        result = run_download(cmd, retries)

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
                attempts=retries,
            )
        except Exception as exc:
            logging.error(f"Failed to send ntfy notification: {exc}")

    logging.info(f"=== sync finished ({len(failed)} failed) ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
