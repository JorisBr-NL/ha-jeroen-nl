"""Config and options flow for Jeroen.nl."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JeroenApi
from .const import (
    CONF_API_KEY,
    CONF_ENERGY_TAX,
    CONF_GAS,
    CONF_GAS_MARKUP,
    CONF_GAS_TAX,
    CONF_LIMIT_POWER,
    CONF_MARKUP,
    CONF_SOLAR,
    CONF_VAT,
    DEFAULTS,
    DOMAIN,
    NAME,
)
from .core import REQ_SOLAR, ApiError, AuthError, RateLimitError

KEY_SCHEMA = vol.Schema({
    vol.Required(CONF_API_KEY): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
})


def _price(step: float = 0.00001) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=0, max=2, step=step, mode=selector.NumberSelectorMode.BOX))


async def _validate(hass, key: str) -> str | None:
    """Check the key with one call to the solar feed (also counts against the
    daily limit, so only once). A missing solar setup still proves the key."""
    try:
        await JeroenApi(async_get_clientsession(hass), key).fetch(REQ_SOLAR)
    except AuthError:
        return "invalid_auth"
    except RateLimitError:
        return None  # key is known; the limit resets at midnight
    except ApiError:
        return "cannot_connect"
    return None


class JeroenConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await _validate(self.hass, user_input[CONF_API_KEY])) is None:
                return self.async_create_entry(title=NAME, data=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="user", data_schema=KEY_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await _validate(self.hass, user_input[CONF_API_KEY])) is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", data_schema=KEY_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return JeroenOptionsFlow()


class JeroenOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**DEFAULTS, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_MARKUP, default=current[CONF_MARKUP]): _price(),
            vol.Required(CONF_ENERGY_TAX, default=current[CONF_ENERGY_TAX]): _price(),
            vol.Required(CONF_VAT, default=current[CONF_VAT]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=30, step=0.1, mode=selector.NumberSelectorMode.BOX)),
            vol.Required(CONF_GAS, default=current[CONF_GAS]): selector.BooleanSelector(),
            vol.Required(CONF_GAS_MARKUP, default=current[CONF_GAS_MARKUP]): _price(),
            vol.Required(CONF_GAS_TAX, default=current[CONF_GAS_TAX]): _price(),
            vol.Required(CONF_SOLAR, default=current[CONF_SOLAR]): selector.BooleanSelector(),
            vol.Required(CONF_LIMIT_POWER, default=current[CONF_LIMIT_POWER]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=10, step=1, mode=selector.NumberSelectorMode.BOX)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
