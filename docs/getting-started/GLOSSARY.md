# Glossary

- **Organizer** – human who supplies context and policy.
- **MeetingConfig** – validated, versioned structured form of the organizer context.
- **Participant** – a meeting-platform identity (display name, platform id).
- **Audio stream identity** – which stream/track/CSRC a chunk arrived on.
- **Diarization label** – anonymous cluster id like `SPEAKER_00`; *not* a person.
- **SpeakerAttribution** – per-utterance claim `{participant_id|null, confidence, evidence}`.
- **Utterance** – one VAD-bounded span of speech with one attribution.
- **Turn** – a floor-holding period, possibly several utterances.
- **Proposal** – model suggestion (e.g. "respond with X"). **Approved action** – a proposal the runtime re-validated against current state.
- **Staleness** – a proposal whose preconditions (floor, topic, epoch) changed since creation.
- **Floor** – the right to speak at the moment.
- **Barge-in** – a human speaks while Tara is speaking.
- **Human takeover** – a human explicitly (or via policy) takes control; Tara yields.
- **Epoch** – monotonic counter bumped on interruption/takeover/reconnect; proposals carry the epoch they were made in.
- **Delivered audio** – audio confirmed played into the meeting (distinct from synthesized).
- **Status labels** – Implemented · Partially implemented · Designed not implemented · Proposed · Researched not selected · Unverified · Blocked · Future.
