# Google Meet Integration Options

Researched 2026-10-19. Sources: [SOURCES.md](SOURCES.md) S1–S4, S14.

## Headline findings
1. **No documented official path lets an arbitrary app both hear and speak in a Meet.** The Meet Media API (S1) is the only official live-media API; it is **Developer Preview, closed to new signups**, receive-only as documented, and needs host/admin consent.
2. The Meet **REST API** (S3) is metadata and *post-meeting* artifacts (transcripts kept 30 days). It cannot give live audio.
3. **Add-ons SDK** (S4) embeds UI in Meet; the fetched page says nothing about audio.
4. A meeting URL is not enough: joining needs an admitted participant identity (signed-in account or guest requiring host admission).

## Option comparison

| Option | How it works | Officially supported | Receive media | Send media | Participant↔audio | Complexity | Hackathon fit |
|---|---|---|---|---|---|---|---|
| A. Meet Media API | WebRTC client, SDP offer, SFU virtual streams | Yes, but preview, **closed signup** (S1) | Audio+video; RTP CSRC maps stream→participant | Not documented (offer is receive-only, S2) | Yes (CSRC) | High (own WebRTC stack) | **Blocked** unless already enrolled |
| B. Meet REST API + Events | Poll conference records / transcripts after the fact | Yes (S3) | Post-meeting only | No | Participant list, not audio | Low | Good for *reports*, useless for live talk |
| C. Add-on | Web app in Meet side panel | Yes (S4) | Unknown; requires verification | Unknown | Unknown | Medium + Marketplace registration | Not a media path as far as verified |
| D. Browser participant (Playwright/Chrome joins as a signed-in account) with virtual audio devices | Real browser joins; capture tab/system audio; play TTS into a virtual mic (e.g. BlackHole/loopback) | **No** – automation of the consumer client; ToS/anti-bot risk unverified | Mixed audio (and captions DOM possible) | Yes via virtual mic/camera | Weak: mixed audio; names only via DOM scraping | Medium, fragile to UI changes | **Best local PoC**, explicitly unofficial |
| E. Third-party bot API (e.g. Recall.ai, S14) | Vendor bot joins, streams per-participant audio, plays Output Media | Vendor-supported (claims unverified) | Per-participant real-time audio, speaker timeline (vendor claim) | Yes via Output Media (vendor claim) | Yes (vendor claim) | Low–medium, paid, cloud-hosted vendor sees all audio | Fastest route to *real* identity; privacy + cost trade-off |
| F. Local loopback adapter (no Meet) | Mic/file as input, speakers as output | n/a | Local audio | Local audio | Simulated | Low | **Phase 1–3 development**; proves nothing about Meet |

## Recommendation (confidence: medium)
- **Develop against F** (a `LocalMeetingAdapter` with scripted participants) until the voice pipeline is measured.
- **First real-Meet PoC: D**, with a dedicated Google account and a test meeting you host. Treat it as unofficial and non-production.
- **Parallel spike: E** as the escape hatch if D cannot deliver participant attribution (D yields mixed audio only).
- **Apply for / check A only if** the team already has preview access; do not plan around it.
- Use **B** after the meeting to cross-check attribution (participant list, Meet transcript) when available.

## Must be verified by experiment
- Does a Chrome automation session get admitted and stay connected for 45 min?
- Can Tara's TTS play through a virtual mic without echo into capture (AEC)?
- Can participant names be scraped reliably from the Meet DOM (speaking indicators)? 
- Google Terms of Service on automated participants; consent notice requirements.
- Whether Media API preview enrollment is still possible for the team.
