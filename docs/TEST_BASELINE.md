# Test Baseline

> **Date:** 2026-11-12  
> **Branch:** `feature/ai-development`  
> **Base branch:** `dev`  
> **Base commit:** `059809861faf4677573ed869b2224e02743f675e`  
> **Purpose:** Record the repository's verification state before implementation work begins.

## 1. Repository test inventory

| Item | Baseline |
|---|---|
| Automated test directory | Not present |
| Test framework configuration | Not present |
| GitHub Actions workflow | Not present |
| Static syntax command | `python -m py_compile slashcommands.py bot.py` (specified in the implementation spec) |
| Playback regression coverage | Manual scenarios documented in `docs/QUEUE_PLAYBACK_HISTORY_SPEC.md` |
| Live Discord / VoiceClient verification | Pending; cannot be inferred from source inspection |

The current project documentation explicitly states that the automated `tests/` directory and GitHub Actions workflow were removed intentionally. The documented playback scenarios are manual acceptance checks and must not be reported as automated tests.

## 2. Baseline verification status

| Check | Status | Evidence / notes |
|---|---|---|
| Repository and branch inspected | Passed | Branch created from the current `dev` commit listed above |
| Test and workflow inventory | Passed | Repository tree contains no `tests/` directory or workflow files |
| Required syntax check | Not run | Requires executing the project in a Python environment |
| Automated unit/integration tests | Not run | No test suite is currently present |
| Manual Discord playback scenarios | Not run | Requires a running bot and a Discord server with voice access |

No runtime check is marked as passed based only on reading source files.

## 3. Existing verification requirements

The implementation specification requires the following syntax check before merge:

```powershell
python -m py_compile slashcommands.py bot.py
```

The playback specification documents 17 manual regression scenarios (F1–F17), including initial playback, navigation races, queue trimming, seek boundaries, repeat modes, stop/disconnect races, playlist loading, interaction errors, Queue pagination, and rapid Player interactions.

These scenarios remain manual until an automated harness is deliberately introduced. Live Discord layout, button callbacks, and VoiceClient behavior require separate verification in a real Discord environment.

## 4. Baseline findings to carry into later steps

- The project has no automated regression suite or CI workflow at this baseline.
- `slashcommands.py` contains the primary bot/player implementation; `bot.py` initializes the Discord bot and registers events.
- The repository's implementation specification and playback specification are the current sources for acceptance criteria.
- A source-level discrepancy exists for playlist fetch concurrency: implementation currently declares `PLAYLIST_FETCH_CONCURRENCY = 4`, while `docs/IMPLEMENTATION_SPEC.md` requires a process-wide maximum of 5. This is recorded for the Playback & Queue step and is intentionally not changed in this baseline task.

## 5. Exit criteria for this baseline step

- [x] Record the current branch and base commit.
- [x] Confirm whether automated tests and CI workflows exist.
- [x] Record the required syntax command and existing manual regression coverage.
- [x] Separate source inspection from checks that require runtime execution.
- [ ] Run the syntax check in the project's Python environment.
- [ ] Establish an executable automated test harness, if approved as part of the test strategy.

**Baseline conclusion:** The repository currently has documented manual regression scenarios but no automated test suite or CI workflow. Runtime verification is still pending; no claim is made that the current code passes the syntax or playback checks.
