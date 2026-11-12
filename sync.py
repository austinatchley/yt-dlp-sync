#!/usr/bin/env python3
import argparse
import logging
import logging.handlers
import re
import subprocess
import sys
import threading
import time
import urllib.parse
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

KNOWN_CONFIG_KEYS = {
    "ytdlp_path",
    "output_dir",
    "playlists",
    "playlist_urls",
    "playlist_url",
    "cookies_from_browser",
    "retries",
    "backup_dir",
}
KNOWN_PLAYLIST_KEYS = {"name", "url"}

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".webm"}


class ConfigError(Exception):
    """Raised when config.toml is missing required or well-typed values."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def get_retries(config: dict[str, Any]) -> int:
    value = config.get("retries", RETRY_ATTEMPTS_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return RETRY_ATTEMPTS_DEFAULT
    return value


def validate_config(config: dict[str, Any]) -> None:
    """Raise :class:`ConfigError` listing every problem found in the config."""
    errors: list[str] = []

    for key in ("ytdlp_path", "output_dir"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key!r} must be a non-empty string")

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

    for key in sorted(set(config) - KNOWN_CONFIG_KEYS):
        errors.append(f"unknown top-level key {key!r}")

    if isinstance(playlists, list):
        for index, entry in enumerate(playlists):
            if not isinstance(entry, dict):
                continue
            for key in sorted(set(entry) - KNOWN_PLAYLIST_KEYS):
                errors.append(f"unknown key 'playlists[{index}].{key}'")

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


def _stream_download(cmd: list[str]) -> tuple[int, int, list[str]]:
    """Run yt-dlp, logging its output live.

    Returns ``(returncode, downloaded, error_lines)``. stdout and stderr are read
    on separate threads so the pipes cannot deadlock.
    """
    downloaded = 0
    errors: list[str] = []

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def read_stdout() -> None:
        nonlocal downloaded
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            logging.info(line)
            if line.startswith("Downloaded:"):
                downloaded += 1

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip("\n")
            errors.append(line)
            logging.error(line)

    threads = [
        threading.Thread(target=read_stdout),
        threading.Thread(target=read_stderr),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if proc.stdout is not None:
        proc.stdout.close()
    if proc.stderr is not None:
        proc.stderr.close()

    return proc.wait(), downloaded, errors


def run_download(
    cmd: list[str],
    attempts: int = RETRY_ATTEMPTS_DEFAULT,
    delay: float = 5.0,
) -> tuple[int, int, list[str]]:
    """Run a yt-dlp download, streaming output and retrying transient failures."""
    returncode, downloaded, errors = _stream_download(cmd)
    for attempt in range(1, attempts):
        if returncode == 0 or not looks_transient("\n".join(errors)):
            return returncode, downloaded, errors
        logging.warning(
            "yt-dlp failed with a transient error; retry %s/%s in %.0fs",
            attempt,
            attempts - 1,
            delay,
        )
        time.sleep(delay)
        returncode, downloaded, errors = _stream_download(cmd)
    return returncode, downloaded, errors


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


def playlist_track_count(
    ytdlp: str, url: str, attempts: int = RETRY_ATTEMPTS_DEFAULT
) -> int:
    """Return the number of entries in the playlist, or -1 if it cannot be loaded.

    Individual unavailable tracks do not affect this; a deleted/private/invalid
    playlist yields -1.
    """
    result = run_capture(
        [ytdlp, "--flat-playlist", "--ignore-errors", "--print", "%(id)s", url],
        attempts=attempts,
    )
    if result.returncode != 0:
        return -1
    return sum(1 for line in result.stdout.splitlines() if line.strip())


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


def select_playlists(
    playlists: list[tuple[str, str]], only: list[str] | None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Filter ``playlists`` to the requested names.

    Returns ``(selected, missing)`` where ``missing`` lists requested names that
    did not match any playlist.
    """
    if not only:
        return playlists, []

    available = {name for name, _ in playlists}
    missing = [name for name in only if name not in available]
    wanted = set(only)
    selected = [(name, url) for name, url in playlists if name in wanted]
    return selected, missing


def playlist_stats(playlist_dir: Path) -> tuple[int, int]:
    """Return (audio file count, archive entry count) for a playlist directory."""
    files = 0
    if playlist_dir.is_dir():
        files = sum(
            1
            for path in playlist_dir.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_EXTS
        )

    archived = 0
    archive = playlist_dir / "archive.txt"
    if archive.exists():
        archived = sum(
            1
            for line in archive.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    return files, archived


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync YouTube playlists to FLAC.")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to config.toml (default: next to this script).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved playlists and exit without downloading.",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="NAME",
        help="Sync only the named playlist(s). May be repeated.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show per-playlist file/archive counts and exit (offline).",
    )
    args = parser.parse_args()

    config_path = args.config or CONFIG_PATH
    try:
        config = load_config(config_path)
        validate_config(config)
    except FileNotFoundError:
        print(f"Config not found: {config_path}", file=sys.stderr)
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
    resolved = resolve_playlists(ytdlp, config, retries)

    playlists, missing = select_playlists(resolved, args.only)
    if missing:
        available = ", ".join(name for name, _ in resolved)
        print(
            f"Unknown playlist(s): {', '.join(missing)}. Available: {available}",
            file=sys.stderr,
        )
        return 2

    if args.status:
        print(f"{'playlist':<16}{'files':>7}{'archive':>9}")
        for name, _url in playlists:
            files, archived = playlist_stats(output_dir / name)
            print(f"{name:<16}{files:>7}{archived:>9}")
        return 0

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
        track_count = playlist_track_count(ytdlp, url, retries)
        if track_count < 0:
            failed.append(url)
            logging.error(f"Playlist could not be loaded: {url}")
            continue

        logging.info(f'Playlist "{name}" has {track_count} track(s)')

        cmd = base_cmd + [
            "--download-archive", str(archive_path),
            "-o", str(playlist_dir / "%(title)s - %(artist,uploader)s.%(ext)s"),
            url,
        ]

        returncode, downloaded, error_lines = run_download(cmd, retries)

        logging.info(
            f"=== finished {name} "
            f"({downloaded} new track(s), {len(error_lines)} error line(s)) ==="
        )

        if returncode != 0:
            logging.warning(
                f"yt-dlp exited with code {returncode} for {name}; "
                "some tracks may be unavailable (see errors above)"
            )

    if failed:
        logging.error(
            f"{len(failed)} playlist(s) failed: {', '.join(failed)}. "
            f"See {log_path} for details."
        )

    logging.info(f"=== sync finished ({len(failed)} failed) ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
