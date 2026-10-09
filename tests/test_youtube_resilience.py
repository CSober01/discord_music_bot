"""Regression tests for YouTube anti-bot handling and playlist progress."""

import io
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import call, patch

import slashcommands as sc


class YouTubeResilienceTests(unittest.TestCase):
    def setUp(self):
        self.old_cooldown = sc._youtube_anti_bot_until
        sc._youtube_anti_bot_until = 0.0

    def tearDown(self):
        sc._youtube_anti_bot_until = self.old_cooldown

    def test_detects_youtube_anti_bot_error(self):
        error = RuntimeError(
            "ERROR: [youtube] Sign in to confirm you're not a bot"
        )
        self.assertTrue(sc._is_youtube_anti_bot_error(error))

    def test_fetch_track_tries_alternative_client_only_after_anti_bot(self):
        result = ("stream-url", "Track title", "3:22", "thumbnail")
        with patch.object(
            sc,
            "_fetch_track_once",
            side_effect=[ValueError("Sign in to confirm you're not a bot"), result],
        ) as extract, patch.object(sc.time, "sleep"):
            self.assertEqual(sc.fetch_track("https://youtu.be/example"), result)

        self.assertEqual(
            extract.call_args_list,
            [
                call("https://youtu.be/example", player_client=None),
                call("https://youtu.be/example", player_client="tv"),
            ],
        )

    def test_regular_error_does_not_trigger_client_fallback(self):
        with patch.object(
            sc, "_fetch_track_once", side_effect=ValueError("Private video")
        ) as extract:
            with self.assertRaisesRegex(ValueError, "Private video"):
                sc.fetch_track("https://youtu.be/private")
        extract.assert_called_once_with(
            "https://youtu.be/private", player_client=None
        )

    def test_open_circuit_breaker_prevents_new_extraction(self):
        sc._youtube_anti_bot_until = time.monotonic() + 120
        with patch.object(sc, "_fetch_track_once") as extract:
            with self.assertRaisesRegex(ValueError, "YOUTUBE_ANTI_BOT_COOLDOWN"):
                sc.fetch_track("https://youtu.be/blocked")
        extract.assert_not_called()

    def test_playlist_does_not_search_title_after_anti_bot(self):
        track = {"id": "CwGbMYLjIpQ", "title": "Blocked title"}
        with patch.object(
            sc, "fetch_track", side_effect=ValueError("YOUTUBE_ANTI_BOT")
        ) as extract, patch.object(sc, "glog"):
            result = sc._fetch_playlist_track_sync(track, 123, "Test guild")

        self.assertEqual(result, (None, "anti_bot"))
        extract.assert_called_once_with(
            "https://www.youtube.com/watch?v=CwGbMYLjIpQ"
        )

    def test_progress_counts_anti_bot_items_without_raw_error_text(self):
        progress = sc._PlaylistFetchProgress(123, "Test guild", total=1)
        output = io.StringIO()
        with redirect_stdout(output):
            progress.record("anti_bot")

        self.assertEqual(progress.done, 1)
        self.assertEqual(progress.skipped, 1)
        self.assertEqual(progress.anti_bot_blocks, 1)
        self.assertIn("YouTube จำกัด 1", output.getvalue())
        self.assertNotIn("ERROR: [youtube]", output.getvalue())

    def test_queue_page_size_and_playlist_cap(self):
        self.assertEqual(sc.QUEUE_PAGE_SIZE, 10)
        self.assertEqual(sc.MAX_PLAYLIST_FETCH, 50)


    def test_playlist_listing_anti_bot_opens_shared_circuit_breaker(self):
        class BlockedYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def extract_info(self, query, download=False):
                raise RuntimeError("Sign in to confirm you're not a bot")

        with patch.object(sc.yt_dlp, "YoutubeDL", BlockedYoutubeDL):
            with self.assertRaisesRegex(ValueError, "YOUTUBE_ANTI_BOT"):
                sc.fetch_playlist_tracks(
                    "https://www.youtube.com/playlist?list=PL123"
                )

        self.assertTrue(sc._is_youtube_cooldown_active())

    def test_search_short_circuits_while_circuit_breaker_is_open(self):
        sc._youtube_anti_bot_until = time.monotonic() + 120
        with patch.object(sc.yt_dlp, "YoutubeDL") as youtube_dl:
            with self.assertRaisesRegex(ValueError, "YOUTUBE_ANTI_BOT_COOLDOWN"):
                sc.search_tracks("test search")
        youtube_dl.assert_not_called()

    def test_search_anti_bot_opens_shared_circuit_breaker(self):
        class BlockedYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def extract_info(self, query, download=False):
                raise RuntimeError("Sign in to confirm you're not a bot")

        with patch.object(sc.yt_dlp, "YoutubeDL", BlockedYoutubeDL):
            with self.assertRaisesRegex(ValueError, "YOUTUBE_ANTI_BOT"):
                sc.search_tracks("blocked search")

        self.assertTrue(sc._is_youtube_cooldown_active())


if __name__ == "__main__":
    unittest.main()
