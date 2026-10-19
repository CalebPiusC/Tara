# ADR-0004: Event transport
**Status:** Proposed
**Context:** single process. **Options:** broker (Redis/NATS), WebSocket everywhere, in-process asyncio bus. **Decision:** in-process bus; WebSocket/HTTP only for external control and observability. **Reasons:** no network hop inside the process; fewer failure modes. **Consequences:** no multi-process scale-out without rework. **Risks:** low. **Evidence:** reasoning. **Validation:** revisit if workers move out-of-process.
