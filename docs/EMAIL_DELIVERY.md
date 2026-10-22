# Emailing the meeting summary (designed, not built)

**Status: designed only. No email code exists.** This page records the plan so it can be built after the multi-person Meet test.

## What the owner wants
1. At the end of a meeting Tara can email the summary or minutes to the owner as a document (DOCX or PDF).
2. During the meeting the owner can say "Tara, send the summary to my email" and she sends what exists so far.

## Rules (outward-facing action, so strict)
- **Recipients come only from an owner-configured allow-list** (`TARA_EMAIL_TO`, one or more addresses). Never from the model, the transcript or anything a participant says. "Send it to bob@x.com" is refused unless that address is on the list.
- **Explicit trigger only.** Either the owner turned on automatic send at meeting end (`TARA_EMAIL_AUTO=1`), or a trusted role asked by voice. Candidates and unknown speakers cannot trigger it.
- **Spoken confirmation first** for the voice command: "I will send the minutes to <address>. Say yes to send." Only a clear yes sends, same pattern as `delete_meeting`.
- **Attachments come from the existing document store** (the same DOCX or PDF made by `create_document`), scoped to the current meeting. A meeting never mixes with another.
- **Secrets only in the environment** (an SMTP app password, or a Gmail API token). Nothing in code, logs or the changelog.
- **Audit log and dry run.** Every send is recorded (time, recipients, document id, outcome, no body text). `TARA_EMAIL_DRY_RUN=1` writes the message to a local folder instead of sending.
- **Failure is spoken and shown**, never silent: "I could not send the email, the document is saved as <name>".
- Interview reports contain candidate data: they are never emailed automatically, only on an explicit command from the recruiter or organizer.

## Planned pieces
| Piece | Notes |
|---|---|
| `src/tara/delivery/email.py` | provider-neutral `EmailSender` interface; an SMTP implementation first (stdlib `smtplib`, no new service) |
| `send_document` tool | arguments: document id only; recipients resolved from the allow-list; confirmation step before send |
| Meeting end hook | if `TARA_EMAIL_AUTO=1`, make the minutes and send after the final summary |
| Tests | fake sender: allow-list enforced, confirmation required, cross-meeting document refused, dry run, failure reported |

## Voice commands (planned)
| Say | Result |
|---|---|
| "Tara, email me the summary" | confirms the address, then sends the current summary as a document |
| "Tara, send the minutes as a PDF to my email" | same, PDF |
| "Tara, stop emailing me after meetings" | turns the automatic send off for this meeting |

## Open questions
- SMTP app password versus Gmail API OAuth (app password is simpler, OAuth is cleaner for a team).
- Whether the bot Google account or the owner's account should be the sender.
