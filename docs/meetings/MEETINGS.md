# Everyday meeting features

Status: implemented and tested offline, and the storage parts also on real PostgreSQL in a temporary schema. Not yet verified: whether real Gemini calls these tools at the right moments, the spoken nudges and opening brief, and the quality of the extracted actions.

All of this needs `TARA_WORK_ENABLED=1`. Open items, the opening brief and the agenda copy also need memory (`TARA_MEMORY_ENABLED=1`).

## Open items that carry over
Action items (with owner and deadline) and parked topics are stored per section and stay open from meeting to meeting until someone completes them.
- Say "add an action item: Sam sends the budget by Friday" (`add_action`), "park that for later" (`park_item`), "what is still open?" (`list_open_items`), "the budget one is done" (`complete_item`).
- The end-of-meeting summary also picks up actions with owner and deadline, as unconfirmed items.
- Tools check that you asked (a 60 second window from your own words); an ambiguous "done" makes Tara ask which one.
- Completed items are marked done with the meeting they were finished in.

## Opening brief
When you start a later meeting of a section that has history, Tara speaks first: a short hello, where things were left (last summary) and what is still open. Nothing is said for a new section or when nothing is open. Switch off with `TARA_OPENING_BRIEF=0`. It goes through the normal gate, so mute and takeover win.

## Agenda and time-keeping
`set_agenda` (items plus optional total minutes), `agenda_status`, `agenda_mark`. With a length set, Tara gives quiet spoken nudges: about five minutes left, one minute left, the planned end, and once when an item runs well over its share (the time is split evenly as a starting plan). Each nudge is sent once, at most six per meeting. Switch off with `TARA_NUDGES=0`. The agenda is also saved as a note in the meeting.

## Recap and catch-up
`recap_meeting` returns the recent transcript (or the whole meeting with `whole_meeting`), decisions noted and open items, and Tara summarises only that. Use it for "recap so far" and "what did I miss". The parking lot is the same open-items mechanism.

## Names and jargon
A word list fixes how names and terms are written. Sources: `work/vocabulary.txt` (one per line, `#` comments), `TARA_VOCAB` (comma list), the participants' names, and the voice tool `add_vocabulary` ("remember that Kubernetes is spelled like this"). It is used in the live instructions, in a conservative fuzzy correction applied to stored transcripts ("Test flow", "Testflo" become "Testflow"), and in the summary prompt. It does not change what Gemini hears, so a badly misheard word is still a misheard word; no input-language or vocabulary hint is sent to the Live API because support for it is unverified.

## Meeting types
`./dev.sh --type standup` (or `TARA_MEETING_TYPE`, or "set the meeting type to retro"): `general`, `standup`, `one_on_one`, `retro`, `client_call`, `brainstorm`. A type changes:
| Type | How Tara behaves | Minutes layout |
|---|---|---|
| standup | brisk, per person done/today/blockers, flags blockers, parks long topics | per person, blockers table, parked |
| one_on_one | warm, person leads, keeps private matters out | topics, feedback, commitments, follow-ups |
| retro | went well / did not / change, no blame, ends with actions | three headings plus actions |
| client_call | polished, captures commitments, never commits for the team | requirements, objections, commitments, next steps |
| brainstorm | captures every idea, labels its own ideas, groups on request | ideas by theme, favourites |
| general | follow along, note decisions and actions | summary, decisions, actions |

The type also steers the behaviour layer's style (never permissions) and the hint given to the end-of-meeting summary.
