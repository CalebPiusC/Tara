# Emailing the meeting summary

**Status: implemented with the Resend API and tested offline with a fake HTTP layer and a dry-run mode (`src/tara/delivery/email.py`, tool `email_summary`). No real email has been sent yet.** Provider docs: https://resend.com/docs/api-reference/emails/send-email (accessed 2026-10-22).

## Setup
Put these in `.env` (the key only ever lives in the environment; nothing here prints it):
```
TARA_RESEND_API_KEY=re_...
TARA_EMAIL_TO=you@example.com        # the allow-list, comma separated; mail goes nowhere else
TARA_EMAIL_AUTO=0                    # 1 = also mail the minutes when every meeting ends
TARA_EMAIL_DRY_RUN=0                 # 1 = write to work/email-dry-run instead of sending (try this first)
TARA_EMAIL_FROM=Tara <onboarding@resend.dev>   # default; without a verified domain Resend only delivers to your own account address
```
The email is a designed card with Tara's picture, the headline summary, decisions and action items, and the document attached. Use `TARA_EMAIL_FROM=Tara <hello@appmd.dev>` for a verified-domain sender.

Say "Tara, email me the summary": she says the masked address, asks, waits for a clear yes from a person, then sends. Every attempt is logged to `work/email-audit.jsonl` (masked address, file name, outcome; no content, no key).


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

## How it was built (was: planned pieces)
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
