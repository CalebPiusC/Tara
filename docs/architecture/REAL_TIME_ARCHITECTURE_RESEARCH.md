# Real-time Architecture Research

Mostly design reasoning; no external benchmarks fetched. Verify claims with prototypes.

- **asyncio** suits network I/O (adapter, Gemini, WebSocket) and timers. **Not** CPU inference: run STT/TTS in worker threads/processes (ML libs usually release the GIL but check) behind bounded queues.
- **Internal transport:** in-process typed event bus (asyncio queues). WebSocket only for an external control/observability API; no broker. Reason: single machine, one process ([ADR-0004](../adr/0004-event-transport.md)).
- **Cancellation:** every generation is a task with a cancel token checked per chunk; `asyncio.CancelledError` is never swallowed; cleanup in `finally`.
- **Backpressure:** bounded queues; policy per queue (drop-oldest for audio, block for control events); overflow metric.
- **Idempotency:** event ids, `answered_triggers`, `(stream_id, segment_start)`.
- **Timeouts/retries:** per provider; bounded, jittered, only for retryable classes.
- **State:** single-writer runtime actor; others send events. Persist snapshots/event log for recovery.
- **Shutdown:** stop accepting audio → cancel generation → flush report → close streams. Supervision: run under a process supervisor (e.g. launchd/systemd/uvicorn reload for dev); crash recovery from event log.
- **Where async adds complexity:** wrapping blocking model calls in executors for little gain; avoid async inside pure policy code.
