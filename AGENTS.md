# AGENTS.md

Long-term context for agents working in this repository. Keep this file up to date when changing the architecture, config schema, or workflow.

## Project overview

`yt-dlp-sync` is a small, dependency-free Python wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp). It syncs one or more YouTube / YouTube Music playlists to local FLAC files on a schedule (cron, launchd, or Windows Task Scheduler) and exits non-zero when a playlist cannot be loaded.

## Repository layout

| Path | Purpose |
|---|---|
| `sync.py` | Main sync script. Resolves playlists, runs `yt-dlp` per playlist, logs, and reports failures via exit code. |
| `migrate.py` | One-off migration script: backup, deduplicate, and move an old flat output directory into the per-playlist layout. |
| `config.example.toml` | Example config. Copy to `config.toml` (gitignored). |
| `tests/` | Standard-library `unittest` suite. Run with `python3 -m unittest discover -s tests`. |
| `.github/workflows/ci.yml` | CI: compile check + test suite on Python 3.11–3.13. |
| `README.md` | User-facing documentation. |
| `AGENTS.md` | This file. |

## Architecture

- Each configured playlist is synced into its own subdirectory of `output_dir`.
- Each playlist directory contains its own `archive.txt` (yt-dlp download archive) and its FLAC files.
- The global run log stays at `output_dir/log.txt`.
- Playlist directory names come from an explicit `name`, or from the playlist title when only a URL is configured.
- Directory names are sanitized for filesystem safety. Collisions are disambiguated by appending the playlist ID.
- A playlist that cannot be loaded at all is fatal: the run exits non-zero. Unavailable/private/age-gated individual tracks are skipped and logged without failing the run.
- yt-dlp output is streamed live (stdout/stderr read on worker threads) and logged immediately, so long runs show progress instead of staying silent.
- Config is validated up front (`validate_config`): every missing, mistyped, or unknown key is reported together and the run exits with code 2 instead of raising `KeyError`.
- Network operations retry with linear backoff (`retries` config key, default 3). Downloads only retry when the error looks transient; permanent per-video errors are not retried.

### Config shapes (all supported)

```toml
# Preferred: explicit names. Top-level keys must come *before* this table.
[[playlists]]
name = "dj"
url = "https://music.youtube.com/playlist?list=..."

# Alternatives (also must precede the [[playlists]] table):
playlist_urls = ["https://music.youtube.com/playlist?list=..."]  # names from titles
playlist_url = "https://music.youtube.com/playlist?list=..."     # legacy single
```

> In TOML, keys that follow a `[[playlists]]` header belong to that playlist, not the document root. Put `ytdlp_path`, `output_dir`, `retries`, etc. above the first `[[playlists]]`.

## Commands

```sh
python3 sync.py              # run the sync
python3 sync.py --dry-run    # print resolved playlists and exit
python3 sync.py --only dj    # sync only the named playlist(s)
python3 sync.py --status     # offline: files vs archive counts
python3 migrate.py           # preview the migration plan (dry run)
python3 migrate.py --apply   # back up, dedupe, and move files
python3 migrate.py --apply --skip-backup   # skip the slow full-copy backup
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
| `retries` | Optional. Attempts for network operations (default `3`). |
| `backup_dir` | Optional backup directory for `migrate.py`. |

## Migration notes

- The migration is a one-off operation. It backs up the output directory before changing anything.
- The backup is a timestamped copy of the output directory. With `rsync`, unchanged files are hardlinked from the most recent previous backup (`--link-dest`), so repeat backups are fast and space-efficient; with no previous backup it is a full copy (`shutil.copytree` is the fallback). `--skip-backup` skips it entirely when a backup already exists.
- Deduplication removes any non-`.flac` media file that has a matching `.flac` sibling (same stem). Duplicates are excluded from the move plan.
- Files are mapped to playlists by reading the `purl` metadata tag with `ffprobe` and extracting the YouTube video ID.
- Files whose ID is not in any configured playlist are moved to `output_dir/_unmatched/`.
- Per-playlist archives are seeded as `youtube <id>` lines (existing lines are preserved, so re-runs don't duplicate).
- `migrate.py` defaults to dry-run; use `--apply` to execute.

## Conventions

- Python 3.11+; use the standard library only (no third-party dependencies).
- Keep `sync.py` and `migrate.py` self-contained and runnable directly with `python3`.
- Use `tomllib` for config parsing.
- Update `README.md` and `config.example.toml` whenever the config schema changes.
- Run `python3 -m py_compile sync.py migrate.py` and `python3 -m unittest discover -s tests` before committing.
- Do not commit `config.toml` (gitignored).

## User preferences

- Commit locally when asked, but **do not push** unless explicitly requested.
- Wait for the user to review changes before pushing.
- Document design decisions and scope in GitHub issues (see issues #1 and #2).
- Prefer simple, dependency-free Python over frameworks.
