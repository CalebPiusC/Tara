# Interviews from the command line

Status: implemented and tested offline, including a deterministic semantic evaluator, one retry, a three-minute code-owned deadline per question, and an explicit interview state machine; the wizard was also run for real against public GitHub, a dropped CV file and the Gemini text model. **Not verified:** a full spoken interview with real Gemini Live (greeting, name check, questions, semantic tool calls, retry, timeout and recovery after an interruption), and the quality of the questions or the evaluator over many real candidates. The dashboard will later do the same job in a browser ([DASHBOARD](../product/DASHBOARD.md)); Google Meet will turn the final code into a meeting link ([GOOGLE_MEET_INTEGRATION](../google-meet/GOOGLE_MEET_INTEGRATION.md)).

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
2. If you confirm, she explains the format (one question at a time, up to three minutes for each answer), asks the first question, and starts that question's timer when its audio playback finishes. If you are not that person she apologises, says the team will follow up, and stops.
3. After each answer she calls `interview_record_answer` with the question id only. The application asks the configured text model to judge the **meaning** of the answer, not exact wording: technical correctness, understanding, relevance, completeness, examples, trade-offs and reasoning. The reference notes and `signals` are guidance, never an answer key, and a valid alternative approach can earn credit. The model returns `correctness`, `technical_accuracy`, `completeness`, `reasoning`, `feedback`, `hint` and `missing_concepts`; code validates the fields and computes the 0–10 score from the component rubric (weighted technical accuracy 45%, completeness 25%, reasoning 30%, with caps for incorrect/unclear and partial answers). If the model calls an answer `correct`, `mostly_correct` or a `valid_alternative` while its own technical-accuracy or completeness score is below 5/10, code downgrades it to `partially_correct`; only `correct`, `mostly_correct` and `valid_alternative` can pass. Confidence alone never raises a score.
4. The first incorrect or unclear answer gets **one** retry with a short, proportional hint; the retry shares the original deadline and does not advance the question. A second failure is acknowledged briefly and the interview moves on without revealing the expected answer. A partially correct answer is acknowledged as partial and followed up only when it makes sense. A correct answer or a valid alternative is credited specifically.
5. The timer is monotonic and code-owned. The application sets a fallback deadline when the question is issued, then starts the answer clock once when the first completed audio playback after that issue finishes; this uses the existing playback-completion signal and avoids counting the time Tara spends reading the question. If playback is muted, interrupted or never completes, the tool-return fallback stays in force. The signal is response-level, not word-level: if the model adds a separate preamble response before the question, the clock can start a few seconds early. At three minutes the application stops accepting content for that question, records a timeout (score 0 when there was no completed answer), does not evaluate the incomplete answer, and moves on. An answer completed before the deadline may finish evaluating after it. A retry shares the same deadline. Duplicate playback callbacks, repeated tool calls, final transcripts and end-of-turn events cannot reset the clock, evaluate twice or advance twice.
6. When no questions remain she finishes, thanks you, and says the team will be in touch. She never reads out a score, correctness label, missing-concept list or other internal field, and never says hire or reject.

In this mode the candidate does not need to say "Tara" to be answered (`lead_interview`), but their identity must be reliable; mute, takeover and the other spoken controls still apply, and a candidate cannot silence her. The interview is its own section (`interview-<code>`), so its transcript and summary never mix with other meetings.

## 3. End and results
When you stop (Ctrl+C) or she finishes, the application ends the interview itself: it saves the result, flags anything missing (for example "only 1 of 2 planned questions were asked", "insufficient evidence for Python", a timed-out question, or a question that used a retry), and writes a DOCX report. `question_results` keeps every attempt, the final question score and whether the question timed out; the criterion averages use only the final attempt per question, so a retry is not counted as an extra question. The terminal prints the per-criterion averages, the flags and where the report is. The result is **decision support for a person**; it always says human review is required. The legacy exact-quote path (`score` plus `quote`) remains available and quote-checked for older callers, but the new interview prompt does not use it.

`./dev.sh interview list` shows each prepared interview and any finished runs.

## Models and keys
- `TARA_HIRING_MODEL` (optional) chooses the text model for reading CVs and writing questions; if empty it uses `TARA_RECORDER_MODEL`, then `TARA_LLM_MODEL`. In testing the larger Flash model often returned "high demand" errors, and a lite model sometimes looped when extracting from a CV; the prompts now tell it to keep strings short and not repeat, and the application falls back to simple rules and template questions (and says so in a note) when the model fails.
- The same text model evaluates answers. If it is unavailable, times out or returns malformed JSON, a deterministic concept-matching fallback scores the answer and is labelled `source: "fallback"`; it never blocks the interview. The fallback is deliberately strict: a multi-word concept needs the full phrase or every meaningful token, a single overlapping word is not enough, and an answer missing a concept is capped as partial and cannot pass. A very short or vague answer is marked `needs_clarification`.
- `TARA_INTERVIEW_QUESTION_LIMIT_S` (default `180`) is the hard per-question limit including retries. `TARA_INTERVIEW_RETRY_LIMIT` (default `1`, allowed 0–2) is the number of retries after an incorrect or unclear first answer. `TARA_INTERVIEW_EVAL_TIMEOUT_S` (default `20`) bounds the evaluator call. `TARA_INTERVIEW_ANSWER_GRACE_S` (default `2`) waits briefly for delayed transcription before saying no answer was heard.
- `TARA_GITHUB_TOKEN` is optional (public access allows 60 requests an hour). If a token is rejected, the reader retries publicly and the wizard notes it, so a stale token no longer blocks GitHub.
- Everything needs `TARA_WORK_ENABLED=1`.

## Safety
- The owner types the paths and links; the model never supplies a path, URL, scope or permission.
- Protected attributes (age, gender, family, religion, health and similar) are redacted before any text reaches a model, and the rubric never scores them.
- CV, GitHub and portfolio text is untrusted data inside delimiters.
- Individual interview reports need reliable speaker identity; with several candidates that depends on per-person audio ([GOOGLE_MEET_INTEGRATION](../google-meet/GOOGLE_MEET_INTEGRATION.md), M3).

## Known limits
The semantic path uses the live input transcript and the committed turn; the legacy exact-quote path only sees the last 40 turns. The evaluator is English-only and its judgement is unverified against real candidates; the fallback is deliberately conservative, cannot judge meaning and is not a substitute for the model. The three-minute timer and retry/state transitions are unit-tested with a fake clock and a fake playback callback, but the real provider's end-of-turn timing, the question response boundary and a spoken timeout have not been observed live. A short thinking pause is not treated as the end of an answer, but a poor microphone or a very long silence can still delay the provider's turn end. The wizard is English only. There is no email or calendar sending. A real group interview needs the Meet connection.
