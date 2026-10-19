# ADR-0011: Memory trust levels and promotion rules
**Status:** Proposed
**Decision:** records start `candidate`; promotion to `confirmed` needs explicit confirmation by a control-role actor above the attribution threshold, or two independent agreeing speakers, or a human action. Records require provenance. Summaries are `inference`. **Reasons:** a transcript line is a claim, not a fact. **Consequences:** reports show unconfirmed items as such. **Risks:** reviewer workload. **Validation:** scripted-meeting tests (unconfirmed-as-confirmed rate must be 0).
