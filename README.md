# yt-dlp-sync

A lightweight Python wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp) for automatically syncing one or more YouTube or YouTube Music playlists to a local machine as FLAC audio files. Designed to run on a schedule (e.g., via cron), it downloads only new tracks on each run, embeds album art and metadata, and exits non-zero (so your scheduler can alert you) if a playlist fails to load. Intended for use on macOS and Windows Subsystem for Linux (WSL).

---

## How it works

On each run, `sync.py`:

1. Calls `yt-dlp` to download only **new** tracks from each configured playlist, in order (already-downloaded tracks are skipped via a per-playlist archive file)
2. Extracts audio, converts to **FLAC**, embeds a thumbnail as JPG album art, and writes metadata tags
3. Downloads up to 4 fragments concurrently per track for speed
4. Skips unavailable, private, or age-gated tracks without aborting the rest of the playlist
5. Logs all activity live to a rotating log file and stdout
6. Exits non-zero if a playlist fails to load, so your scheduler can alert you

Each playlist is downloaded into its own subdirectory of `output_dir` and keeps its own `archive.txt`, so playlists stay isolated and can be added or removed independently. The output directory is designed to live on a shared cloud drive (e.g., Google Drive) so multiple machines share the same state and never duplicate downloads.

### Directory layout

```
output_dir/
  dj/
    archive.txt
    Song Title - Artist.flac
  nov14/
    archive.txt
    Song Title - Artist.flac
  log.txt
```

- `output_dir/<playlist>/archive.txt` — yt-dlp download archive for that playlist.
- `output_dir/<playlist>/*.flac` — downloaded tracks.
- `output_dir/log.txt` — global run log.

---

## Prerequisites

- **Python 3.11+** — uses the built-in `tomllib` module. 
- **yt-dlp** — see [installation options](#yt-dlp-installation) below
- **ffmpeg** — required by yt-dlp for audio extraction, FLAC conversion, and thumbnail embedding

---

## Setup

1. Clone the repository:
   ```sh
   git clone https://github.com/yourusername/yt-dlp-sync.git
   cd yt-dlp-sync
   ```

2. Copy the example config and fill in your values:
   ```sh
   cp config.example.toml config.toml
   ```

3. Edit `config.toml` — see the [Configuration](#configuration) section below.

4. Run it once manually to verify everything works:
   ```sh
   python3 sync.py
   ```

---

## Configuration

All configuration lives in `config.toml` (gitignored — each machine has its own). Copy `config.example.toml` to get started.

| Key | Description |
|---|---|
| `ytdlp_path` | Path to the yt-dlp binary or wrapper script. Supports `~` expansion. See [yt-dlp installation](#yt-dlp-installation). |
| `output_dir` | Root directory for downloads. Each playlist gets its own subdirectory here. Created automatically if it doesn't exist. |
| `playlists` | List of tables with `name` and `url`. Each playlist gets its own subdirectory and `archive.txt`. |
| `playlist_urls` | Optional alternative to `playlists`. A list of URLs; directory names are derived from the playlist titles. |
| `playlist_url` | Optional legacy single URL. Still supported for backward compatibility. |
| `cookies_from_browser` | Optional. Read cookies from a browser to reach age-restricted or members-only tracks (e.g. `"chrome"`, `"firefox"`, or `"browser:profile"`). |
| `retries` | Optional. Number of attempts for network operations (metadata fetches and downloads) before giving up. Default `3`. |
| `backup_dir` | Optional. Where the [migration script](#migrating-existing-downloads) stores its backup. Defaults to a sibling directory named `<output_dir>-backup`. |

> **TOML ordering:** keep all top-level keys (`ytdlp_path`, `output_dir`, `retries`, …) **above** the first `[[playlists]]` table. In TOML, any key that follows a `[[playlists]]` header belongs to that playlist, not the document root.

Example `config.toml`:

```toml
ytdlp_path = "~/dev/yt-dlp/yt-dlp.sh"
output_dir = "~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/Music"

[[playlists]]
name = "dj"
url = "https://music.youtube.com/playlist?list=YOURPLAYLISTID"

[[playlists]]
name = "nov14"
url = "https://music.youtube.com/playlist?list=YOURPLAYLISTID"
```

If `name` is omitted (or when using `playlist_urls`), the directory name is derived from the playlist title. If the title is empty or invalid, the playlist ID is used. If two playlists resolve to the same name, the playlist ID is appended to disambiguate.

---

## Migrating existing downloads

If you previously ran an older version of this script that downloaded everything into a single flat directory, `migrate.py` can reorganize those files into the per-playlist layout. It:

1. Backs up the current `output_dir` to a timestamped directory (default: a sibling named `<output_dir>-backup`, configurable via `backup_dir`).
2. Removes duplicate files (for example, an `.mp3` when a matching `.flac` exists).
3. Reads the `purl` metadata tag from each remaining file to find its YouTube video ID, then maps it to the playlist that contains it.
4. Moves the file into `output_dir/<playlist>/`.
5. Seeds each per-playlist `archive.txt` with the moved files' IDs.
6. Moves files whose ID is not in any configured playlist to `output_dir/_unmatched/`.

The script is a **dry run by default**. Preview the plan first:

```sh
python3 migrate.py
```

Then apply it:

```sh
python3 migrate.py --apply
```

The backup is a full copy of the output directory, which is slow for large libraries. If you already have a backup, skip it with:

```sh
python3 migrate.py --apply --skip-backup
```

> **Note:** Run the migration only once. The backup is your safety net; if anything looks wrong, restore from the backup directory.

---

## yt-dlp installation

The `ytdlp_path` config key accepts any path to a yt-dlp executable. Choose whichever installation method fits your workflow:

### Standalone binary (recommended for cron reliability)

Download the prebuilt binary directly from GitHub releases and point `ytdlp_path` at it. This approach is self-contained and immune to system Python/pip upgrades breaking the install.

```sh
mkdir -p ~/dev/yt-dlp
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o ~/dev/yt-dlp/yt-dlp
chmod +x ~/dev/yt-dlp/yt-dlp
```

```toml
ytdlp_path = "~/dev/yt-dlp/yt-dlp"
```

### Shell script wrapper

A thin wrapper script is useful if you want to pin a specific version, set environment variables, or inject flags on every call. This is the pattern shown in `config.example.toml`:

```sh
# ~/dev/yt-dlp/yt-dlp.sh
#!/bin/bash
exec ~/dev/yt-dlp/yt-dlp "$@"
```

```toml
ytdlp_path = "~/dev/yt-dlp/yt-dlp.sh"
```

### Homebrew (macOS)

```sh
brew install yt-dlp
```

```toml
ytdlp_path = "/opt/homebrew/bin/yt-dlp"   # Apple Silicon
# ytdlp_path = "/usr/local/bin/yt-dlp"    # Intel
```

> **Note for cron:** Homebrew's bin directory may not be on `PATH` in cron's minimal environment. Using an absolute path in `ytdlp_path` avoids this issue.

### pipx

```sh
pipx install yt-dlp
```

```toml
ytdlp_path = "~/.local/bin/yt-dlp"
```

### pip (in a virtual environment)

```sh
python3 -m venv ~/dev/yt-dlp-env
~/dev/yt-dlp-env/bin/pip install yt-dlp
```

```toml
ytdlp_path = "~/dev/yt-dlp-env/bin/yt-dlp"
```

---

## Scheduling with cron

### macOS — crontab

Open your crontab for editing:

```sh
crontab -e
```

Add a line to run the sync on a schedule. For example, every day at 6 AM:

```cron
0 6 * * * /usr/bin/python3 /path/to/yt-dlp-sync/sync.py
```

> **Tip:** Use the full path to `python3` (find it with `which python3`). cron runs with a minimal environment and your shell's `PATH` is not available.

To verify the crontab was saved:
```sh
crontab -l
```

### macOS — launchd (alternative)

launchd is the native macOS scheduler and is more robust than cron for user-level jobs (it handles sleep/wake cycles, logs to the system log, and restarts on failure).

Create a plist at `~/Library/LaunchAgents/com.yourname.yt-dlp-sync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourname.yt-dlp-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/yourname/dev/yt-dlp-sync/sync.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/yt-dlp-sync.out</string>
    <key>StandardErrorPath</key>
    <string>/tmp/yt-dlp-sync.err</string>
</dict>
</plist>
```

Load and enable it:

```sh
launchctl load ~/Library/LaunchAgents/com.yourname.yt-dlp-sync.plist
```

To unload:

```sh
launchctl unload ~/Library/LaunchAgents/com.yourname.yt-dlp-sync.plist
```

### WSL (Windows Subsystem for Linux)

WSL does not run `cron` by default. You have two options:

**Option A: Start cron in your WSL session**

Add this to your `~/.bashrc` or `~/.zshrc` so cron starts when you open a terminal:

```sh
# Start cron if not running
if ! pgrep -x cron > /dev/null; then
    sudo service cron start
fi
```

Then add your job:
```sh
crontab -e
```
```cron
0 6 * * * /usr/bin/python3 /path/to/yt-dlp-sync/sync.py
```

**Option B: Windows Task Scheduler (more reliable)**

Create a task that calls the WSL binary directly so it runs on a Windows schedule regardless of whether a WSL terminal is open:

1. Open **Task Scheduler** and create a new basic task
2. Set the trigger (e.g., daily at 6:00 AM)
3. Set the action to:
   - **Program:** `C:\Windows\System32\wsl.exe`
   - **Arguments:** `-e python3 /home/yourname/dev/yt-dlp-sync/sync.py`

---

## Exit codes and scheduling

Unavailable, private, and age-gated tracks are skipped and logged; they do not fail the run. When a playlist itself cannot be loaded (deleted, private, or invalid URL), `sync.py` logs the failure and exits with a non-zero status.

Use your scheduler to surface failures — for example, cron emails stdout/stderr from the job, and launchd/Windows Task Scheduler can run a follow-up action. The script keeps no network notification dependency; all output goes to stdout and `log.txt`.

---

## Logging

Logs are written to both stdout and the log.txt file in the output directory. The log file rotates automatically at 1 MB and keeps the last 5 rotated files.

Log format:
```
2025-01-15 06:00:01,234 INFO === sync started ===
2025-01-15 06:00:04,891 INFO Downloaded: Song Title - Artist Name
2025-01-15 06:01:12,003 INFO === sync finished (0 failed) ===
```

---

## Development

The project uses only the standard library. Run the tests with:

```sh
python3 -m unittest discover -s tests
```

CI runs the compile check and test suite on Python 3.11–3.13 for every push and pull request.
