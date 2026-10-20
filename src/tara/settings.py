"""Runtime settings: one typed place for model selection, limits, timeouts, Gemini
and behavior. Loaded from environment variables (no secrets in code or files).

Nothing here loads a model or touches the network. `validate` only inspects values
(and, when asked, whether configured local paths exist).
"""
from __future__ import annotations

import os
import re
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
class MemorySettings:
    """Persistent long-term memory. PostgreSQL is the only required service in v1."""

    enabled: bool = False
    database_url: str = field(default="", repr=False)  # never shown in repr or logs
    search_limit: int = 5
    context_max_chars: int = 3500          # startup block cap; token estimate is chars/4
    startup_context: bool = True           # inject recent memories when a live session opens
    write_timeout_s: float = 3.0
    recall_timeout_s: float = 2.0
    schema: str = ""                       # optional; tests use a per-run schema


@dataclass(frozen=True)
class WorkSettings:
    """Secretary, interviewing and document generation (docs/WORKFLOWS.md). Off by default."""

    enabled: bool = False
    dir: str = "./work"                      # inbox, candidates, interviews, documents live here
    auto_memory: str = "candidate"           # off | candidate (auto-saves are candidates with expiry, never confirmed)
    auto_memory_ttl_days: int = 30
    hiring: bool = True                      # interview tools (only registered when enabled)
    documents: bool = True                   # document tools
    github_token: str = field(default="", repr=False)   # optional; raises the GitHub limit from 60 to 5,000 per hour
    github_max_repos: int = 6
    doc_font: str = "Calibri"
    doc_font_heading: str = "Calibri"
    doc_font_size: float = 11.0
    doc_accent: str = "1F4E79"               # hex RGB
    pdf_font_regular: str = ""               # TTF paths for Unicode PDFs; empty = auto-detect, then Helvetica
    pdf_font_bold: str = ""
    pdf_font_italic: str = ""
    job_timeout_s: float = 60.0
    max_jobs: int = 2
    auto_minutes: bool = True                # at meeting end, save a minutes file and a long-term summary of confirmed items
    auto_record: bool = True                 # keep the meeting automatically: transcript chunks, summary, candidate facts
    hiring_model: str = ""                  # text model for reading CVs and writing questions (empty = TARA_RECORDER_MODEL, then TARA_LLM_MODEL)
    meeting_type: str = "general"           # general | standup | one_on_one | retro | client_call | brainstorm
    opening_brief: bool = True               # when a continuing section starts, Tara says what was left open
    nudges: bool = True                      # spoken time nudges when an agenda with a length is set
    vocab_file: str = ""                      # names and jargon, one per line (empty = <work dir>/vocabulary.txt)
    vocab_terms: str = ""                     # extra comma-separated terms
    recorder_model: str = ""                # fast text model for summaries (empty = TARA_LLM_MODEL); a lite model is faster
    digest_timeout_s: float = 45.0           # how long to wait for a summary at checkpoints and at the end
    chunk_turns: int = 6                     # turns per stored transcript chunk
    episode_retention_days: int = 90         # stored transcript chunks are deleted after this
    fact_retention_days: int = 180           # unconfirmed extracted facts expire after this
    recall_nudges: bool = True               # quiet "we confirmed this earlier" notes to the model (never spoken unprompted)
    interview_question_limit_s: float = 180.0  # hard limit per question, including retries; monotonic timer in code
    interview_retry_limit: int = 1             # one retry after an incorrect or unclear first answer
    interview_eval_timeout_s: float = 20.0     # semantic evaluation must answer within this; fallback scoring otherwise
    interview_answer_grace_s: float = 2.0      # wait this long for delayed transcription before saying no answer was heard


@dataclass(frozen=True)
class MeetSettings:
    """The Google Meet browser bot (docs/GOOGLE_MEET_BOT.md). No credentials live here: the bot signs in once by hand into the profile."""

    profile_dir: str = "./work/meet/profile"   # the bot's own Chrome profile (its Google sign-in lives only here)
    lock_dir: str = "./work/meet/locks"         # one lock file per meeting so two workers never join the same one
    bot_name: str = "Tara"                      # the name shown in the meeting (guest join) and ignored in the people list
    channel: str = "chrome"                     # the installed browser Playwright drives; no browser is downloaded
    headless: bool = False                      # first real tests should be visible
    admit_timeout_s: float = 600.0              # how long to wait in the waiting room for a person to admit the bot
    join_timeout_s: float = 60.0
    reconnects: int = 3
    audio: str = "page"                         # "page" (taps inside the page) or "devices" (two virtual audio cables, the route other bots use)
    in_device: str = "BlackHole 2ch"            # devices mode: Chrome's speaker, read by Tara
    out_device: str = "BlackHole 16ch"          # devices mode: Tara's voice, Chrome's microphone
    half_duplex: bool = False                   # silence the meeting audio while Tara speaks (stops her hearing her own voice back; no voice interruption)


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
    search: bool = False               # requires a provider key (Brave or Tavily)
    fetch: bool = True                 # public URL reading; SSRF checks always apply
    research: bool = True              # only registered when search + fetch are available
    background: bool = True
    search_provider: str = "brave"      # brave | tavily
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
    memory: MemorySettings = field(default_factory=MemorySettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    work: WorkSettings = field(default_factory=WorkSettings)
    meet: MeetSettings = field(default_factory=MeetSettings)
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
            memory=MemorySettings(
                enabled=_bool(e.get("TARA_MEMORY_ENABLED"), False),
                database_url=e.get("TARA_MEMORY_DATABASE_URL", ""),
                search_limit=_num(e, "TARA_MEMORY_SEARCH_LIMIT", MemorySettings.search_limit, int),
                context_max_chars=_num(e, "TARA_MEMORY_CONTEXT_CHARS", MemorySettings.context_max_chars, int),
                startup_context=_bool(e.get("TARA_MEMORY_STARTUP_CONTEXT"), True),
                write_timeout_s=_num(e, "TARA_MEMORY_WRITE_TIMEOUT_S", MemorySettings.write_timeout_s),
                recall_timeout_s=_num(e, "TARA_MEMORY_RECALL_TIMEOUT_S", MemorySettings.recall_timeout_s),
                schema=e.get("TARA_MEMORY_SCHEMA", "")),
            meet=MeetSettings(
                profile_dir=e.get("TARA_MEET_PROFILE_DIR", MeetSettings.profile_dir), lock_dir=e.get("TARA_MEET_LOCK_DIR", MeetSettings.lock_dir),
                bot_name=(e.get("TARA_MEET_BOT_NAME", "Tara").strip() or "Tara")[:60], channel=e.get("TARA_MEET_CHANNEL", "chrome").strip(),
                headless=_bool(e.get("TARA_MEET_HEADLESS"), False),
                admit_timeout_s=_num(e, "TARA_MEET_ADMIT_TIMEOUT_S", MeetSettings.admit_timeout_s),
                join_timeout_s=_num(e, "TARA_MEET_JOIN_TIMEOUT_S", MeetSettings.join_timeout_s),
                reconnects=_num(e, "TARA_MEET_RECONNECTS", MeetSettings.reconnects, int),
                audio=e.get("TARA_MEET_AUDIO", "page").strip().lower() or "page", half_duplex=_bool(e.get("TARA_MEET_HALF_DUPLEX"), False),
                in_device=e.get("TARA_MEET_IN_DEVICE", MeetSettings.in_device).strip(), out_device=e.get("TARA_MEET_OUT_DEVICE", MeetSettings.out_device).strip()),
            work=WorkSettings(
                enabled=_bool(e.get("TARA_WORK_ENABLED"), False), dir=e.get("TARA_WORK_DIR", "./work"),
                auto_memory=e.get("TARA_AUTO_MEMORY", "candidate").strip().lower(),
                auto_memory_ttl_days=_num(e, "TARA_AUTO_MEMORY_TTL_DAYS", WorkSettings.auto_memory_ttl_days, int),
                hiring=_bool(e.get("TARA_HIRING_ENABLED"), True), documents=_bool(e.get("TARA_DOCS_ENABLED"), True),
                github_token=e.get("TARA_GITHUB_TOKEN", ""),
                github_max_repos=_num(e, "TARA_GITHUB_MAX_REPOS", WorkSettings.github_max_repos, int),
                doc_font=e.get("TARA_DOC_FONT", "Calibri"), doc_font_heading=e.get("TARA_DOC_FONT_HEADING", "Calibri"),
                doc_font_size=_num(e, "TARA_DOC_FONT_SIZE", WorkSettings.doc_font_size),
                doc_accent=e.get("TARA_DOC_ACCENT", "1F4E79").strip().lstrip("#"),
                pdf_font_regular=e.get("TARA_PDF_FONT_REGULAR", ""), pdf_font_bold=e.get("TARA_PDF_FONT_BOLD", ""),
                pdf_font_italic=e.get("TARA_PDF_FONT_ITALIC", ""),
                job_timeout_s=_num(e, "TARA_JOB_TIMEOUT_S", WorkSettings.job_timeout_s),
                max_jobs=_num(e, "TARA_MAX_JOBS", WorkSettings.max_jobs, int),
                auto_minutes=_bool(e.get("TARA_AUTO_MINUTES"), True),
                auto_record=_bool(e.get("TARA_AUTO_RECORD"), True),
                hiring_model=e.get("TARA_HIRING_MODEL", "").strip(),
                meeting_type=e.get("TARA_MEETING_TYPE", "general").strip() or "general",
                opening_brief=_bool(e.get("TARA_OPENING_BRIEF"), True), nudges=_bool(e.get("TARA_NUDGES"), True),
                vocab_file=e.get("TARA_VOCAB_FILE", "").strip(), vocab_terms=e.get("TARA_VOCAB", ""),
                recorder_model=e.get("TARA_RECORDER_MODEL", "").strip(),
                digest_timeout_s=_num(e, "TARA_DIGEST_TIMEOUT_S", WorkSettings.digest_timeout_s),
                chunk_turns=_num(e, "TARA_CHUNK_TURNS", WorkSettings.chunk_turns, int),
                episode_retention_days=_num(e, "TARA_EPISODE_RETENTION_DAYS", WorkSettings.episode_retention_days, int),
                fact_retention_days=_num(e, "TARA_FACT_RETENTION_DAYS", WorkSettings.fact_retention_days, int),
                recall_nudges=_bool(e.get("TARA_RECALL_NUDGES"), True),
                interview_question_limit_s=_num(e, "TARA_INTERVIEW_QUESTION_LIMIT_S",
                                                WorkSettings.interview_question_limit_s),
                interview_retry_limit=_num(e, "TARA_INTERVIEW_RETRY_LIMIT", WorkSettings.interview_retry_limit, int),
                interview_eval_timeout_s=_num(e, "TARA_INTERVIEW_EVAL_TIMEOUT_S",
                                               WorkSettings.interview_eval_timeout_s),
                interview_answer_grace_s=_num(e, "TARA_INTERVIEW_ANSWER_GRACE_S",
                                               WorkSettings.interview_answer_grace_s)),
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
        mem = self.memory
        tools = {k: v for k, v in t.__dict__.items() if k != "search_api_key"}
        tools["search_api_key"] = "set" if t.search_api_key else "missing"
        work = {k: v for k, v in self.work.__dict__.items() if k != "github_token"}
        work["github_token"] = "set" if self.work.github_token else "none"
        return {"work": work, "persona": self.persona.__dict__, "live": self.live.__dict__, "models": self.models.__dict__,
                "inference": self.inference.__dict__, "limits": self.limits.__dict__, "tools": tools,
                "gemini": {"enabled": g.enabled, "model": g.model, "api_key": "set" if g.api_key else "missing"},
                "memory": {"enabled": mem.enabled, "database_url": "set" if mem.database_url else "missing",
                           "search_limit": mem.search_limit, "context_max_chars": mem.context_max_chars,
                           "startup_context": mem.startup_context, "schema": mem.schema or "public"},
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
    mt = s.meet
    if not mt.profile_dir.strip() or not mt.lock_dir.strip():
        err("TARA_MEET_PROFILE_DIR", "profile and lock folders must not be empty")
    if not 5 <= mt.join_timeout_s <= 300 or not 10 <= mt.admit_timeout_s <= 3600:
        err("TARA_MEET_ADMIT_TIMEOUT_S", "join timeout must be 5 to 300 s and admit timeout 10 to 3600 s")
    if not 0 <= mt.reconnects <= 10:
        err("TARA_MEET_RECONNECTS", "must be between 0 and 10")
    if mt.audio not in ("page", "devices"):
        err("TARA_MEET_AUDIO", "must be page or devices")
    if mt.audio == "devices" and (not mt.in_device or not mt.out_device or mt.in_device == mt.out_device):
        err("TARA_MEET_IN_DEVICE", "devices mode needs two different cables (a single loopback would feed the meeting back into itself)")
    wk = s.work
    if wk.enabled:
        if wk.auto_memory not in ("off", "candidate"):
            err("TARA_AUTO_MEMORY", "must be 'off' or 'candidate'")
        if not wk.dir.strip():
            err("TARA_WORK_DIR", "must not be empty")
        if not 1 <= wk.auto_memory_ttl_days <= 365:
            err("TARA_AUTO_MEMORY_TTL_DAYS", "must be between 1 and 365")
        if not 1 <= wk.github_max_repos <= 12:
            err("TARA_GITHUB_MAX_REPOS", "must be between 1 and 12")
        if not 6 <= wk.doc_font_size <= 20:
            err("TARA_DOC_FONT_SIZE", "must be between 6 and 20")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", wk.doc_accent):
            err("TARA_DOC_ACCENT", "must be a 6-digit hex colour such as 1F4E79")
        if not 5 <= wk.job_timeout_s <= 600:
            err("TARA_JOB_TIMEOUT_S", "must be between 5 and 600 seconds")
        try:
            from .meetings.types import parse_type
            parse_type(wk.meeting_type)
        except ValueError as exc:
            err("TARA_MEETING_TYPE", str(exc))
        if not 5 <= wk.digest_timeout_s <= 180:
            err("TARA_DIGEST_TIMEOUT_S", "must be between 5 and 180 seconds")
        if not 2 <= wk.chunk_turns <= 40:
            err("TARA_CHUNK_TURNS", "must be between 2 and 40")
        if not 1 <= wk.episode_retention_days <= 3650 or not 1 <= wk.fact_retention_days <= 3650:
            err("TARA_EPISODE_RETENTION_DAYS", "retention must be between 1 and 3650 days")
        if not 1 <= wk.max_jobs <= 8:
            err("TARA_MAX_JOBS", "must be between 1 and 8")
        if not 10 <= wk.interview_question_limit_s <= 600:
            err("TARA_INTERVIEW_QUESTION_LIMIT_S", "must be between 10 and 600 seconds")
        if not 0 <= wk.interview_retry_limit <= 2:
            err("TARA_INTERVIEW_RETRY_LIMIT", "must be between 0 and 2")
        if not 1 <= wk.interview_eval_timeout_s <= 60:
            err("TARA_INTERVIEW_EVAL_TIMEOUT_S", "must be between 1 and 60 seconds")
        if not 0 <= wk.interview_answer_grace_s <= 10:
            err("TARA_INTERVIEW_ANSWER_GRACE_S", "must be between 0 and 10 seconds")
        if (wk.documents or wk.hiring) and not g.model:
            warn("TARA_LLM_MODEL", "no text model id set: documents and interview plans fall back to plain templates")
        if (wk.documents or wk.hiring) and g.model and not g.api_key:
            warn("GEMINI_API_KEY", "a text model id is set but there is no key: documents and plans fall back to templates")
    if ts.enabled:
        if ts.search_provider not in ("brave", "tavily"):
            err("TARA_SEARCH_PROVIDER", "must be 'brave' or 'tavily'")
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
    mem = s.memory
    if mem.enabled:
        if not mem.database_url:
            err("TARA_MEMORY_DATABASE_URL", "required when TARA_MEMORY_ENABLED is on")
        elif not mem.database_url.startswith(("postgresql://", "postgres://")):
            err("TARA_MEMORY_DATABASE_URL", "must be a postgresql:// or postgres:// URL")
        if mem.search_limit < 1: err("TARA_MEMORY_SEARCH_LIMIT", "must be >= 1")
        if mem.context_max_chars < 200: err("TARA_MEMORY_CONTEXT_CHARS", "must be >= 200")
        if mem.write_timeout_s <= 0 or mem.recall_timeout_s <= 0:
            err("TARA_MEMORY_*_TIMEOUT_S", "must be positive")
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
