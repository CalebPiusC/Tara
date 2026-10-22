"""`tara`: one command for everything that runs. Each subcommand hands its arguments to the module that owns it.

    tara meet join <link> [...]      Google Meet bot          (tara.meet.run)
    tara interview new|list [...]    candidate wizard         (tara.hiring.wizard)
    tara prepare [...]               prepare a candidate      (tara.hiring.prepare)
    tara demo [...]                  local mic/speaker demo   (tara.voice.demo)
    tara smoke                       one real Live turn       (tara.voice.smoke)
    tara migrate [...]               memory tables            (tara.memory.migrate)
    tara tools                       list every tool Tara can call

Modules are imported only when their command runs, so `tara --help` needs none of the optional extras.
"""
from __future__ import annotations

import importlib
import sys

COMMANDS: dict[str, tuple[str, str]] = {
    "meet": ("tara.meet.run", "Google Meet bot: check, login, probe, join"),
    "interview": ("tara.hiring.wizard", "Candidate wizard: new, list"),
    "prepare": ("tara.hiring.prepare", "Prepare a candidate brief from a CV, GitHub and a portfolio"),
    "demo": ("tara.voice.demo", "Local microphone/speaker conversation"),
    "smoke": ("tara.voice.smoke", "One real Gemini Live turn (needs a key; opt-in)"),
    "migrate": ("tara.memory.migrate", "Create or update the PostgreSQL memory tables"),
}


def _usage() -> str:
    width = max(map(len, [*COMMANDS, "tools"]))
    rows = [f"  {name:<{width}}  {help_}" for name, (_, help_) in COMMANDS.items()]
    rows.append(f"  {'tools':<{width}}  List every tool the model can call, by group")
    return "usage: tara <command> [args...]\n\ncommands:\n" + "\n".join(rows) + "\n\nRun `tara <command> --help` for details."


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "tools":
        from .tools.catalog import render
        print(render())
        return 0
    if cmd not in COMMANDS:
        print(f"tara: unknown command {cmd!r}\n\n{_usage()}", file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[cmd][0])
    sys.argv = [f"tara {cmd}", *rest]          # the modules parse sys.argv themselves
    result = module.main()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
