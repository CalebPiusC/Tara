"""Opt-in real-provider checks. These make network calls but no model calls.

Run:
  TARA_EXTERNAL_TESTS=1 .venv/bin/python -m pytest -m external tests/external -q

Brave or Tavily is exercised only when TARA_SEARCH_API_KEY is present and
TARA_SEARCH_PROVIDER selects it (Brave by default). Nothing is claimed about
live Gemini here; that remains the separate live_api tier.
"""
from __future__ import annotations

import os

import pytest

from tara.orchestration.fetch import SafeFetcher
from tara.orchestration.http import StdlibHttpClient
from tara.orchestration.places import CountriesDevProvider, OpenMeteoGeocoder
from tara.orchestration.search import make_search_provider
from tara.orchestration.weather import OpenMeteoWeather

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(os.getenv("TARA_EXTERNAL_TESTS") != "1", reason="set TARA_EXTERNAL_TESTS=1"),
]


def test_open_meteo_geocoding_weather_and_country_facts():
    import asyncio

    async def go():
        http = StdlibHttpClient()
        places = await OpenMeteoGeocoder(http).search("Lagos", 3)
        assert places and places[0].timezone == "Africa/Lagos"
        assert -90 <= places[0].latitude <= 90 and -180 <= places[0].longitude <= 180
        snapshot = await OpenMeteoWeather(http).current(places[0], 1)
        assert snapshot.timezone == "Africa/Lagos"
        assert snapshot.observed_at
        assert len(snapshot.forecast) == 1
        assert snapshot.attribution.startswith("Weather data by Open-Meteo")
        facts = await CountriesDevProvider(http).lookup("Nigeria")
        assert facts.name == "Nigeria" and facts.capital == "Abuja"

    asyncio.run(go())


def test_public_url_fetch_is_bounded_and_readable():
    import asyncio

    async def go():
        page = await SafeFetcher().fetch("https://example.com/", 1000)
        assert page["title"] and page["text"] and page["content_type"] == "text/html"

    asyncio.run(go())


@pytest.mark.skipif(not os.getenv("TARA_SEARCH_API_KEY"), reason="TARA_SEARCH_API_KEY not set")
def test_configured_search_provider_returns_attributable_results():
    import asyncio

    provider_name = os.getenv("TARA_SEARCH_PROVIDER", "brave").strip().lower()

    async def go():
        provider = make_search_provider(provider_name, StdlibHttpClient(), os.environ["TARA_SEARCH_API_KEY"])
        result = await provider.search("Python programming language official site", 3)
        assert result.provider == provider_name
        assert all(hit.url.startswith(("http://", "https://")) for hit in result.hits)
        assert all(hit.title for hit in result.hits)

    asyncio.run(go())
