# Meeting Controls: spoken commands as real application actions

**Status: implemented and tested offline only.** The grammar, permission policy, persistent control state, engine actions and notes recorder are implemented in `src/tara/control/`, `src/tara/voice/participation.py`, `src/tara/voice/engine.py` and `src/tara/voice/memory.py`. Nothing here has run against the real Gemini Live service, a microphone, or Google Meet, so event ordering, model compliance and platform behaviour are unverified. Tests: `tests/control/test_control.py`, `tests/control/test_notes_control.py`, plus the command and notes cases in `tests/voice/test_voice_playback_participation_memory.py`.

## Principle: silence is easy, expansion is hard

Anyone may reduce Tara's footprint (stop talking now, ask her to listen only, hide her picture). Anything that expands her footprint or affects the meeting (resume in some cases, leave, camera, notes, confirming a note, muting someone else) needs a **control role** and a **reliable identity**. The meeting adapter's real capabilities are checked before anything is promised.

A recognized command is executed by the application, never by the model. It is also never answered by the model as a question: for the command's own turn, response audio is dropped. After an allowed action, or a refusal that is worth explaining, the application sends a short prompt and the model says one true sentence. If the adapter failed or lacks the capability, Tara says so briefly and the state is not updated.

## Grammar and addressing

Commands are English, regular-expression based, and deterministic (`src/tara/control/commands.py`). Tara must be addressed by name near the start of the sentence: at the start, or after `hey`, `hi`, `hello`, `ok`, `okay`, `so`, `um`, `uh`. "Yesterday Tara said she would leave" is speech about Tara and does not fire. "Alex, please leave the meeting" is not addressed to Tara and does not fire.

| Command | Example | Who may do it | Platform requirement |
|---|---|---|---|
| `mic_mute` | "Tara, mute your microphone" | Anyone, except candidates in interviews | Adapter `can_mute_self` for the platform mute; application silence always works |
| `listen_only` | "Tara, stay quiet but keep listening" | Anyone, except candidates in interviews | None |
| `resume` | "Tara, you can speak again" | Control role with reliable identity, or the person who silenced her | None |
| `leave` | "Tara, leave the meeting" | Control role with reliable identity | None |
| `camera_on` / `camera_off` | "Tara, turn your camera on" | Control role with reliable identity | Adapter `can_camera` |
| `show_identity` | "Tara, can we see you?" | Anyone, except candidates in interviews | Adapter `can_show_image` and a configured image |
| `notes_on` / `notes_off` | "Tara, take notes" / "Tara, stop taking notes" | Control role with reliable identity | None |
| `confirm_note` | "Tara, confirm that" | Control role with reliable identity | None |
| `mute_other` | "Tara, mute Bob" | Control role with reliable identity, and explicitly enabled | Adapter `can_mute_others`; off by default |

Control roles are `organizer` and `recruiter` (`CONTROL_ROLES`). Reliable identity means a participant id, attribution confidence at or above the meeting threshold, and no simultaneous-speech overlap.

`allowed_commands` in the meeting config can restrict the list. An empty tuple means the defaults, which are every command except `mute_other`. A non-empty tuple replaces the defaults; `mute_other` must be named explicitly.

### A bare "be quiet" is an interruption, not persistent control

"Please be quiet" with no name, said while Tara is speaking, stops her current reply for anyone, including candidates in an interview. It does not set listen-only. "Tara, please be quiet" is an addressed command and is authorized as above, so in an interview a candidate cannot keep her silent.

## Persistent state and reconnect

`ControlState` (`src/tara/control/state.py`) holds `speech_mode` (`active`, `listen_only`, `muted`), `set_by`, `notes_active`, `camera_on`, `identity_shown` and `left`. It survives provider reconnects and is consulted by the participation gate before any policy that would let Tara speak. Listen-only and mute override proactive policy. Audio still flows to the model while she is silent: she keeps listening, and notes mode can keep recording.

The person who silenced her may resume her even without a control role, provided the identity is reliable and matches `set_by`. A new session does not clear the state.

## What the platform can really do

The repo has a scripted `LocalMeetingAdapter` for development and tests, and a local microphone/speaker demo adapter (`adapters/audio_device.py`) for one participant with no platform controls. There is **no Google Meet adapter**. Google Meet must not be assumed to support muting Tara's microphone, changing her camera, showing a picture, or muting another participant: the adapter would have to expose those capabilities explicitly, and today none does.

`AdapterCapabilities` defaults every control to `False`. The local adapter can simulate them with `controls=True`, and can simulate a failing call with `fail_controls=True`. Policy returns `unsupported` before execution when a capability is absent, and the engine refuses honestly rather than claiming success. If a platform call fails, the control state is not updated; for `mic_mute`, application-level silence still holds and `control_platform_failed` is counted.

## Notes, confirmation and summaries

Notes are candidates, never confirmed facts:

- `record_note` is a granted tool (`kind`: `decision` | `action_item` | `open_question` | `summary`, `body`, optional `owner`, optional `deadline`). It is declared to the model only when notes are enabled by config, and its handler refuses unless `ControlState.notes_active` is true. The memory scope is bound by the application; the model never supplies it.
- Every record carries provenance (the tool-call sequence for the session) and is stored with `status="candidate"`. `add()` rejects a record that is already confirmed, so promotion is the only path to `confirmed`.
- "Tara, confirm that" is a control-role command. It calls `MeetingMemory.promote_latest`, which promotes the newest candidate `decision` or `action_item` and preserves provenance. If there is no candidate, Tara says there is nothing to confirm; nothing is invented.
- `summarise_agreed(memory, scope)` is a read function that returns confirmed records and, separately, unconfirmed candidates. There is no spoken "summarise what we have agreed" command yet: the function exists for an application or a future command, and it never fills gaps.
- The only notes implementation is in-memory. Long-term memory v1 (explicit save/recall/forget) is a separate PostgreSQL store (`src/tara/memory/`), but meeting notes still do not survive the process.

## Metrics and events

Counters include `control.<command>`, `control.barge_in`, `control_denied.<command>.<reason>`, `control_platform_failed`, `control_note_confirmed`, `control_note_confirm_empty`, `command_audio_dropped` and `live_tool_calls`. Events carry ids, kinds and reasons only, never command text, note bodies, transcripts or audio.

## What is not done

No real-service or real-platform test exists. A live Google Meet adapter, a real-device run of the local microphone capture path, per-participant attribution from a real platform, durable meeting memory, and a spoken summary command are all unimplemented. The command grammar is English-only.
