# Music Player and YouTube Fetch Logic

This document describes the expected behavior and operational safeguards in `slashcommands.py`.

## Playback UI

- The elapsed-time display and progress bar refresh every **10 seconds** while Discord reports that audio is playing.
- Pausing freezes the playback clock; pause/resume refreshes the player immediately.
- Seek-back and seek-forward buttons move the source position by 10 seconds. The controls are compact icon-only buttons (no `-10` / `+10` text labels); the requested custom emoji IDs are:
  - Forward 10 seconds: `1455985627714551839`
  - Back 10 seconds: `1455985625097306142`
- The player uses the custom emoji when it is available in the server or the bot has permission to use external emoji. Otherwise it falls back to Unicode controls so the buttons remain usable.
- The divider before the controls uses the shared `QUEUE_DIVIDER` constant so its length remains consistent.
- Playlist and Mix/Radio URLs use an explicit choice flow without preview tracks. Playlist choice shows two lines (`🎵 เลือกเพลงจาก Playlist` and `พบรายการเพลงใน Playlist นี้`) with `เล่นเพลงนี้เพลงเดียว`, `เลือกเพลงเพิ่มเติม`, and `ยกเลิก`. The Radio/Mix menu in the main player shows `เล่นเพลงนี้เท่านั้น`, `โหลดเพลงจาก Mix`, and a full-width `ยกเลิก` row. When a player is active, these controls render inside the same Components V2 player container; the standalone picker uses the same source-aware labels. If a URL identifies a specific video, the single-track action removes the playlist parameter and plays that video; for a playlist-only URL, it plays the first resolved entry. The count picker offers each of 5/10/20/30 that is less than or equal to the detected track count, plus the exact total for a non-standard count up to 30. `เพิ่มทั้งหมด` is capped at 50 tracks. Cancel stays on its own row.
- A seek replaces the FFmpeg audio source and increments the playback generation. The completion callback from the replaced source must not advance the queue.

## Queue and playlist behavior

- A playlist import is capped at `MAX_PLAYLIST_FETCH = 50` entries.
- The first playable entry is resolved first so playback can begin before the entire playlist has been processed.
- Remaining entries share one process-wide semaphore and a five-second delay to reduce request bursts.
- Playlist progress is an in-place console line. yt-dlp's raw stderr is suppressed so a fatal `ERROR: [youtube]...` line cannot overwrite the progress display; exceptions still propagate and are recorded in the guild log. A completed batch prints a summary; a batch invalidated by stop/disconnect must not print a false completion summary.
- The player queue page contains 10 tracks. Previous/next controls are disabled at the page boundaries.
- Previously played entries are retained in the queue up to `HISTORY_LIMIT = 10`; the player displays the latest three previous entries and up to three upcoming entries.

## YouTube anti-bot handling

The message `Sign in to confirm you're not a bot` means YouTube is refusing the current unauthenticated or suspicious request. Repeated retries and title-search fallbacks can increase request volume, so the bot follows a conservative policy:

1. Check the shared cooldown before starting an extraction.
2. On an anti-bot/login challenge, stop retrying alternate player clients and open a **10-minute process-wide cooldown**.
3. Do not perform a title-search replacement for an entry when the direct fetch was blocked by an anti-bot challenge.
4. During cooldown, count remaining playlist entries as skipped/cooldown instead of sending another extraction request.
5. If the playlist is stopped, disconnected, or invalidated by a new playback session, background workers check the playlist generation before making their next request. Stale workers return without starting additional fetches. A synchronous yt-dlp request already in progress cannot be forcibly cancelled and may finish, but queued workers will not start more requests.

This reduces unnecessary requests but cannot guarantee that YouTube will never block a host. If YouTube continues to reject requests, wait for the cooldown and configure valid authentication rather than increasing retries.

## Configure YouTube cookies (Windows)

The bot loads environment variables from `.env` via `python-dotenv`. Start with `.env.example` and copy the required settings into your local `.env`.

Choose one of the following optional authentication methods:

### Option A — browser profile on the same computer

Set a browser that is already signed in to YouTube:

```dotenv
YTDLP_COOKIES_FROM_BROWSER=edge
```

Supported examples include `edge`, `chrome`, `firefox`, `brave`, and `chromium`. The browser must be installed on the same machine that runs the bot. Depending on browser/OS locking and cookie encryption, extraction may fail; if so, close the browser and retry, or use Option B.

### Option B — exported cookies file (use if browser access fails)

Set a full path to a Netscape-format YouTube cookies file:

```dotenv
YTDLP_COOKIES_FILE=C:/discord-music-bot/cookies.txt
```

If the same bot-check keeps happening, refresh the export carefully:

1. Open a **private/incognito** browser window and sign in to YouTube.
2. In that private session, open YouTube and export only the required YouTube cookies using a reputable Netscape-format cookie exporter.
3. Close the private window immediately after exporting; do not reopen that session in the browser.
4. Save the file at the path configured in `YTDLP_COOKIES_FILE`, then restart the bot.

YouTube can rotate account cookies while sessions are active, so an exported file may stop working. Keep the bot and browser on the same network/IP where possible. If the error continues with a fresh cookie file, wait for the cooldown and inspect yt-dlp's verbose diagnostics; cookies cannot guarantee that a blocked IP/session will be accepted. See the [official yt-dlp cookie FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) and [YouTube cookie export notes](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

When both variables are set, `YTDLP_COOKIES_FILE` takes precedence. If the file is configured but missing or unreadable, yt-dlp will report a file error rather than silently falling back to anonymous access.

**Security requirements**

- Treat cookies as passwords: they can grant access to the signed-in YouTube session.
- Do not upload cookies to chat, commit them to Git, or share them with other people.
- The repository ignores files matching `*cookies*.txt`, but verify `git status` before every commit.
- Cookies can expire or be revoked; export a fresh file when necessary.
- Use an account you are comfortable using for this purpose and follow YouTube's terms.

## Bug-prevention checks

Before merging or deploying changes, verify:

- [ ] Python syntax check passes for `slashcommands.py`.
- [ ] With a valid YouTube URL, the bot starts playback and the elapsed display updates after 10 seconds.
- [ ] Pause/resume freezes and resumes the displayed elapsed time.
- [ ] Seek controls move exactly 10 seconds and do not cause the next track to start unexpectedly.
- [ ] A 20-track queue has two pages; previous/next controls disable at the correct boundaries.
- [ ] Stop/disconnect during playlist loading prevents stale workers from issuing more requests.
- [ ] Simulated `Sign in to confirm you're not a bot` opens cooldown, skips title fallback, and produces a readable user message.
- [ ] No cookie file or token appears in logs, commits, or repository files.

## Current limitations

- The cooldown is in-memory and resets when the bot process restarts.
- Browser-cookie access depends on the installed browser profile and operating-system permissions.
- A valid cookie file reduces authentication failures but cannot guarantee access to every video or prevent all YouTube rate limits.
- Code-level checks do not replace a live test in a Discord server and voice channel.
