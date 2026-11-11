#!/usr/bin/env python3
"""One-off migration: back up, deduplicate, and move existing downloads into
per-playlist directories.

Dry-run by default; pass ``--apply`` to execute.

The script:
1. Backs up ``output_dir`` to a timestamped directory.
2. Removes duplicate files (e.g. ``.mp3``) that have a matching ``.flac``.
3. Maps each remaining file to a playlist via its ``purl`` metadata tag.
4. Moves files into ``output_dir/<playlist>/``.
5. Seeds each per-playlist ``archive.txt`` with the moved files' video IDs.
"""

import argparse
import logging
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import tomllib

CONFIG_PATH = Path(__file__).parent / "config.toml"
MEDIA_EXTS = {".flac", ".mp3", ".webm", ".m4a", ".opus", ".wav", ".ogg", ".aac"}
SKIP_NAMES = {"log.txt", "archive.txt", ".DS_Store"}


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def get_playlists(config: dict[str, Any]) -> list[dict[str, str | None]]:
    playlists = config.get("playlists")
    if playlists:
        return [{"name": p.get("name"), "url": p["url"]} for p in playlists]

    urls = config.get("playlist_urls")
    if urls is not None:
        return [{"name": None, "url": u} for u in urls]

    return [{"name": None, "url": config["playlist_url"]}]


def sanitize_dir_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = name.strip().strip(".")
    return name or "playlist"


def playlist_url_id(url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    ids = query.get("list")
    return ids[0] if ids else ""


def resolve_playlist_name(ytdlp: str, url: str) -> str:
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


def playlist_ids(ytdlp: str, url: str) -> set[str]:
    result = subprocess.run(
        [ytdlp, "--flat-playlist", "--ignore-errors", "--print", "%(id)s", url],
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def video_id_from_file(path: Path) -> str | None:
    """Read the ``purl`` tag from a media file and return its YouTube video id."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format_tags=purl",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None

    purl = result.stdout.strip()
    if not purl:
        return None

    query = urllib.parse.parse_qs(urllib.parse.urlparse(purl).query)
    ids = query.get("v")
    return ids[0] if ids else None


def resolve_playlists(ytdlp: str, config: dict[str, Any]) -> list[tuple[str, str]]:
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


def backup_output_dir(output_dir: Path, backup_dir: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{output_dir.name}-{timestamp}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    logging.info(f"Backing up {output_dir} -> {dest}")

    if shutil.which("rsync"):
        subprocess.run(["rsync", "-a", f"{output_dir}/", f"{dest}/"], check=True)
    else:
        shutil.copytree(output_dir, dest, dirs_exist_ok=True)

    return dest


def dedupe_plan(output_dir: Path) -> list[Path]:
    """Return non-.flac files that have a matching .flac sibling."""
    duplicates: list[Path] = []
    for path in output_dir.iterdir():
        if not path.is_file() or path.suffix.lower() == ".flac":
            continue
        if path.with_suffix(".flac").exists():
            duplicates.append(path)
    return duplicates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate flat yt-dlp output into per-playlist directories."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the migration (default: dry-run).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    config = load_config()
    output_dir = Path(config["output_dir"]).expanduser()
    if not output_dir.is_dir():
        print(f"output_dir not found: {output_dir}", file=sys.stderr)
        return 1

    ytdlp = str(Path(config["ytdlp_path"]).expanduser())
    backup_dir = Path(
        config.get("backup_dir", output_dir.parent / f"{output_dir.name}-backup")
    ).expanduser()

    playlists = resolve_playlists(ytdlp, config)

    # Map video id -> playlist directory name. First playlist wins.
    playlist_by_id: dict[str, str] = {}
    for name, url in playlists:
        for video_id in playlist_ids(ytdlp, url):
            playlist_by_id.setdefault(video_id, name)

    duplicates = dedupe_plan(output_dir)

    files: list[tuple[Path, str | None]] = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        if path.suffix.lower() not in MEDIA_EXTS:
            continue
        video_id = video_id_from_file(path)
        files.append((path, playlist_by_id.get(video_id)))

    print(f"Output dir: {output_dir}")
    print(f"Backup dir: {backup_dir}")
    for name, url in playlists:
        print(f"Playlist: {name} <- {url}")
    print(f"Duplicates to remove: {len(duplicates)}")
    for path in duplicates:
        print(f"  remove {path.name}")
    print(f"Files to move: {len(files)}")
    for path, name in files:
        print(f"  {path.name} -> {name or '_unmatched'}")

    if not args.apply:
        print("Dry run. Re-run with --apply to execute.")
        return 0

    backup = backup_output_dir(output_dir, backup_dir)
    print(f"Backup created: {backup}")

    for path in duplicates:
        path.unlink()
        print(f"removed duplicate: {path.name}")

    for name, _ in playlists:
        (output_dir / name).mkdir(parents=True, exist_ok=True)

    archives: dict[str, set[str]] = {}
    for path, name in files:
        dest = output_dir / (name or "_unmatched")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest / path.name))

        if name:
            video_id = video_id_from_file(dest / path.name)
            if video_id:
                archives.setdefault(name, set()).add(video_id)

    for name, ids in archives.items():
        archive = output_dir / name / "archive.txt"
        with open(archive, "a", encoding="utf-8") as f:
            for video_id in sorted(ids):
                f.write(f"youtube {video_id}\n")

    print(
        f"Migration complete. {len(files)} files moved, "
        f"{len(duplicates)} duplicates removed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
