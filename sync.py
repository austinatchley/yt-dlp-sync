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


def main() -> int:
    config = load_config() 

    output_dir = Path(config["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    log_path = str(output_dir / "log.txt")
    archive_path = str(output_dir / "archive.txt")

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

    cmd: list[str] = [
        str(Path(config["ytdlp_path"]).expanduser()),
        "--extract-audio",
        "--audio-format", "flac",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--add-metadata",
        "--yes-playlist",
        "--download-archive", archive_path,
        "-N", "4",
        "--quiet",
        "--no-warnings",
        "--no-write-thumbnail",
        # only prints for newly downloaded files (after move to final location)
        "--print", "after_move:Downloaded: %(title)s - %(artist,uploader)s",
        "-o", str(output_dir / "%(title)s - %(artist,uploader)s.%(ext)s"),
        config["playlist_url"],
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    for line in result.stdout.strip().splitlines():
        logging.info(line)

    for line in result.stderr.strip().splitlines():
        logging.error(line)

    if result.returncode != 0:
        logging.error(f"yt-dlp exited with code {result.returncode}")
        try:
            send_ntfy(
                config["ntfy_topic"],
                title="yt-dlp-sync failed",
                message=f"Exit code {result.returncode}. Check {log_path} for details.",
                priority="high",
            )
        except Exception as exc:
            logging.error(f"Failed to send ntfy notification: {exc}")

    logging.info(f"=== sync finished (exit code {result.returncode}) ===")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
