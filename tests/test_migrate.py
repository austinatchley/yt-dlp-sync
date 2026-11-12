import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import migrate


def completed(stdout: str):
    class Completed:
        returncode = 0

    result = Completed()
    result.stdout = stdout
    return result


class GetPlaylistsTests(unittest.TestCase):
    def test_tables(self):
        self.assertEqual(
            migrate.get_playlists({"playlists": [{"name": "dj", "url": "u"}]}),
            [{"name": "dj", "url": "u"}],
        )

    def test_urls(self):
        self.assertEqual(
            migrate.get_playlists({"playlist_urls": ["u"]}),
            [{"name": None, "url": "u"}],
        )

    def test_legacy_single(self):
        self.assertEqual(
            migrate.get_playlists({"playlist_url": "u"}),
            [{"name": None, "url": "u"}],
        )


class SanitizeDirNameTests(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(migrate.sanitize_dir_name("a/b:c"), "a_b_c")

    def test_empty(self):
        self.assertEqual(migrate.sanitize_dir_name(""), "playlist")


class DedupePlanTests(unittest.TestCase):
    def test_dedupes_non_flac_with_flac_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.flac").write_text("")
            (root / "a.mp3").write_text("")
            (root / "b.mp3").write_text("")
            (root / "c.webm").write_text("")
            (root / "c.flac").write_text("")
            duplicates = sorted(p.name for p in migrate.dedupe_plan(root))
        self.assertEqual(duplicates, ["a.mp3", "c.webm"])


class VideoIdTests(unittest.TestCase):
    def test_extracts_v_param(self):
        fake = completed("https://www.youtube.com/watch?v=abc123\n")
        with mock.patch.object(migrate.subprocess, "run", return_value=fake):
            self.assertEqual(migrate.video_id_from_file(Path("x.flac")), "abc123")

    def test_returns_none_without_purl(self):
        fake = completed("\n")
        with mock.patch.object(migrate.subprocess, "run", return_value=fake):
            self.assertIsNone(migrate.video_id_from_file(Path("x.flac")))


class LatestBackupTests(unittest.TestCase):
    def test_returns_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "out-20260101-000000").mkdir()
            (root / "out-20260202-000000").mkdir()
            (root / "out-notadir").write_text("")
            result = migrate.latest_backup(root, "out")
        self.assertEqual(result.name, "out-20260202-000000")

    def test_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(migrate.latest_backup(Path(tmp), "out"))

    def test_excludes_given_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "out-a").mkdir()
            (root / "out-b").mkdir()
            result = migrate.latest_backup(root, "out", exclude=root / "out-b")
        self.assertEqual(result.name, "out-a")


class ValidateConfigTests(unittest.TestCase):
    def test_valid(self):
        migrate.validate_config(
            {"ytdlp_path": "/bin/ls", "output_dir": "~/x", "playlist_url": "u"}
        )

    def test_invalid(self):
        with self.assertRaises(migrate.ConfigError):
            migrate.validate_config(
                {"playlist_urls": "nope", "backup_dir": 5, "retries": -1}
            )

    def test_unknown_keys(self):
        with self.assertRaises(migrate.ConfigError) as ctx:
            migrate.validate_config(
                {
                    "ytdlp_path": "/bin/ls",
                    "output_dir": "~/x",
                    "playlists": [{"url": "u", "oops": 1}],
                    "extra": 2,
                }
            )
        message = str(ctx.exception)
        self.assertIn("unknown top-level key 'extra'", message)
        self.assertIn("unknown key 'playlists[0].oops'", message)


if __name__ == "__main__":
    unittest.main()
