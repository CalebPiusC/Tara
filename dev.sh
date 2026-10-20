#!/usr/bin/env bash
# Run Tara locally with one command.
#   ./dev.sh              speakers (mic muted while Tara talks)
#   ./dev.sh --headphones full duplex, you can interrupt her
#   ./dev.sh --quiet      hide debug counters
#   ./dev.sh --section NAME   a section is a chat of numbered meetings; each run is its next meeting
#   ./dev.sh --transcripts  also print what Tara heard and said (to see mishearings)
#   ./dev.sh --type standup  meeting type: general, standup, one_on_one, retro, client_call, brainstorm
#   ./dev.sh interview new "Name"        create an interview (GitHub, CV, questions) and get a code
#   ./dev.sh interview list              prepared interviews and results
#   ./dev.sh interview run <code>        Tara runs that interview (you play the candidate when testing)
#   ./dev.sh --list       list sections and meetings with dates
# Reads keys from .env; never prints them.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "Missing .env. Copy .env.example to .env and fill in the keys."; exit 1; }
[ -x .venv/bin/python ] || { echo "Missing .venv. Run: python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev,live,audio,memory,work]'"; exit 1; }

set -a; source .env; set +a

# interview commands (see docs/INTERVIEWS.md)
if [ "${1:-}" = "interview" ]; then
  sub="${2:-}"; shift 2 || true
  case "$sub" in
    new)  exec .venv/bin/python -m tara.hiring.wizard new "$@" ;;
    list) exec .venv/bin/python -m tara.hiring.wizard list ;;
    run)  code="${1:?usage: ./dev.sh interview run <code> [--headphones]}"; shift
          mode="--speaker"; [ "${1:-}" = "--headphones" ] && mode="--headphones"
          exec .venv/bin/python -m tara.voice.demo --interview "$code" $mode --debug ;;
    *)    echo "usage: ./dev.sh interview new \"Name\" | list | run <code> [--headphones]"; exit 2 ;;
  esac
fi

[ -n "${GEMINI_API_KEY:-}" ] || { echo "GEMINI_API_KEY is empty in .env"; exit 1; }
[ -n "${TARA_LIVE_MODEL:-}" ] || { echo "TARA_LIVE_MODEL is empty in .env"; exit 1; }

if [ "${TARA_MEMORY_ENABLED:-0}" = "1" ]; then
  if command -v pg_isready >/dev/null && ! pg_isready -q; then
    echo "PostgreSQL is not running. Start it (brew services start postgresql) or set TARA_MEMORY_ENABLED=0."; exit 1
  fi
  .venv/bin/python -m tara.memory.migrate >/dev/null
fi

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
