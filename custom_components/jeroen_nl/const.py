"""Constants for the Jeroen.nl integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "jeroen_nl"
NAME = "Jeroen.nl"

URL_POWER = "https://jeroen.nl/api/dynamische-energieprijzen/v2/"
URL_GAS = "https://jeroen.nl/api/dynamische-gasprijzen/"
URL_SOLAR = "https://jeroen.nl/account/data/opwekvoorspelling/feed"

# The coordinator only calls the API when core.due_requests says so; this is how
# often it checks. Sensors also refresh on every quarter hour.
UPDATE_INTERVAL = timedelta(minutes=5)

CONF_API_KEY = "api_key"
CONF_MARKUP = "markup"
CONF_ENERGY_TAX = "energy_tax"
CONF_VAT = "vat"
CONF_GAS = "gas"
CONF_GAS_MARKUP = "gas_markup"
CONF_GAS_TAX = "gas_energy_tax"
CONF_SOLAR = "solar"
CONF_LIMIT_POWER = "limit_power"

# Defaults excluding VAT. Energy tax 2027 (EUR 0.1065/kWh and 0.7457/m3 incl. VAT,
# per the Energie notes) converted to excl. VAT; markup is a typical supplier fee.
DEFAULTS = {
    CONF_MARKUP: 0.02,
    CONF_ENERGY_TAX: 0.08802,
    CONF_VAT: 21.0,
    CONF_GAS: True,
    CONF_GAS_MARKUP: 0.05,
    CONF_GAS_TAX: 0.61628,
    CONF_SOLAR: True,
    CONF_LIMIT_POWER: 6,
}

UNIT_KWH_PRICE = "EUR/kWh"
UNIT_M3_PRICE = "EUR/m³"
