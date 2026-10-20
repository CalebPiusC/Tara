"""Runtime settings: one typed place for model selection, limits, timeouts, Gemini
and behavior. Loaded from environment variables (no secrets in code or files).

Nothing here loads a model or touches the network. `validate` only inspects values
(and, when asked, whether configured local paths exist).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .config import Issue
from .errors import ConfigError

STT_BACKENDS = {"mlx_whisper", "fake"}
LLM_BACKENDS = {"mlx_lm", "fake"}
TTS_BACKENDS = {"kokoro", "fake"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _num(env: Mapping[str, str], key: str, default: float, cast=float):
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except ValueError as e:
        raise ConfigError(f"{key} must be a number") from e


@dataclass(frozen=True)
class ModelSettings:
    stt_backend: str = "mlx_whisper"
    stt_model: str = ""        # HF repo id or local path; empty = not configured
    llm_backend: str = "mlx_lm"
    llm_model: str = ""        # Qwen3-0.6B (a quantized MLX build) repo id or local path
    tts_backend: str = "kokoro"
    tts_voice: str = ""
    models_dir: str = "./models"


@dataclass(frozen=True)
class InferenceSettings:
    local_max_tokens: int = 160
    # Qwen3 model card, non-thinking mode: temperature 0.7, top_p 0.8, top_k 20. Not yet applied
    # by the adapter until the mlx-lm sampler API is verified (docs/RUNTIME_FOUNDATION.md).
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    enable_thinking: bool = False  # keep off: spoken replies must be short and fast


@dataclass(frozen=True)
class LimitSettings:
    local_context_tokens: int = 4096        # operator-chosen working window, NOT the model maximum
    cloud_context_tokens: int = 0           # must be set by the operator if Gemini is enabled
    max_concurrent_inference: int = 1       # per pool; M4 Pro 24 GB, three local models share memory
    max_queue: int = 4                      # waiting requests beyond this are refused, not buffered
    stt_timeout_s: float = 30.0
    local_llm_timeout_s: float = 15.0
    cloud_timeout_s: float = 20.0
    tts_timeout_s: float = 20.0
    tool_timeout_s: float = 10.0
    model_load_timeout_s: float = 120.0
    model_retry_cooldown_s: float = 30.0


@dataclass(frozen=True)
class GeminiSettings:
    enabled: bool = False
    api_key: str = field(default="", repr=False)   # never shown in repr
    model: str = ""                                 # verify at ai.google.dev/gemini-api/docs/models


@dataclass(frozen=True)
class LiveSettings:
    """Gemini Live is the primary conversational voice engine (docs/GEMINI_LIVE.md)."""

    enabled: bool = False
    model: str = ""                    # TARA_LIVE_MODEL: required; ids change, check the models page
    voice_name: str = ""
    connect_timeout_s: float = 10.0
    reconnect_attempts: int = 3
    reconnect_backoff_s: float = 0.5
    max_concurrent_sessions: int = 2
    hold_ms: int = 1500                # how long response audio may wait for a participation verdict
    playback_queue_chunks: int = 64
    uplink_queue_chunks: int = 100
    local_barge_in: bool = True
    input_transcription: bool = True
    output_transcription: bool = True
    session_resumption: bool = True
    context_compression: bool = True   # docs: audio-only sessions are capped at 15 minutes without it
    silence_duration_ms: int = 0       # 0 = provider default
    proactive_audio: bool = False      # leave False/unset on gemini-3.8-live: it is always on there and sending false errors (docs)
    hybrid_end_of_speech: bool = False
    api_version: str = ""              # empty = SDK default
    tool_behavior: str = "default"     # default | blocking | non_blocking (gemini-3.8-live defaults to non-blocking per docs)


@dataclass(frozen=True)
class ToolSettings:
    """Read-only information and research tools (docs/ORCHESTRATION.md).

    These are application-level grants, not meeting-config grants.  Side-effecting
    tools still need their own policy; nothing here can speak or widen participation.
    """

    enabled: bool = True
    time: bool = True
    calculator: bool = True
    location: bool = True
    weather: bool = True
    search: bool = False               # requires a subscription token
    fetch: bool = True                 # public URL reading; SSRF checks always apply
    research: bool = True              # only registered when search + fetch are available
    background: bool = True
    search_provider: str = "brave"
    search_api_key: str = field(default="", repr=False)
    weather_base_url: str = "https://api.open-meteo.com/v1/forecast"
    geocoding_base_url: str = "https://geocoding-api.open-meteo.com/v1"
    country_base_url: str = "https://countries.dev"
    http_timeout_s: float = 8.0
    research_timeout_s: float = 25.0
    research_sources: int = 3
    research_chars: int = 8000
    background_tasks: int = 2
    task_ttl_s: float = 1800.0
    max_calls_per_turn: int = 8
    search_calls_per_session: int = 8
    weather_cache_s: float = 300.0
    geocode_cache_s: float = 86400.0
    country_cache_s: float = 604800.0
    search_cache_s: float = 60.0
    user_agent: str = "Tara/0.0 (local voice assistant)"


@dataclass(frozen=True)
class PersonaSettings:
    """Conversational behaviour (docs/CONVERSATIONAL_BEHAVIOUR.md). Style only: no permissions live here."""

    profile: str = "professional"            # professional | conversational | expressive
    guidance_injection: bool = True          # send short style notes mid-session (documented as supported; effect unverified)
    guidance_min_interval_s: float = 15.0
    vocal_reactions: bool = False            # allow brief spoken amusement; non-verbal laughter is NOT documented for Live
    floor_wait_ms: int = 2500                # how long allowed speech waits for another participant to finish


@dataclass(frozen=True)
class BehaviorSettings:
    lazy_load: bool = True
    keep_models_loaded: bool = True     # False = unload after idle_unload_s
    idle_unload_s: float = 0.0          # 0 = never
    escalate_below_confidence: float = 0.6
    log_level: str = "INFO"
    max_meeting_minutes: int = 30       # initial target; longer meetings are future work


@dataclass(frozen=True)
class RuntimeSettings:
    models: ModelSettings = field(default_factory=ModelSettings)
    inference: InferenceSettings = field(default_factory=InferenceSettings)
    limits: LimitSettings = field(default_factory=LimitSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    live: LiveSettings = field(default_factory=LiveSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    persona: PersonaSettings = field(default_factory=PersonaSettings)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeSettings:
        e = os.environ if env is None else env
        d_lim, d_inf, d_bh = LimitSettings(), InferenceSettings(), BehaviorSettings()
        return cls(
            models=ModelSettings(
                stt_backend=e.get("TARA_STT_BACKEND", "mlx_whisper"), stt_model=e.get("TARA_STT_MODEL", ""),
                llm_backend=e.get("TARA_LOCAL_LLM_BACKEND", "mlx_lm"), llm_model=e.get("TARA_LOCAL_LLM_MODEL", ""),
                tts_backend=e.get("TARA_TTS_BACKEND", "kokoro"), tts_voice=e.get("TARA_TTS_VOICE", ""),
                models_dir=e.get("TARA_MODELS_DIR", "./models")),
            inference=InferenceSettings(
                local_max_tokens=_num(e, "TARA_LOCAL_MAX_TOKENS", d_inf.local_max_tokens, int),
                temperature=_num(e, "TARA_LOCAL_TEMPERATURE", d_inf.temperature),
                top_p=_num(e, "TARA_LOCAL_TOP_P", d_inf.top_p), top_k=_num(e, "TARA_LOCAL_TOP_K", d_inf.top_k, int),
                enable_thinking=_bool(e.get("TARA_LOCAL_THINKING"), False)),
            limits=LimitSettings(
                local_context_tokens=_num(e, "TARA_LOCAL_CONTEXT_TOKENS", d_lim.local_context_tokens, int),
                cloud_context_tokens=_num(e, "TARA_CLOUD_CONTEXT_TOKENS", d_lim.cloud_context_tokens, int),
                max_concurrent_inference=_num(e, "TARA_MAX_CONCURRENT_INFERENCE", d_lim.max_concurrent_inference, int),
                max_queue=_num(e, "TARA_MAX_QUEUE", d_lim.max_queue, int),
                stt_timeout_s=_num(e, "TARA_STT_TIMEOUT_S", d_lim.stt_timeout_s),
                local_llm_timeout_s=_num(e, "TARA_LOCAL_LLM_TIMEOUT_S", d_lim.local_llm_timeout_s),
                cloud_timeout_s=_num(e, "TARA_CLOUD_TIMEOUT_S", d_lim.cloud_timeout_s),
                tts_timeout_s=_num(e, "TARA_TTS_TIMEOUT_S", d_lim.tts_timeout_s),
                tool_timeout_s=_num(e, "TARA_TOOL_TIMEOUT_S", d_lim.tool_timeout_s),
                model_load_timeout_s=_num(e, "TARA_MODEL_LOAD_TIMEOUT_S", d_lim.model_load_timeout_s),
                model_retry_cooldown_s=_num(e, "TARA_MODEL_RETRY_COOLDOWN_S", d_lim.model_retry_cooldown_s)),
            gemini=GeminiSettings(
                enabled=_bool(e.get("TARA_GEMINI_ENABLED"), False),
                api_key=e.get("GEMINI_API_KEY", ""), model=e.get("TARA_LLM_MODEL", "")),
            live=LiveSettings(
                enabled=_bool(e.get("TARA_LIVE_ENABLED"), False), model=e.get("TARA_LIVE_MODEL", ""),
                voice_name=e.get("TARA_LIVE_VOICE", ""),
                connect_timeout_s=_num(e, "TARA_LIVE_CONNECT_TIMEOUT_S", LiveSettings.connect_timeout_s),
                reconnect_attempts=_num(e, "TARA_LIVE_RECONNECT_ATTEMPTS", LiveSettings.reconnect_attempts, int),
                reconnect_backoff_s=_num(e, "TARA_LIVE_RECONNECT_BACKOFF_S", LiveSettings.reconnect_backoff_s),
                max_concurrent_sessions=_num(e, "TARA_LIVE_MAX_SESSIONS", LiveSettings.max_concurrent_sessions, int),
                hold_ms=_num(e, "TARA_LIVE_HOLD_MS", LiveSettings.hold_ms, int),
                playback_queue_chunks=_num(e, "TARA_LIVE_PLAYBACK_QUEUE", LiveSettings.playback_queue_chunks, int),
                uplink_queue_chunks=_num(e, "TARA_LIVE_UPLINK_QUEUE", LiveSettings.uplink_queue_chunks, int),
                local_barge_in=_bool(e.get("TARA_LIVE_LOCAL_BARGE_IN"), True),
                input_transcription=_bool(e.get("TARA_LIVE_INPUT_TRANSCRIPTION"), True),
                output_transcription=_bool(e.get("TARA_LIVE_OUTPUT_TRANSCRIPTION"), True),
                session_resumption=_bool(e.get("TARA_LIVE_RESUMPTION"), True),
                context_compression=_bool(e.get("TARA_LIVE_COMPRESSION"), True),
                silence_duration_ms=_num(e, "TARA_LIVE_SILENCE_MS", 0, int),
                proactive_audio=_bool(e.get("TARA_LIVE_PROACTIVE_AUDIO"), False),
                hybrid_end_of_speech=_bool(e.get("TARA_LIVE_HYBRID_EOS"), False),
                api_version=e.get("TARA_LIVE_API_VERSION", ""),
                tool_behavior=e.get("TARA_LIVE_TOOL_BEHAVIOR", "default")),
            tools=ToolSettings(
                enabled=_bool(e.get("TARA_TOOLS_ENABLED"), True),
                time=_bool(e.get("TARA_TOOLS_TIME"), True),
                calculator=_bool(e.get("TARA_TOOLS_CALCULATOR"), True),
                location=_bool(e.get("TARA_TOOLS_LOCATION"), True),
                weather=_bool(e.get("TARA_TOOLS_WEATHER"), True),
                search=_bool(e.get("TARA_TOOLS_SEARCH"), False),
                fetch=_bool(e.get("TARA_TOOLS_FETCH"), True),
                research=_bool(e.get("TARA_TOOLS_RESEARCH"), True),
                background=_bool(e.get("TARA_TOOLS_BACKGROUND"), True),
                search_provider=e.get("TARA_SEARCH_PROVIDER", "brave").strip().lower(),
                search_api_key=e.get("TARA_SEARCH_API_KEY", ""),
                weather_base_url=e.get("TARA_TOOLS_WEATHER_URL", "https://api.open-meteo.com/v1/forecast"),
                geocoding_base_url=e.get("TARA_TOOLS_GEOCODING_URL", "https://geocoding-api.open-meteo.com/v1"),
                country_base_url=e.get("TARA_TOOLS_COUNTRY_URL", "https://countries.dev"),
                http_timeout_s=_num(e, "TARA_TOOLS_HTTP_TIMEOUT_S", ToolSettings.http_timeout_s),
                research_timeout_s=_num(e, "TARA_TOOLS_RESEARCH_TIMEOUT_S", ToolSettings.research_timeout_s),
                research_sources=_num(e, "TARA_TOOLS_RESEARCH_SOURCES", ToolSettings.research_sources, int),
                research_chars=_num(e, "TARA_TOOLS_RESEARCH_CHARS", ToolSettings.research_chars, int),
                background_tasks=_num(e, "TARA_TOOLS_TASKS", ToolSettings.background_tasks, int),
                task_ttl_s=_num(e, "TARA_TOOLS_TASK_TTL_S", ToolSettings.task_ttl_s),
                max_calls_per_turn=_num(e, "TARA_TOOLS_MAX_CALLS_PER_TURN", ToolSettings.max_calls_per_turn, int),
                search_calls_per_session=_num(e, "TARA_TOOLS_SEARCH_SESSION_LIMIT",
                                              ToolSettings.search_calls_per_session, int),
                weather_cache_s=_num(e, "TARA_TOOLS_WEATHER_CACHE_S", ToolSettings.weather_cache_s),
                geocode_cache_s=_num(e, "TARA_TOOLS_GEOCODE_CACHE_S", ToolSettings.geocode_cache_s),
                country_cache_s=_num(e, "TARA_TOOLS_COUNTRY_CACHE_S", ToolSettings.country_cache_s),
                search_cache_s=_num(e, "TARA_TOOLS_SEARCH_CACHE_S", ToolSettings.search_cache_s),
                user_agent=e.get("TARA_TOOLS_USER_AGENT", ToolSettings.user_agent)),
            persona=PersonaSettings(
                profile=e.get("TARA_PERSONA_PROFILE", "professional").strip().lower(),
                guidance_injection=_bool(e.get("TARA_PERSONA_GUIDANCE"), True),
                guidance_min_interval_s=_num(e, "TARA_PERSONA_GUIDANCE_INTERVAL_S", PersonaSettings.guidance_min_interval_s),
                vocal_reactions=_bool(e.get("TARA_PERSONA_VOCAL_REACTIONS"), False),
                floor_wait_ms=_num(e, "TARA_PERSONA_FLOOR_WAIT_MS", PersonaSettings.floor_wait_ms, int)),
            behavior=BehaviorSettings(
                lazy_load=_bool(e.get("TARA_LAZY_LOAD"), True),
                keep_models_loaded=_bool(e.get("TARA_KEEP_MODELS_LOADED"), True),
                idle_unload_s=_num(e, "TARA_IDLE_UNLOAD_S", d_bh.idle_unload_s),
                escalate_below_confidence=_num(e, "TARA_ESCALATE_BELOW_CONFIDENCE", d_bh.escalate_below_confidence),
                log_level=e.get("TARA_LOG_LEVEL", "INFO").upper(),
                max_meeting_minutes=_num(e, "TARA_MAX_MEETING_MINUTES", d_bh.max_meeting_minutes, int)),
        )

    def redacted(self) -> dict:
        """Safe to log: no secrets."""
        g, t = self.gemini, self.tools
        tools = {k: v for k, v in t.__dict__.items() if k != "search_api_key"}
        tools["search_api_key"] = "set" if t.search_api_key else "missing"
        return {"persona": self.persona.__dict__, "live": self.live.__dict__, "models": self.models.__dict__,
                "inference": self.inference.__dict__, "limits": self.limits.__dict__, "tools": tools,
                "gemini": {"enabled": g.enabled, "model": g.model, "api_key": "set" if g.api_key else "missing"},
                "behavior": self.behavior.__dict__}


def validate(s: RuntimeSettings, *, check_files: bool = False, require_local_models: bool = False) -> list[Issue]:
    """Pure validation. `check_files` verifies that configured *paths* exist (no loading)."""
    out: list[Issue] = []

    def err(f: str, m: str) -> None:
        out.append(Issue("error", f, m))

    def warn(f: str, m: str) -> None:
        out.append(Issue("warning", f, m))

    m, i, lim, g, b, ts = s.models, s.inference, s.limits, s.gemini, s.behavior, s.tools
    if m.stt_backend not in STT_BACKENDS: err("TARA_STT_BACKEND", f"unknown backend {m.stt_backend!r}")
    if m.llm_backend not in LLM_BACKENDS: err("TARA_LOCAL_LLM_BACKEND", f"unknown backend {m.llm_backend!r}")
    if m.tts_backend not in TTS_BACKENDS: err("TARA_TTS_BACKEND", f"unknown backend {m.tts_backend!r}")
    if require_local_models:
        if m.stt_backend != "fake" and not m.stt_model: err("TARA_STT_MODEL", "not set")
        if m.llm_backend != "fake" and not m.llm_model: err("TARA_LOCAL_LLM_MODEL", "not set")
        if m.tts_backend != "fake" and not m.tts_voice: err("TARA_TTS_VOICE", "not set")
    if check_files:
        for key, ref in (("TARA_STT_MODEL", m.stt_model), ("TARA_LOCAL_LLM_MODEL", m.llm_model)):
            if ref and (ref.startswith(("/", "./", "../", "~"))) and not Path(ref).expanduser().exists():
                err(key, f"path does not exist: {ref}")
    if g.enabled:
        if not g.api_key: err("GEMINI_API_KEY", "required when TARA_GEMINI_ENABLED is on")
        if not g.model: err("TARA_LLM_MODEL", "required when Gemini is enabled (check the models page; ids change)")
        if lim.cloud_context_tokens <= 0:
            err("TARA_CLOUD_CONTEXT_TOKENS", "set the usable cloud context size; it is not assumed")
    lv = s.live
    if lv.enabled:
        if not g.api_key: err("GEMINI_API_KEY", "required when TARA_LIVE_ENABLED is on")
        if not lv.model: err("TARA_LIVE_MODEL", "required when Live is enabled (ids change; check the models page)")
        if lv.connect_timeout_s <= 0: err("TARA_LIVE_CONNECT_TIMEOUT_S", "must be positive")
        if lv.max_concurrent_sessions < 1: err("TARA_LIVE_MAX_SESSIONS", "must be >= 1")
        if lv.reconnect_attempts < 0: err("TARA_LIVE_RECONNECT_ATTEMPTS", "must be >= 0")
        if lv.reconnect_backoff_s < 0: err("TARA_LIVE_RECONNECT_BACKOFF_S", "must be >= 0")
        if lv.hold_ms < 0 or lv.playback_queue_chunks < 1 or lv.uplink_queue_chunks < 1:
            err("TARA_LIVE_*", "hold_ms >= 0 and queue sizes >= 1")
        if not lv.context_compression and lv.session_resumption is False:
            warn("TARA_LIVE_COMPRESSION", "without compression audio-only sessions end after about 15 minutes (docs)")
    if ts.enabled:
        if ts.search_provider != "brave":
            err("TARA_SEARCH_PROVIDER", "only 'brave' is implemented")
        if ts.search and not ts.search_api_key:
            err("TARA_SEARCH_API_KEY", "required when TARA_TOOLS_SEARCH is on")
        if not ts.user_agent.strip():
            err("TARA_TOOLS_USER_AGENT", "must not be empty")
        if ts.research and not ts.fetch:
            warn("TARA_TOOLS_RESEARCH", "research needs URL retrieval; it will not be registered while fetch is off")
        for field_name, timeout_value in (
                ("TARA_TOOLS_HTTP_TIMEOUT_S", ts.http_timeout_s),
                ("TARA_TOOLS_RESEARCH_TIMEOUT_S", ts.research_timeout_s),
                ("TARA_TOOLS_TASK_TTL_S", ts.task_ttl_s)):
            if not 0 < timeout_value <= 86_400.0:
                err(field_name, "must be a positive number of seconds")
        for field_name, count_value, low, high in (
                ("TARA_TOOLS_RESEARCH_SOURCES", ts.research_sources, 1, 5),
                ("TARA_TOOLS_RESEARCH_CHARS", ts.research_chars, 1000, 20_000),
                ("TARA_TOOLS_TASKS", ts.background_tasks, 1, 8),
                ("TARA_TOOLS_MAX_CALLS_PER_TURN", ts.max_calls_per_turn, 1, 20),
                ("TARA_TOOLS_SEARCH_SESSION_LIMIT", ts.search_calls_per_session, 1, 50)):
            if not low <= count_value <= high:
                err(field_name, f"must be between {low} and {high}")
        for field_name, cache_value in (("TARA_TOOLS_WEATHER_CACHE_S", ts.weather_cache_s),
                                        ("TARA_TOOLS_GEOCODE_CACHE_S", ts.geocode_cache_s),
                                        ("TARA_TOOLS_COUNTRY_CACHE_S", ts.country_cache_s),
                                        ("TARA_TOOLS_SEARCH_CACHE_S", ts.search_cache_s)):
            if cache_value < 0:
                err(field_name, "must be >= 0")
        for field_name, url_value in (("TARA_TOOLS_WEATHER_URL", ts.weather_base_url),
                                      ("TARA_TOOLS_GEOCODING_URL", ts.geocoding_base_url),
                                      ("TARA_TOOLS_COUNTRY_URL", ts.country_base_url)):
            if not url_value.startswith("https://"):
                err(field_name, "must be an https URL")
    pr = s.persona
    if pr.profile not in ("professional", "conversational", "expressive"):
        err("TARA_PERSONA_PROFILE", "must be professional, conversational or expressive")
    if pr.guidance_min_interval_s < 0 or pr.floor_wait_ms < 0:
        err("TARA_PERSONA_*", "intervals must be >= 0")
    if lv.tool_behavior not in ("default", "blocking", "non_blocking"):
        err("TARA_LIVE_TOOL_BEHAVIOR", "must be default, blocking or non_blocking")
    if lim.local_context_tokens <= 0: err("TARA_LOCAL_CONTEXT_TOKENS", "must be positive")
    if i.local_max_tokens <= 0 or i.local_max_tokens >= lim.local_context_tokens > 0:
        err("TARA_LOCAL_MAX_TOKENS", "must be positive and smaller than the local context")
    if lim.max_concurrent_inference < 1: err("TARA_MAX_CONCURRENT_INFERENCE", "must be >= 1")
    if lim.max_queue < 0: err("TARA_MAX_QUEUE", "must be >= 0")
    for tkey, tval in (("stt_timeout_s", lim.stt_timeout_s), ("local_llm_timeout_s", lim.local_llm_timeout_s),
                     ("cloud_timeout_s", lim.cloud_timeout_s), ("tts_timeout_s", lim.tts_timeout_s),
                     ("tool_timeout_s", lim.tool_timeout_s), ("model_load_timeout_s", lim.model_load_timeout_s)):
        if tval <= 0: err(tkey, "must be positive")
    if not 0.0 <= b.escalate_below_confidence <= 1.0: err("TARA_ESCALATE_BELOW_CONFIDENCE", "must be in [0, 1]")
    if b.log_level not in LOG_LEVELS: err("TARA_LOG_LEVEL", f"must be one of {sorted(LOG_LEVELS)}")
    if b.idle_unload_s < 0: err("TARA_IDLE_UNLOAD_S", "must be >= 0")
    if not b.keep_models_loaded and b.idle_unload_s == 0:
        warn("TARA_IDLE_UNLOAD_S", "keep_models_loaded is off but idle_unload_s is 0, so models never unload")
    if lim.max_concurrent_inference > 1:
        warn("TARA_MAX_CONCURRENT_INFERENCE", "more than 1 may exceed memory/thermal budget; measure first")
    return out


def load_settings(env: Mapping[str, str] | None = None, **kw) -> RuntimeSettings:
    """Parse + validate; raises ConfigError listing problems (never including secrets)."""
    s = RuntimeSettings.from_env(env)
    errors = [f"{i.field}: {i.message}" for i in validate(s, **kw) if i.severity == "error"]
    if errors:
        raise ConfigError("invalid settings: " + "; ".join(errors))
    return s
