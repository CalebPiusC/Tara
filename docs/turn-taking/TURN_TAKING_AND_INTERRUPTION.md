# Turn Taking, Participation Decision, Interruption

**Status: Designed, not implemented.** Related: [MEETING_RUNTIME.md](MEETING_RUNTIME.md), [VAD_AND_TURN_DETECTION_RESEARCH.md](VAD_AND_TURN_DETECTION_RESEARCH.md), reference patterns in [research/LIVE_VOICE_REFERENCE.md](../engine/LIVE_VOICE_REFERENCE.md).

## Intent taxonomy for each finalized utterance
`addressed_to_tara` · `instruction_to_tara` · `invitation_to_contribute` · `about_tara_not_to_her` · `human_to_human` · `silence_request` · `takeover` · `unclear`.

## Deterministic vs semantic
| Decision | Kind |
|---|---|
| Is Tara already speaking / floor busy / takeover active / policy silent / epoch fresh / permission exists | Deterministic |
| Speaker confidence ≥ threshold | Deterministic over a computed score |
| Name token present in transcript (fuzzy, ASR-tolerant) | Deterministic **hint** |
| Is this utterance addressed to Tara? Is it a question for her scope? Is a proactive remark useful? | **Semantic** (LLM classifier returning structured `{intent, confidence}`), then gated deterministically |
| Is the speaker done with their turn? | Mixed: VAD silence + syntactic completeness + context |

## Pseudocode
```python
def decide(utt, rt) -> ParticipationDecision:
    if rt.state in (STOPPED, RECOVERING):            return Deny("not_ready")
    if rt.takeover_active:                           return Deny("takeover")
    if rt.config.speaking.policy == "silent":        return Deny("policy_silent")
    hint = name_hint(utt.text)                       # fuzzy: "Tara", "Tarah", ASR variants
    intent = rt.classifier(utt, rt.recent_context, hint)   # SEMANTIC, untrusted
    if intent.kind == "silence_request":             rt.mute(); return Deny("asked_to_be_quiet")
    if intent.kind in ("human_to_human","about_tara_not_to_her","unclear") and not proactive_ok(rt):
        return Deny("not_addressed")
    if utt.attribution.confidence < rt.config.min_conf and needs_identity(intent, rt.config):
        return AskClarification("who_is_speaking") if rt.config.mode.is_interview else Deny("identity_low")
    if not in_scope(intent, rt.config):              return Refuse("out_of_scope")   # speak a brief refusal if policy allows
    if rt.other_speaking and not intent.is_direct_question: return Wait("floor_busy")
    if intent.kind == "invitation_to_contribute" or intent.kind in ("addressed_to_tara","instruction_to_tara"):
        return Allow(trigger=utt.event_id, epoch=rt.epoch)
    if proactive_ok(rt) and intent.usefulness >= rt.config.proactive_threshold and rt.pause_ms >= rt.config.proactive_min_pause_ms:
        return Allow(trigger=utt.event_id, epoch=rt.epoch, proactive=True)
    return Deny("listening")
# After Allow: GENERATING → VALIDATING (re-run floor/epoch/relevance checks) → WAITING_TO_SPEAK → SPEAKING
```
`proactive_ok` is true only for `proactive_allowed` policy. Thresholds are config values calibrated on simulation corpora, not hard-coded.

## Name detection caveats (Example F)
Direct address uses name hint **plus** semantics: "Tara, can you..." (hint+question), "can you explain deployment?" in a 1-human+Tara context (no name), "I asked Tara earlier" (mention, not address), "Sara" vs "Tara" ASR confusion, another person named Tara. Evaluate false-activation and missed-address rates on a labeled corpus.

## Barge-in / interruption
1. VAD `speech_started` from a participant while SPEAKING → INTERRUPTED immediately; cancel TTS+playback (does not wait for transcript).
2. Compute `delivered_ms`; truncate stored assistant turn to what was heard (pattern from S13).
3. Transcribe the interrupter; re-run `decide`. If it wasn't addressed to Tara, remain silent; the pending proposal is discarded (epoch bump).
4. Policy `barge_in: finish_sentence` is allowed only for short remaining spans and never for takeover phrases.
5. Backchannels ("mm-hm") must not trigger interruption forever: require min speech duration and/or non-backchannel text (tuning item).
6. Echo guard: Tara's own output must not be treated as a participant ([AUDIO_PIPELINE.md](../engine/AUDIO_PIPELINE.md)).

## Structured-interview turn management
Round-robin queue of candidates; a candidate answer window closes on end-of-turn estimate + recruiter policy; interjections from a non-current candidate are stored as `unsolicited` evidence attached to that speaker only if attribution is confident.
