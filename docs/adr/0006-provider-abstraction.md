# ADR-0006: Provider abstraction
**Status:** Proposed
**Decision:** `typing.Protocol` contracts, constructor injection, model ids and backends from config, fakes for tests. **Reasons:** model ids and availability change (S5 lists several); local and cloud swaps. **Risks:** over-abstraction: only abstract the boundaries listed in [API_AND_EVENT_CONTRACTS](../architecture/API_AND_EVENT_CONTRACTS.md). **Validation:** implement two STT backends behind the interface.
