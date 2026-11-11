# AGENTS.md

Long-term context for agents working in this repository. Keep this file up to date when changing the architecture, config schema, or workflow.

## Project overview

`yt-dlp-sync` is a small, dependency-free Python wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp). It syncs one or more YouTube / YouTube Music playlists to local FLAC files on a schedule (cron, launchd, or Windows Task Scheduler) and sends an [ntfy.sh](https://ntfy.sh) push notification when a playlist cannot be loaded.

## Repository layout

| Path | Purpose |
|---|---|
| `sync.py` | Main sync script. Resolves playlists, runs `yt-dlp` per playlist, logs, and notifies. |
| `migrate.py` | One-off migration script: backup, deduplicate, and move an old flat output directory into the per-playlist layout. |
| `config.example.toml` | Example config. Copy to `config.toml` (gitignored). |
| `README.md` | User-facing documentation. |
| `AGENTS.md` | This file. |

## Architecture

- Each configured playlist is synced into its own subdirectory of `output_dir`.
- Each playlist directory contains its own `archive.txt` (yt-dlp download archive) and its FLAC files.
- The global run log stays at `output_dir/log.txt`.
- Playlist directory names come from an explicit `name`, or from the playlist title when only a URL is configured.
- Directory names are sanitized for filesystem safety. Collisions are disambiguated by appending the playlist ID.
- A playlist that cannot be loaded at all is fatal: the run exits non-zero and sends an ntfy notification. Unavailable/private/age-gated individual tracks are skipped and logged without failing the run.

### Config shapes (all supported)

```toml
# Preferred: explicit names
[[playlists]]
name = "dj"
url = "https://music.youtube.com/playlist?list=..."

# URLs only; names derived from playlist titles
playlist_urls = ["https://music.youtube.com/playlist?list=..."]

# Legacy single URL
playlist_url = "https://music.youtube.com/playlist?list=..."
```

## Commands

```sh
python3 sync.py              # run the sync
python3 sync.py --dry-run    # print resolved playlists and exit
python3 migrate.py           # preview the migration plan (dry run)
python3 migrate.py --apply   # back up, dedupe, and move files
python3 -m py_compile sync.py migrate.py
```

## Configuration keys

| Key | Description |
|---|---|
| `ytdlp_path` | Path to the yt-dlp binary/wrapper. Supports `~`. |
| `output_dir` | Root download directory. Each playlist gets a subdirectory. |
| `playlists` | List of tables with `name` and `url`. |
| `playlist_urls` | Optional list of URLs; names derived from titles. |
| `playlist_url` | Optional legacy single URL. |
| `cookies_from_browser` | Optional browser name/profile for cookies. |
| `backup_dir` | Optional backup directory for `migrate.py`. |
| `ntfy_topic` | ntfy.sh topic for failure notifications. |

## Migration notes

- The migration is a one-off operation. It backs up the output directory before changing anything.
- The backup is a full copy (`rsync -a`, falling back to `shutil.copytree`) in a timestamped directory.
- Deduplication removes any non-`.flac` media file that has a matching `.flac` sibling (same stem).
- Files are mapped to playlists by reading the `purl` metadata tag with `ffprobe` and extracting the YouTube video ID.
- Files whose ID is not in any configured playlist are moved to `output_dir/_unmatched/`.
- Per-playlist archives are seeded as `youtube <id>` lines.
- `migrate.py` defaults to dry-run; use `--apply` to execute.

## Conventions

- Python 3.11+; use the standard library only (no third-party dependencies).
- Keep `sync.py` and `migrate.py` self-contained and runnable directly with `python3`.
- Use `tomllib` for config parsing.
- Update `README.md` and `config.example.toml` whenever the config schema changes.
- Run `python3 -m py_compile sync.py migrate.py` before committing.
- Do not commit `config.toml` (gitignored).

## User preferences

- Commit locally when asked, but **do not push** unless explicitly requested.
- Wait for the user to review changes before pushing.
- Document design decisions and scope in GitHub issues (see issues #1 and #2).
- Prefer simple, dependency-free Python over frameworks.
