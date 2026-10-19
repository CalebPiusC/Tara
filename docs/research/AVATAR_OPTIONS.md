# Avatar Options (future capability — nothing implemented or tested)

Candidates named in the brief: MuseTalk, LivePortrait. **Not researched from primary sources in this phase**; licensing, hardware, latency, and quality are all "Unknown; requires verification."

Architecture constraints we can state independent of model:
- Avatar is an optional consumer of `AvatarProvider` events (see [API_AND_EVENT_CONTRACTS.md](../API_AND_EVENT_CONTRACTS.md)); core must run with `NullAvatarProvider`.
- Inputs: TTS audio chunks with `utterance_id`, `start_ts`, planned duration; state events (`speaking_started`, `interrupted`, `yielded`).
- Output path: video frames → virtual camera → meeting platform (needs the same media path as audio; unverified for Meet, see Q1).
- Sync rule: audio clock is master; video drops/holds frames to follow it; `interrupted` must flush video within a bound to be measured.
- Real-time diffusion/reenactment on 24 GB unified memory shared with STT+TTS is a risk to measure, not assume.
