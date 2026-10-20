"""Interview wizard: create an interview from the command line, then run it.

    ./dev.sh interview new "Feremi"          ask for GitHub, CV, portfolio, then how many questions and how hard
    ./dev.sh interview list                  prepared interviews and finished results
    ./dev.sh interview run <code>            Tara greets the candidate, confirms their name and runs the questions

`new` is the terminal version of what the dashboard will do. It reads whatever can be read (public GitHub profile, a CV you
drag into the terminal, a portfolio page), says when it has enough context, asks how many questions and how hard, and prints
a code. The code is what `run` needs today and what will turn into a Google Meet link when that is connected.

Nothing here puts a path or a URL under the model's control: the owner types them, the CV is copied into the inbox, and
only the public GitHub API and the portfolio URL you give are fetched. Protected attributes are redacted before any text
reaches a model, and the result is decision support, never a hire or reject.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import shutil
import string
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..orchestration.fetch import SafeFetcher
from ..orchestration.http import StdlibHttpClient
from ..settings import RuntimeSettings
from ..workflows import text_llm_from_settings
from .cv import SUFFIXES
from .github import GitHubReader
from .intake import CandidateBuilder
from .planner import InterviewPlan, QuestionPlanner
from .profile import CandidateProfile, slug
from .workspace import Workspace

MAX_CV_BYTES = 10 * 1024 * 1024
_DIFFICULTY = (("entry", ("easy", "simple", "basic", "entry", "junior", "beginner")),
               ("senior", ("hard", "difficult", "tough", "senior", "advanced", "challenging")),
               ("intermediate", ("medium", "intermediate", "moderate", "normal", "mid")))


def clean_path(raw: str) -> Path | None:
    """What a terminal gives you when you drag a file into it: quotes, escaped spaces, or a file:// URL."""
    text = raw.strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if text.startswith("file://"):
        text = unquote(urlparse(text).path)
    text = re.sub(r"\\(.)", r"\1", text)                   # "My\ CV.pdf" -> "My CV.pdf"
    return Path(text).expanduser()


def parse_amount(text: str, default_n: int = 5) -> tuple[int, str]:
    """'2 easy' -> (2, 'entry'); 'six hard questions' -> (6, 'senior'); nothing -> (5, 'intermediate')."""
    low = text.lower()
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12}
    n = default_n
    m = re.search(r"\d+", low)
    if m:
        n = int(m.group())
    else:
        for w, v in words.items():
            if re.search(rf"\b{w}\b", low):
                n = v
                break
    level = "intermediate"
    for name, keys in _DIFFICULTY:
        if any(re.search(rf"\b{k}\b", low) for k in keys):
            level = name
            break
    return max(1, min(n, 12)), level


def parse_skills(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,;\n]", text) if s.strip()][:10]


def new_code(label: str, taken: set[str]) -> str:
    base = slug(label)
    if base not in taken:
        return base
    while True:
        code = f"{base[:34]}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=4))}"
        if code not in taken:
            return code


def stash_cv(path: Path, inbox: Path, code: str) -> Path:
    """Copy the dropped CV into the inbox (the only place the reader may open) under a safe name."""
    if not path.is_file():
        raise ValueError("that file does not exist")
    if path.suffix.lower() not in SUFFIXES:
        raise ValueError(f"I can read {', '.join(SUFFIXES)} files, not {path.suffix or 'this type'}")
    if path.stat().st_size > MAX_CV_BYTES:
        raise ValueError("that file is larger than 10 MB")
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / f"{code}-cv{path.suffix.lower()}"
    shutil.copy2(path, target)
    return target


class Wizard:
    """The conversation. `ask` reads a line, `say` prints as Tara; both are injected so tests can script it."""

    def __init__(self, workspace: Workspace, builder: CandidateBuilder, planner: QuestionPlanner,
                 ask: Callable[[str], str], say: Callable[[str], None]):
        self.ws, self.builder, self.planner, self.ask, self.say = workspace, builder, planner, ask, say

    def _ask_path(self) -> Path | None:
        for _ in range(3):                                   # a few tries, then move on rather than loop forever
            raw = self.ask("Drag the CV file into this window (or paste its path). Press Enter to skip: ")
            if not raw.strip():
                return None
            path = clean_path(raw)
            try:
                if path is None or not path.is_file():
                    raise ValueError("I cannot find that file")
                if path.suffix.lower() not in SUFFIXES:
                    raise ValueError(f"I can read {', '.join(SUFFIXES)} files, not {path.suffix or 'this type'}")
                return path
            except ValueError as exc:
                self.say(f"{exc}. Try again, or press Enter to skip.")
        return None

    async def create(self, name: str, *, role: str = "", skills: list[str] | None = None, github: str = "",
                     portfolio: str = "", cv: str = "", amount: str = "") -> dict[str, Any] | None:
        name = name.strip()
        if not name:
            name = self.ask("Candidate's name: ").strip()
        if not name:
            self.say("I need a name to refer to the candidate.")
            return None
        self.say(f"Let's set up an interview for {name}. A few questions first.")
        role = role or self.ask(f"What role are you hiring {name} for? ").strip()
        if not role:
            self.say("I need the role to write the questions.")
            return None
        skills = skills or parse_skills(self.ask("Which skills matter most for this role? (comma separated, e.g. Python, SQL): "))
        if not skills:
            skills = [role]
            self.say(f"No skills given, so I will base the questions on the role ({role}).")
        github = github or self.ask("GitHub link or username (Enter to skip): ").strip()
        portfolio = portfolio or self.ask("Portfolio link (Enter to skip): ").strip()
        cv_path = clean_path(cv) if cv else self._ask_path()

        taken = set(self.ws.list_candidates())
        code = new_code(name, taken)
        for attempt in range(2):
            local_cv: Path | None = None
            if cv_path is not None:
                try:
                    local_cv = stash_cv(cv_path, self.ws.inbox, code)
                except ValueError as exc:
                    self.say(f"I could not use the CV: {exc}.")
            if not (github or portfolio or local_cv):
                self.say("I have nothing to read yet. Give me a GitHub link, a portfolio or a CV.")
                github = self.ask("GitHub link or username: ").strip()
                portfolio = portfolio or self.ask("Portfolio link: ").strip()
                cv_path = cv_path or self._ask_path()
                continue
            self.say("Reading what I can find" + (" on GitHub" if github else "") + (", the portfolio" if portfolio else "")
                     + (", the CV" if local_cv else "") + " ... (the text model can take up to a minute when it is busy)")
            profile = await self.builder.build(name, role, cv_path=local_cv, portfolio_url=portfolio, github=github)
            profile.candidate_id = code
            found = len(profile.skills) + len(profile.projects) + (1 if profile.summary else 0)
            where = ", ".join(dict.fromkeys(s.kind for s in profile.sources)) or "nothing"
            if found >= 2:
                self.say(f"I have gotten enough context about {name}: {len(profile.skills)} skills and {len(profile.projects)} "
                         f"projects, from {where}.")
                break
            self.say(f"I only found a little about {name} (from {where}).")
            for w in profile.warnings[:3]:
                self.say(f"  note: {w}")
            if attempt == 0 and self.ask("Add another source (GitHub, portfolio or CV) now? [y/N]: ").strip().lower().startswith("y"):
                github = github or self.ask("GitHub link or username (Enter to skip): ").strip()
                portfolio = portfolio or self.ask("Portfolio link (Enter to skip): ").strip()
                cv_path = cv_path or self._ask_path()
                continue
            if not self.ask("Continue with questions based on the role only? [y/N]: ").strip().lower().startswith("y"):
                self.say("Okay, nothing was saved.")
                return None
            break
        else:  # the loop ended without a profile
            self.say("Okay, nothing was saved.")
            return None

        n, level = parse_amount(amount or self.ask(
            f"How many questions should I ask {name}, and how hard? (for example: 2 easy, or 6 hard) [5 medium]: "))
        self.say(f"Writing {n} {'easy' if level == 'entry' else 'hard' if level == 'senior' else 'medium'} question"
                 f"{'s' if n != 1 else ''} ...")
        plan = await self.planner.plan(profile, role, skills, n=n, difficulty=level)
        self.ws.save_candidate(code, {"profile": profile.to_dict(), "plan": plan.to_dict(), "requirements": skills,
                                      "difficulty": level})
        return {"code": code, "profile": profile, "plan": plan, "difficulty": level}


def build_wizard(ask: Callable[[str], str] = input, say: Callable[[str], None] = lambda t: print(f"Tara: {t}", flush=True)) -> Wizard:
    settings = RuntimeSettings.from_env(os.environ)
    w = settings.work
    ws = Workspace(w.dir)
    ws.ensure()
    model = w.hiring_model or w.recorder_model or settings.gemini.model
    llm = text_llm_from_settings(settings.gemini.api_key, model, 60.0)
    http = StdlibHttpClient(user_agent=settings.tools.user_agent)
    builder = CandidateBuilder(ws.inbox, llm, SafeFetcher(user_agent=settings.tools.user_agent, timeout_s=settings.tools.http_timeout_s),
                               GitHubReader(http, w.github_token, max_repos=w.github_max_repos), llm_timeout_s=75.0)
    return Wizard(ws, builder, QuestionPlanner(llm, timeout_s=75.0), ask, say)


def describe_plan(plan: InterviewPlan) -> list[str]:
    return [f"  {i + 1}. [{q.criterion}] {q.text}" for i, q in enumerate(plan.questions)]


def list_interviews(ws: Workspace) -> list[str]:
    done: dict[str, list[str]] = {}
    for iid in ws.list_interviews():
        try:
            r = ws.load_interview(iid).get("result", {})
        except Exception:  # noqa: BLE001
            continue
        done.setdefault(str(r.get("candidate_id", "")), []).append(f"{iid} ({r.get('questions_covered', 0)}/{r.get('questions_total', 0)} covered)")
    rows = []
    for cid in ws.list_candidates():
        try:
            d = ws.load_candidate(cid)
            prof = CandidateProfile.from_dict(d["profile"])
            plan = InterviewPlan.from_dict(d["plan"])
        except Exception:  # noqa: BLE001
            continue
        rows.append(f"{cid}  {prof.label} for {prof.role_title or plan.role_title}, {len(plan.questions)} questions, "
                    f"{d.get('difficulty', 'medium')}" + (f"  | finished: {'; '.join(done[cid])}" if cid in done else "  | not run yet"))
    return rows


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tara.hiring.wizard", description="Create and list interviews.")
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="create an interview")
    n.add_argument("name", nargs="?", default="")
    n.add_argument("--role", default="")
    n.add_argument("--skills", default="", help="comma separated")
    n.add_argument("--github", default="")
    n.add_argument("--portfolio", default="")
    n.add_argument("--cv", default="")
    n.add_argument("--amount", default="", help="for example '2 easy' or '6 hard'")
    sub.add_parser("list", help="list prepared interviews and results")
    return p


async def _new(args: argparse.Namespace) -> int:
    wiz = build_wizard()
    out = await wiz.create(args.name, role=args.role, skills=parse_skills(args.skills), github=args.github,
                           portfolio=args.portfolio, cv=args.cv, amount=args.amount)
    if out is None:
        return 1
    prof: CandidateProfile = out["profile"]
    plan: InterviewPlan = out["plan"]
    for note in prof.warnings + plan.notes:
        wiz.say(f"note: {note}")
    print()
    print(f"Interview ready for {prof.label}: {len(plan.questions)} questions, "
          f"{'easy' if out['difficulty'] == 'entry' else 'hard' if out['difficulty'] == 'senior' else 'medium'}.")
    print("\n".join(describe_plan(plan)))
    print(f"\n  Code:  {out['code']}")
    print(f"  Run it:  ./dev.sh interview run {out['code']}")
    print("  (When Google Meet is connected, this step will create the meeting link instead.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "list":
            ws = Workspace(RuntimeSettings.from_env(os.environ).work.dir)
            rows = list_interviews(ws)
            print("\n".join(rows) if rows else "No interviews prepared yet. Create one with: ./dev.sh interview new \"Name\"")
            return 0
        return asyncio.run(_new(args))
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Nothing was saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
