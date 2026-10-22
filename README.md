# Tara 🎙️

### An AI team member that knows when to talk, and when not to.

## What is Tara?

Tara is an AI participant for live meetings. She joins a conversation by voice, works out why she is there, tracks who is speaking, and takes part only as far as the organizer's rules and the live state of the conversation allow. She can interview a candidate, sit in on a team meeting and take the minutes, present a product from approved material, or simply listen and remember.

Most meeting bots transcribe. Tara takes part. The difference is judgement: a model that speaks whenever it has something to say is worse than silence in a hiring interview or a customer call. So Tara is built on one rule: **deterministic code owns the floor, permissions, identity and tools, and the language model only proposes.**

---

## Features

### Conversation
- **Natural voice** with Gemini Live: speech in, speech out, low latency.
- **Knows when to speak.** Being addressed, being invited, being told to be quiet, being interrupted and being taken over are separate states, not one flag.
- **Interruption handling.** Speech from a human cancels Tara mid-sentence, and history keeps only what was actually heard.
- **Human takeover.** "Thanks Tara, I'll take it from here" closes her speech path at once.
- **Identity as evidence.** A speaker can be `unknown`. Candidate A's answer never becomes Candidate B's.
- **A personality, not a script.** Profiles (`professional`, `conversational`, `expressive`) shape tone and length. They never decide whether she speaks.

### Memory
- **Keeps the meeting without being asked.** Tara stores the conversation in PostgreSQL as it happens (transcript chunks, a summary and key facts), so nobody has to say "remember this". Saying it still works and marks something as confirmed.
- **Sections and numbered meetings.** A section is like a chat. Every run of the same section is its next numbered meeting (meeting 1, meeting 2, ...), stored with its date and time. Ask "what did we talk about last meeting?", "what was meeting two?" or "which meetings have we had?". A different section name starts a separate memory, so sections never mix.
- **Editable.** Say "delete the last meeting" and Tara removes it, asking "are you sure?" first when it held something important. Say "that's wrong, it's Monday" and she updates the memory, keeping the old version as history.
- **Careful by design.** Extracted facts need a word-for-word quote from the transcript and are labelled unconfirmed. Passwords, health and money details are never stored. Old chunks are deleted after a retention period you control.
- **Carries over.** The next session of a meeting starts with its summary and key facts, and Tara looks things up when asked and says plainly when nothing is recorded.

### Running a meeting
- **Open items that carry over.** Action items (owner and deadline) and parked topics stay open from meeting to meeting. Ask "what's still open?" or say "the budget one is done".
- **Opening brief.** When a continuing section starts, Tara speaks first: where things were left and what is still open.
- **Agenda and time-keeping.** Set an agenda and a length; Tara tracks what is covered and gives quiet nudges ("five minutes left").
- **Recap and catch-up.** "Recap so far" and "what did I miss", plus a parking lot for off-topic points.
- **Names and jargon.** A word list (file, env, or by voice) so names are written correctly.
- **Meeting types.** Stand-up, one-to-one, retro, client call and brainstorm each change how Tara behaves and what the minutes look like (`./dev.sh local --type standup`).

### Secretary
- **Takes minutes** while the meeting runs: decisions, actions with owners, open questions.
- **Reminds the room** ("we said this earlier") when a confirmed item comes up again.
- **Recaps on demand.**

### Interviews
- **Give her a CV, a GitHub account or a portfolio link.** She reads them and builds interview questions from the role and the candidate's skills.
- **Runs the interview**, then grades each criterion with a verbatim quote from the candidate as evidence.
- **Returns a report** that always asks for human review. She never says hire or reject.
- **Create one from the terminal:** `./dev.sh interview new "Name"`, then `./dev.sh interview run <code>` (add `--meet` to run it in Google Meet) ([INTERVIEWS](docs/INTERVIEWS.md)).

### Documents
- **PDF, DOCX and Markdown** from an interview, a meeting, or anything you ask for.
- Gemini Flash writes the readable text from stored facts only. Python renders the file with your fonts and formatting.

### Tools
Time zones, calculator, location, weather, public web pages, optional web search (Brave or Tavily), and bounded background research.

### Safety
If something fails Tara says so out loud and the screen shows a problem line. AI-presence disclosure before a meeting, tool use granted per meeting, no raw audio stored by default, secrets never logged, and CV, web and transcript text treated as untrusted data.

---

## Architecture

Deterministic code sits between the model and the meeting. Gemini Live understands, answers and speaks. Our code decides who may be heard, when playback stops, which tools run and what is remembered.

```mermaid
flowchart TB
  subgraph Bot["Meeting bot (src/tara/meet)"]
    direction LR
    GM["Google Meet"] <--> CH["Bot's own Chrome (separate profile)"]
    CH <-->|"BlackHole 2ch in, 16ch out"| CAB["Virtual audio cables"]
    CAB --> PRE["Quiet-voice gain, echo guard"]
    PRE --> REC2["Audio recorder (opt-in, WAV files)"]
  end
  LOCAL["Local mic and speakers (./dev.sh local)"] <--> MA
  CAB <--> MA["MeetingAdapter"]
  PRE --> MA
  MA --> UP

  subgraph Voice["Voice path"]
    direction LR
    UP["Bounded uplink"] --> PROV["Voice provider interface"]
    PROV --> GATE["Participation gate (standby: name, model wake, follow-up)"]
    GATE -->|"allowed audio only"| PB["Playback (cancellable)"]
  end
  PB --> MA
  PROV <-->|"WSS"| GEM["Gemini Live (cloud)"]

  subgraph Guard["Control and safety"]
    direction LR
    CTRL["Spoken controls and permissions"]
    BEH["Behaviour (style only)"]
    CLOCK["Agenda clock and opening brief"]
    WATCH["Error watchdog"]
  end
  Guard -.->|"shapes, nudges, explains failures"| Voice

  subgraph Do["What Tara can do"]
    direction LR
    TOOLS["Tool executor (granted, checked in code)"]
    MT["Memory tools"]
    MEETT["Meeting tools"]
    DOCT["Documents and interviews"]
    RO["Lookups"]
    REC["Recorder"]
    TOOLS --> MT
    TOOLS --> MEETT
    TOOLS --> DOCT
    TOOLS --> RO
  end
  PROV --> TOOLS
  PROV -.->|"finished turns"| REC

  subgraph Keep["Storage and text models"]
    direction LR
    MS["Memory service (scoped to the section)"] --> PG[("PostgreSQL")]
    FILES[("work folder")]
    FLASH["Gemini Flash"]
  end
  MT --> MS
  MEETT --> MS
  REC --> MS
  REC -->|"summaries"| FLASH
  DOCT -->|"document text"| FLASH
  DOCT --> FILES
```

Memory tools are remember, recall, update, forget and delete meeting. Meeting tools are open items, agenda, recap, meeting type and vocabulary. Lookups are time, weather, public pages and search. Working memory (the last turns, plus a short digest of older ones) is re-seeded to the model after a reconnect, framed as data and never as instructions; a stale resume handle is dropped after one failed try, and reconnecting keeps trying for about a minute. Gemini Live also compresses its own context window (sliding window) so a long call does not end at 15 minutes. Metrics carry no content. Details of each layer are in [SYSTEM_ARCHITECTURE](docs/SYSTEM_ARCHITECTURE.md).

Tara is cloud-backed: audio and transcripts go to Google. Details: [GEMINI_LIVE](docs/GEMINI_LIVE.md), [SYSTEM_ARCHITECTURE](docs/SYSTEM_ARCHITECTURE.md).

### One interaction
```mermaid
sequenceDiagram
  participant P as Participant
  participant M as MeetingAdapter
  participant E as Engine
  participant G as Gemini Live
  participant B as Playback
  P->>M: "Tara, how do you handle retries?"
  M->>E: audio
  E->>G: 16 kHz audio stream
  G-->>E: input transcript
  G-->>E: reply audio (held until the gate decides)
  E->>E: gate: addressed to Tara? allowed?
  E->>B: allowed audio
  B->>M: play to the meeting
  G-->>E: turn complete
```

### Interruption and takeover
```mermaid
sequenceDiagram
  participant H as Human
  participant R as Engine
  participant T as Playback
  R->>T: speaking
  H->>R: speech detected (local VAD or provider interrupted)
  R->>T: cancel + flush, mark response dead
  T-->>R: delivered_ms
  R->>R: epoch++, keep only the heard part, drop late chunks
  H->>R: "Thanks Tara, I'll take it from here"
  R->>R: takeover_active = true (speech path closed)
```

### Sections and numbered meetings
A section is a chat. Every run of the same section is its next numbered meeting. Memory never crosses sections.
```mermaid
erDiagram
  SECTION ||--o{ MEETING : "has numbered"
  MEETING ||--o{ RECORD : "produces"
  SECTION ||--o{ RECORD : "scopes"
  SECTION {
    string name "for example join or hiring"
  }
  MEETING {
    int number "1, 2, 3 and never reused"
    time started "stored in UTC, spoken in local time"
    time ended "empty if the exit was cut short"
  }
  RECORD {
    string kind "episode, summary, fact, decision, action, note"
    string status "confirmed or candidate"
    string source "provenance is required"
    time expires "retention, empty for kept items"
  }
```

### The life of a meeting
```mermaid
sequenceDiagram
  participant U as You
  participant T as Tara
  participant R as Recorder
  participant D as PostgreSQL
  participant F as Gemini Flash
  U->>T: start section "join"
  T->>D: start meeting N, load earlier meetings and open items
  T->>U: opening brief: where things were left, what is open
  loop during the meeting
    U->>T: speak
    T->>U: reply, run tools, nudge on time
    T->>R: finished turns
    R->>D: transcript chunk every few turns
  end
  U->>T: stop
  T->>D: mark meeting N ended
  R->>D: save the last chunk
  R->>F: transcript, ask for summary and facts
  F-->>R: summary and facts with exact quotes
  R->>D: keep the summary, and only facts whose quote is real
  Note over R,D: if this step fails, the next start catches up
```

### How memory is organized
```mermaid
flowchart TD
  SAY["What is said"] --> WM["Working memory: last turns, this session only"]
  SAY --> CH["Transcript chunks: stored word for word with a retention date"]
  SAY -->|"you ask: remember, update, forget"| EX["Explicit memory: confirmed, can be edited, history kept"]
  SAY -->|"you ask: action item, park it, done"| OI["Open items: carry over until completed"]
  CH --> SUM["Summary and key facts (checked against a real quote)"]
  SUM --> CAND["Unconfirmed facts: promoted when seen again"]
  EX --> STORE[("Stored per section")]
  OI --> STORE
  SUM --> STORE
  CAND --> STORE
  STORE --> RECALL["Recall tools: recall, meeting lookup, list meetings, open items"]
  STORE --> BRIEF["Opening brief and startup context for the next meeting"]
  STORE --> DOCS["Minutes and reports"]
  STORE -.->|"expired or deleted"| GONE["Purged or hidden"]
```

### Where things live
| Area | Code |
|---|---|
| Gemini Live adapter and voice contracts | [voice/](src/tara/voice/) |
| Participation gate, floor, identity | [participation.py](src/tara/participation.py), [voice/participation.py](src/tara/voice/participation.py) |
| Behaviour (style only) | [behaviour/](src/tara/behaviour/) |
| Memory | [memory/](src/tara/memory/), [secretary/](src/tara/secretary/) |
| Meeting features (open items, agenda, vocabulary, types, brief) | [meetings/](src/tara/meetings/) |
| Interviews | [hiring/](src/tara/hiring/) |
| Documents | [docgen/](src/tara/docgen/) |
| Tools | [tools.py](src/tara/tools.py), [orchestration/](src/tara/orchestration/), [workflows.py](src/tara/workflows.py) |

---

## How to run it

You need Python 3.11+ and a Gemini API key from [Google AI Studio](https://aistudio.google.com/).

**1. Install**
```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev,live,audio,memory,work]"
```

**2. Configure.** Copy [.env.example](.env.example) to `.env` (git-ignored) and fill in the keys (every other option has a default; the full list is [env.full.example](env.full.example)):
```
GEMINI_API_KEY=<your key>
TARA_LIVE_MODEL=<a Gemini Live model id from ai.google.dev/gemini-api/docs/models>
TARA_LLM_MODEL=<a Gemini Flash text model id, for documents and minutes>
TARA_WORK_ENABLED=1                 # secretary, interviews, documents
TARA_MEMORY_ENABLED=1               # remember across meetings
TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory
```
`TARA_GITHUB_TOKEN` is optional and raises the GitHub rate limit.

**3. Set up memory (PostgreSQL)**
```bash
createdb tara_memory
.venv/bin/python -m tara.memory.migrate
```

**4. Join a meeting (the main way to use Tara).** One-time setup for the bot's own Google account and the two virtual audio cables is in [GOOGLE_MEET_BOT](docs/GOOGLE_MEET_BOT.md). After that:
```bash
./dev.sh                                  # asks "Which meeting?", then joins (Enter reuses the last link)
./dev.sh https://meet.google.com/abc-defg-hij   # same, no question
```
Both run exactly this, which you can also type yourself:
```bash
./dev.sh meet join https://meet.google.com/abc-defg-hij --audio devices --half-duplex --transcripts --model-wake
```
Tara joins as her own participant, waits to be admitted, and shows her picture as the camera. She listens and takes notes the whole time and answers only when called ("Hey Tara", at the start, middle or end of a sentence); for 8 seconds after an answer you can keep talking without her name. Useful extras: `--record` (save the audio as WAV files, tell everyone first), `--reply-to-all`, `--no-camera`, `--no-gain`, `--follow-up 0`.

**5. Run an interview (the other way in).** Create one from the terminal: drop a GitHub link and a CV, say how many questions and how hard, and get a code.
```bash
./dev.sh interview new "Feremi"             # asks for the role, GitHub, portfolio and CV (drag the file in)
./dev.sh interview run feremi --meet        # asks for the meeting link, then runs it inside Google Meet
./dev.sh interview run feremi               # or on this computer only (you play the candidate)
./dev.sh interview list                     # prepared interviews and results
```
Reports are written to `work/documents`. See [INTERVIEWS](docs/INTERVIEWS.md).

**Local only, no meeting.** To talk to Tara on this computer (headphones give the best result; give the section a name to keep its memory separate):
```bash
./dev.sh local --headphones
./dev.sh local --section join      # the next numbered meeting of section 'join'
./dev.sh local --list              # sections and their meetings with dates
```
Say "Tara, ..." to get her attention.

**6. Run the tests**
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests && .venv/bin/mypy src
```

More: [LOCAL_DEVELOPMENT](docs/LOCAL_DEVELOPMENT.md), [CONFIGURATION](docs/CONFIGURATION.md), [WORKFLOWS](docs/WORKFLOWS.md).

---

## Sample

An illustrative one-on-one session.

```text
You:   Tara, we're hiring a backend engineer. Prepare Alex from the inbox, GitHub alexkim.
Tara:  On it. I'll tell you when the interview plan is ready.
Tara:  Alex's plan is ready: six questions on Go, PostgreSQL and API design.
You:   Start the interview.
Tara:  Alex, thanks for joining. First question: how would you stop a payment
       from being charged twice if the client retries?
Alex:  I'd use an idempotency key with a unique constraint, inside a transaction.
Tara:  Good. What happens if two requests with the same key arrive together?
...
You:   Finish the interview and make a PDF report.
Tara:  Done. The report is in work/documents. It includes scores with Alex's own
       words as evidence, and it needs a human decision.
You:   Remember we agreed to send the offer decision by Friday.
Tara:  Saved.
```

Next meeting:
```text
You:   What did we decide last time?
Tara:  You agreed to send the offer decision by Friday.
```

---

## What is next
The Google Meet bot is built and tested against a local stand-in ([GOOGLE_MEET_BOT](docs/GOOGLE_MEET_BOT.md)); a first run in a real meeting is next. Real speaker names, calendar roster, teams, a web dashboard and meaning-based search are planned in one phased plan: [GOOGLE_MEET_INTEGRATION](docs/GOOGLE_MEET_INTEGRATION.md) and [DASHBOARD](docs/DASHBOARD.md). Emailing the summary as a document, by voice or automatically, is planned in [EMAIL_DELIVERY](docs/EMAIL_DELIVERY.md) (not built). Every command Tara understands, and which ones carry over to Meet, is in [COMMANDS](docs/COMMANDS.md).

---

## Contributors

Contributions are welcome.

1. Read [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) for the rules.
2. Product intent is in [initial.md](initial.md), project history in [CHANGELOG.md](CHANGELOG.md), and all docs are indexed at [docs/README.md](docs/README.md).
3. Make your change on a branch, run `pytest`, `ruff check src tests` and `mypy src`, and update the docs and changelog when behaviour changes.
4. Never commit keys, `.env` files, candidate data or the `work/` folder.

**Pull requests are open.** Small, focused PRs with tests are easiest to review.

## License

MIT. See [LICENSE](LICENSE).

*A good meeting participant is mostly someone who knows when not to speak.*
