# Meeting Platform Integration

**Status: Blocked/Unverified for Google Meet. Designed contract only.**

`MeetingAdapter` hides the platform. Capabilities are declared, not assumed:
```python
class AdapterCapabilities(TypedDict):
    per_participant_audio: bool
    participant_events: bool
    send_audio: bool
    send_video: bool
    delivery_confirmation: bool   # can we know audio was actually heard?
```
Runtime adapts: no `per_participant_audio` → diarization + stricter interview gating; no `delivery_confirmation` → treat speech as undelivered after disconnect.

Adapters: `LocalMeetingAdapter` (Phase 1–3, scripted/mic), `BrowserMeetAdapter` (proposed PoC, unofficial), `VendorBotAdapter` (optional spike), `MediaApiAdapter` (only if preview access). Findings and comparison: [GOOGLE_MEET_RESEARCH.md](GOOGLE_MEET_RESEARCH.md), [research/GOOGLE_MEET_OPTIONS.md](GOOGLE_MEET_OPTIONS.md). A mock adapter proves nothing about Meet.

The phased plan for the Meet adapter, speaker identification, calendar roster, teams, dashboard and search is [GOOGLE_MEET_INTEGRATION](GOOGLE_MEET_INTEGRATION.md). The command list with what carries over is [COMMANDS](../getting-started/COMMANDS.md).
