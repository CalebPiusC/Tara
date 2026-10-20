"""Typed contracts for orchestration tools and external providers.

The types here are deliberately data-only.  Nothing in this module decides whether
Tara may speak; that remains in `tara.voice.participation` and `tara.voice.engine`.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---- HTTP -----------------------------------------------------------------
@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()


class HttpClient(Protocol):
    async def get(self, url: str, *, params: Mapping[str, Any] | None = None,
                  headers: Mapping[str, str] | None = None, timeout_s: float = 8.0,
                  max_bytes: int = 1_000_000) -> HttpResponse: ...

    async def post(self, url: str, *, json_body: Mapping[str, Any] | None = None,
                   headers: Mapping[str, str] | None = None, timeout_s: float = 8.0,
                   max_bytes: int = 1_000_000) -> HttpResponse: ...


# ---- search / retrieval ---------------------------------------------------
@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    page_age: str = ""
    publisher: str = ""


@dataclass(frozen=True)
class SearchResponse:
    query: str
    hits: tuple[SearchHit, ...]
    provider: str
    altered_query: str = ""


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 5) -> SearchResponse: ...


# ---- geography ------------------------------------------------------------
@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    timezone: str = ""
    country: str = ""
    country_code: str = ""
    admin1: str = ""
    population: int = 0
    feature_code: str = ""


@dataclass(frozen=True)
class CountryFacts:
    name: str
    capital: str = ""
    region: str = ""
    subregion: str = ""
    population: int = 0
    timezones: tuple[str, ...] = ()
    alpha2: str = ""
    alpha3: str = ""
    currencies: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


class Geocoder(Protocol):
    async def search(self, query: str, count: int = 5, country_code: str = "") -> tuple[Place, ...]: ...


class CountryProvider(Protocol):
    async def lookup(self, name: str) -> CountryFacts: ...


# ---- weather --------------------------------------------------------------
@dataclass(frozen=True)
class ForecastDay:
    date: str
    weather_code: int | None = None
    description: str = ""
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    precipitation_probability_max: float | None = None
    precipitation_sum_mm: float | None = None


@dataclass(frozen=True)
class WeatherSnapshot:
    place: Place
    observed_at: str
    timezone: str
    temperature_c: float | None
    apparent_temperature_c: float | None
    humidity_pct: float | None
    wind_kmh: float | None
    precipitation_mm: float | None
    weather_code: int | None
    description: str
    forecast: tuple[ForecastDay, ...] = ()
    alerts: tuple[dict[str, Any], ...] = ()
    alerts_supported: bool = False
    provider: str = "open-meteo"
    attribution: str = "Weather data by Open-Meteo.com (CC BY 4.0)"


class WeatherProvider(Protocol):
    async def current(self, place: Place, forecast_days: int = 0) -> WeatherSnapshot: ...


# ---- research -------------------------------------------------------------
@dataclass(frozen=True)
class ResearchSource:
    id: str
    title: str
    url: str
    snippet: str = ""
    text: str = ""
    published: str = ""
    publisher: str = ""
    fetched: bool = False
    error: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class ResearchResult:
    query: str
    queries: tuple[str, ...]
    sources: tuple[ResearchSource, ...]
    warnings: tuple[str, ...] = ()
    provider: str = ""
    started_at: str = ""
    finished_at: str = ""
    session_id: str = ""
    tenant_id: str = ""
    meeting_id: str = ""


@dataclass(frozen=True)
class ResearchContext:
    session_id: str
    tenant_id: str
    meeting_id: str


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    status: str                  # queued | running | completed | failed | cancelled
    query: str
    created_at: float
    updated_at: float
    deadline_at: float
    progress: str = ""
    error: str = ""
    retryable: bool = False
    result: ResearchResult | None = field(default=None)


# ---- JSON helpers ---------------------------------------------------------
def as_dict(value: Any) -> Any:
    """Convert frozen dataclasses/tuples into JSON-safe data without leaking methods."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        return as_dict(asdict(value))
    if isinstance(value, tuple):
        return [as_dict(v) for v in value]
    if isinstance(value, list):
        return [as_dict(v) for v in value]
    if isinstance(value, dict):
        return {str(k): as_dict(v) for k, v in value.items()}
    return value
