"""Voice-controlled meeting actions as real, permission-checked application actions.

A spoken command is recognized by deterministic code (never by the model alone), authorized against role,
identity confidence, meeting policy and what the platform adapter can actually do, then executed by the
application. Fail-safe direction: anything that reduces Tara's footprint is easy; anything that expands
it (resume, camera, leave, mute others) needs a trusted role.
"""
