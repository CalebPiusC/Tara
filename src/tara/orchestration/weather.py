"""Current weather and short forecasts from Open-Meteo.

Open-Meteo's free service is non-commercial, no-key and CC BY 4.0; every result
carries attribution.  It does not provide official weather alerts, so Tara must
say alerts are unavailable rather than inventing one.
"""
from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..tools import ToolDef
from .contracts import ForecastDay, Geocoder, HttpClient, Place, WeatherProvider, WeatherSnapshot, as_dict
from .http import TTLCache, parse_json

ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
_URL = "https://api.open-meteo.com/v1/forecast"
_CURRENT = "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,precipitation,weather_code"
_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"

_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def describe_weather(code: int | None) -> str:
    return _WMO.get(code, "unknown weather code") if code is not None else "unknown"


class OpenMeteoWeather:
    def __init__(self, http: HttpClient, *, base_url: str = _URL, timeout_s: float = 8.0,
                 cache_ttl_s: float = 300.0):
        self.http, self.base_url, self.timeout_s = http, base_url, timeout_s
        self.cache_ttl_s = cache_ttl_s
        self.cache = TTLCache(max_entries=128)

    async def current(self, place: Place, forecast_days: int = 0) -> WeatherSnapshot:
        forecast_days = max(0, min(int(forecast_days), 7))
        key = f"{place.latitude:.4f}|{place.longitude:.4f}|{forecast_days}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        params: dict[str, Any] = {
            "latitude": f"{place.latitude:.4f}", "longitude": f"{place.longitude:.4f}",
            "current": _CURRENT, "timezone": "auto", "wind_speed_unit": "kmh",
        }
        if forecast_days:
            params["daily"] = _DAILY
            params["forecast_days"] = forecast_days
        response = await self.http.get(self.base_url, params=params, timeout_s=self.timeout_s, max_bytes=300_000)
        if response.status != 200:
            raise ToolError("weather_unavailable", "The weather service is unavailable", retryable=True)
        data = parse_json(response.body)
        if not isinstance(data, dict) or not isinstance(data.get("current"), dict):
            raise ToolError("weather_unavailable", "The weather service returned no current conditions")
        current = data["current"]
        code = _int_or_none(current.get("weather_code"))
        snapshot = WeatherSnapshot(
            place=place,
            observed_at=str(current.get("time", "")),
            timezone=str(data.get("timezone", place.timezone)),
            temperature_c=_float_or_none(current.get("temperature_2m")),
            apparent_temperature_c=_float_or_none(current.get("apparent_temperature")),
            humidity_pct=_float_or_none(current.get("relative_humidity_2m")),
            wind_kmh=_float_or_none(current.get("wind_speed_10m")),
            precipitation_mm=_float_or_none(current.get("precipitation")),
            weather_code=code,
            description=describe_weather(code),
            forecast=_forecast(data.get("daily")),
            alerts=(),
            alerts_supported=False,
            attribution=ATTRIBUTION,
        )
        self.cache.set(key, snapshot, self.cache_ttl_s)
        return snapshot


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _forecast(daily: Any) -> tuple[ForecastDay, ...]:
    if not isinstance(daily, dict):
        return ()
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    probs = daily.get("precipitation_probability_max") or []
    sums = daily.get("precipitation_sum") or []
    out: list[ForecastDay] = []
    for i, date in enumerate(dates if isinstance(dates, list) else []):
        code = _int_or_none(codes[i] if isinstance(codes, list) and i < len(codes) else None)
        out.append(ForecastDay(
            date=str(date), weather_code=code, description=describe_weather(code),
            temp_max_c=_float_or_none(highs[i] if isinstance(highs, list) and i < len(highs) else None),
            temp_min_c=_float_or_none(lows[i] if isinstance(lows, list) and i < len(lows) else None),
            precipitation_probability_max=_float_or_none(probs[i] if isinstance(probs, list) and i < len(probs) else None),
            precipitation_sum_mm=_float_or_none(sums[i] if isinstance(sums, list) and i < len(sums) else None),
        ))
    return tuple(out)


def weather_tool_defs(geocoder: Geocoder, weather: WeatherProvider) -> tuple[ToolDef, ...]:
    async def get_weather(location: str, country: str = "", forecast_days: int = 0) -> dict[str, Any]:
        query = f"{location}, {country}" if country.strip() else location
        places = await geocoder.search(query, 5)
        best = places[0]
        snapshot = await weather.current(best, forecast_days)
        assumed = len(places) > 1 and not country.strip()
        return {
            "location": as_dict(best),
            "weather": as_dict(snapshot),
            "ambiguous": len(places) > 1,
            "assumed": assumed,
            "note": "More than one place matched; state the assumption or ask which one is meant."
                    if assumed else "",
            "alerts_supported": False,
            "alerts_note": "This provider does not supply official weather alerts; never invent one.",
        }

    return (
        ToolDef("get_weather", {"location": str}, get_weather, optional={"country": str, "forecast_days": int},
                description="Get current conditions and an optional short forecast from Open-Meteo for a place. "
                            "Report the observation time and attribution; alerts are not available.",
                timeout_s=12.0, permission="network_read", side_effect=False, category="weather",
                parameters_schema={"type": "OBJECT", "properties": {
                    "location": {"type": "STRING", "description": "City or place name"},
                    "country": {"type": "STRING", "description": "Optional country to disambiguate"},
                    "forecast_days": {"type": "INTEGER", "description": "0 for current only, up to 7"}},
                    "required": ["location"]}),
    )
