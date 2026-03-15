#!/usr/bin/env python3
import logging
import logging.handlers
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # pip install tomli for Python < 3.11
    except ImportError:
        print("Error: requires Python 3.11+ or 'pip install tomli'", file=sys.stderr)
        sys.exit(1)

CONFIG_PATH = Path(__file__).parent / "config.toml"


def load_config() -> dict:
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

    log_path = Path(config["log_file"]).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

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

    output_dir = Path(config["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(Path(config["ytdlp_path"]).expanduser()),
        "--extract-audio",
        "--audio-format", "flac",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--add-metadata",
        "--yes-playlist",
        "--download-archive", str(Path(config["archive_file"]).expanduser()),
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
                message=f"Exit code {result.returncode}. Check {config['log_file']} for details.",
                priority="high",
            )
        except Exception as exc:
            logging.error(f"Failed to send ntfy notification: {exc}")

    logging.info(f"=== sync finished (exit code {result.returncode}) ===")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
