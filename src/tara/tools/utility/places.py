"""Location lookup and country facts.

Open-Meteo geocoding is used for cities because it returns a validated IANA zone
and coordinates.  countries.dev is a keyless reference-data service for country
facts.  Neither provider is the user's location service; Tara never claims to know
where the user physically is.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from ...core.errors import ToolError
from ..executor import ToolDef
from .contracts import CountryFacts, CountryProvider, Geocoder, HttpClient, Place, as_dict
from .http import TTLCache, clean_text, parse_json

OPEN_METEO_ATTRIBUTION = "Location data by Open-Meteo.com / GeoNames (CC BY 4.0)"
COUNTRIES_ATTRIBUTION = "Country data by countries.dev"


class OpenMeteoGeocoder:
    def __init__(self, http: HttpClient, *, base_url: str = "https://geocoding-api.open-meteo.com/v1",
                 timeout_s: float = 8.0, cache_ttl_s: float = 86_400.0):
        self.http, self.base_url, self.timeout_s = http, base_url.rstrip("/"), timeout_s
        self.cache_ttl_s = cache_ttl_s
        self.cache = TTLCache(max_entries=256)

    async def search(self, query: str, count: int = 5, country_code: str = "") -> tuple[Place, ...]:
        text = clean_text(query, 120)
        if not text:
            raise ToolError("bad_location", "A place name is required")
        count = max(1, min(int(count), 10))
        key = f"{text.lower()}|{count}|{country_code.upper()}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        params: dict[str, Any] = {"name": text, "count": count, "language": "en", "format": "json"}
        if country_code:
            params["countryCode"] = country_code.upper()
        response = await self.http.get(f"{self.base_url}/search", params=params, timeout_s=self.timeout_s,
                                       max_bytes=200_000)
        if response.status == 400:
            raise ToolError("bad_location", "The location lookup was rejected")
        if response.status != 200:
            raise ToolError("geocoding_unavailable", "The location service is unavailable", retryable=True)
        data = parse_json(response.body)
        raw = data.get("results") if isinstance(data, dict) else None
        places: list[Place] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            try:
                lat, lon = float(item["latitude"]), float(item["longitude"])
                name = clean_text(str(item.get("name", "")), 120)
                if not name or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    continue
                places.append(Place(
                    name=name, latitude=lat, longitude=lon,
                    timezone=clean_text(str(item.get("timezone", "")), 80),
                    country=clean_text(str(item.get("country", "")), 120),
                    country_code=clean_text(str(item.get("country_code", "")), 2).upper(),
                    admin1=clean_text(str(item.get("admin1", "")), 120),
                    population=int(item.get("population") or 0),
                    feature_code=clean_text(str(item.get("feature_code", "")), 20),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        if not places:
            raise ToolError("location_not_found", "No location matched that name", details={"query": text})
        places.sort(key=lambda p: p.population, reverse=True)
        result = tuple(places)
        self.cache.set(key, result, self.cache_ttl_s)
        return result


class CountriesDevProvider:
    def __init__(self, http: HttpClient, *, base_url: str = "https://countries.dev", timeout_s: float = 8.0,
                 cache_ttl_s: float = 604_800.0):
        self.http, self.base_url, self.timeout_s = http, base_url.rstrip("/"), timeout_s
        self.cache_ttl_s = cache_ttl_s
        self.cache = TTLCache(max_entries=256)

    async def lookup(self, name: str) -> CountryFacts:
        text = clean_text(name, 120)
        if not text:
            raise ToolError("bad_country", "A country name or ISO code is required")
        key = text.lower()
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if len(text) in (2, 3) and text.isalpha():
            url = f"{self.base_url}/alpha/{urllib.parse.quote(text.upper())}"
        else:
            url = f"{self.base_url}/name/{urllib.parse.quote(text)}"
        response = await self.http.get(url, timeout_s=self.timeout_s, max_bytes=200_000)
        if response.status == 404:
            raise ToolError("country_not_found", "No country matched that name")
        if response.status != 200:
            raise ToolError("country_service_unavailable", "The country reference service is unavailable",
                            retryable=True)
        data = parse_json(response.body)
        item = data[0] if isinstance(data, list) and data else data
        if not isinstance(item, dict):
            raise ToolError("country_not_found", "No country matched that name")
        capital = item.get("capital", "")
        if isinstance(capital, list):
            capital = ", ".join(clean_text(str(x), 80) for x in capital if x)
        currencies = item.get("currencies") or []
        currency_names = []
        for currency in currencies if isinstance(currencies, list) else []:
            if isinstance(currency, dict):
                code, cname = currency.get("code", ""), currency.get("name", "")
                currency_names.append(f"{code} ({cname})".strip())
        languages = item.get("languages") or []
        language_names = [clean_text(str(x.get("name", "")), 80) for x in languages
                          if isinstance(languages, list) and isinstance(x, dict)]
        facts = CountryFacts(
            name=clean_text(str(item.get("name", text)), 120),
            capital=clean_text(str(capital), 120),
            region=clean_text(str(item.get("region", "")), 80),
            subregion=clean_text(str(item.get("subregion", "")), 80),
            population=int(item.get("population") or 0),
            timezones=tuple(clean_text(str(x), 80) for x in (item.get("timezones") or []) if x),
            alpha2=clean_text(str(item.get("alpha2Code", "")), 2).upper(),
            alpha3=clean_text(str(item.get("alpha3Code", "")), 3).upper(),
            currencies=tuple(currency_names),
            languages=tuple(x for x in language_names if x),
        )
        self.cache.set(key, facts, self.cache_ttl_s)
        return facts


def _best(places: tuple[Place, ...]) -> Place:
    return places[0]


def place_tool_defs(geocoder: Geocoder, country: CountryProvider) -> tuple[ToolDef, ...]:
    async def geocode_place(name: str, count: int = 5) -> dict[str, Any]:
        places = await geocoder.search(name, count)
        best = _best(places)
        return {
            "query": name,
            "best": as_dict(best),
            "results": [as_dict(p) for p in places],
            "ambiguous": len(places) > 1,
            "assumed": len(places) > 1,
            "note": "More than one place matched; state the assumption or ask which one is meant."
                    if len(places) > 1 else "",
            "provider": "open-meteo-geocoding",
            "attribution": OPEN_METEO_ATTRIBUTION,
        }

    async def country_info(country_name: str) -> dict[str, Any]:
        facts = await country.lookup(country_name)
        return {"country": as_dict(facts), "provider": "countries.dev", "attribution": COUNTRIES_ATTRIBUTION}

    async def time_at_place(place: str, at: str = "") -> dict[str, Any]:
        from .time import get_time

        places = await geocoder.search(place, 5)
        best = _best(places)
        if not best.timezone:
            raise ToolError("unknown_timezone", "The location service did not return a time zone")
        return {
            "location": as_dict(best),
            "time": get_time(best.timezone, at),
            "ambiguous": len(places) > 1,
            "assumed": len(places) > 1,
            "provider": "open-meteo-geocoding",
            "attribution": OPEN_METEO_ATTRIBUTION,
        }

    return (
        ToolDef("geocode_place", {"name": str}, geocode_place, optional={"count": int},
                description="Resolve a city or place name to coordinates, country, region, population and IANA time zone. "
                            "If several places match, report the assumption.",
                timeout_s=10.0, permission="network_read", side_effect=False, category="location",
                parameters_schema={"type": "OBJECT", "properties": {
                    "name": {"type": "STRING", "description": "Place name, optionally with a country"},
                    "count": {"type": "INTEGER", "description": "Maximum candidates (1-10)"}},
                    "required": ["name"]}),
        ToolDef("country_info", {"country_name": str}, country_info,
                description="Look up a country's capital, region, time zones, population, currencies and languages.",
                timeout_s=10.0, permission="network_read", side_effect=False, category="location",
                parameters_schema={"type": "OBJECT", "properties": {
                    "country_name": {"type": "STRING", "description": "Country name or ISO code"}},
                    "required": ["country_name"]}),
        ToolDef("get_time_at_place", {"place": str}, time_at_place, optional={"at": str},
                description="Resolve a place and get its exact local time in the place's IANA time zone.",
                timeout_s=12.0, permission="network_read", side_effect=False, category="time",
                parameters_schema={"type": "OBJECT", "properties": {
                    "place": {"type": "STRING", "description": "City or place name"},
                    "at": {"type": "STRING", "description": "Optional ISO-8601 datetime or HH:MM"}},
                    "required": ["place"]}),
    )
