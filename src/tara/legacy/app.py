"""TaraApp: composition root. Builds the unified runtime from validated settings.

Nothing is loaded at construction: local models load lazily on first use (or at
`start()` when lazy_load is off). Real adapters are only created for configured
backends; tests inject fakes through AppDeps.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..core.config import MeetingConfig, Mode, Role
from ..core.context_assembly import TokenBudget
from ..core.contracts import KnowledgeProvider, LanguageModelProvider, MeetingAdapter, SpeechRecognizer, SpeechSynthesizer
from ..core.errors import ConfigError
from ..core.events import EventBus
from ..core.metrics import Metrics
from ..core.models import Health, InferencePool, ModelHandle, ModelManager
from ..core.settings import RuntimeSettings, validate
from ..floor.audio import EnergyVad, Segmenter
from ..floor.classify import IntentClassifier, LlmIntentClassifier, RuleIntentClassifier
from ..floor.identity import LabelMap, ParticipantRegistry, SpeakerAttributor
from ..floor.participation import Thresholds
from ..providers.gemini import GeminiProvider
from ..tools.executor import ToolExecutor
from .conversation import ConversationEngine
from .engine import EngineParams, MeetingEngine
from .qwen_local import QwenLocalProvider, mlx_loader
from .respond import ResponseOrchestrator
from .routing import Capabilities, LlmRouteClassifier, Router

DEFAULT_THRESHOLDS = Thresholds(min_intent_confidence=0.5, proactive_usefulness=0.7,
                                proactive_min_pause_ms=1500, default_attribution=0.8)


@dataclass
class AppDeps:
    """Optional overrides. Anything left None is built from settings."""

    adapter: MeetingAdapter | None = None
    stt: SpeechRecognizer | None = None
    tts: SpeechSynthesizer | None = None
    local_llm: LanguageModelProvider | None = None
    cloud_llm: LanguageModelProvider | None = None
    knowledge: KnowledgeProvider | None = None
    tools: ToolExecutor | None = None
    classifier: IntentClassifier | None = None
    thresholds: Thresholds = DEFAULT_THRESHOLDS
    bus: EventBus | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class TaraApp:
    def __init__(self, settings: RuntimeSettings, cfg: MeetingConfig, roles: dict[str, Role],
                 names: dict[str, str], deps: AppDeps, meeting_id: str = "local"):
        self.settings, self.cfg, self.deps = settings, cfg, deps
        self.metrics = Metrics()
        self.manager = ModelManager(self.metrics)
        lim = settings.limits
        self.pool = InferencePool("local_llm", lim.max_concurrent_inference, lim.max_queue, self.metrics)

        if deps.adapter is None or deps.stt is None or deps.tts is None:
            raise ConfigError("adapter, stt and tts must be provided (real backends arrive in Phase 2)")
        self.local = deps.local_llm or self._build_local()
        self.cloud = deps.cloud_llm or self._build_cloud()
        caps = Capabilities(
            local_available=self.local is not None,
            cloud_available=self.cloud is not None,
            retrieval_available=deps.knowledge is not None,
            web_search_granted=bool(deps.tools and any(t["name"] == "web_search" for t in deps.tools.describe())),
            knowledge_bound=cfg.mode is Mode.PITCH)
        self.router = Router(caps, LlmRouteClassifier(self.local) if self.local else None)
        local_engine = (ConversationEngine(self.local, cfg, deps.knowledge,
                                           budget=TokenBudget(lim.local_context_tokens, settings.inference.local_max_tokens))
                        if self.local else None)
        cloud_engine = (ConversationEngine(self.cloud, cfg, deps.knowledge,
                                           budget=TokenBudget(lim.cloud_context_tokens, 512))
                        if self.cloud else None)
        self.orchestrator = ResponseOrchestrator(
            cfg, self.router, local_engine, cloud_engine, deps.knowledge, deps.tools, self.metrics,
            escalate_below=settings.behavior.escalate_below_confidence)
        registry = ParticipantRegistry()
        classifier = deps.classifier or (LlmIntentClassifier(self.local, RuleIntentClassifier()) if self.local
                                         else RuleIntentClassifier())
        self.engine = MeetingEngine(
            cfg=cfg, roles=roles, names=names, adapter=deps.adapter, stt=deps.stt, tts=deps.tts,
            classifier=classifier, conversation=self.orchestrator, thresholds=deps.thresholds,
            registry=registry, attributor=SpeakerAttributor(registry, LabelMap()),
            segmenter_factory=lambda sid: Segmenter(EnergyVad(), stream_id=sid),   # Silero arrives in Phase 2
            bus=deps.bus, tools=deps.tools, params=EngineParams(gen_timeout_s=lim.cloud_timeout_s + lim.local_llm_timeout_s,
                                                                stt_timeout_s=lim.stt_timeout_s),
            metrics=self.metrics, meeting_id=meeting_id)
        self._maint: asyncio.Task | None = None

    # ---- building blocks --------------------------------------------------
    def _build_local(self) -> LanguageModelProvider | None:
        m = self.settings.models
        if m.llm_backend != "mlx_lm" or not m.llm_model:
            return None
        lim = self.settings.limits
        handle = self.manager.register(ModelHandle(
            "local_llm", mlx_loader(m.llm_model), metrics=self.metrics,
            load_timeout_s=lim.model_load_timeout_s, retry_cooldown_s=lim.model_retry_cooldown_s))
        return QwenLocalProvider(handle, self.pool, self.settings.inference, lim.local_llm_timeout_s, metrics=self.metrics)

    def _build_cloud(self) -> LanguageModelProvider | None:
        g = self.settings.gemini
        if not g.enabled or not self.cfg.send_transcript_to_llm:
            return None          # meeting privacy can veto the cloud even when Gemini is configured
        if not g.api_key or not g.model:
            return None          # incomplete config is reported by validate() in start(), not by a crash here
        return GeminiProvider(g.api_key, g.model, timeout_s=self.settings.limits.cloud_timeout_s)

    # ---- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        errors = [f"{i.field}: {i.message}" for i in validate(self.settings, check_files=True) if i.severity == "error"]
        if errors:
            raise ConfigError("invalid settings: " + "; ".join(errors))
        await self.engine.start()
        if not self.settings.behavior.lazy_load:
            failed = await self.manager.warm_up()
            for name, err in failed.items():
                if err:
                    self.metrics.inc(f"warmup_failed.{name}")   # degrade: routing falls back when a model is down
        if not self.settings.behavior.keep_models_loaded and self.settings.behavior.idle_unload_s > 0:
            self._maint = asyncio.create_task(self._maintenance())

    async def _maintenance(self) -> None:
        idle = self.settings.behavior.idle_unload_s
        while True:
            await asyncio.sleep(max(1.0, idle / 4))
            await self.manager.unload_idle(idle)

    async def shutdown(self) -> list[str]:
        if self._maint:
            self._maint.cancel()
            try:
                await self._maint
            except asyncio.CancelledError:
                pass
            self._maint = None
        await self.engine.shutdown()
        return await self.manager.shutdown()

    def health(self) -> dict[str, Any]:
        models: dict[str, Health] = self.manager.health()
        return {"runtime_state": self.engine.rt.state.value,
                "models": {n: {"state": h.state.value, "error": h.last_error} for n, h in models.items()},
                "cloud": "configured" if self.cloud else "off",
                "queue": {"waiting": self.pool.waiting, "active": self.pool.active}}
