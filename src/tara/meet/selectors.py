"""What the bot looks for on Google Meet pages, in one place.

**Unverified against the live Meet site.** Google changes this page without notice and the labels depend on the account's
language. They are written from how Meet has labelled these controls (aria-labels and visible text); the first real test must
check each one, and a failure here shows up as the bot reporting an unknown state, never as a guess. Everything is matched
case-insensitively as a substring so small wording changes still work. English only.
"""
from __future__ import annotations

# --- pre-join screen ---
NAME_INPUT = 'input[aria-label*="name" i], input[placeholder*="name" i]'
JOIN_BUTTONS = ("join now", "ask to join", "join here too", "switch here")          # the order matters: first visible wins
MIC_TOGGLE_ON = 'button[aria-label*="turn off microphone" i], [role="button"][aria-label*="turn off microphone" i]'
CAMERA_TOGGLE_ON = 'button[aria-label*="turn off camera" i], [role="button"][aria-label*="turn off camera" i]'
DISMISS_BUTTONS = ("got it", "dismiss", "no thanks", "continue without microphone and camera", "not now")

# --- states, as visible text ---
WAITING_TEXT = ("asking to be let in", "asking to join", "someone will let you in soon", "waiting for the host", "you'll join when")
DENIED_TEXT = ("you can't join this video call", "denied your request", "removed from the meeting", "you were removed")
ENDED_TEXT = ("you left the meeting", "the meeting has ended", "this video call has ended", "return to home screen")
SIGN_IN_TEXT = ("sign in", "use your google account")
IN_CALL = 'button[aria-label*="leave call" i], [role="button"][aria-label*="leave call" i]'

# --- people ---
PEOPLE_BUTTON = 'button[aria-label*="people" i], [role="button"][aria-label*="people" i]'
PARTICIPANT_ITEMS = '[role="list"][aria-label*="participants" i] [role="listitem"], [data-participant-id]'
LEAVE_BUTTON = IN_CALL
