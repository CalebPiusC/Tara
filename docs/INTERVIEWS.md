# Interviews from the command line

Status: implemented and tested offline; the wizard was also run for real against public GitHub, a dropped CV file and the Gemini text model. **Not verified:** a full spoken interview with real Gemini Live (greeting, name check, questions, scoring tool calls) and the quality of the questions over many candidates. The dashboard will later do the same job in a browser ([DASHBOARD](DASHBOARD.md)); Google Meet will turn the final code into a meeting link ([GOOGLE_MEET_INTEGRATION](GOOGLE_MEET_INTEGRATION.md)).

## The three commands
```bash
./dev.sh interview new "Feremi"       # create an interview and get a code
./dev.sh interview list               # prepared interviews and finished results
./dev.sh interview run <code>         # Tara runs it (add --headphones for full duplex)
```

## 1. Create: `interview new`
Tara asks, in this order:
1. **Role** being hired for, and the **skills that matter** (these decide the questions).
2. **GitHub** link or username (public data only).
3. **Portfolio** link (optional).
4. **CV**: drag the file into the terminal window (macOS pastes its path) or paste the path. `.pdf`, `.docx`, `.txt` and `.md` up to 10 MB. The file is copied into the candidate inbox under a safe name; nothing else on disk is read.

Then she reads what can be read (GitHub profile and repositories, the CV, the portfolio page) and says when she has enough context:
> I have gotten enough context about Feremi: 7 skills and 7 projects, from cv, github.

If she found very little she says so and offers to add another source, or to continue with questions based on the role only. Then she asks:
> How many questions should I ask Feremi, and how hard? (for example: 2 easy, or 6 hard)

Plain words work: "2 easy", "six hard", "ask two easy questions". Easy, medium and hard map to entry, intermediate and senior. She writes the questions (about two thirds from the role's skills, the rest personalised to the candidate's own projects), saves everything, and prints a **code** and the command to run it. Flags skip the prompts for scripting: `--role`, `--skills`, `--github`, `--portfolio`, `--cv`, `--amount`.

Today the code feeds `interview run`. When Google Meet is connected, this same step creates the meeting link and Tara waits in it.

## 2. Run: `interview run <code>`
You play the candidate when testing alone. Tara is already there:
1. Greets, introduces herself as an AI interviewer, says the interview is recorded for the hiring team, and asks "You are Feremi, right?"
2. If you confirm, she explains the format and asks the first question. If you are not that person she apologises, says the team will follow up, and stops.
3. After each answer she records a score for one criterion with an **exact quote** of your words (the tool refuses a quote that was not said), follows up if the answer was thin, then moves on.
4. When no questions remain she finishes, thanks you, and says the team will be in touch. She never says a score and never says hire or reject.

In this mode the candidate does not need to say "Tara" to be answered (`lead_interview`), but their identity must be reliable; mute, takeover and the other spoken controls still apply, and a candidate cannot silence her. The interview is its own section (`interview-<code>`), so its transcript and summary never mix with other meetings.

## 3. End and results
When you stop (Ctrl+C) or she finishes, the application ends the interview itself: it saves the evidence-linked result, flags anything missing (for example "only 1 of 2 planned questions were asked", "insufficient evidence for Python"), and writes a DOCX report. The terminal prints the per-criterion averages, the flags and where the report is. The result is **decision support for a person**; it always says human review is required.

`./dev.sh interview list` shows each prepared interview and any finished runs.

## Models and keys
- `TARA_HIRING_MODEL` (optional) chooses the text model for reading CVs and writing questions; if empty it uses `TARA_RECORDER_MODEL`, then `TARA_LLM_MODEL`. In testing the larger Flash model often returned "high demand" errors, and a lite model sometimes looped when extracting from a CV; the prompts now tell it to keep strings short and not repeat, and the application falls back to simple rules and template questions (and says so in a note) when the model fails.
- `TARA_GITHUB_TOKEN` is optional (public access allows 60 requests an hour). If a token is rejected, the reader retries publicly and the wizard notes it, so a stale token no longer blocks GitHub.
- Everything needs `TARA_WORK_ENABLED=1`.

## Safety
- The owner types the paths and links; the model never supplies a path, URL, scope or permission.
- Protected attributes (age, gender, family, religion, health and similar) are redacted before any text reaches a model, and the rubric never scores them.
- CV, GitHub and portfolio text is untrusted data inside delimiters.
- Individual interview reports need reliable speaker identity; with several candidates that depends on per-person audio ([GOOGLE_MEET_INTEGRATION](GOOGLE_MEET_INTEGRATION.md), M3).

## Known limits
Quote checks only see the last 40 turns, so answers must be recorded promptly (the instructions say so). The wizard is English only. There is no email or calendar sending. A real group interview needs the Meet connection.
