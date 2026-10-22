"""Google Meet bot: a dedicated browser participant that joins a meeting, hears it, and speaks into it.

Layers, kept separate from the interview engine (see docs/google-meet/GOOGLE_MEET_BOT.md):
  controller  sessions and locks (one worker per meeting, no duplicate joins)
  worker      Chrome through Playwright: join, wait to be admitted, watch the room, leave, reconnect
  bridge      in-page audio: remote audio out of the page, Tara's speech in as the microphone
  adapter     the MeetingAdapter the Live voice engine already understands
Nothing here records or stores audio or video, and nothing here decides whether Tara may speak.
"""
