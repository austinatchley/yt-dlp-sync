# yt-dlp-sync

A lightweight Python wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp) for automatically syncing a YouTube or YouTube Music playlist to a local machine as FLAC audio files. Designed to run on a schedule (e.g., via cron), it downloads only new tracks on each run, embeds album art and metadata, and sends a push notification via [ntfy.sh](https://ntfy.sh) if anything goes wrong. Intended for use on macOS and Windows Subsystem for Linux (WSL).

---

## How it works

On each run, `sync.py`:

1. Calls `yt-dlp` to download only **new** tracks from the configured playlist (already-downloaded tracks are skipped via a persistent archive file)
2. Extracts audio, converts to **FLAC**, embeds a thumbnail as JPG album art, and writes metadata tags
3. Uses 4 parallel download workers for speed
4. Logs all activity to a rotating log file and stdout
5. Sends a high-priority [ntfy.sh](https://ntfy.sh) push notification if `yt-dlp` exits with a non-zero status

The archive file is designed to live on a shared cloud drive (e.g., Google Drive) so multiple machines share the same state and never duplicate downloads.

---

## Prerequisites

- **Python 3.11+** — uses the built-in `tomllib` module. On Python < 3.11, install `tomli` instead:
  ```sh
  pip install tomli
  ```
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
| `archive_file` | Path to the yt-dlp download archive file. Tracks already-downloaded video IDs so re-runs are incremental. Keep this on a shared drive (e.g., Google Drive) to sync state across machines. |
| `output_dir` | Directory where downloaded FLAC files are saved. Created automatically if it doesn't exist. |
| `log_file` | Path to the rotating log file (local to each machine). Rotates at 1 MB, keeps 5 backups. |
| `playlist_url` | The YouTube or YouTube Music playlist URL to sync. |
| `ntfy_topic` | Your [ntfy.sh](https://ntfy.sh) topic name for failure notifications. Pick something unique and keep it private. |

Example `config.toml`:

```toml
ytdlp_path   = "~/dev/yt-dlp/yt-dlp.sh"
archive_file = "~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/yt-dlp-archive.txt"
output_dir   = "~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/Music"
log_file     = "~/.local/log/yt-dlp-sync/sync.log"
playlist_url = "https://music.youtube.com/playlist?list=YOURPLAYLISTID"
ntfy_topic   = "your-unique-topic-name"
```

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

## Notifications

When `yt-dlp` exits with a non-zero status code, `sync.py` sends a high-priority push notification to your [ntfy.sh](https://ntfy.sh) topic with the exit code and a pointer to the log file.

To receive notifications, subscribe to your topic:
- **Web:** `https://ntfy.sh/<your-topic>`
- **Mobile:** Download the [ntfy app](https://ntfy.sh/) and subscribe to your topic name

The notification is sent over plain HTTPS using Python's standard library (`urllib.request`) — no extra dependencies required.

---

## Logging

Logs are written to both stdout and the `log_file` path configured in `config.toml`. The log file rotates automatically at 1 MB and keeps the last 5 rotated files.

Log format:
```
2025-01-15 06:00:01,234 INFO === sync started ===
2025-01-15 06:00:04,891 INFO Downloaded: Song Title - Artist Name
2025-01-15 06:01:12,003 INFO === sync finished (exit code 0) ===
```
