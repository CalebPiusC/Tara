"""Conversation engine: prompt assembly, provider call, response validation.
It proposes text; it never speaks and never executes tools (runtime does)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import MeetingConfig, Mode
from .context_assembly import ContextAssembler, TokenBudget, TranscriptLine, render_transcript, system_prompt  # noqa: F401
from .contracts import (
    KnowledgeProvider,
    LanguageModelProvider,
    Passage,
    ToolCall,
)
from .errors import MalformedOutput

UNKNOWN_LINE = "I don't have verified information on that, so I'd rather not guess. I'll flag it for follow-up."
COMMIT_LINE = "I can't commit to that myself; a teammate will follow up."
CLARIFY_SPEAKER = "Sorry, I didn't catch who was speaking. Could you say your name before answering?"


@dataclass
class RuntimeView:
    lines: list[TranscriptLine]
    current_question: str | None = None
    notes: list[str] = field(default_factory=list)
    summary: str = ""
    topic: str | None = None


@dataclass(frozen=True)
class ResponseDraft:
    say: str
    cites: tuple[str, ...] = ()
    asks_clarification: bool = False
    admits_unknown: bool = False
    needs_tool: ToolCall | None = None
    confidence: float | None = None
    needs_more_reasoning: bool = False


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "say": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}},
        "asks_clarification": {"type": "boolean"}, "admits_unknown": {"type": "boolean"},
        "needs_tool": {"type": "object", "properties": {"name": {"type": "string"}, "args": {"type": "object"}}},
        "confidence": {"type": "number"}, "needs_more_reasoning": {"type": "boolean"},
    },
    "required": ["say"],
}


def parse_response(d: Any) -> ResponseDraft:
    if not isinstance(d, dict) or not isinstance(d.get("say"), str):
        raise MalformedOutput("response must be an object with string 'say'")
    cites = d.get("cites") or []
    if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
        raise MalformedOutput("cites must be a list of strings")
    tool = None
    nt = d.get("needs_tool")
    if nt:
        if not isinstance(nt, dict) or not isinstance(nt.get("name"), str) or not isinstance(nt.get("args", {}), dict):
            raise MalformedOutput("needs_tool malformed")
        tool = ToolCall(nt["name"], nt.get("args", {}))
    conf = d.get("confidence")
    if conf is not None and (isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1):
        raise MalformedOutput("confidence must be a number in [0, 1]")
    return ResponseDraft(d["say"].strip(), tuple(cites), bool(d.get("asks_clarification")),
                         bool(d.get("admits_unknown")), tool, float(conf) if conf is not None else None,
                         bool(d.get("needs_more_reasoning")))


@dataclass
class Validated:
    text: str | None
    reason: str = "ok"
    unanswered: bool = False
    tool: ToolCall | None = None
    confidence: float | None = None
    grounded: bool = True
    needs_more_reasoning: bool = False
    route: str | None = None
    escalated: bool = False


def validate_response(draft: ResponseDraft, cfg: MeetingConfig, retrieved: list[Passage], max_chars: int) -> Validated:
    if not draft.say:
        return Validated(None, "empty")
    if len(draft.say) > max_chars:
        return Validated(None, "too_long")
    low = draft.say.lower()
    for term in cfg.no_commitments:
        if term.lower() in low:
            return Validated(COMMIT_LINE, "commitment_blocked")
    if cfg.mode is Mode.PITCH and not draft.admits_unknown and not draft.asks_clarification:
        ids = {p.id for p in retrieved}
        if not draft.cites or not set(draft.cites) <= ids:
            return Validated(UNKNOWN_LINE, "ungrounded_claim", unanswered=True, grounded=False)
    return Validated(draft.say, tool=draft.needs_tool, confidence=draft.confidence,
                     needs_more_reasoning=draft.needs_more_reasoning)


class ConversationEngine:
    """Builds a bounded request, calls ONE provider, validates the result. Routing and
    escalation between providers live in respond.ResponseOrchestrator."""

    def __init__(self, llm: LanguageModelProvider, cfg: MeetingConfig, knowledge: KnowledgeProvider | None = None,
                 max_chars: int = 600, budget: TokenBudget | None = None):
        self.llm, self.cfg, self.knowledge, self.max_chars = llm, cfg, knowledge, max_chars
        self.assembler = ContextAssembler(cfg, budget or TokenBudget(total=8192))
        self.unanswered: list[str] = []
        self.last_context: object | None = None

    async def propose(self, trigger_text: str, view: RuntimeView, evidence: list[Passage] | None = None,
                      tool_snippets: list[str] | None = None) -> Validated:
        passages: list[Passage] = list(evidence) if evidence is not None else []
        if evidence is None and self.knowledge is not None and self.cfg.mode in (Mode.PITCH, Mode.NATURAL):
            passages = await self.knowledge.search(trigger_text, 3)
            if self.cfg.mode is Mode.PITCH and not passages:
                self.unanswered.append(trigger_text)
                return Validated(UNKNOWN_LINE, "no_knowledge", unanswered=True)
        ctx = self.assembler.build(trigger_text, view.lines, passages, tool_snippets, view.current_question,
                                   view.summary, view.topic)
        self.last_context = ctx
        draft = await self.llm.generate(ctx.request, parse_response, RESPONSE_SCHEMA)
        v = validate_response(draft, self.cfg, passages, self.max_chars)
        if v.unanswered:
            self.unanswered.append(trigger_text)
        return v
