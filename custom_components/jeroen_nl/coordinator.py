"""Coordinator: decides which API calls are due, caches everything in HA storage."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import JeroenApi
from .const import CONF_GAS, CONF_LIMIT_POWER, CONF_SOLAR, DEFAULTS, DOMAIN, UPDATE_INTERVAL
from .core import (
    NL_TZ,
    REQ_FORECAST,
    REQ_GAS,
    REQ_SOLAR,
    REQUEST_KIND,
    ApiError,
    AuthError,
    Cache,
    Limits,
    RateLimitError,
    due_requests,
    parse_gas,
    parse_prices,
    parse_solar,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1

type JeroenConfigEntry = ConfigEntry[JeroenCoordinator]


class JeroenCoordinator(DataUpdateCoordinator[Cache]):
    config_entry: JeroenConfigEntry

    def __init__(self, hass: HomeAssistant, entry: JeroenConfigEntry, api: JeroenApi) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=UPDATE_INTERVAL)
        self.api = api
        self.cache = Cache()
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    def option(self, key: str) -> Any:
        return self.config_entry.options.get(key, DEFAULTS[key])

    async def _async_setup(self) -> None:
        stored = await self._store.async_load()
        if stored:
            try:
                self.cache = Cache.from_dict(stored)
            except (KeyError, ValueError, TypeError) as err:
                _LOGGER.warning("Ignoring unreadable cache: %s", err)

    async def _async_update_data(self) -> Cache:
        now = dt_util.utcnow()
        today = now.astimezone(NL_TZ).date()
        limits = Limits(power=int(self.option(CONF_LIMIT_POWER)))
        changed = False
        errors: list[str] = []

        for request in due_requests(self.cache, now, limits, want_gas=self.option(CONF_GAS),
                                    want_solar=self.option(CONF_SOLAR)):
            kind = REQUEST_KIND[request]
            if self.cache.calls_today(kind, today) >= limits.for_kind(kind):
                continue  # an earlier request in this round used the last call
            self.cache.last_attempt[request] = now
            self.cache.count_call(kind, today)
            changed = True
            try:
                self._apply(request, await self.api.fetch(request), now)
            except AuthError as err:
                await self._save()
                raise ConfigEntryAuthFailed(str(err)) from err
            except RateLimitError as err:
                # the account limit is shared with other keys: stop for today
                self.cache.calls[kind] = limits.for_kind(kind)
                errors.append(f"{request}: {err}")
            except ApiError as err:
                errors.append(f"{request}: {err}")

        self.cache.prune(today)
        if changed:
            await self._save()
        if errors:
            if not self.cache.prices.get(today):
                raise UpdateFailed("; ".join(errors))
            _LOGGER.warning("Jeroen.nl: %s (using cached data)", "; ".join(errors))
        return self.cache

    def _apply(self, request: str, data: Any, now) -> None:
        if request == REQ_SOLAR:
            self.cache.solar = parse_solar(data)
            self.cache.solar_fetched = now
        elif request == REQ_GAS:
            self.cache.gas.update(parse_gas(data))
            self.cache.gas_fetched = now
        else:
            points, made = parse_prices(data)
            if request == REQ_FORECAST:
                if points:
                    self.cache.forecast, self.cache.forecast_made = points, made or now
            else:
                self.cache.store_prices(points)

    async def _save(self) -> None:
        await self._store.async_save(self.cache.to_dict())
