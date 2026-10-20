"""Owner-run command: prepare a candidate brief and interview plan before the meeting.

    set -a; source .env; set +a
    .venv/bin/python -m tara.hiring.prepare --label "Alex" --role "Go Backend Engineer" \
        --require Go --require PostgreSQL --require "system design" \
        --cv ~/Downloads/alex-cv.pdf --github alexdev --portfolio https://alex.dev

Reads the CV from a path YOU give (this is not a model-controlled path), fetches the public portfolio page and public
GitHub profile, asks the text model (TARA_LLM_MODEL, e.g. a Flash model) to extract a profile and draft questions, and
saves both under TARA_WORK_DIR. Without a model it still works with simple rules and template questions.
Prints counts and warnings only, never the CV text.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from ..orchestration.fetch import SafeFetcher
from ..orchestration.http import StdlibHttpClient
from ..settings import RuntimeSettings
from ..workflows import text_llm_from_settings
from .github import GitHubReader
from .intake import CandidateBuilder
from .planner import QuestionPlanner
from .workspace import Workspace


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tara.hiring.prepare", description="Prepare a candidate brief and interview plan.")
    p.add_argument("--label", required=True, help="how you refer to the candidate (not used for scoring)")
    p.add_argument("--role", required=True, help="the role being hired for")
    p.add_argument("--require", action="append", default=[], help="a required skill or topic (repeat for several)")
    p.add_argument("--cv", default="", help="path to a CV (.pdf, .docx, .txt, .md)")
    p.add_argument("--github", default="", help="GitHub username or profile URL")
    p.add_argument("--portfolio", default="", help="portfolio page URL")
    p.add_argument("--questions", type=int, default=8, help="number of interview questions (3 to 12)")
    p.add_argument("--difficulty", default="intermediate", choices=("entry", "intermediate", "senior"))
    return p


async def run(args: argparse.Namespace) -> int:
    settings = RuntimeSettings.from_env(os.environ)
    w = settings.work
    if not args.require:
        print("give at least one --require skill so the questions can be based on the role", file=sys.stderr)
        return 2
    if not (args.cv or args.github or args.portfolio):
        print("give --cv, --github or --portfolio (at least one source)", file=sys.stderr)
        return 2
    ws = Workspace(w.dir)
    ws.ensure()
    llm = text_llm_from_settings(settings.gemini.api_key, settings.gemini.model, 30.0)
    http = StdlibHttpClient(user_agent=settings.tools.user_agent)
    builder = CandidateBuilder(ws.inbox, llm, SafeFetcher(user_agent=settings.tools.user_agent, timeout_s=settings.tools.http_timeout_s),
                               GitHubReader(http, w.github_token, max_repos=w.github_max_repos))
    cv_path = Path(args.cv).expanduser() if args.cv else None
    profile = await builder.build(args.label, args.role, cv_path=cv_path, portfolio_url=args.portfolio, github=args.github)
    plan = await QuestionPlanner(llm).plan(profile, args.role, args.require, n=args.questions, difficulty=args.difficulty)
    ws.save_candidate(profile.candidate_id, {"profile": profile.to_dict(), "plan": plan.to_dict(), "requirements": args.require})
    print(f"candidate id: {profile.candidate_id}")
    print(f"sources: {', '.join(f'{s.kind}' for s in profile.sources) or 'none'}")
    print(f"skills found: {len(profile.skills)} | projects found: {len(profile.projects)} | questions: {len(plan.questions)}")
    print(f"text model used: {'yes' if plan.used_model else 'no (templates)'}")
    for note in profile.warnings + plan.notes:
        print(f"note: {note}")
    print(f"saved under {ws.base}")
    return 0


def main() -> int:
    return asyncio.run(run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
