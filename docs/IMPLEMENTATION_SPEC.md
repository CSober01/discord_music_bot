# Discord Music Bot — Implementation Specification

> **Document ID:** DMB-IMPL-SPEC  
> **Document Version:** 1.0.0  
> **Status:** Approved requirements baseline (implementation pending)  
> **Last Updated:** 2026-10-10  
> **Target Branch:** `fix/clean-player-title`  
> **Scope:** Playlist loading, loading-status refresh, Main Player volume control, History/Up Next rendering, and regression acceptance criteria.

This is the implementation source of truth for the requirements listed here. If older notes in `PLAYER_LOGIC.md` or `QUEUE_PLAYBACK_HISTORY_SPEC.md` conflict with this document on these topics, follow this document. Do not infer undocumented behavior; preserve existing behavior outside this scope.

## 1. Versioning and change history

Use Semantic Versioning for this document:
- **MAJOR**: a breaking change to an approved behavior or interface contract.
- **MINOR**: a new requirement or a material behavior/design change that remains compatible.
- **PATCH**: clarification, typo correction, or a non-behavioral detail.

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-10-10 | Establishes explicit implementation requirements for playlist worker concurrency, loading status timing, volume control UI, History/Up Next rendering, and acceptance tests. |

When requirements change, update this table and the version at the top in the same documentation change.

## 2. Non-negotiable implementation rules

1. Use supported `discord.py` / Discord Components V2 APIs only. Do not simulate unsupported HTML/CSS layouts.
2. A visible speaker control must remain a real clickable Discord button. Never put the speaker glyph only in ordinary text as a substitute for the button.
3. Do not use Unicode padding as the only correctness mechanism for a fixed-width text column. Discord clients use proportional fonts and wrap text differently.
4. Playlist concurrency means resolving/fetching track metadata for adding tracks to the queue. It does **not** mean playing multiple audio tracks simultaneously.
5. Preserve playlist order even when fetches complete out of order.
6. Do not let stale background work add tracks after Stop, disconnect, or replacement by a newer playlist/playback generation.
7. Do not claim a check passed unless it was actually run. Static checks and live Discord/VoiceClient tests must be reported separately.

## 3. Playlist loading and queue insertion

### 3.1 Limits and concurrency

- `MAX_PLAYLIST_FETCH` remains **50 entries per import**.
- The first playable entry is resolved with priority so playback can start promptly.
- Remaining playlist entries use a continuous worker pool with **at most 5 in-flight yt-dlp extractions globally across the process**, shared across all guilds.
- The concurrency limit applies to fetching/resolving entries for queue insertion. It must not create concurrent audio playback.
- Successful entries are retained in their original playlist order, regardless of fetch completion order.
- Follow the existing queue consistency contract: append the successful remainder to the queue once after all remaining fetch attempts finish. Do not append results in worker-completion order or repeatedly mutate the queue as individual workers finish.
- Failed, unavailable, duplicate-if-current-logic-deduplicates, or cooldown-skipped entries must be accounted for using the existing result categories. Do not invent substitute tracks for an entry blocked by an anti-bot challenge.
- Retain the existing shared anti-bot/rate-limit cooldown and configured yt-dlp request pacing. Increasing worker concurrency must not remove those safeguards.

### 3.2 Lifecycle and race safety

- Each playlist load is tied to a session/generation token.
- Before starting a queued extraction and before applying its results, verify that the load is still current.
- After Stop, voice disconnect, or a newer playback/playlist session, stale workers must not start further queued requests or append stale results. A synchronous extraction already in progress may finish if yt-dlp cannot cancel it; its result must still be discarded when stale.
- Worker exceptions must be caught and counted so the loader can finish cleanup and remove its loading indicator.
- Queue state, Player state, and open Queue views must be refreshed from the same authoritative queue state. Do not construct a second queue with different numbering/order for the UI.

## 4. Playlist loading status

### 4.1 Required behavior

- When background playlist loading starts, show the loading status in the Main Player, using the existing style, for example:
  `⏳ กำลังโหลดเพลงเพิ่มเติม · 1/50`
- The counter represents entries whose fetch attempt has completed, including failed/skipped entries, divided by the number of entries selected for this import. It is progress information, not the number of tracks already appended to Queue.
- After the initial status is shown, refresh the progress status **on a 10-second timer** while loading continues.
- Remove all progress-update triggers based on every 5 completed tracks or every 10 completed tracks. Do not refresh the status because a particular number of entries has completed.
- Refresh immediately for lifecycle transitions that require correctness: loading starts, loading completes/fails, or the load is cancelled/invalidated. These are not count-based progress updates.
- On completion, clear the temporary loading line and refresh Player/Queue from current state.
- Do not show a loading line when no background work is required.
- A loading status from an older generation must never appear in a newer Player session.

### 4.2 Timer rules

- Use one owned refresh task per active loading operation (or a clearly shared task with equivalent ownership guarantees).
- Ensure the task exits and is cancelled/awaited during completion, failure, Stop, disconnect, and replacement by a newer generation.
- Do not create duplicate 10-second loops on repeated Player refreshes.
- Keep the playback elapsed-time refresh (every 10 seconds while audio is playing) logically separate from playlist-loading progress, even if both cause a Player render.

## 5. Main Player volume control

### 5.1 Desired appearance and behavior

The desired visual order is:

`[🔊 button]  ▰▱▱▱▱▱▱▱▱▱  10%`

- The speaker glyph is a **separate, icon-only, clickable button**.
- The meter and percentage are ordinary text and reflect the current volume value.
- The button opens the existing `VolumeModal` and must preserve existing volume adjustment behavior.
- Meter length is 10 slots. Percentage must be derived from the actual volume value, clamped to the valid supported range, and formatted consistently.
- A Player refresh must not replace the speaker button with a text glyph or break its callback.

### 5.2 Discord Components V2 layout constraint and required fallback

Discord Components V2 does not provide a general-purpose horizontal text-and-button layout with arbitrary left/right alignment. A `Section` accessory is placed by Discord on the right side; it cannot be relied upon as a left-side accessory. Do not claim that a button placed as a Section accessory is visually before the text.

Implementation priority:
1. Keep the speaker as a real separate button and keep the meter/percentage accurate.
2. Use only supported Components V2 structures.
3. If the exact same-line, button-first arrangement cannot be achieved with the installed discord.py and Discord API, use the nearest supported layout and document the actual result. Do not embed `🔊` in the meter text to fake compliance.
4. Before claiming the exact desired arrangement is achieved, verify it in a real Discord client. A code-level layout object alone does not prove visual placement.

## 6. History and Up Next presentation

### 6.1 Content contract

- Render the latest **three previous tracks** under `HISTORY · ประวัติเพลง`, newest first.
- Render up to **three upcoming tracks** under `UP NEXT · เพลงถัดไป`.
- Do not include the current track in either list.
- Display-local row numbering begins at `01` and continues from History into Up Next. If both sections contain three entries, the visible row numbers are `01–03` for History and `04–06` for Up Next.
- These display-local numbers are not logical Queue numbers and must not be generated by `display_no()`.
- When a safe YouTube source-page URL exists, the track title must be a Markdown link to that page. Never link to a temporary FFmpeg stream URL. If no safe page URL exists, show the title as plain text.
- Display duration when known. For unknown/live duration, use the project's established live/unknown label; do not invent a numeric duration.
- Preserve safe URL handling and the existing title truncation policy.

### 6.2 Re-rendering and state freshness

After a track transition, Previous/Next, queue mutation that changes the visible upcoming list, or history mutation:
1. Read History, Current, and Upcoming from the authoritative live queue state.
2. Build the visible rows and local numbering from that snapshot.
3. Replace the rendered Player layout and synchronize the new component/button state in the established safe order.
4. Ensure old views/buttons remain dispatchable for the duration of the edit hand-off, following the current per-guild serialization safeguards.
5. Refresh open Queue views where applicable.

A refresh must not reuse stale title/duration strings from the previous render. Repeated refreshes must not duplicate rows or numbering.

### 6.3 Alignment: achievable behavior vs. mockup

Discord Components V2 is not an HTML/CSS grid and does not expose a general fixed-width text column with a right-aligned duration while keeping arbitrary Markdown links in the neighboring text cell. Proportional fonts, wrapping, and client differences make figure-space padding unreliable.

Required presentation decision:
- Keep each track title clickable when a safe URL exists.
- Do not use long runs of Unicode spaces as the correctness mechanism for duration alignment.
- Prefer a robust row layout that places the title/link and duration in distinct, clearly readable text segments. If a true aligned column cannot be implemented with supported Discord components, put the duration on a separate line for each track rather than claiming fixed-column alignment.
- A code block may align columns but makes the embedded title link non-clickable; therefore it is not an acceptable replacement when clickable titles are required.
- Any stronger alignment solution must be demonstrated in a real Discord client and must preserve clickable titles. Document platform/client limitations rather than promising pixel-perfect alignment.

### 6.4 Example content hierarchy

The following is a semantic example, not a promise of fixed pixel columns:

```text
HISTORY · ประวัติเพลง
01  ♫ PREP - Who's Got You Singing Again (Official Video)
    เวลา 4:50
02  ♫ pami - pity dirty (Official Video)
    เวลา 3:22

UP NEXT · เพลงถัดไป
03  ♫ PREP - Line By Line feat. Cory Wong & Paul Jackson jr
    เวลา 3:58
04  ♫ PREP - Cheapest Flight
    เวลา 4:29
```

In the actual Discord message, titles with safe source-page URLs remain Markdown links. The example prioritizes readability and correctness over fake fixed-width alignment.

## 7. Discord compatibility requirements

- Use the repository's supported `discord.py` version and only component classes supported by that version.
- Main Player uses `discord.ui.LayoutView` / Components V2 per the current project architecture.
- Do not combine `content=` or `embed=` with a Components V2 `LayoutView` message when the API disallows that combination; place content inside supported V2 components.
- Respect Discord's component-count and layout constraints. If History/Up Next rows would exceed limits, reduce visible content only according to the defined 3+3 limit; do not create unsupported pseudo-columns.
- All component callbacks must continue to work after layout rebuilds and message reposts.
- Do not change unrelated Queue/history semantics, playback controls, anti-bot protections, or menu flows while implementing this specification.

## 8. Acceptance criteria

### Playlist loader

- [ ] A playlist import never selects more than 50 entries.
- [ ] At most 5 yt-dlp extraction tasks are in flight process-wide, including across multiple guilds.
- [ ] The first playable entry can start without waiting for every remaining entry to resolve.
- [ ] Out-of-order fetch completion still produces queue insertion in original playlist order.
- [ ] Successful remaining entries are appended once after the remaining fetch attempts finish.
- [ ] Stop/disconnect/new-generation invalidation prevents stale results from being appended and prevents queued stale workers from starting new requests.
- [ ] A worker exception does not strand the loading line or leave the refresh task running.

### Loading status

- [ ] Status appears when background loading starts and includes completed-attempt count / selected count.
- [ ] Progress status refreshes on a 10-second timer while loading continues.
- [ ] No status refresh is triggered specifically at 5 or 10 completed tracks.
- [ ] Completion, failure, cancellation, and invalidation clear or replace the status immediately.
- [ ] No duplicate 10-second refresh loops are created.

### Volume control

- [ ] Speaker remains an icon-only clickable button and opens the existing volume modal.
- [ ] Meter has 10 slots and its percentage matches the actual volume.
- [ ] No text-only speaker glyph is used as a substitute for the button.
- [ ] Actual placement is checked in Discord. If exact button-first same-line placement is unsupported, the documented fallback is used and the limitation is reported honestly.

### History / Up Next

- [ ] History contains at most three previous tracks, newest first; Up Next contains at most three upcoming tracks.
- [ ] Current is excluded from both sections.
- [ ] Display-local numbers are continuous across visible History and Up Next rows and begin at 01.
- [ ] Safe YouTube page links remain clickable; stream URLs are never used as title links.
- [ ] Titles and durations refresh after track changes and relevant queue mutations; no stale or duplicated rows remain.
- [ ] Duration readability does not depend on long Unicode-space padding.
- [ ] If fixed columns cannot be achieved using supported components, duration is displayed in the documented readable fallback.

## 9. Verification and reporting

Before merging or deploying:
1. Run `python -m py_compile slashcommands.py bot.py`.
2. Inspect the code diff for stale 4-worker limits, count-based progress triggers (every 5/10 entries), duplicate timer tasks, and outdated duration-padding logic.
3. Test concurrency with instrumented/fake fetches and verify the process-wide maximum is 5 and insertion order remains stable.
4. Manually test a short playlist, a 50-entry playlist, a failure midway through loading, Stop/disconnect during loading, and two guilds loading concurrently.
5. In a real Discord server/client, verify the loading status cadence, volume button callback and actual placement, clickable track links, History/Up Next refreshes, and long Thai/Latin titles.
6. Report each check as **passed**, **failed**, **not run**, or **requires live Discord verification**. Do not label manual scenarios as automated tests.

**Verification status for this document:** requirements written; implementation and runtime behavior have not been verified by this document change.
