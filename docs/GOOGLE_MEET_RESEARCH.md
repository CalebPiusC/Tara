# Google Meet Research Summary (2026-10-19)

Full comparison: [research/GOOGLE_MEET_OPTIONS.md](research/GOOGLE_MEET_OPTIONS.md).

1. The official live-media API (Meet Media API) is **Developer Preview and closed to new signups**, documents **receiving** audio/video with per-participant source ids (CSRC), requires host/admin consent, and does not document sending media (S1, S2).
2. REST API gives post-meeting artifacts only (S3). Useful for reports and cross-checks.
3. Add-ons run UI inside Meet; no audio path established (S4).
4. Therefore a speaking Tara on Meet today most plausibly uses a **browser participant with virtual audio devices (unofficial)** or a **third-party bot API** (vendor claims, S14). Both need hands-on verification.
5. Recommended: develop on a local adapter; first Meet PoC = browser participant in a self-hosted test meeting.
Confidence: medium on the API facts (single-source fetches), low on PoC viability until tested.
