"""ResponseOrchestrator: route -> (retrieve | tool) -> local model -> maybe escalate to cloud.

It PROPOSES text (a `Validated`). It never speaks and never runs side-effecting tools;
the engine/runtime approves and acts. Qwen (local) never decides permissions: rules
define the allowed routes, the model may only pick among them (routing.py).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from .config import MeetingConfig
from .contracts import KnowledgeProvider, Passage, ToolCall
from .conversation import UNKNOWN_LINE, ConversationEngine, RuntimeView, Validated
from .errors import ProviderError
from .metrics import CLOUD_LLM, RETRIEVAL, ROUTE, Metrics
from .routing import LocalOutcome, Route, Router, build_escalation_payload, should_escalate
from .tools import ToolExecutor

HANDOFF_LINE = "That's something a teammate should answer; I'll flag it for follow-up."
NO_LOOKUP_LINE = "I can't look that up from here right now."
UNSURE_LINE = "I'm not certain about that, so I'd rather not guess."
RETRIEVAL_DOWN_LINE = "I can't verify that against our approved material right now."

StageCb = Callable[[str, str | None], None]


class Responder(Protocol):
    async def propose(self, trigger_text: str, view: RuntimeView) -> Validated: ...


class ResponseOrchestrator:
    def __init__(self, cfg: MeetingConfig, router: Router, local: ConversationEngine | None,
                 cloud: ConversationEngine | None, knowledge: KnowledgeProvider | None = None,
                 tools: ToolExecutor | None = None, metrics: Metrics | None = None,
                 escalate_below: float = 0.6, retrieval_timeout_s: float = 3.0, instructions: tuple[str, ...] = ()):
        self.cfg, self.router, self.local, self.cloud = cfg, router, local, cloud
        self.knowledge, self.tools, self.metrics = knowledge, tools, metrics or Metrics()
        self.escalate_below, self.retrieval_timeout_s, self.instructions = escalate_below, retrieval_timeout_s, instructions
        self.stage: StageCb = lambda stage, route: None
        self.unanswered: list[str] = []
        self.last_route: str | None = None

    def bind_stage(self, cb: StageCb) -> None:
        self.stage = cb

    async def propose(self, trigger_text: str, view: RuntimeView) -> Validated:
        self.stage("routing", None)
        with self.metrics.time(ROUTE):
            d = await self.router.route(trigger_text)
        self.last_route = d.route.value
        self.metrics.inc(f"route.{d.route.value}")
        self.stage("routing", d.route.value)
        r = d.route
        if r is Route.HUMAN_HANDOFF:
            self.unanswered.append(trigger_text)
            return self._tag(Validated(HANDOFF_LINE, "human_handoff", unanswered=True), r)
        if r is Route.DECLINE:
            self.unanswered.append(trigger_text)
            line = NO_LOOKUP_LINE if "current_information" in d.reason else UNKNOWN_LINE
            return self._tag(Validated(line, d.reason, unanswered=True), r)

        evidence: list[Passage] | None = None
        snippets: list[str] | None = None
        if r in (Route.LOCAL_RETRIEVAL, Route.CLOUD_RETRIEVAL):
            evidence, early = await self._retrieve(trigger_text)
            if early is not None:
                return self._tag(early, r)
        elif r is Route.WEB_SEARCH:
            snippets, early = await self._search(trigger_text)
            if early is not None:
                return self._tag(early, r)

        want_cloud = r in (Route.CLOUD, Route.CLOUD_RETRIEVAL) or (r is Route.WEB_SEARCH and self.local is None)
        if not want_cloud and self.local is not None:
            self.stage("generating", r.value)
            out, v = await self._run(self.local, trigger_text, view, evidence, snippets)
            caps = self.router.caps
            esc = should_escalate(out, r, caps, self.escalate_below)
            if not esc.escalate:
                if v is None or (v.confidence is not None and v.confidence < self.escalate_below):
                    self.unanswered.append(trigger_text)   # no cloud to help: admit it, don't speak a shaky answer
                    reason = out.failure or "low_confidence_no_cloud"
                    return self._tag(Validated(UNSURE_LINE, reason, unanswered=True), r)
                return self._tag(v, r)
            self.metrics.inc(f"escalation.{esc.why.split(':')[0]}")
            return await self._cloud(trigger_text, view, evidence, snippets, r, escalated=True, fallback=v)
        return await self._cloud(trigger_text, view, evidence, snippets, r, escalated=False, fallback=None)

    # ---- pieces --------------------------------------------------------------
    async def _retrieve(self, q: str) -> tuple[list[Passage] | None, Validated | None]:
        if self.knowledge is None:
            return None, Validated(UNKNOWN_LINE, "retrieval_unavailable", unanswered=True)
        self.stage("retrieving", None)
        try:
            with self.metrics.time(RETRIEVAL):
                ev = await asyncio.wait_for(self.knowledge.search(q, 4), self.retrieval_timeout_s)
        except (TimeoutError, ProviderError):
            self.metrics.inc("retrieval_failures")
            self.unanswered.append(q)
            return None, Validated(RETRIEVAL_DOWN_LINE, "retrieval_unavailable", unanswered=True)
        if not ev:
            self.unanswered.append(q)
            return None, Validated(UNKNOWN_LINE, "no_knowledge", unanswered=True)
        return ev, None

    async def _search(self, q: str) -> tuple[list[str] | None, Validated | None]:
        if self.tools is None:
            return None, Validated(NO_LOOKUP_LINE, "no_search_tool", unanswered=True)
        self.stage("retrieving", None)
        res = await self.tools.execute_read_only(ToolCall("web_search", {"query": q[:200]}), f"search:{abs(hash(q))}")
        if not res.ok or not res.output:
            return None, Validated(NO_LOOKUP_LINE, f"search_failed:{res.error}", unanswered=True)
        return [str(x.get("text", ""))[:400] for x in res.output][:3], None

    async def _run(self, engine: ConversationEngine, q: str, view: RuntimeView, ev, sn) -> tuple[LocalOutcome, Validated | None]:
        try:
            v = await engine.propose(q, view, evidence=ev, tool_snippets=sn)
        except TimeoutError:
            return LocalOutcome(False, failure="timeout"), None
        except ProviderError as e:
            self.metrics.inc("inference_failures")
            return LocalOutcome(False, failure=type(e).__name__), None
        if v.text is None:
            return LocalOutcome(False, failure=v.reason), None
        return LocalOutcome(True, v.confidence, v.grounded, v.needs_more_reasoning), v

    async def _cloud(self, q: str, view: RuntimeView, ev, sn, r: Route, escalated: bool,
                     fallback: Validated | None) -> Validated:
        caps = self.router.caps
        if self.cloud is None or not caps.cloud_available:
            self.unanswered.append(q)
            return self._tag(fallback if fallback is not None and fallback.text else
                             Validated(UNSURE_LINE, "cloud_unavailable", unanswered=True), r)
        self.stage("generating", r.value)
        hist = [f"{ln.speaker or 'unknown'}: {ln.text}" for ln in view.lines]
        payload = build_escalation_payload(q, hist, list(ev or []), list(self.instructions))
        small = RuntimeView(view.lines[-len(payload.history):] if payload.history else [], view.current_question,
                            summary=view.summary, topic=view.topic)
        with self.metrics.time(CLOUD_LLM):
            out, v = await self._run(self.cloud, q, small, list(payload.evidence) or None, sn)
        if v is None:
            self.unanswered.append(q)
            return self._tag(Validated(UNSURE_LINE, out.failure or "cloud_failed", unanswered=True, escalated=escalated), r)
        v.escalated = escalated
        return self._tag(v, r)

    @staticmethod
    def _tag(v: Validated, r: Route) -> Validated:
        v.route = r.value
        return v
