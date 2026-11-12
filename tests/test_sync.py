import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sync


def completed(stdout: str, returncode: int = 0):
    class Completed:
        pass

    result = Completed()
    result.stdout = stdout
    result.returncode = returncode
    return result


class SanitizeDirNameTests(unittest.TestCase):
    def test_replaces_path_separators(self):
        self.assertEqual(sync.sanitize_dir_name("a/b\\c:d"), "a_b_c_d")

    def test_empty_becomes_playlist(self):
        self.assertEqual(sync.sanitize_dir_name("   "), "playlist")

    def test_strips_leading_trailing_dots(self):
        self.assertEqual(sync.sanitize_dir_name(".dj."), "dj")


class GetPlaylistsTests(unittest.TestCase):
    def test_tables(self):
        config = {"playlists": [{"name": "dj", "url": "u"}]}
        self.assertEqual(sync.get_playlists(config), [{"name": "dj", "url": "u"}])

    def test_urls(self):
        config = {"playlist_urls": ["u1", "u2"]}
        self.assertEqual(
            sync.get_playlists(config),
            [{"name": None, "url": "u1"}, {"name": None, "url": "u2"}],
        )

    def test_legacy_single(self):
        self.assertEqual(
            sync.get_playlists({"playlist_url": "u"}),
            [{"name": None, "url": "u"}],
        )


class ResolvePlaylistsTests(unittest.TestCase):
    def test_explicit_names_skip_subprocess(self):
        config = {
            "playlists": [
                {"name": "dj", "url": "u1"},
                {"name": "nov14", "url": "u2"},
            ]
        }
        with mock.patch.object(sync, "resolve_playlist_name") as stub:
            resolved = sync.resolve_playlists("/nonexistent/yt-dlp", config)
        stub.assert_not_called()
        self.assertEqual(resolved, [("dj", "u1"), ("nov14", "u2")])

    def test_collision_disambiguated_by_id(self):
        urls = [
            "https://x/playlist?list=AAA",
            "https://x/playlist?list=BBB",
        ]
        config = {"playlists": [{"url": urls[0]}, {"url": urls[1]}]}
        with mock.patch.object(sync, "resolve_playlist_name", return_value="dj"):
            resolved = sync.resolve_playlists("/nonexistent/yt-dlp", config)
        self.assertEqual(resolved, [("dj", urls[0]), ("dj-BBB", urls[1])])


class SelectPlaylistsTests(unittest.TestCase):
    def test_no_filter_returns_all(self):
        playlists = [("dj", "u1"), ("nov14", "u2")]
        self.assertEqual(sync.select_playlists(playlists, None), (playlists, []))

    def test_filters(self):
        playlists = [("dj", "u1"), ("nov14", "u2")]
        selected, missing = sync.select_playlists(playlists, ["nov14"])
        self.assertEqual(selected, [("nov14", "u2")])
        self.assertEqual(missing, [])

    def test_missing(self):
        selected, missing = sync.select_playlists([("dj", "u1")], ["nope"])
        self.assertEqual(selected, [])
        self.assertEqual(missing, ["nope"])


class ValidateConfigTests(unittest.TestCase):
    def test_valid(self):
        sync.validate_config(
            {
                "ytdlp_path": "/bin/ls",
                "output_dir": "~/x",
                "playlists": [{"name": "dj", "url": "u"}],
            }
        )

    def test_collects_all_errors(self):
        with self.assertRaises(sync.ConfigError) as ctx:
            sync.validate_config({"playlists": [{"name": 1}]})
        message = str(ctx.exception)
        self.assertIn("'ytdlp_path'", message)
        self.assertIn("'output_dir'", message)
        self.assertIn("playlists[0].url", message)
        self.assertIn("playlists[0].name", message)

    def test_no_playlist_source(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config({"ytdlp_path": "/bin/ls", "output_dir": "~/x"})

    def test_missing_ytdlp_path_on_disk(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(
                {
                    "ytdlp_path": "~/definitely/missing/yt-dlp",
                    "output_dir": "~/x",
                    "playlist_url": "u",
                }
            )

    def test_bad_retries(self):
        with self.assertRaises(sync.ConfigError):
            sync.validate_config(
                {
                    "ytdlp_path": "/bin/ls",
                    "output_dir": "~/x",
                    "playlist_url": "u",
                    "retries": 0,
                }
            )

    def test_unknown_top_level_key(self):
        with self.assertRaises(sync.ConfigError) as ctx:
            sync.validate_config(
                {
                    "ytdlp_path": "/bin/ls",
                    "output_dir": "~/x",
                    "playlist_url": "u",
                    "not_a_key": 1,
                }
            )
        self.assertIn("unknown top-level key 'not_a_key'", str(ctx.exception))

    def test_unknown_playlist_key(self):
        with self.assertRaises(sync.ConfigError) as ctx:
            sync.validate_config(
                {
                    "ytdlp_path": "/bin/ls",
                    "output_dir": "~/x",
                    "playlists": [{"name": "dj", "url": "u", "ntfy_topic": "x"}],
                }
            )
        self.assertIn("unknown key 'playlists[0].ntfy_topic'", str(ctx.exception))


class RetryHelperTests(unittest.TestCase):
    def test_get_retries_default_and_override(self):
        self.assertEqual(sync.get_retries({}), 3)
        self.assertEqual(sync.get_retries({"retries": 5}), 5)
        self.assertEqual(sync.get_retries({"retries": 0}), 3)

    def test_looks_transient(self):
        self.assertTrue(
            sync.looks_transient("ERROR: Unable to download webpage: HTTP Error 500")
        )
        self.assertFalse(sync.looks_transient("ERROR: [youtube] x: Video unavailable"))
        self.assertFalse(sync.looks_transient("ERROR: Sign in to confirm your age"))


class PlaylistTrackCountTests(unittest.TestCase):
    def test_counts_nonempty_lines(self):
        with mock.patch.object(sync, "run_capture", return_value=completed("a\n\nb\n")):
            self.assertEqual(sync.playlist_track_count("/x", "u"), 2)

    def test_unreachable_returns_negative(self):
        with mock.patch.object(
            sync, "run_capture", return_value=completed("", returncode=1)
        ):
            self.assertEqual(sync.playlist_track_count("/x", "u"), -1)


class PlaylistStatsTests(unittest.TestCase):
    def test_counts_files_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.flac").write_text("")
            (root / "b.mp3").write_text("")
            (root / "notes.txt").write_text("")
            (root / "archive.txt").write_text("youtube a\n\nyoutube b\n")
            files, archived = sync.playlist_stats(root)
        self.assertEqual((files, archived), (2, 2))

    def test_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sync.playlist_stats(Path(tmp) / "nope"), (0, 0))


if __name__ == "__main__":
    unittest.main()
