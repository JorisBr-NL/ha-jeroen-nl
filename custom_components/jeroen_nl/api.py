"""HTTP client for the Jeroen.nl API."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from .const import URL_GAS, URL_POWER, URL_SOLAR
from .core import (
    REQ_FORECAST,
    REQ_GAS,
    REQ_SOLAR,
    REQ_TODAY,
    REQ_TOMORROW,
    ApiError,
    AuthError,
    RateLimitError,
)

REQUESTS: dict[str, tuple[str, dict[str, str]]] = {
    REQ_TODAY: (URL_POWER, {"period": "vandaag"}),
    REQ_TOMORROW: (URL_POWER, {"period": "morgen"}),
    REQ_FORECAST: (URL_POWER, {"period": "voorspelling"}),
    REQ_GAS: (URL_GAS, {"period": "alltime"}),
    REQ_SOLAR: (URL_SOLAR, {}),
}


class JeroenApi:
    """Fetches one request and returns the decoded JSON (parsing lives in core)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key

    async def fetch(self, request: str) -> Any:
        url, params = REQUESTS[request]
        params = {**params, "type": "json", "key": self._key}
        try:
            async with asyncio.timeout(30):
                resp = await self._session.get(url, params=params)
                text = (await resp.text()).strip()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ApiError(f"Connection failed: {err}") from err

        if resp.status == 403:
            raise AuthError("API key unknown or revoked")
        if resp.status == 429:
            raise RateLimitError(text[:200] or "Daily limit reached")
        if resp.status != 200:
            raise ApiError(f"HTTP {resp.status}")
        if not text:
            return []  # e.g. tomorrow before ~13:15
        try:
            return json.loads(text)
        except ValueError as err:
            raise ApiError(f"No valid JSON: {text[:200]}") from err
