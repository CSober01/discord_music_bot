"""Regression tests for YouTube anti-bot handling and playlist progress."""

import asyncio
import io
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

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

    def test_detects_temporary_youtube_rate_limit_messages(self):
        messages = (
            "HTTP Error 429: Too Many Requests",
            "This content isn't available, try again later",
            "YouTube rate limit exceeded",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(sc._is_youtube_anti_bot_error(RuntimeError(message)))

    def test_spotify_playlist_entries_share_youtube_circuit_breaker_path(self):
        result = ("stream-url", "Song", "3:30", "thumbnail")
        track = {"title": "Song", "artist": "Artist"}
        with patch.object(sc, "fetch_track", return_value=result) as fetch, patch.object(sc, "glog"):
            self.assertEqual(
                sc._fetch_playlist_track_sync(track, 123, "Test guild"),
                (result, "direct"),
            )
        fetch.assert_called_once_with("Song Artist")

    def test_spotify_direct_lookup_preserves_youtube_anti_bot_error(self):
        class BlockedYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def extract_info(self, query, download=False):
                raise RuntimeError("Sign in to confirm you're not a bot")

        with patch.object(sc, "extract_spotify_track_id", return_value="spotify-id"), \
             patch.object(sc, "get_spotify_track_info", return_value={"title": "Song", "artist": "Artist"}), \
             patch.object(sc.yt_dlp, "YoutubeDL", BlockedYoutubeDL):
            with self.assertRaisesRegex(RuntimeError, "Sign in to confirm"):
                sc._fetch_track_once("https://open.spotify.com/track/spotify-id")

    def test_cooldown_skips_are_counted_separately_from_actual_blocked_requests(self):
        progress = sc._PlaylistFetchProgress(123, "Test guild", total=2)
        progress.record("anti_bot")
        progress.record("cooldown")
        self.assertEqual(progress.anti_bot_blocks, 1)
        self.assertEqual(progress.cooldown_skipped, 1)
        self.assertEqual(progress.skipped, 2)

    def test_ydl_options_use_safe_request_pacing_and_ten_second_player_refresh(self):
        options = sc.get_ydl_options()
        self.assertEqual(options["sleep_interval_requests"], 1.0)
        self.assertEqual(sc.PLAYER_PROGRESS_INTERVAL_SECONDS, 10)
        self.assertEqual(sc.PLAYLIST_FETCH_CONCURRENCY, 1)
        self.assertEqual(sc.PLAYLIST_TRACK_FETCH_DELAY_SECONDS, 5.0)
        self.assertEqual(sc.get_youtube_playlist_fetch_semaphore()._value, 1)
        with patch.dict("os.environ", {"YTDLP_SLEEP_REQUESTS": "2.5"}):
            self.assertEqual(sc.get_ydl_options()["sleep_interval_requests"], 2.5)
        with patch.dict("os.environ", {"YTDLP_SLEEP_REQUESTS": "invalid"}):
            self.assertEqual(sc.get_ydl_options()["sleep_interval_requests"], 1.0)

    def test_quiet_ytdlp_suppresses_raw_stderr_but_preserves_exceptions(self):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def to_stderr(self, message):
                print(message, file=sys.stderr)

            def extract_info(self, query, download=False):
                raise RuntimeError("Sign in to confirm you're not a bot")

        with patch.object(sc.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            ydl = sc._quiet_ytdlp({})
            output = io.StringIO()
            with redirect_stderr(output):
                ydl.to_stderr("ERROR: [youtube] noisy message")
            self.assertEqual(output.getvalue(), "")
            with self.assertRaisesRegex(RuntimeError, "Sign in to confirm"):
                ydl.extract_info("https://youtu.be/blocked")

    def test_cookie_options_support_browser_and_prefer_explicit_file(self):
        with patch.dict("os.environ", {"YTDLP_COOKIES_FROM_BROWSER": "edge"}, clear=True):
            options = sc.get_ydl_options()
            self.assertEqual(options["cookiesfrombrowser"], ("edge", None, None, None))
            self.assertNotIn("cookiefile", options)

        cookie_path = "C:/discord-music-bot/cookies.txt"
        with patch.dict(
            "os.environ",
            {
                "YTDLP_COOKIES_FILE": cookie_path,
                "YTDLP_COOKIES_FROM_BROWSER": "edge",
            },
            clear=True,
        ):
            options = sc.get_ydl_options()
            self.assertEqual(options["cookiefile"], cookie_path)
            self.assertNotIn("cookiesfrombrowser", options)

    def test_fetch_track_opens_circuit_breaker_without_retry_after_anti_bot(self):
        query = "https://youtu.be/blocked"
        with patch.object(
            sc, "_fetch_track_once",
            side_effect=RuntimeError("Sign in to confirm you're not a bot"),
        ) as extract:
            with self.assertRaisesRegex(ValueError, "YOUTUBE_ANTI_BOT"):
                sc.fetch_track(query)

        extract.assert_called_once_with(query)
        self.assertTrue(sc._is_youtube_cooldown_active())
        self.assertGreaterEqual(
            sc._youtube_anti_bot_until - time.monotonic(),
            sc._YOUTUBE_ANTI_BOT_COOLDOWN_SECONDS - 1,
        )

    def test_fetch_track_does_not_retry_after_anti_bot(self):
        query = "https://youtu.be/example"
        with patch.object(
            sc, "_fetch_track_once",
            side_effect=ValueError("Sign in to confirm you're not a bot"),
        ) as extract:
            with self.assertRaisesRegex(ValueError, "YOUTUBE_ANTI_BOT"):
                sc.fetch_track(query)
        extract.assert_called_once_with(query)

    def test_regular_error_does_not_trigger_retry(self):
        query = "https://youtu.be/private"
        with patch.object(
            sc, "_fetch_track_once", side_effect=ValueError("Private video")
        ) as extract:
            with self.assertRaisesRegex(ValueError, "Private video"):
                sc.fetch_track(query)
        extract.assert_called_once_with(query)

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

    def test_playlist_rate_limit_does_not_trigger_title_search_fallback(self):
        track = {"id": "CwGbMYLjIpQ", "title": "Blocked title"}
        with patch.object(
            sc, "fetch_track", side_effect=ValueError("HTTP Error 429: Too Many Requests")
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

    def test_playlist_url_ordinary_failure_uses_title_fallback(self):
        track = {
            "url": "https://www.youtube.com/watch?v=example",
            "title": "Replacement Song",
        }
        result = ("stream-url", "Replacement Song", "3:30", "thumbnail")
        with patch.object(
            sc, "fetch_track",
            side_effect=[ValueError("Private video"), result],
        ) as fetch, patch.object(sc, "glog"):
            actual = sc._fetch_playlist_track_sync(track, 123, "Test guild")

        self.assertEqual(actual, (result, "fallback_ok"))
        self.assertEqual(fetch.call_count, 2)
        fetch.assert_any_call(track["url"])
        fetch.assert_any_call("Replacement Song")

    def test_playlist_url_anti_bot_does_not_use_title_fallback(self):
        track = {
            "url": "https://www.youtube.com/watch?v=blocked",
            "title": "Blocked Song",
        }
        with patch.object(
            sc, "fetch_track", side_effect=ValueError("YOUTUBE_ANTI_BOT")
        ) as fetch, patch.object(sc, "glog"):
            actual = sc._fetch_playlist_track_sync(track, 123, "Test guild")

        self.assertEqual(actual, (None, "anti_bot"))
        fetch.assert_called_once_with(track["url"])

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


class StalePlaylistWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalidated_playlist_workers_do_not_fetch_or_print_false_summary(self):
        class FakeGuild:
            id = 987654
            name = "Test guild"

        guild = FakeGuild()
        progress = sc._PlaylistFetchProgress(guild.id, guild.name, total=2)
        previous_generation = sc.playlist_fetch_generation.get(guild.id)
        sc.playlist_fetch_generation[guild.id] = 2
        sc.playlist_loading_status[guild.id] = progress
        output = io.StringIO()
        try:
            with patch.object(
                sc, "get_youtube_playlist_fetch_semaphore", return_value=asyncio.Semaphore(1)
            ), patch.object(sc, "_fetch_playlist_track_sync") as fetch, \
                 patch.object(sc, "_refresh_player", new_callable=AsyncMock) as refresh, \
                 redirect_stdout(output):
                await sc._bg_fetch_rest(
                    guild, None, [{"id": "one"}, {"id": "two"}],
                    None, progress, fetch_token=1,
                )

            fetch.assert_not_called()
            refresh.assert_awaited_once_with(guild.id)
            self.assertNotIn("โหลดครบ", output.getvalue())
            self.assertNotIn(guild.id, sc.playlist_loading_status)
        finally:
            sc.playlist_loading_status.pop(guild.id, None)
            if previous_generation is None:
                sc.playlist_fetch_generation.pop(guild.id, None)
            else:
                sc.playlist_fetch_generation[guild.id] = previous_generation



class QueueHistoryTests(unittest.TestCase):
    """Regression coverage for rolling history trimming without dropping upcoming tracks."""

    def setUp(self):
        self.guild_id = 987653
        self.saved = {}
        for name in ("full_queues", "now_playing_idx", "queue_seq_offset", "guild_total_added"):
            mapping = getattr(sc, name)
            self.saved[name] = (self.guild_id in mapping, mapping.get(self.guild_id))
        sc.full_queues[self.guild_id] = [
            (f"url-{i}", f"Track {i + 1}", "3:00", None, None)
            for i in range(14)
        ]
        sc.now_playing_idx[self.guild_id] = 11
        sc.queue_seq_offset[self.guild_id] = 0
        sc.guild_total_added[self.guild_id] = 14

    def tearDown(self):
        for name, (existed, value) in self.saved.items():
            mapping = getattr(sc, name)
            if existed:
                mapping[self.guild_id] = value
            else:
                mapping.pop(self.guild_id, None)
        sc.queue_add_msgs.pop(self.guild_id, None)

    def test_trim_history_keeps_current_and_all_upcoming_tracks(self):
        sc._trim_queue(self.guild_id)
        queue = sc.get_full_queue(self.guild_id)

        self.assertEqual(len(queue), 13)
        self.assertEqual(queue[sc.get_now_idx(self.guild_id)][1], "Track 12")
        self.assertEqual(sc.get_now_idx(self.guild_id), 10)
        self.assertEqual(sc.get_seq_offset(self.guild_id), 1)
        self.assertEqual(sc.display_no(self.guild_id, sc.get_now_idx(self.guild_id)), 12)
        self.assertEqual([track[1] for track in queue[-2:]], ["Track 13", "Track 14"])

if __name__ == "__main__":
    unittest.main()
