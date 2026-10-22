# Phase 0 Spike: Browser Participant in Google Meet (outline — NOT yet run)

**Status: Proposed. No part of this has been executed.** Goal: decide in a time-boxed session (suggest half a day) whether Option D in [GOOGLE_MEET_OPTIONS.md](GOOGLE_MEET_OPTIONS.md) can hear and speak. Result is a yes/no plus measurements recorded in this file.

## Hypotheses
H1 An automated Chrome session using a dedicated test Google account can join and be admitted to a meeting we host. H2 We can capture the meeting's incoming audio. H3 We can play audio into the meeting as the microphone without echoing into capture. H4 Participant names and a speaking indicator are readable from the page. H5 The session stays connected ≥10 min.

## Setup (to verify; commands intentionally not written until tested)
- Two Google accounts: host (human, own browser) and Tara test account. Use only meetings you own and only participants who know it is an AI test.
- macOS virtual audio loopback driver (e.g. BlackHole) to route TTS → virtual mic and meeting audio → capture. Needs install + Chrome mic permission; **needs your approval before installing**.
- Playwright (Chromium) or real Chrome with a persistent profile signed in by hand once (never script the password entry).

## Steps
1. Host a meeting; Tara account joins via link; host admits. Record: prompts, captchas, "Ask to join" flow.
2. Capture audio: record 60 s of host speaking; save WAV locally (delete after).
3. Play a pre-recorded WAV into the virtual mic; host confirms audibility and quality.
4. Echo test: play while host is silent; check whether the capture contains the played audio.
5. Barge-in: host speaks mid-playback; measure time from host onset to playback stop with a stopwatch harness.
6. Read DOM for participant list and active-speaker indicator; compare with ground truth over 5 min of scripted turns (3 speakers if possible).
7. Leave connected 10 min; kill network 20 s; observe rejoin behavior.

## Record
| Item | Result | Notes |
|---|---|---|
| H1–H5 yes/no | | |
| Join friction / admission | | |
| Audio sample rate / channels captured | | |
| Echo present? | | |
| Names + speaking indicator reliability | | |
| ToS/automation warnings seen | | |

## Decision rule
H1–H3 pass → build `BrowserMeetAdapter`, plan diarization (H4 fail) or hints (H4 pass). H1 or H3 fail → run vendor-bot spike (Option E). Both fail → ship with `LocalMeetingAdapter` demo and document the blocker.
## Risks
Account restrictions or bans for automation; UI changes; Google terms (unverified); privacy of recorded test audio.
