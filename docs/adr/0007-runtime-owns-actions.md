# ADR-0007: Models propose, runtime approves
**Status:** Proposed
**Decision:** only MeetingRuntime may start playback or tools; proposals carry `epoch` and trigger id; checked at approval and playback start. **Reasons:** staleness, takeover, duplicates (Example G). **Consequences:** extra state to maintain. **Validation:** staleness/takeover/dedupe tests.
