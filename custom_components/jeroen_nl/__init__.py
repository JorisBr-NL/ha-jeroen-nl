"""Jeroen.nl: Dutch day-ahead prices, price forecast, gas price and solar forecast.

Built as the data source for Home EMS; also usable on its own.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .api import JeroenApi
from .const import CONF_API_KEY
from .coordinator import JeroenConfigEntry, JeroenCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: JeroenConfigEntry) -> bool:
    api = JeroenApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = JeroenCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _quarter(_now: datetime) -> None:
        coordinator.async_update_listeners()

    # the current price changes every quarter hour: refresh the sensors on the quarter
    entry.async_on_unload(async_track_time_change(hass, _quarter, minute=[0, 15, 30, 45], second=1))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: JeroenConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JeroenConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
