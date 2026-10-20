"""Location, country facts, and weather parsing with fake providers (offline)."""
from __future__ import annotations

import pytest
from orchestration_fakes import FakeHttp, json_response

from tara.errors import ToolError
from tara.orchestration.places import CountriesDevProvider, OpenMeteoGeocoder, place_tool_defs
from tara.orchestration.weather import OpenMeteoWeather, describe_weather, weather_tool_defs

GEO = "https://geo.example/search"
COUNTRY = "https://countries.example/name/Nigeria"
WEATHER = "https://weather.example/forecast"


def places_http() -> FakeHttp:
    return FakeHttp({
        GEO: json_response({"results": [
            {"name": "Lagos", "latitude": 6.454, "longitude": 3.394, "timezone": "Africa/Lagos",
             "country": "Nigeria", "country_code": "NG", "admin1": "Lagos", "population": 15388000},
            {"name": "Lagos", "latitude": 43.2, "longitude": -0.2, "timezone": "Europe/Paris",
             "country": "France", "country_code": "FR", "population": 540},
        ]}),
        COUNTRY: json_response([{
            "name": "Nigeria", "capital": "Abuja", "region": "Africa", "subregion": "Western Africa",
            "population": 206139587, "timezones": ["UTC+01:00"], "alpha2Code": "NG", "alpha3Code": "NGA",
            "currencies": [{"code": "NGN", "name": "Nigerian naira"}], "languages": [{"name": "English"}],
        }]),
    })


def test_geocode_and_country_tools_label_ambiguity_and_provenance():
    http = places_http()
    geocoder = OpenMeteoGeocoder(http, base_url="https://geo.example")
    country = CountriesDevProvider(http, base_url="https://countries.example")
    defs = {d.name: d for d in place_tool_defs(geocoder, country)}

    async def go():
        result = await defs["geocode_place"].handler("Lagos")
        assert result["best"]["name"] == "Lagos" and result["best"]["timezone"] == "Africa/Lagos"
        assert result["ambiguous"] is True and result["assumed"] is True
        assert "Open-Meteo" in result["attribution"]
        facts = await defs["country_info"].handler("Nigeria")
        assert facts["country"]["capital"] == "Abuja"
        assert facts["country"]["currencies"] == ["NGN (Nigerian naira)"]
        assert facts["country"]["languages"] == ["English"]

    import asyncio
    asyncio.run(go())


def test_weather_parses_current_and_forecast_without_inventing_alerts():
    http = places_http()
    http.responses[WEATHER] = json_response({
        "timezone": "Africa/Lagos",
        "current": {"time": "2026-10-20T12:00", "temperature_2m": 29.1, "apparent_temperature": 34.7,
                    "relative_humidity_2m": 75, "wind_speed_10m": 11.4, "precipitation": 0.0,
                    "weather_code": 3},
        "daily": {"time": ["2026-10-20"], "weather_code": [61], "temperature_2m_max": [30.0],
                  "temperature_2m_min": [25.0], "precipitation_probability_max": [80],
                  "precipitation_sum": [2.5]},
    })
    geocoder = OpenMeteoGeocoder(http, base_url="https://geo.example")
    weather = OpenMeteoWeather(http, base_url="https://weather.example/forecast")
    defs = {d.name: d for d in weather_tool_defs(geocoder, weather)}

    async def go():
        result = await defs["get_weather"].handler("Lagos", "", 1)
        current = result["weather"]
        assert current["temperature_c"] == 29.1
        assert current["description"] == "overcast"
        assert current["observed_at"] == "2026-10-20T12:00"
        assert current["forecast"][0]["description"] == "slight rain"
        assert current["alerts"] == [] and current["alerts_supported"] is False
        assert "Open-Meteo" in current["attribution"]
        assert result["alerts_supported"] is False

    import asyncio
    asyncio.run(go())


def test_weather_tool_returns_explicit_failure_when_provider_fails():
    http = places_http()
    http.responses[WEATHER] = json_response({"error": "down"}, status=503)
    geocoder = OpenMeteoGeocoder(http, base_url="https://geo.example")
    weather = OpenMeteoWeather(http, base_url="https://weather.example/forecast")
    defs = {d.name: d for d in weather_tool_defs(geocoder, weather)}

    async def go():
        with pytest.raises(ToolError) as exc:
            await defs["get_weather"].handler("Lagos")
        assert exc.value.code == "weather_unavailable" and exc.value.retryable is True

    import asyncio
    asyncio.run(go())


def test_geocoder_not_found_does_not_return_a_placeholder():
    http = FakeHttp({GEO: json_response({"results": []})})
    geocoder = OpenMeteoGeocoder(http, base_url="https://geo.example")

    async def go():
        with pytest.raises(ToolError) as exc:
            await geocoder.search("Nowhere-At-All")
        assert exc.value.code == "location_not_found"

    import asyncio
    asyncio.run(go())


def test_wmo_description_is_stable_and_unknown_is_not_fabricated():
    assert describe_weather(0) == "clear sky"
    assert describe_weather(999) == "unknown weather code"
    assert describe_weather(None) == "unknown"
