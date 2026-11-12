import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC = REPO_ROOT / "sync.py"
MIGRATE = REPO_ROOT / "migrate.py"

YTDLP_STUB = '''\
#!/usr/bin/env python3
import os
import sys

PLAYLISTS = {
    "AAAA": {"title": "alpha", "ids": ["a1", "a2", "a3"]},
    "BBBB": {"title": "beta", "ids": ["b1", "b2"]},
}


def list_id(url):
    for token in url.replace("&", "?").split("?"):
        if token.startswith("list="):
            return token.split("=", 1)[1]
    return ""


def main():
    argv = sys.argv[1:]
    url = argv[-1]
    entry = PLAYLISTS.get(list_id(url))

    if entry is None:
        print("ERROR: playlist not found", file=sys.stderr)
        return 1

    if "--flat-playlist" in argv:
        wanted = ""
        for i, arg in enumerate(argv):
            if arg == "--print" and i + 1 < len(argv):
                wanted = argv[i + 1]
            elif arg.startswith("--print="):
                wanted = arg.split("=", 1)[1]
        if "playlist_title" in wanted:
            print(entry["title"])
        else:
            for vid in entry["ids"]:
                print(vid)
        return 0

    archive = None
    out = None
    for i, arg in enumerate(argv):
        if arg == "--download-archive" and i + 1 < len(argv):
            archive = argv[i + 1]
        elif arg == "-o" and i + 1 < len(argv):
            out = argv[i + 1]

    existing = set()
    if archive and os.path.exists(archive):
        for line in open(archive):
            parts = line.split()
            if len(parts) == 2:
                existing.add(parts[1])

    outdir = os.path.dirname(out) if out else "."
    os.makedirs(outdir, exist_ok=True)
    for vid in entry["ids"]:
        if vid in existing:
            continue
        with open(os.path.join(outdir, "%s_%s.flac" % (entry["title"], vid)), "w") as fh:
            fh.write("fake flac")
        if archive:
            with open(archive, "a") as fh:
                fh.write("youtube %s\\n" % vid)
        print("Downloaded: %s %s - artist" % (entry["title"], vid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

FFPROBE_STUB = '''\
#!/usr/bin/env python3
import json
import os
import sys


def main():
    path = sys.argv[-1]
    mapping = {}
    map_path = os.environ.get("FFPROBE_STUB_MAP")
    if map_path and os.path.exists(map_path):
        with open(map_path) as fh:
            mapping = json.load(fh)
    url = mapping.get(os.path.basename(path), "")
    if url:
        print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class IntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.ytdlp = self.bin / "yt-dlp"
        self.ytdlp.write_text(YTDLP_STUB)
        make_executable(self.ytdlp)
        self.ffprobe = self.bin / "ffprobe"
        self.ffprobe.write_text(FFPROBE_STUB)
        make_executable(self.ffprobe)

        self.output = self.root / "output"
        self.config = self.root / "config.toml"
        self.write_config()

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, playlists=None):
        if playlists is None:
            playlists = [("alpha", "AAAA"), ("beta", "BBBB")]
        lines = [
            f'ytdlp_path = "{self.ytdlp}"',
            f'output_dir = "{self.output}"',
            "",
        ]
        for name, pid in playlists:
            lines += [
                "[[playlists]]",
                f'name = "{name}"',
                f'url = "https://music.youtube.com/playlist?list={pid}"',
                "",
            ]
        self.config.write_text("\n".join(lines))

    def run_cli(self, script, *args, env=None):
        return subprocess.run(
            [sys.executable, str(script), "--config", str(self.config), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def env_with_ffprobe(self, mapping):
        map_path = self.root / "ffprobe_map.json"
        map_path.write_text(json.dumps(mapping))
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["FFPROBE_STUB_MAP"] = str(map_path)
        return env


class SyncIntegrationTests(IntegrationTestCase):
    def test_end_to_end(self):
        result = self.run_cli(SYNC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.output / "alpha" / "archive.txt").read_text().split(),
            ["youtube", "a1", "youtube", "a2", "youtube", "a3"],
        )
        self.assertEqual(
            sorted(p.name for p in (self.output / "alpha").glob("*.flac")),
            ["alpha_a1.flac", "alpha_a2.flac", "alpha_a3.flac"],
        )
        self.assertEqual(
            sorted(p.name for p in (self.output / "beta").glob("*.flac")),
            ["beta_b1.flac", "beta_b2.flac"],
        )
        self.assertTrue((self.output / "log.txt").exists())

    def test_second_run_downloads_nothing(self):
        self.run_cli(SYNC)
        result = self.run_cli(SYNC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Downloaded:", result.stdout)

    def test_dry_run_lists_playlists(self):
        result = self.run_cli(SYNC, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha\t", result.stdout)
        self.assertIn("beta\t", result.stdout)
        self.assertFalse(self.output.exists())

    def test_status_creates_nothing(self):
        result = self.run_cli(SYNC, "--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.output.exists())

    def test_only(self):
        result = self.run_cli(SYNC, "--only", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / "alpha" / "archive.txt").exists())
        self.assertFalse((self.output / "beta").exists())

    def test_only_unknown_name(self):
        result = self.run_cli(SYNC, "--only", "nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown playlist", result.stderr)

    def test_status(self):
        self.run_cli(SYNC)
        result = self.run_cli(SYNC, "--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"alpha\s+3\s+3")
        self.assertRegex(result.stdout, r"beta\s+2\s+2")

    def test_unreachable_playlist_fails(self):
        self.write_config(playlists=[("ghost", "ZZZZ")])
        result = self.run_cli(SYNC)
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not be loaded", result.stdout + result.stderr)


class MigrateIntegrationTests(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.flat = self.root / "flat"
        self.flat.mkdir()
        (self.flat / "Song A.flac").write_text("a")
        (self.flat / "Song A.mp3").write_text("a")
        (self.flat / "Song B.flac").write_text("b")
        (self.flat / "Unmatched.flac").write_text("u")
        self.output = self.flat
        self.write_config()

    def mapping(self):
        return {
            "Song A.flac": "https://www.youtube.com/watch?v=a1",
            "Song A.mp3": "https://www.youtube.com/watch?v=a1",
            "Song B.flac": "https://www.youtube.com/watch?v=b1",
            "Unmatched.flac": "https://www.youtube.com/watch?v=zzz",
        }

    def test_apply_moves_dedupes_and_seeds(self):
        env = self.env_with_ffprobe(self.mapping())
        result = self.run_cli(MIGRATE, "--apply", "--skip-backup", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTrue((self.flat / "alpha" / "Song A.flac").exists())
        self.assertIn(
            "youtube a1",
            (self.flat / "alpha" / "archive.txt").read_text().splitlines(),
        )
        self.assertTrue((self.flat / "beta" / "Song B.flac").exists())
        self.assertIn(
            "youtube b1",
            (self.flat / "beta" / "archive.txt").read_text().splitlines(),
        )
        self.assertTrue((self.flat / "_unmatched" / "Unmatched.flac").exists())
        self.assertFalse((self.flat / "Song A.mp3").exists())

        leftover = [
            path.name
            for path in self.flat.iterdir()
            if path.is_file() and path.suffix.lower() in {".flac", ".mp3"}
        ]
        self.assertEqual(leftover, [])

    def test_apply_creates_backup(self):
        env = self.env_with_ffprobe(self.mapping())
        result = self.run_cli(MIGRATE, "--apply", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        backup_root = Path(f"{self.flat}-backup")
        backups = list(backup_root.glob(f"{self.flat.name}-*"))
        self.assertTrue(backups, f"no backup created under {backup_root}")
        # the backup captured the original .mp3 before it was deleted
        self.assertTrue((backups[0] / "Song A.mp3").exists())


if __name__ == "__main__":
    unittest.main()
