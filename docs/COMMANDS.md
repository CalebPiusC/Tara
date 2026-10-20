# Commands: what you can say, what you can run, and what carries over to Google Meet

This is the one list of everything Tara can be told to do. Status words follow the rest of the docs: **implemented** means code exists and offline tests ran; **designed** means written down only. Nothing below has been proven inside Google Meet yet, because the Meet connection is not built ([GOOGLE_MEET_INTEGRATION](GOOGLE_MEET_INTEGRATION.md)).

**How to read the "In Meet" column**
- **Same**: the feature lives in the application, not in the meeting platform, so it works the same once audio flows.
- **Needs identity**: it needs to know who is speaking. With the local demo that is always "You". In Meet it depends on per-participant audio or diarization (see the integration plan, section "Who said what").
- **Needs platform**: it needs a capability the Meet adapter must expose (mute, camera, picture). Not assumed to exist.
- **New in Meet**: only meaningful in a real multi-person meeting.

---

## 1. Things you say to Tara

### Meeting controls (grammar is deterministic; say her name first)
| Say | What happens | Who may | In Meet |
|---|---|---|---|
| "Tara, mute your microphone" | she goes silent in the app (and mutes her platform mic if the platform allows) | anyone, except candidates in interviews | Needs platform for the platform mute; silence always works |
| "Tara, stay quiet but keep listening" | listen-only mode, notes can still be recorded | anyone, except candidates in interviews | Same |
| "Tara, you can speak again" | resumes | organizer or recruiter with reliable identity, or whoever silenced her | Needs identity |
| "Tara, leave the meeting" | she leaves | organizer or recruiter with reliable identity | Needs identity, Needs platform |
| "Tara, turn your camera on / off" | camera control | organizer or recruiter | Needs platform |
| "Tara, can we see you?" | shows her picture | anyone, except candidates | Needs platform |
| "Tara, take notes" / "stop taking notes" | notes mode on or off | organizer or recruiter | Needs identity |
| "Tara, confirm that" | promotes the last noted decision or action to confirmed | organizer or recruiter | Needs identity |
| "Tara, mute Bob" | mutes someone else (off by default) | organizer or recruiter, explicitly enabled | Needs platform, Needs identity |
| "Please be quiet" (while she talks) | stops her current reply | anyone | Same |
| "Thanks Tara, I'll take it from here" | human takeover: her speech path closes | trusted role | Same |

Details and permissions: [MEETING_CONTROLS](MEETING_CONTROLS.md).

### Memory and records
Tara only saves or changes something when your own words just asked for it (a 60 second window), and says "saved" only after the save worked.
| Say | Tool | In Meet |
|---|---|---|
| "Remember that the offer decision is due Friday" / "save that", "make a note that", "write this down", "don't forget" | `remember_memory` | Same |
| "What do you remember about Testflow?" / "do you remember anything about last week?" | `recall_memory` | Same |
| "That's wrong, it's Monday" / "update that" / "change it to Monday" | `update_memory` (old version kept as history) | Same |
| "Forget that" / "delete that memory" | `forget_memory` | Same |
| "What did we talk about last meeting?" / "what was meeting two?" | `meeting_lookup` | Same |
| "Which meetings have we had?" | `list_meetings` (numbers, dates, times) | Same |
| "Delete the last meeting" / "clear meeting 2, we no longer need it" | `delete_meeting` (asks "are you sure?" first when the meeting held something important; you must say a clear yes) | Same |

Nothing needs to be said for the meeting itself to be kept: the recorder stores transcript chunks, a summary and key facts automatically ([adr/0015](adr/0015-automatic-meeting-memory.md)).

### Running a meeting
| Say | Tool | In Meet |
|---|---|---|
| "Add an action item: Sam sends the budget by Friday" | `add_action` | Needs identity for the owner to be reliable |
| "Park that for later" / "put it in the parking lot" | `park_item` | Same |
| "What's still open?" | `list_open_items` | Same |
| "The budget one is done" / "mark the budget task done" | `complete_item` | Same |
| "Set an agenda: budget, hiring, wrap up, 30 minutes" | `set_agenda` | Same |
| "Where are we on the agenda?" / "mark budget as done" | `agenda_status`, `agenda_mark` | Same |
| "Recap so far" / "what did I miss?" | `recap_meeting` | New in Meet (late joiners) |
| "This is a stand-up" / "set the meeting type to retro" | `set_meeting_type` (general, standup, one_on_one, retro, client_call, brainstorm) | Same |
| "Remember that Kubernetes is spelled like this" | `add_vocabulary` | Same |
| "Take the minutes" / "write up the minutes as a PDF" | `create_document`, then `job_status` | Same |
| "What have we agreed?" / "what did we say about pricing?" | `meeting_recap`, `recall_meeting` | Same |

Unprompted, Tara also: greets a continuing section with what was left open (opening brief), and gives time nudges when an agenda with a length is set. Both go through the normal speaking gate.

### Looking things up (read-only)
| Say | Tool |
|---|---|
| "What time is it in Lagos?" / "what is 14:00 London in Tokyo?" | `get_time`, `get_time_at_place`, `convert_time`, `time_until` |
| "What is 18 percent of 4,250?" / "convert 12 miles to km" | `calculate`, `convert_units` |
| "What's the weather in Ibadan?" / "tell me about Nigeria" | `get_weather`, `geocode_place`, `country_info` |
| "Read this page: <url>" | `fetch_url` (public pages only) |
| "Search the web for ..." / "research X and tell me when it's done" | `web_search`, `research`, `start_research`, `research_status`, `research_result`, `cancel_research` (search needs a Brave or Tavily key) |

### Interviews (organizer or recruiter only)
| Say | Tool |
|---|---|
| "Prepare Alex from the inbox, GitHub alexkim" | `prepare_candidate` |
| "Who have we prepared?" / "tell me about Alex" | `list_candidates`, `get_candidate` |
| "Start the interview with Alex" | `start_interview`, then `interview_next_question` |
| (she evaluates each answer by meaning, with one retry and a three-minute limit) | `interview_record_answer` (qid only; the legacy score+quote path is still accepted) |
| "Finish the interview" | `finish_interview` (decision support only, never hire or reject) |
| "Make a PDF report of the interview" | `create_document` (kind `interview_report`) |

---

## 2. Things you run in a terminal

### Start Tara
```bash
./dev.sh                              # speakers, debug lines on, section "default"
./dev.sh --headphones                 # full duplex, you can interrupt her
./dev.sh --section join               # the next numbered meeting of section "join"
./dev.sh --type standup               # general | standup | one_on_one | retro | client_call | brainstorm
./dev.sh --transcripts                # also print what Tara heard and said (shows speech)
./dev.sh --quiet                      # hide debug counters
./dev.sh --list                       # sections and their numbered meetings with dates
```
The same options exist on `python -m tara.voice.demo`: `--profile professional|conversational|expressive`, `--debug`, `--one-on-one`, `--speaker`, `--headphones`, `--output auto|headphones|speaker`, `--section`, `--type`, `--transcripts`, `--list-meetings`.

### Interviews
```bash
./dev.sh interview new "Feremi"       # wizard: role, skills, GitHub, portfolio, CV (drag in), questions and difficulty, then a code
./dev.sh interview list               # prepared interviews and finished results
./dev.sh interview run <code>         # Tara greets the candidate, confirms the name and runs the interview (add --headphones)
```
Details: [INTERVIEWS](INTERVIEWS.md).

### Google Meet bot
```bash
./dev.sh meet check                                   # what is set up (no browser opened)
./dev.sh meet login                                   # open Chrome with the bot's profile so you sign the BOT's account in
./dev.sh meet join <link or code> --interview <code>  # Tara joins as the bot, waits to be admitted, greets the candidate, runs the interview
```
Details and the exact first-test steps: [GOOGLE_MEET_BOT](GOOGLE_MEET_BOT.md). Not yet run in a real Meet.

### Prepare, inspect, maintain
```bash
python -m tara.hiring.prepare --label alex --role "Backend Engineer" --require Go --cv alex.pdf --github alexkim --questions 6
python -m tara.memory.migrate                         # create or update the memory tables (safe to rerun)
python scripts/memory_report.py --meeting join        # read-only: what is stored, by kind and status
python -m tara.voice.smoke                            # one real Gemini Live turn (opt-in, prints numbers only)
```

### Check the code
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests && .venv/bin/mypy src
```

### Settings that matter most (full list: `env.full.example`)
`GEMINI_API_KEY`, `TARA_LIVE_MODEL`, `TARA_LLM_MODEL`, `TARA_RECORDER_MODEL`, `TARA_WORK_ENABLED`, `TARA_MEMORY_ENABLED`, `TARA_MEMORY_DATABASE_URL`, `TARA_SECTION`, `TARA_MEETING_TYPE`, `TARA_OPENING_BRIEF`, `TARA_NUDGES`, `TARA_VOCAB_FILE`, `TARA_GITHUB_TOKEN`, `TARA_SEARCH_API_KEY`, `TARA_INTERVIEW_QUESTION_LIMIT_S`, `TARA_INTERVIEW_RETRY_LIMIT`, `TARA_INTERVIEW_EVAL_TIMEOUT_S`, `TARA_INTERVIEW_ANSWER_GRACE_S`.

---

## 3. Planned commands (designed, not built)
These belong to the Google Meet connection and the dashboard. They are listed so the final system has one command surface.

| Command | Where | Purpose |
|---|---|---|
| `./dev.sh --meet <meeting url or code>` | terminal | (superseded by `./dev.sh meet join`, built, not yet run in a real Meet) |
| `./dev.sh interview new` (Meet step) | terminal | the code it prints becomes a Google Meet link with Tara already waiting inside |
| `./dev.sh --calendar` | terminal | list today's meetings from the calendar and start Tara in one |
| "Tara, who is in the meeting?" | voice | list attendees with names from the roster |
| "Tara, who said that?" | voice | answer from per-participant attribution, or say it is unknown |
| `./dev.sh --dashboard` | terminal | start the backend API and the web dashboard |
| Dashboard actions | browser | view the live transcript, notes and open items; browse, edit, export and delete memory; manage sections, vocabulary and meeting types ([DASHBOARD](DASHBOARD.md)) |

## 4. Does Google Meet cover everything?
Short answer: everything in section 1 except the rows marked **Needs platform** is application logic and carries over unchanged. The Meet connection must supply three things the local demo fakes:
1. **Two-way audio** (hear the meeting, speak into it).
2. **Per-participant audio or speaker labels**, so "who said what", owners of action items and control-role checks are real. Without it, everything is attributed to "unknown".
3. **A roster** (names, and who is the organizer) from the invite or calendar.

Platform controls (mute her mic, camera, picture, mute others) are optional extras that the adapter may or may not be able to do; Tara will say so honestly if not. The step-by-step plan is in [GOOGLE_MEET_INTEGRATION](GOOGLE_MEET_INTEGRATION.md).
