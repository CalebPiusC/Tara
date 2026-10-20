"""WorkServices: the secretary, interview and document tools, wired for one live session.

Everything the model can do here goes through code-owned ToolDefs. The model never supplies a path, a scope, a model id,
a URL to fetch outside the allow-listed readers, or a permission. Starting or finishing an interview, creating documents
and preparing candidates need the CURRENT speaker to be an organizer or recruiter with reliable identity (`ControlRoleGate`).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .config import CONTROL_ROLES, MeetingConfig, Role
from .contracts import LanguageModelProvider
from .control.state import ControlState
from .docgen.composer import KINDS, DocumentComposer, LlmTextGenerator, fallback_markdown
from .docgen.model import DocStyle, parse_markdown
from .docgen.store import FORMATS, DocumentStore
from .errors import PermissionDenied, ToolError
from .hiring.intake import CandidateBuilder
from .hiring.interview import InterviewError, InterviewResult, InterviewSession, result_facts
from .hiring.planner import InterviewPlan, QuestionPlanner
from .hiring.profile import CandidateProfile
from .hiring.workspace import Workspace, WorkspaceError, check_id
from .jobs import JobBoard, JobLimitReached
from .meetings.agenda import Agenda, MeetingClock
from .meetings.brief import opening_brief
from .meetings.items import (
    OpenItems,
    add_action_tool,
    complete_item_tool,
    list_open_items_tool,
    park_item_tool,
)
from .meetings.types import RULES, MeetingType, parse_type
from .meetings.vocab import Vocabulary
from .memory.intent import MemoryWriteGate
from .memory.models import MeetingSession, MemoryScope
from .memory.service import MemoryService
from .metrics import Metrics
from .orchestration.fetch import SafeFetcher
from .participation import Attribution, identity_reliable
from .secretary.auto_memory import AutoMemory, AutoMemoryConfig, TurnRecord
from .secretary.minutes import _line, collect_minutes, minutes_facts
from .secretary.recall import RecallNudger, meeting_recap_tool, recall_meeting_tool
from .secretary.recorder import MeetingRecorder, RecorderConfig
from .settings import WorkSettings
from .tools import ToolDef
from .voice.memory import MeetingMemory, Scope, WorkingMemory


def tool_failure(code: str, message: str, *, retryable: bool = False) -> ToolError:
    """The executor shows the model only `code` and `details`, so the reason must travel in details (bounded, no secrets)."""
    return ToolError(code, message, retryable=retryable, details={"reason": message[:240]})


class ControlRoleGate:
    """True while the latest finished participant turn came from an organizer/recruiter with reliable identity."""

    def __init__(self, roles: dict[str, Role], min_conf: float):
        self.roles, self.min_conf = roles, min_conf
        self._ok = False

    def observe(self, _text: str, att: Attribution) -> None:
        self._ok = identity_reliable(att, self.min_conf) and self.roles.get(att.participant_id or "") in CONTROL_ROLES

    def authorized(self) -> bool:
        return self._ok

    def require(self, what: str) -> None:
        if not self._ok:
            raise tool_failure("not_authorized", f"{what} must be requested by the organizer or recruiter, and I could not confirm who asked")


def doc_style(w: WorkSettings) -> DocStyle:
    h = w.doc_accent
    return DocStyle(font_body=w.doc_font, font_heading=w.doc_font_heading, size_body=w.doc_font_size,
                    accent_rgb=(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), pdf_font_regular=w.pdf_font_regular,
                    pdf_font_bold=w.pdf_font_bold, pdf_font_italic=w.pdf_font_italic)


class WorkServices:
    def __init__(self, settings: WorkSettings, *, session_id: str, scope: Scope, cfg: MeetingConfig, roles: dict[str, Role],
                 names: dict[str, str], meeting_memory: MeetingMemory, working: WorkingMemory, control: ControlState,
                 min_conf: float, llm: LanguageModelProvider | None = None, fetcher: SafeFetcher | None = None,
                 github: Any | None = None, memory_service: MemoryService | None = None,
                 memory_scope: MemoryScope | None = None, metrics: Metrics | None = None,
                 send_note: Callable[[str], None] | None = None, candidate_speakers: frozenset[str] = frozenset(),
                 recorder_llm: LanguageModelProvider | None = None, memory_gate: MemoryWriteGate | None = None,
                 speak: Callable[[str], Awaitable[None]] | None = None,
                 on_kind: Callable[[str | None], None] | None = None):
        self.settings, self.session_id, self.scope, self.cfg = settings, session_id, scope, cfg
        self.roles, self.names, self.memory, self.working, self.control = roles, names, meeting_memory, working, control
        self.metrics = metrics or Metrics()
        self.workspace = Workspace(settings.dir)
        self.store = DocumentStore(settings.dir, doc_style(settings))
        self.jobs = JobBoard(max_running=settings.max_jobs, timeout_s=settings.job_timeout_s, metrics=self.metrics)
        self.llm = llm
        self.composer = DocumentComposer(LlmTextGenerator(llm) if llm is not None else None, timeout_s=settings.job_timeout_s - 5)
        self.planner = QuestionPlanner(llm, timeout_s=settings.job_timeout_s - 5)
        self.builder = CandidateBuilder(self.workspace.inbox, llm, fetcher, github, llm_timeout_s=settings.job_timeout_s - 5)
        self.gate = ControlRoleGate(roles, min_conf)
        self.candidate_speakers = candidate_speakers
        self.session: InterviewSession | None = None
        self.autopilot = False                      # set by begin_interview: the owner started this run from the command line
        self.candidate_label = ""
        self.last_result: InterviewResult | None = None
        self.last_plan: InterviewPlan | None = None
        self.last_brief = ""
        self.auto = AutoMemory(AutoMemoryConfig(settings.auto_memory, settings.auto_memory_ttl_days), session_id=session_id,
                               scope=scope, meeting_memory=meeting_memory, memory_service=memory_service,
                               memory_scope=memory_scope, metrics=self.metrics)
        # meeting type, vocabulary, open items, agenda (docs/MEETINGS.md)
        try:
            self.meeting_type = parse_type(settings.meeting_type)
        except ValueError:
            self.meeting_type = MeetingType.GENERAL
        vfile = settings.vocab_file or str(Path(settings.dir) / "vocabulary.txt")
        self.vocab = Vocabulary(vfile, [t.strip() for t in settings.vocab_terms.split(",") if t.strip()] + list(names.values()))
        self.memory_gate = memory_gate
        self.speak, self.on_kind = speak, on_kind
        self.agenda = Agenda()
        self.clock = MeetingClock(self.agenda, speak if settings.nudges else None)
        self._clock_task: asyncio.Task[None] | None = None
        self.items = (OpenItems(memory_service, memory_scope, session_id)
                      if memory_service is not None and memory_scope is not None and memory_scope.meeting_id else None)
        self.current_meeting: MeetingSession | None = None
        self.recorder: MeetingRecorder | None = None
        if (memory_service is not None and memory_scope is not None and memory_scope.meeting_id
                and settings.auto_record and settings.auto_memory != "off"):
            self.recorder = MeetingRecorder(
                memory_service, memory_scope, session_id=session_id, working=working,
                llm=LlmTextGenerator(recorder_llm) if recorder_llm is not None else self.composer.generator,
                cfg=RecorderConfig(chunk_turns=settings.chunk_turns, digest_timeout_s=settings.digest_timeout_s, episode_retention_days=settings.episode_retention_days,
                                   fact_retention_days=settings.fact_retention_days), metrics=self.metrics,
                vocab=self.vocab, digest_hint=RULES[self.meeting_type].digest_hint)
        self.nudger = RecallNudger(meeting_memory, scope, send_note) if (send_note and settings.recall_nudges) else None
        self._min_conf = min_conf

    # ---- engine hooks ------------------------------------------------------------------------------------------
    def on_transcript(self, text: str, att: Attribution) -> None:
        """Sync, early (finished input transcript): updates who is allowed to start or finish things."""
        self.gate.observe(text, att)

    async def on_turn(self, turn: TurnRecord) -> None:
        """After a participant turn is committed. Never raises."""
        await self.auto.observe(turn)
        if self.recorder is not None:
            await self.recorder.on_turn()
        if self.nudger is not None:
            self.nudger.observe(turn.text)

    # ---- tools -----------------------------------------------------------------------------------------------------
    @property
    def tool_defs(self) -> tuple[ToolDef, ...]:
        defs: list[ToolDef] = [recall_meeting_tool(self.memory, self.scope, self.working), meeting_recap_tool(self.memory, self.scope)]
        defs += [self._recap_tool(), self._set_type_tool(), self._add_vocab_tool(), *self._agenda_tools()]
        if self.items is not None and self.memory_gate is not None:
            defs += [add_action_tool(self.items, self.memory_gate), park_item_tool(self.items, self.memory_gate),
                     list_open_items_tool(self.items), complete_item_tool(self.items, self.memory_gate)]
        if self.settings.documents:
            defs += [self._create_document_tool(), self._job_status_tool()]
        if self.settings.hiring:
            defs += [self._list_candidates_tool(), self._get_candidate_tool(), self._prepare_candidate_tool(),
                     self._start_interview_tool(), self._next_question_tool(), self._record_answer_tool(),
                     self._finish_interview_tool()]
            if not self.settings.documents:
                defs.append(self._job_status_tool())
        return tuple(defs)

    def instructions(self) -> str:
        lines = ["You also act as the team's secretary. Use recall_meeting to answer 'what did we say about X' and meeting_recap for "
                 "'what have we agreed'. Say whether something was confirmed or only mentioned, and say you have no record when "
                 "nothing matches. Never invent meeting history."]
        lines.append("Meeting type: " + RULES[self.meeting_type].behaviour)
        if self.vocab.terms:
            lines.append(self.vocab.instruction())
        lines.append("For \"recap so far\", \"what did I miss\" or a quick summary, call recap_meeting (whole_meeting=true when someone "
                     "joined late) and summarise only what it returns. To set an agenda call set_agenda; use agenda_status and "
                     "agenda_mark as the meeting moves on, and say when an item is covered. When the participant asks you to add "
                     "an action item, park something for later, or says something is done, call add_action, park_item or "
                     "complete_item. Use list_open_items for \"what is still open\". add_vocabulary saves a name or term the "
                     "participant spells out for you. Report success only when a tool returns it.")
        if self.settings.documents:
            lines.append("To produce a PDF, Word document or minutes, call create_document (kinds: minutes, interview_report, summary, "
                         "custom). It starts a background job: say it has started, then call job_status and report the file name "
                         "(saved in the work/documents folder) only once it says completed. Never claim a document exists before "
                         "that, and never say you will create one without calling create_document.")
        if self.settings.hiring:
            lines.append("You can interview candidates. Use list_candidates and get_candidate to see prepared briefs; prepare_candidate "
                         "builds one from a CV file in the inbox, a portfolio link and a GitHub user. To interview: start_interview, then "
                         "ask questions one at a time from interview_next_question, listen, follow up when an answer is thin, and after "
                         "each answer call interview_record_answer with a score 0-4 for one criterion and an EXACT quote of the "
                         "candidate's words. Never score without a quote. Do not use or ask about age, gender, family, religion, "
                         "nationality, health or appearance. At the end call finish_interview and say the result supports a human "
                         "decision; never say hire or reject.")
        return "\n".join(lines)

    # ---- documents ---------------------------------------------------------------------------------------------------
    def _facts_for(self, kind: str, interview_id: str = "") -> dict[str, Any]:
        if kind == "interview_report":
            result, plan, brief = self.last_result, self.last_plan, self.last_brief
            if interview_id:
                data = self.workspace.load_interview(check_id(interview_id))
                result = InterviewResult(**data["result"])
                plan = InterviewPlan.from_dict(data["plan"])
                brief = str(data.get("brief", ""))
            if result is None or plan is None:
                raise tool_failure("no_interview", "There is no finished interview to report on yet")
            return result_facts(result, plan, brief)
        m = collect_minutes(self.memory, self.scope, title=self.cfg.purpose or "Meeting", attendees=self.names.values(),
                            working=self.working, excerpt_turns=15)
        facts = minutes_facts(m, include_excerpt=True)
        if self.last_result is not None and kind != "minutes":
            facts["Latest interview result"] = json.dumps(self.last_result.to_dict())[:3000]
        return facts

    # ---- recap, type, vocabulary, agenda -----------------------------------------------------------------------------
    def _recap_tool(self) -> ToolDef:
        async def handler(whole_meeting: bool = False) -> dict[str, Any]:
            older: list[str] = []
            if whole_meeting and self.recorder is not None:
                older = list(self.recorder._lines)[-60:]
            recent = [f"{sp}: {self.vocab.correct(tx)}" for sp, tx in list(self.working.turns)]
            text = "\n".join((older + recent) if whole_meeting else recent)[-7000:]
            out: dict[str, Any] = {"transcript": text, "covers": "the whole meeting so far" if whole_meeting else "the most recent turns"}
            if self.items is not None:
                rows = await self.items.describe(await self.items.open_items())
                out["open_items_now"] = [f"{r['type']}: {r['text']}" for r in rows[:10]]
            m = collect_minutes(self.memory, self.scope, working=None)
            out["decisions_noted"] = [r.body for r in m.decisions][:8]
            out["note"] = "Summarise only this. If the transcript is empty, say nothing has been said yet."
            return out

        return ToolDef("recap_meeting", {}, handler, optional={"whole_meeting": bool},
                       description="Recap the meeting so far or what someone missed. Returns the recent transcript, decisions noted "
                                   "and open items; summarise only that. whole_meeting=true covers everything since the start.",
                       timeout_s=5.0, permission="memory_read", side_effect=False)

    def _set_type_tool(self) -> ToolDef:
        def handler(meeting_type: str) -> dict[str, Any]:
            try:
                t = parse_type(meeting_type)
            except ValueError as exc:
                raise tool_failure("bad_type", str(exc)) from exc
            self.meeting_type = t
            if self.recorder is not None:
                self.recorder.digest_hint = RULES[t].digest_hint
            if self.on_kind is not None:
                self.on_kind(RULES[t].kind or None)
            return {"status": "set", "meeting_type": t.value, "how_to_run_it": RULES[t].behaviour,
                    "say": "Say in one short sentence which kind of meeting this is now."}

        return ToolDef("set_meeting_type", {"meeting_type": str}, handler,
                       description="Set the kind of meeting: general, standup, one_on_one, retro, client_call or brainstorm. It changes "
                                   "how you run the meeting and the layout of the minutes.",
                       timeout_s=3.0, permission="meeting_setup", side_effect=False)

    def _add_vocab_tool(self) -> ToolDef:
        def handler(term: str) -> dict[str, Any]:
            if self.memory_gate is not None and not (self.memory_gate.remember_allowed() or self.memory_gate.update_allowed()):
                raise PermissionDenied("add_vocabulary needs the participant to ask for it, and I did not hear that request")
            added = self.vocab.add(term)
            return {"status": "added" if added else "already_known_or_not_usable", "term": term.strip()[:60],
                    "say": "Say the term back with its spelling, or say it was already known."}

        return ToolDef("add_vocabulary", {"term": str}, handler,
                       description="Remember the spelling of a name or term the participant asks you to learn (a person, product or "
                                   "jargon word), so it is understood and written correctly later.",
                       timeout_s=3.0, permission="memory_write", side_effect=True, live_write=True)

    def _agenda_tools(self) -> list[ToolDef]:
        async def set_agenda(items: str, minutes: int = 0) -> dict[str, Any]:
            titles = [t.strip(" -*0123456789.)") for t in items.replace(";", "\n").replace(",", "\n").splitlines()]
            try:
                self.agenda.set([t for t in titles if t], minutes or None)
            except ValueError as exc:
                raise tool_failure("bad_agenda", str(exc)) from exc
            if self._clock_task is None and self.speak is not None and self.settings.nudges:
                self._clock_task = asyncio.create_task(self.clock.run())
            if self.items is not None:
                try:
                    await self.items.service.remember(
                        self.items.scope, "Agenda: " + "; ".join(i.title for i in self.agenda.items), source=f"session:{self.session_id}:agenda",
                        kind="note", session_id=self.session_id, metadata={"type": "agenda"})
                except Exception:  # noqa: BLE001 - the in-meeting agenda works without the copy
                    pass
            return {"status": "set", **self.agenda.status(),
                    "say": "Read the agenda back in a few words and say which item we start with."}

        def status() -> dict[str, Any]:
            if not self.agenda.items:
                return {"status": "none", "say": "Say plainly that there is no agenda yet and offer to set one."}
            return self.agenda.status()

        def mark(item: str = "current", state: str = "done") -> dict[str, Any]:
            hit = self.agenda.mark(item, state)
            if hit is None:
                return {"status": "not_found", "say": "Say plainly that no agenda item matches."}
            return {"status": "marked", "item": hit.title, "state": hit.state, **self.agenda.status(),
                    "say": "Say which item is covered and what is next, in one short sentence."}

        return [
            ToolDef("set_agenda", {"items": str}, set_agenda, optional={"minutes": int},
                    description="Set the meeting agenda. items is the agenda points separated by semicolons or new lines; minutes is "
                                "the total planned length (optional, enables time nudges).",
                    timeout_s=5.0, permission="meeting_setup", side_effect=False),
            ToolDef("agenda_status", {}, status,
                    description="Which agenda items are covered, which is current, and the time left.",
                    timeout_s=3.0, permission="meeting_setup", side_effect=False),
            ToolDef("agenda_mark", {}, mark, optional={"item": str, "state": str},
                    description="Mark an agenda item (by number or words from its title; default the current one) as done or skipped.",
                    timeout_s=3.0, permission="meeting_setup", side_effect=False)]

    async def opening_prompt(self, meeting: MeetingSession) -> str:
        """What Tara should say when a continuing meeting starts, or "" (new section, nothing open, or switched off)."""
        self.current_meeting = meeting
        if not self.settings.opening_brief or self.items is None:
            return ""
        try:
            return await opening_brief(self.items.service, self.items.scope, meeting, self.items)
        except Exception:  # noqa: BLE001 - a missing brief is better than a failed start
            self.metrics.inc("opening_brief_failures")
            return ""

    async def stored_facts(self) -> dict[str, Any]:
        """What the database holds for THIS meeting (earlier sessions included), grouped for a document."""
        svc, scope = self.auto.service, self.auto.memory_scope
        if svc is None or scope is None:
            return {}
        try:
            items = await svc.store.list_recent(scope, 80)
        except Exception:  # noqa: BLE001 - a document can still be written from the live notes
            return {}
        out: dict[str, Any] = {}
        groups = (("Earlier summaries", ("summary",)), ("Recorded decisions", ("decision",)),
                  ("Recorded actions", ("action",)), ("Other recorded facts", ("fact", "preference", "project", "note", "correction")))
        for title, kinds in groups:
            lines = [(i.text if i.status == "confirmed" else f"{i.text} (unconfirmed)")[:400] for i in items if i.kind in kinds][:12]
            if lines:
                out[title] = lines
        episodes = [i.text[:600] for i in items if i.kind == "episode"][:4]
        if episodes:
            out["Recent transcript excerpts"] = episodes
        return out

    def _create_document_tool(self) -> ToolDef:
        async def handler(kind: str, title: str, request: str = "", format: str = "docx", interview_id: str = "") -> dict[str, Any]:
            self.gate.require("creating a document")
            if kind not in KINDS:
                raise tool_failure("bad_kind", f"kind must be one of {list(KINDS)}")
            if format not in FORMATS:
                raise tool_failure("bad_format", f"format must be one of {list(FORMATS)}")
            if not title.strip():
                raise tool_failure("bad_title", "give the document a title")
            facts = self._facts_for(kind, interview_id.strip())
            if kind in ("minutes", "summary", "custom"):
                facts = {**(await self.stored_facts()), **facts}
            size = self.settings.doc_font_size

            async def work() -> dict[str, Any]:
                composed = await self.composer.compose(kind, title.strip()[:120], request.strip()[:1500], facts,
                                                       structure=RULES[self.meeting_type].minutes if kind == "minutes" else "")
                saved = await asyncio.to_thread(self.store.save, composed.document, format)
                return {"file": saved.name, "path": saved.path, "format": saved.format, "bytes": saved.size,
                        "written_by_model": composed.used_model, "note": composed.note, "warnings": list(saved.warnings),
                        "font_size": size}

            try:
                job = self.jobs.start(self.session_id, f"document:{kind}", work)
            except JobLimitReached as exc:
                raise tool_failure("busy", str(exc), retryable=True) from exc
            return {"status": "started", "job_id": job.job_id,
                    "note": "the document is being written; check job_status before saying it is ready"}

        return ToolDef("create_document", {"kind": str, "title": str}, handler, optional={"request": str, "format": str, "interview_id": str},
                       description="Start writing a document from recorded facts: kind is minutes, interview_report, summary or custom; "
                                   "format is docx, pdf or md. Returns a job id immediately. Organizer or recruiter only.",
                       timeout_s=5.0, permission="documents", side_effect=True, live_write=True, category="documents", latency="background")

    def _job_status_tool(self) -> ToolDef:
        def handler(job_id: str) -> dict[str, Any]:
            job = self.jobs.get(self.session_id, job_id.strip())
            if job is None:
                raise tool_failure("unknown_job", "There is no such job in this session")
            return job.public()

        return ToolDef("job_status", {"job_id": str}, handler,
                       description="Check a background job (document or candidate preparation). Report success only when state is "
                                   "completed; if failed, say so briefly.",
                       timeout_s=3.0, permission="jobs_read", side_effect=False, category="documents")

    # ---- candidates ------------------------------------------------------------------------------------------------------
    def _list_candidates_tool(self) -> ToolDef:
        def handler() -> dict[str, Any]:
            out = []
            for cid in self.workspace.list_candidates():
                try:
                    d = self.workspace.load_candidate(cid)
                    out.append({"candidate_id": cid, "role": d.get("profile", {}).get("role_title", ""),
                                "questions": len(d.get("plan", {}).get("questions", []))})
                except WorkspaceError:
                    continue
            return {"candidates": out, "note": "none prepared yet" if not out else ""}

        return ToolDef("list_candidates", {}, handler, description="List candidates whose briefs are prepared.", timeout_s=3.0,
                       permission="hiring_read", side_effect=False, category="hiring")

    def _get_candidate_tool(self) -> ToolDef:
        def handler(candidate_id: str) -> dict[str, Any]:
            try:
                d = self.workspace.load_candidate(check_id(candidate_id.strip().lower()))
            except WorkspaceError as exc:
                raise tool_failure("no_candidate", "No prepared candidate with that id") from exc
            prof = CandidateProfile.from_dict(d["profile"])
            plan = InterviewPlan.from_dict(d["plan"])
            return {"candidate_id": prof.candidate_id, "brief": prof.brief(), "criteria": [c.name for c in plan.criteria],
                    "question_count": len(plan.questions), "warnings": prof.warnings,
                    "note": "the brief is the candidate's own claims, unverified"}

        return ToolDef("get_candidate", {"candidate_id": str}, handler,
                       description="Read a prepared candidate brief (claimed skills, projects) and the planned criteria.",
                       timeout_s=3.0, permission="hiring_read", side_effect=False, category="hiring")

    def _prepare_candidate_tool(self) -> ToolDef:
        async def handler(label: str, role_title: str, requirements: str, cv_file: str = "", github: str = "", portfolio_url: str = "",
                          questions: int = 8) -> dict[str, Any]:
            self.gate.require("preparing a candidate")
            reqs = [r.strip() for r in requirements.split(",") if r.strip()][:10]
            if not reqs:
                raise tool_failure("no_requirements", "list the required skills, comma separated")
            if not (cv_file or github or portfolio_url):
                raise tool_failure("no_sources", "give a CV file name from the inbox, a GitHub user, or a portfolio link")

            async def work() -> dict[str, Any]:
                prof = await self.builder.build(label, role_title, cv_file=cv_file.strip(), portfolio_url=portfolio_url.strip(),
                                                github=github.strip())
                plan = await self.planner.plan(prof, role_title, reqs, n=int(questions))
                await asyncio.to_thread(self.workspace.save_candidate, prof.candidate_id,
                                        {"profile": prof.to_dict(), "plan": plan.to_dict(), "requirements": reqs})
                return {"candidate_id": prof.candidate_id, "questions": len(plan.questions), "skills_found": len(prof.skills),
                        "projects_found": len(prof.projects), "warnings": prof.warnings[:5], "notes": plan.notes}

            try:
                job = self.jobs.start(self.session_id, "prepare_candidate", work)
            except JobLimitReached as exc:
                raise tool_failure("busy", str(exc), retryable=True) from exc
            return {"status": "started", "job_id": job.job_id}

        return ToolDef("prepare_candidate", {"label": str, "role_title": str, "requirements": str}, handler,
                       optional={"cv_file": str, "github": str, "portfolio_url": str, "questions": int},
                       description="Prepare a candidate in the background: read a CV file from the inbox, a public portfolio page and a "
                                   "public GitHub profile, then plan interview questions for the role. Organizer or recruiter only.",
                       timeout_s=5.0, permission="hiring_write", side_effect=True, live_write=True, category="hiring", latency="background")

    # ---- the interview ------------------------------------------------------------------------------------------------------
    def _start_interview_tool(self) -> ToolDef:
        def handler(candidate_id: str) -> dict[str, Any]:
            self.gate.require("starting an interview")
            if self.session is not None and not self.session.done:
                raise tool_failure("already_running", "An interview is already in progress; finish it first")
            try:
                d = self.workspace.load_candidate(check_id(candidate_id.strip().lower()))
            except WorkspaceError as exc:
                raise tool_failure("no_candidate", "No prepared candidate with that id") from exc
            prof = CandidateProfile.from_dict(d["profile"])
            plan = InterviewPlan.from_dict(d["plan"])
            self.session = InterviewSession(plan, prof.candidate_id, lambda: tuple(self.working.turns),
                                            candidate_speakers=self.candidate_speakers)
            self.last_plan, self.last_brief, self.last_result = plan, prof.brief(), None
            self.metrics.inc("interviews_started")
            return {"status": "started", "interview_id": self.session.interview_id, "questions": len(plan.questions),
                    "criteria": [c.name for c in plan.criteria],
                    "note": "ask one question at a time with interview_next_question; do not reveal scores to the candidate"}

        return ToolDef("start_interview", {"candidate_id": str}, handler,
                       description="Begin interviewing a prepared candidate. Organizer or recruiter only.",
                       timeout_s=3.0, permission="hiring_write", side_effect=True, live_write=True, category="hiring")

    def begin_interview(self, candidate_id: str) -> str:
        """Start the interview for a candidate prepared with the wizard. Used by `interview run`, where the person who ran the
        command is the authorization, so the spoken role checks do not apply to the questions. Returns Tara's first-words prompt."""
        d = self.workspace.load_candidate(check_id(candidate_id.strip().lower()))
        prof = CandidateProfile.from_dict(d["profile"])
        plan = InterviewPlan.from_dict(d["plan"])
        self.session = InterviewSession(plan, prof.candidate_id, lambda: tuple(self.working.turns),
                                        candidate_speakers=self.candidate_speakers)
        self.last_plan, self.last_brief, self.last_result = plan, prof.brief(), None
        self.autopilot, self.candidate_label = True, prof.label
        self.metrics.inc("interviews_started")
        n = len(plan.questions)
        return (f"A candidate named {prof.label} has just joined the interview for the {plan.role_title} role. Say hello, introduce "
                f"yourself as Tara, an AI interviewer on the hiring team, and say plainly that you are an AI and that the interview "
                f"is recorded for the hiring team. Then ask: \"You are {prof.label}, right?\" and wait for their answer. If they "
                f"confirm, explain briefly that you will ask {n} question{'s' if n != 1 else ''}, one at a time, and that they should "
                f"take their time; then call interview_next_question and ask the first one in your own words. If they are not "
                f"{prof.label}, apologise, say the team will follow up, and do not continue. After every answer, call "
                f"interview_record_answer with an exact quote, then follow up if the answer was thin or go on to the next question. "
                f"When interview_next_question says it is done, call finish_interview, thank them, and say the team will be in "
                f"touch. Never say a score, never say hire or reject, and never ask about age, family, religion, health or similar.")

    async def end_interview(self) -> dict[str, Any]:
        """At the end of a run: finish an interview that is still open, save the evidence and write the report. Never raises."""
        out: dict[str, Any] = {}
        if self.session is None:
            return out
        try:
            if not self.session.done:
                finished = self.session.finish()
                self.last_result = finished
                self.workspace.save_interview(self.session.interview_id, {
                    "result": finished.to_dict(), "plan": self.session.plan.to_dict(), "brief": self.last_brief})
            result = self.last_result
            if result is None or self.last_plan is None:
                return out
            out["interview_id"] = result.interview_id
            out["criteria"] = [(c["criterion"], c["average"]) for c in result.criteria]
            out["flags"] = list(result.flags)
            title = f"Interview report: {self.candidate_label or result.candidate_id}"
            composed = await self.composer.compose("interview_report", title, "", result_facts(result, self.last_plan, self.last_brief))
            saved = await asyncio.to_thread(self.store.save, composed.document, "docx")
            out["report_file"] = saved.name
            out["report_path"] = saved.path
            self.metrics.inc("interview_reports_saved")
        except Exception:  # noqa: BLE001 - shutdown must finish
            self.metrics.inc("interview_end_failures")
        return out

    def _active(self) -> InterviewSession:
        if self.session is None or self.session.done:
            raise tool_failure("no_interview", "No interview is in progress")
        return self.session

    def _next_question_tool(self) -> ToolDef:
        def handler() -> dict[str, Any]:
            try:
                return self._active().next_question()
            except InterviewError as exc:
                raise tool_failure("interview_error", str(exc)) from exc

        return ToolDef("interview_next_question", {}, handler,
                       description="Get the next planned interview question. Ask it naturally in your own words.",
                       timeout_s=3.0, permission="hiring_write", side_effect=True, live_write=True, category="hiring")

    def _record_answer_tool(self) -> ToolDef:
        def handler(qid: str, criterion: str, score: int, quote: str, rationale: str = "") -> dict[str, Any]:
            try:
                return self._active().record(qid.strip(), criterion.strip(), score, rationale, quote)
            except InterviewError as exc:
                raise tool_failure("interview_error", str(exc)) from exc

        return ToolDef("interview_record_answer", {"qid": str, "criterion": str, "score": int, "quote": str}, handler,
                       optional={"rationale": str},
                       description="Record your assessment of the candidate's answer: one criterion, a score 0-4 and an EXACT quote of the "
                                   "candidate's words from the conversation. Rejected if the quote is not found. Do not say the score aloud.",
                       timeout_s=3.0, permission="hiring_write", side_effect=True, live_write=True, category="hiring")

    def _finish_interview_tool(self) -> ToolDef:
        def handler() -> dict[str, Any]:
            if not self.autopilot:
                self.gate.require("finishing an interview")
            session = self._active()
            if self.autopilot and session.index < len(session.plan.questions):
                raise tool_failure("not_finished_yet", "There are planned questions left; ask them before finishing")
            result = session.finish()
            self.last_result = result
            self.workspace.save_interview(session.interview_id, {"result": result.to_dict(), "plan": session.plan.to_dict(),
                                                                 "brief": self.last_brief})
            self.metrics.inc("interviews_finished")
            return {"status": "finished", "interview_id": result.interview_id, "criteria": result.criteria, "flags": result.flags,
                    "human_review_required": True,
                    "note": "this supports a human decision; do not say hire or reject. Offer to create the interview_report document."}

        return ToolDef("finish_interview", {}, handler,
                       description="End the interview, save the evidence-linked result and return per-criterion results and flags. "
                                   "Organizer or recruiter only.",
                       timeout_s=5.0, permission="hiring_write", side_effect=True, live_write=True, category="hiring")

    async def finalize(self) -> dict[str, Any]:
        """End of meeting: keep a minutes file and one long-term summary, with no request from anyone. Never raises.

        Only confirmed decisions and actions go into the long-term summary; unconfirmed claims stay in the file.
        """
        out: dict[str, Any] = {}
        if self.session is not None:
            out.update(await self.end_interview())
        if self.recorder is not None:
            done = await self.recorder.finish()
            out["recorded"] = f"{done['summary']} summary, {done['facts']} facts"
        if not self.settings.auto_minutes or not self.auto.active:
            return out
        try:
            m = collect_minutes(self.memory, self.scope, title=self.cfg.purpose or "Meeting",
                                attendees=self.names.values(), working=self.working)
            stored = await self.stored_facts()
            if m.empty and not stored:
                return out
            facts = {**minutes_facts(m, include_excerpt=False), **stored}
            md = fallback_markdown("minutes", f"{m.title} minutes", facts)
            saved = await asyncio.to_thread(self.store.save, parse_markdown(md, title=f"{m.title} minutes"), "docx")
            out["minutes_file"] = saved.name
            self.metrics.inc("auto_minutes_saved")
        except Exception:  # noqa: BLE001 - shutdown must finish
            self.metrics.inc("auto_minutes_failures")
            return out
        try:
            if self.auto.service is not None and self.auto.memory_scope is not None and (m.decisions or m.actions):
                lines = [f"Decision: {_line(r)}" for r in m.decisions] + [f"Action: {_line(r)}" for r in m.actions]
                text = f"Meeting '{m.title}' on {m.started[:10]}. " + " ".join(lines)
                await self.auto.service.remember(
                    self.auto.memory_scope, text[:1900], source=f"session:{self.session_id}:minutes", kind="note",
                    session_id=self.session_id, metadata={"auto": True, "minutes": True})
                out["memory"] = "saved"
                self.metrics.inc("auto_minutes_remembered")
        except Exception:  # noqa: BLE001
            self.metrics.inc("auto_minutes_memory_failures")
        return out

    async def close(self) -> None:
        if self._clock_task is not None:
            self._clock_task.cancel()
        await self.jobs.close()


def text_llm_from_settings(api_key: str, model: str, timeout_s: float = 30.0) -> LanguageModelProvider | None:
    """The Flash text model used for documents and plans. None when not configured (callers fall back to templates)."""
    if not api_key or not model:
        return None
    from .providers.gemini import GeminiProvider
    return GeminiProvider(api_key, model, timeout_s=timeout_s)


def inbox_path(settings: WorkSettings) -> Path:
    return Workspace(settings.dir).inbox
