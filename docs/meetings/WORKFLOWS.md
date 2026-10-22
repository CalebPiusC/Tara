# Work features: secretary, interviews, documents

Status: **implemented and tested offline with fakes**. Not verified: real Gemini tool calls, a live Flash call, live GitHub calls.

## What it does
- **Memory tiers.** Each turn is ephemeral, session (meeting notes) or a long-term candidate. Automatic long-term saves are candidates with an expiry (`TARA_AUTO_MEMORY_TTL_DAYS`) and become confirmed only after repeat sightings or an explicit confirmation. Sensitive text and personal facts from unknown speakers are skipped.
- **Secretary.** `recall_meeting` and `meeting_recap` answer "what did we say about X". A quiet note can remind the model that something was already confirmed.
- **Interviews.** Prepare a candidate from a CV in `work/inbox`, a GitHub handle or profile URL, and a portfolio link. Questions follow the role and required skills. Answers are judged semantically against the question, not against an exact reference wording; valid alternatives can earn credit, an incorrect or unclear first answer gets one retry, and each question has a code-owned three-minute limit that starts after the question playback finishes (with a tool-return fallback). Output is a graded report that preserves every attempt, flags timeouts, and always requires human review. The legacy exact-quote scoring path remains for compatibility. Tara never says hire or reject.
- **Documents.** Gemini Flash writes the text from stored facts only. Python renders DOCX, PDF or Markdown. Kinds: minutes, interview_report, summary, custom.

## Where to switch it on
Private `.env` (copy from `.env.example`): `TARA_WORK_ENABLED=1` (secretary, interviews, documents), `TARA_MEMORY_ENABLED=1` plus `TARA_MEMORY_DATABASE_URL` (carry memory into the next meeting), `TARA_LLM_MODEL` (Flash text). `TARA_AUTO_MEMORY=candidate` and `TARA_AUTO_MINUTES=1` are on by default once work is enabled. At meeting end Tara writes minutes to `work/documents` and remembers confirmed decisions and actions; unconfirmed claims stay in the file only.

The terminal interview wizard (`./dev.sh interview new` and `run`) is described in [INTERVIEWS](../interviews/INTERVIEWS.md).

## Run it
1. `pip install -e ".[dev,live,audio,memory,work]"`
2. In your private `.env`: `TARA_WORK_ENABLED=1`, `TARA_LLM_MODEL=<Flash text model id from ai.google.dev>`, optional `TARA_GITHUB_TOKEN`.
3. Prepare a candidate (owner CLI):
   `python -m tara.hiring.prepare --label alex --role "Backend Engineer" --require Go --cv alex.pdf --github alexkim --questions 6`
4. Start the demo, then ask Tara to start the interview for the candidate.

## Resources
| Need | Where |
|---|---|
| Gemini key | `GEMINI_API_KEY` |
| Flash text model | `TARA_LLM_MODEL` |
| GitHub | public REST, 60 requests/hour; `TARA_GITHUB_TOKEN` raises it to 5000 |
| Libraries | python-docx (MIT), pypdf (BSD-3), fpdf2 (LGPL-3.0, used unmodified) |
| Fonts | `TARA_DOC_FONT*` for DOCX; `TARA_PDF_FONT_*` Unicode .ttf for PDF |

## Security rules
- Starting or finishing an interview, creating documents and preparing candidates need an organizer or recruiter with reliable identity.
- The model never supplies file paths, scopes or model ids. Files are confined to `TARA_WORK_DIR`.
- Names and contact details, and protected attributes, are redacted before any text reaches a model.
- CV, web and transcript text is untrusted data wrapped in delimiter tags.

## Limits
Jobs live in process and vanish on restart. The legacy quote check sees only the last 40 turns; the semantic path captures the live answer before the committed turn. The evaluator is English only and its real spoken behaviour is unverified. There is no email sending. DOCX output uses python-docx, not an MCP server.
