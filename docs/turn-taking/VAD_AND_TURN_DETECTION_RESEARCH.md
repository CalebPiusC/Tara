# VAD and Turn Detection Research

Silero VAD (S11) is the proposed detector. VAD answers "is there speech now", **not** "is the turn over".

## Why a pause ≠ end of turn
Thinking pauses in interviews ("...so, um, the uniqueness constraint...") can exceed typical silence thresholds; candidates are *told* to take time. Conversely a short pause after "Thanks." may end a turn.

## Combined end-of-turn estimate (proposed)
Inputs: silence duration (VAD), transcript syntactic completeness (partial STT or LLM check), question/answer state (Tara asked an open design question → allow longer), mode (`interview` → longer patience), speaker history (this person pauses long), others starting to speak. Output: `p_end_of_turn` + hold timer. Interview mode must prefer waiting over cutting off. OpenAI's reference offers a "semantic VAD" option for the same reason (S13).

## Topics
| Topic | Handling |
|---|---|
| Onset | pre-roll buffer so first phonemes aren't lost |
| Endpoint | hangover + completeness check |
| Noise / false activation | min speech duration, energy gate, drop segments STT returns empty |
| Overlap | mark `overlap`; don't force single attribution |
| Barge-in | VAD onset during SPEAKING triggers cancel immediately |
| Latency | VAD <1 ms/chunk (S11) is negligible; hangover dominates |
All thresholds are **[benchmark]** values.
