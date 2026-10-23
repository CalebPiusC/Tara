#!/usr/bin/env bash
# Run Tara locally with one command. The meeting is the main way to use her.
#
#   ./dev.sh                         MEETING: asks for the Google Meet link (Enter reuses the last one), then joins with
#                                    --audio devices --half-duplex --transcripts --model-wake
#   ./dev.sh <link or code> [flags]  same, without the question; extra flags are passed on (e.g. --record, --reply-to-all)
#   ./dev.sh meet join <link> ...    the same thing spelled out, with every flag under your control
#   ./dev.sh meet check|login|probe  set up and inspect the bot (docs/google-meet/GOOGLE_MEET_BOT.md)
#
#   ./dev.sh interview new "Name"    create an interview (GitHub, CV, questions) and get a code
#   ./dev.sh interview list          prepared interviews and results
#   ./dev.sh interview run <code>    run that interview on this computer (you play the candidate when testing)
#   ./dev.sh interview run <code> --meet   run it inside a Google Meet (asks for the link)
#
#   ./dev.sh local [flags]           talk to Tara on this computer only, no meeting:
#       --headphones  --quiet  --transcripts  --section NAME  --type standup  --list
# Reads keys from .env; never prints them.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "Missing .env. Copy .env.example to .env and fill in the keys."; exit 1; }
[ -x .venv/bin/python ] || { echo "Missing .venv. Run: make setup"; exit 1; }

set -a; source .env; set +a

MEET_DEFAULTS=(--audio devices --half-duplex --transcripts --model-wake)
LAST_LINK_FILE="work/meet/last-link"

launch() { if [ -n "${TARA_DRY_RUN:-}" ]; then echo "$@"; exit 0; fi; exec "$@"; }   # TARA_DRY_RUN=1 only prints the command

ensure_memory_schema() {
  case "${TARA_MEMORY_ENABLED:-0}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) ;;
    *) return 0 ;;
  esac
  if command -v pg_isready >/dev/null && ! pg_isready -q; then
    echo "PostgreSQL is not running. Start it (brew services start postgresql) or set TARA_MEMORY_ENABLED=0."; exit 1
  fi
  .venv/bin/python -m tara.memory.migrate >/dev/null
}

ask_link() {
  local last="" answer=""
  [ -f "$LAST_LINK_FILE" ] && last="$(cat "$LAST_LINK_FILE")"
  if [ -n "$last" ]; then read -r -p "Which meeting? Paste the Google Meet link or code [Enter = $last]: " answer
  else read -r -p "Which meeting? Paste the Google Meet link or code: " answer; fi
  LINK="${answer:-$last}"
  [ -n "$LINK" ] || { echo "No meeting given."; exit 2; }
  mkdir -p "$(dirname "$LAST_LINK_FILE")"; printf '%s' "$LINK" > "$LAST_LINK_FILE"
}

# the meeting is the main way in: no arguments, or a link/code first
case "${1:-}" in
  "" | http*://* | meet.google.com/* | [a-z][a-z][a-z]-[a-z][a-z][a-z][a-z]-[a-z][a-z][a-z])
    if [ $# -eq 0 ]; then ask_link; else LINK="$1"; shift; mkdir -p "$(dirname "$LAST_LINK_FILE")"; printf '%s' "$LINK" > "$LAST_LINK_FILE"; fi
    launch .venv/bin/python -m tara.meet.run join "$LINK" "${MEET_DEFAULTS[@]}" "$@" ;;
esac

# google meet bot (see docs/google-meet/GOOGLE_MEET_BOT.md)
if [ "${1:-}" = "meet" ]; then
  sub="${2:-}"; shift 2 || true
  case "$sub" in
    check) exec .venv/bin/python -m tara.meet.run check ;;
    login) exec .venv/bin/python -m tara.meet.run login ;;
    probe) exec .venv/bin/python -m tara.meet.run probe "${1:?usage: ./dev.sh meet probe <meet link>}" ;;
    join)  link="${1:?usage: ./dev.sh meet join <meet link or code> [--interview <code>] [--address-only] [--transcripts]}"; shift
           ensure_memory_schema
           exec .venv/bin/python -m tara.meet.run join "$link" "$@" ;;
    *)     echo "usage: ./dev.sh meet check | login | probe <link> | join <link> --interview <code>"; exit 2 ;;
  esac
fi

# interview commands (see docs/interviews/INTERVIEWS.md)
if [ "${1:-}" = "interview" ]; then
  sub="${2:-}"; shift 2 || true
  case "$sub" in
    new)  exec .venv/bin/python -m tara.hiring.wizard new "$@" ;;
    list) exec .venv/bin/python -m tara.hiring.wizard list ;;
    run)  code="${1:?usage: ./dev.sh interview run <code> [--meet | --headphones]}"; shift
          if [ "${1:-}" = "--meet" ]; then
            shift; ask_link
            ensure_memory_schema
            launch .venv/bin/python -m tara.meet.run join "$LINK" "${MEET_DEFAULTS[@]}" --interview "$code" "$@"
          fi
          mode="--speaker"; [ "${1:-}" = "--headphones" ] && mode="--headphones"
          ensure_memory_schema
          exec .venv/bin/python -m tara.voice.demo --interview "$code" $mode --debug ;;
    *)    echo "usage: ./dev.sh interview new \"Name\" | list | run <code> [--meet | --headphones]"; exit 2 ;;
  esac
fi

[ -n "${GEMINI_API_KEY:-}" ] || { echo "GEMINI_API_KEY is empty in .env"; exit 1; }
[ -n "${TARA_LIVE_MODEL:-}" ] || { echo "TARA_LIVE_MODEL is empty in .env"; exit 1; }

ensure_memory_schema

[ "${1:-}" = "local" ] && shift
MODE="--speaker"; DEBUG="--debug"; MEETING=(); TRANSCRIPTS=""; TYPE=()
while [ $# -gt 0 ]; do
  case "$1" in
    --headphones) MODE="" ;;
    --quiet) DEBUG="" ;;
    --transcripts) TRANSCRIPTS="--transcripts" ;;
    --type) TYPE=(--type "${2:?--type needs a value}"); shift ;;
    --section|--meeting) MEETING=(--section "${2:?--section needs a name}"); shift ;;
    --list) exec .venv/bin/python -m tara.voice.demo --list-meetings ;;
  esac
  shift
done

exec .venv/bin/python -m tara.voice.demo --one-on-one $MODE $DEBUG $TRANSCRIPTS ${MEETING[@]+"${MEETING[@]}"} ${TYPE[@]+"${TYPE[@]}"}
