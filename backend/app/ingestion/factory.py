"""Select real data providers or explicit demo fixtures from Settings.

Service and API read paths also apply the selected data mode so a live
deployment cannot surface old demo publications or auto-generate fallback data.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.domain.providers import PollutionDataProvider, WeatherProvider
from app.ingestion.demo import DemoPollutionDataProvider, DemoWeatherProvider
from app.ingestion.open_meteo import OpenMeteoProvider
from app.ingestion.openaq import OpenAQProvider


def build_pollution_provider(
    settings: Settings, client: httpx.AsyncClient
) -> PollutionDataProvider | None:
    """None means "skip ingestion for this run" — only possible in live
    mode without an OPENAQ_API_KEY configured; Demo Mode never needs one.
    """
    if settings.demo_mode:
        return DemoPollutionDataProvider()

    api_key = settings.openaq_api_key.get_secret_value() if settings.openaq_api_key else ""
    if not api_key:
        return None
    return OpenAQProvider(
        api_key=api_key,
        client=client,
        base_url=settings.openaq_base_url,
        timeout_seconds=settings.openaq_timeout_seconds,
        max_retries=settings.openaq_max_retries,
        locations_limit=settings.openaq_locations_limit,
    )


def build_weather_provider(settings: Settings, client: httpx.AsyncClient) -> WeatherProvider:
    if settings.demo_mode:
        return DemoWeatherProvider()

    return OpenMeteoProvider(
        client=client,
        base_url=settings.open_meteo_base_url,
        timeout_seconds=settings.open_meteo_timeout_seconds,
        max_retries=settings.open_meteo_max_retries,
        max_locations_per_request=settings.open_meteo_max_locations_per_request,
    )
