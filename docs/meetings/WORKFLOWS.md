# Work features: secretary, interviews, documents

Status: **implemented and tested offline with fakes**. Not verified: real Gemini tool calls, a live Flash call, live GitHub calls.

## What it does
- **Memory tiers.** Each turn is ephemeral, session (meeting notes) or a long-term candidate. Automatic long-term saves are candidates with an expiry (`TARA_AUTO_MEMORY_TTL_DAYS`) and become confirmed only after repeat sightings or an explicit confirmation. Sensitive text and personal facts from unknown speakers are skipped.
- **Secretary.** `recall_meeting` and `meeting_recap` answer "what did we say about X". A quiet note can remind the model that something was already confirmed.
- **Interviews.** Prepare a candidate from a CV in `work/inbox`, a GitHub handle or profile URL, and a portfolio link. Questions follow the role and required skills. Answers are judged semantically against the question, not against an exact reference wording; valid alternatives can earn credit, an incorrect or unclear first answer gets one retry, and each question has a code-owned three-minute limit that starts after the question playback finishes (with a tool-return fallback). Output is a graded report that preserves every attempt, flags timeouts, and always requires human review. The legacy exact-quote scoring path remains for compatibility. Tara never says hire or reject.
- **Documents.** `create_document` remains for existing meeting and interview reports. `create_document_from_source` requires an explicit mode: `meeting`, `web_research`, `tool_catalog`, `approved_knowledge`, or `provided_material`. It also requires a title, audience, purpose, and length (`brief`, `standard`, or `detailed`); format is DOCX by default and may be PDF or Markdown. Tara asks for missing essential details before starting. It never substitutes another source when one is unavailable. Meeting mode uses only the current session by default; `meeting_selector` explicitly selects `previous` or a numbered meeting.
   - `web_research` uses the existing bounded search/fetch path (up to five public sources), cites source IDs, includes URLs and publication dates where available, and reports thin retrieval. The drafting prompt instructs the model to describe conflicting evidence rather than resolve it without support. Code flags missing or unknown citation IDs; it does not prove that a cited page semantically supports a claim.
   - `tool_catalog` deterministically lists tools registered for the current session, with descriptions, parameters, and access type; meeting facts are not used.
   - `approved_knowledge` searches only approved chunks readable in the authenticated user's tenant, section, and ACL scope. It cites document/version/chunk provenance and fails when nothing authorized matches.
   - `provided_material` reads a named file from `work/inbox` only. Supported formats are PDF, DOCX, TXT, and Markdown; the existing reader bounds files to 10 MB, 30 PDF pages, and 60,000 extracted characters, and the document prompt receives at most 20,000 characters. Personal details and protected-attribute lines are heuristically redacted before model use by default. Preserving them requires an explicit authorized-speaker phrase in the current turn and a code-checked opt-in. This is one-off use, not knowledge-base ingestion.
   All source text is untrusted data. Web, knowledge, and provided-material contents are sent to the configured text model for drafting. Document jobs run in the background and require an organizer or recruiter.

## Where to switch it on
Private `.env` (copy from `.env.example`): `TARA_WORK_ENABLED=1` (secretary, interviews, documents), `TARA_MEMORY_ENABLED=1` plus `TARA_MEMORY_DATABASE_URL` (carry memory into the next meeting), and `TARA_LLM_MODEL` (Flash text). `web_research` also needs `TARA_TOOLS_ENABLED=1` and `TARA_SEARCH_API_KEY`; `approved_knowledge` needs `TARA_KNOWLEDGE_ENABLED=1` and memory enabled. For `provided_material`, place the file in `work/inbox` and refer to its filename, not a path. `TARA_AUTO_MEMORY=candidate` and `TARA_AUTO_MINUTES=1` are on by default once work is enabled. At meeting end Tara writes minutes to `work/documents` and remembers confirmed decisions and actions; unconfirmed claims stay in the file only.

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
- For document source mode `provided_material`, only a filename under `work/inbox` is accepted; the resolved path must remain inside that directory. These one-off files are not ingested into approved knowledge.
- Approved-knowledge documents are retrieved through the scope- and ACL-enforcing knowledge service; unapproved or cross-section content is never passed to document generation.
- Names and contact details, and protected attributes, are redacted before any text reaches a model.
- CV, web and transcript text is untrusted data wrapped in delimiter tags.
- Research documents fail rather than substitute meeting notes when search returns no usable sources. Web pages remain untrusted input; generated claims should cite the appended source IDs and URLs.

## Limits
Jobs live in process and vanish on restart. The legacy quote check sees only the last 40 turns; the semantic path captures the live answer before the committed turn. The evaluator is English only and its real spoken behaviour is unverified. There is no email sending. DOCX output uses python-docx, not an MCP server.
