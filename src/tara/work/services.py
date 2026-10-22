"""WorkServices: the secretary, interview and document tools, wired for one live session.

Everything the model can do here goes through code-owned ToolDefs. The model never supplies a path, a scope, a model id,
a URL to fetch outside the allow-listed readers, or a permission. Starting or finishing an interview, creating documents
and preparing candidates need the CURRENT speaker to be an organizer or recruiter with reliable identity (`ControlRoleGate`).

The tools live in one module per group (meeting, document, candidate, interview); this class combines them and owns the
engine hooks, the tool list, the instructions and the end of the meeting.
"""
from __future__ import annotations

from typing import Any

from ..floor.participation import Attribution
from ..meetings.items import (
    add_action_tool,
    complete_item_tool,
    list_open_items_tool,
    park_item_tool,
)
from ..meetings.types import RULES
from ..secretary.auto_memory import TurnRecord
from ..secretary.minutes import _line
from ..secretary.recall import meeting_recap_tool, recall_meeting_tool
from ..tools.executor import ToolDef
from .candidate_tools import CandidateTools
from .document_tools import DocumentTools
from .interview_tools import InterviewTools
from .meeting_tools import MeetingTools


class WorkServices(MeetingTools, DocumentTools, CandidateTools, InterviewTools):
    def on_transcript(self, text: str, att: Attribution) -> None:
        """Sync, early (finished input transcript): updates who is allowed to start or finish things, and captures the
        answer text before the model's tool call arrives (the committed turn is only added after the model finishes)."""
        self.gate.observe(text, att)
        self._observe_answer_text(text, att.participant_id or "")

    async def on_turn(self, turn: TurnRecord) -> None:
        """After a participant turn is committed. Never raises."""
        await self.auto.observe(turn)
        if self.recorder is not None:
            await self.recorder.on_turn()
        if self.nudger is not None:
            self.nudger.observe(turn.text)
        self._observe_committed_answer(turn)

    @property
    def tool_defs(self) -> tuple[ToolDef, ...]:
        defs: list[ToolDef] = [recall_meeting_tool(self.memory, self.scope, self.working), meeting_recap_tool(self.memory, self.scope)]
        defs += [self._recap_tool(), self._set_type_tool(), self._add_vocab_tool(), *self._agenda_tools()]
        if self.items is not None and self.memory_gate is not None:
            defs += [add_action_tool(self.items, self.memory_gate), park_item_tool(self.items, self.memory_gate),
                     list_open_items_tool(self.items), complete_item_tool(self.items, self.memory_gate)]
        if self.settings.documents:
            defs += [self._create_document_tool(), self._job_status_tool()]
            if self.email is not None:
                defs.append(self._email_summary_tool())
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
        if self.jobs.interrupted:
            lines.append("Earlier background work was cut off by a restart and never finished: " + ", ".join(sorted(set(self.jobs.interrupted)))
                         + ". Mention this once, briefly, when it fits, and offer to redo it.")
        if self.settings.documents:
            lines.append("To produce a PDF, Word document or minutes, call create_document (kinds: minutes, interview_report, summary, "
                         "custom). It starts a background job: say it has started, then call job_status and report the file name "
                         "(saved in the work/documents folder) only once it says completed. Never claim a document exists before "
                         "that, and never say you will create one without calling create_document.")
            if self.email is not None:
                lines.append("To email the minutes to the owner, call email_summary. It first returns confirm_needed with a masked "
                             "address: say that address and ask if you should send it, wait for a clear yes from the participant, then "
                             "call email_summary again with confirm=true. You cannot choose or change the address. Say it was sent only "
                             "when the tool says status sent; if it fails, say what failed.")
        if self.settings.hiring:
            lines.append("You can interview candidates. Use list_candidates and get_candidate to see prepared briefs; prepare_candidate "
                         "builds one from a CV file in the inbox, a portfolio link and a GitHub user. To interview: start_interview, then "
                         "ask questions one at a time from interview_next_question. After the candidate finishes an answer, call "
                         "interview_record_answer with the qid only. The application evaluates the meaning of the answer against the "
                         "question, accepts different wording and valid alternative approaches, and returns needs_retry and "
                         "should_advance. Never read out scores, correctness labels, missing concepts or other internal fields. If "
                         "needs_retry is true, give one short proportional hint and ask them to try once more; do not reveal the "
                         "expected answer. If it is false, acknowledge only what the answer actually showed, then continue. Do not "
                         "use or ask about age, gender, family, religion, nationality, health or appearance. At the end call "
                         "finish_interview and say the result supports a human decision; never say hire or reject.")
        return "\n".join(lines)

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
            built = await self._build_minutes("docx")
            if built is None:
                return out
            saved, m, facts = built
            out["minutes_file"] = saved.name
            self.metrics.inc("auto_minutes_saved")
        except Exception:  # noqa: BLE001 - shutdown must finish
            self.metrics.inc("auto_minutes_failures")
            return out
        if self.email is not None and self.email.cfg.auto:
            res = await self._mail_minutes(saved, m, facts, complete=True)
            out["emailed"] = (f"sent to {', '.join(res['to'])}" if res.get("ok") else f"not sent ({res.get('error')})")   # type: ignore[arg-type]
            self.metrics.inc("auto_minutes_emailed" if res.get("ok") else "auto_minutes_email_failed")
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
        self._closing = True
        for t in list(self._announcing):
            t.cancel()
        left = await self.jobs.drain(self.settings.close_grace_s)      # a document still being written gets a moment to finish
        if left:
            self.metrics.inc("jobs_cut_at_close", left)
        self._cancel_question_timer()
        self._question_awaiting_playback = ""
        if self._clock_task is not None:
            self._clock_task.cancel()
        await self.jobs.close()
