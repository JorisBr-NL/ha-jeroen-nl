"""Sensors for Jeroen.nl.

The price sensor carries the full price list (final prices plus forecast) as an
attribute for Home EMS. Those big lists are excluded from the recorder.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENERGY_TAX,
    CONF_GAS,
    CONF_GAS_MARKUP,
    CONF_GAS_TAX,
    CONF_MARKUP,
    CONF_SOLAR,
    CONF_VAT,
    DOMAIN,
    NAME,
    UNIT_KWH_PRICE,
    UNIT_M3_PRICE,
)
from .coordinator import JeroenConfigEntry, JeroenCoordinator
from .core import (
    KIND_GAS,
    KIND_POWER,
    KIND_SOLAR,
    NL_TZ,
    DayStats,
    PricePoint,
    Tariff,
    current_point,
    day_stats,
    next_point,
)

BIG_LISTS = frozenset({"prices", "forecast", "today", "tomorrow", "hours", "days"})


def _tariff(c: JeroenCoordinator) -> Tariff:
    return Tariff(c.option(CONF_MARKUP), c.option(CONF_ENERGY_TAX), c.option(CONF_VAT))


def _gas_tariff(c: JeroenCoordinator) -> Tariff:
    return Tariff(c.option(CONF_GAS_MARKUP), c.option(CONF_GAS_TAX), c.option(CONF_VAT))


def _current(c: JeroenCoordinator) -> PricePoint | None:
    now = dt_util.utcnow()
    return current_point(c.cache.combined_prices(now), now)


def _next(c: JeroenCoordinator) -> PricePoint | None:
    now = dt_util.utcnow()
    return next_point(c.cache.combined_prices(now), now)


def _day_points(c: JeroenCoordinator, offset: int) -> list[PricePoint]:
    day = dt_util.now().astimezone(NL_TZ).date() + timedelta(days=offset)
    return c.cache.prices.get(day, [])


def _stats(c: JeroenCoordinator, offset: int) -> DayStats | None:
    return day_stats(_day_points(c, offset))


def _all_in(c: JeroenCoordinator, p: PricePoint | None) -> float | None:
    return _tariff(c).all_in(p.price) if p else None


def _span(c: JeroenCoordinator, p: PricePoint | None) -> dict[str, Any]:
    if p is None:
        return {}
    return {"start": p.start.isoformat(), "end": p.end.isoformat(), "price": _all_in(c, p),
            "market": p.price}


def _row(p: PricePoint, t: Tariff) -> dict[str, Any]:
    row: dict[str, Any] = {"start": p.start.isoformat(), "end": p.end.isoformat(),
                           "price": t.all_in(p.price), "market": p.price}
    if p.forecast:
        row.update(low=t.all_in(p.low), high=t.all_in(p.high), forecast=True)
    return row


def _day_rows(c: JeroenCoordinator, offset: int) -> list[dict[str, Any]]:
    t = _tariff(c)
    return [_row(p, t) for p in _day_points(c, offset)]


def _price_attrs(c: JeroenCoordinator) -> dict[str, Any]:
    t = _tariff(c)
    now = dt_util.utcnow()
    today = _day_rows(c, 0)
    prices = [x["price"] for x in today]
    current = _current(c)
    return {
        "market_price": current.price if current else None,
        "min_today": min(prices) if prices else None,
        "max_today": max(prices) if prices else None,
        "avg_today": round(sum(prices) / len(prices), 5) if prices else None,
        "tomorrow_available": bool(_day_rows(c, 1)),
        "forecast_made": c.cache.forecast_made.isoformat() if c.cache.forecast_made else None,
        "today": today,
        "tomorrow": _day_rows(c, 1),
        # final prices from now on, then the forecast (with low/high band): for planning
        "prices": [_row(p, t) for p in c.cache.combined_prices(now) if p.end > now],
    }


def _solar_day(c: JeroenCoordinator, offset: int):
    if c.cache.solar is None:
        return None
    return c.cache.solar.day(dt_util.now().astimezone(NL_TZ).date() + timedelta(days=offset))


def _solar_attrs(c: JeroenCoordinator, offset: int) -> dict[str, Any]:
    d = _solar_day(c, offset)
    attrs: dict[str, Any] = {"p10": d.p10 if d else None, "p90": d.p90 if d else None}
    s = c.cache.solar
    if offset == 0 and s is not None:
        attrs.update(total_kwp=s.total_kwp,
                     forecast_updated=s.updated.isoformat() if s.updated else None,
                     days=s.to_dict()["days"], hours=s.to_dict()["hours"])
    return attrs


def _gas_price(c: JeroenCoordinator) -> float | None:
    today = dt_util.now().astimezone(NL_TZ).date()
    return _gas_tariff(c).all_in(c.cache.gas.get(today))


@dataclass(frozen=True, kw_only=True)
class JeroenSensorDescription(SensorEntityDescription):
    value_fn: Callable[[JeroenCoordinator], Any]
    attrs_fn: Callable[[JeroenCoordinator], dict[str, Any]] | None = None
    enabled_fn: Callable[[JeroenCoordinator], bool] = lambda c: True


def _calls(kind: str) -> Callable[[JeroenCoordinator], int]:
    return lambda c: c.cache.calls_today(kind, dt_util.now().astimezone(NL_TZ).date())


def _day_sensors(offset: int, suffix: str) -> tuple[JeroenSensorDescription, ...]:
    """Lowest, highest, average and cheapest moment for today (0) or tomorrow (1)."""
    price = {"native_unit_of_measurement": UNIT_KWH_PRICE, "suggested_display_precision": 4}
    return (
        JeroenSensorDescription(
            key=f"lowest_price_{suffix}", **price,
            value_fn=lambda c: _all_in(c, s.lowest) if (s := _stats(c, offset)) else None,
            attrs_fn=lambda c: _span(c, s.lowest) if (s := _stats(c, offset)) else {}),
        JeroenSensorDescription(
            key=f"highest_price_{suffix}", **price,
            value_fn=lambda c: _all_in(c, s.highest) if (s := _stats(c, offset)) else None,
            attrs_fn=lambda c: _span(c, s.highest) if (s := _stats(c, offset)) else {}),
        JeroenSensorDescription(
            key=f"average_price_{suffix}", **price,
            value_fn=lambda c: _tariff(c).all_in(s.average) if (s := _stats(c, offset)) else None),
        # a timestamp sensor, usable directly as a time trigger in automations
        JeroenSensorDescription(
            key=f"cheapest_moment_{suffix}", device_class=SensorDeviceClass.TIMESTAMP,
            value_fn=lambda c: s.lowest.start if (s := _stats(c, offset)) else None,
            attrs_fn=lambda c: _span(c, s.lowest) if (s := _stats(c, offset)) else {}),
    )


SENSORS: tuple[JeroenSensorDescription, ...] = (
    JeroenSensorDescription(
        key="electricity_price", native_unit_of_measurement=UNIT_KWH_PRICE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4,
        value_fn=lambda c: _tariff(c).all_in(p.price) if (p := _current(c)) else None,
        attrs_fn=_price_attrs),
    JeroenSensorDescription(
        key="electricity_market_price", native_unit_of_measurement=UNIT_KWH_PRICE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4,
        value_fn=lambda c: p.price if (p := _current(c)) else None),
    JeroenSensorDescription(
        key="next_price", native_unit_of_measurement=UNIT_KWH_PRICE, suggested_display_precision=4,
        value_fn=lambda c: _all_in(c, _next(c)), attrs_fn=lambda c: _span(c, _next(c))),
    *_day_sensors(0, "today"),
    *_day_sensors(1, "tomorrow"),
    JeroenSensorDescription(
        key="gas_price", native_unit_of_measurement=UNIT_M3_PRICE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4,
        value_fn=_gas_price, enabled_fn=lambda c: bool(c.option(CONF_GAS))),
    JeroenSensorDescription(
        key="solar_forecast_today", device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=1,
        value_fn=lambda c: d.kwh if (d := _solar_day(c, 0)) else None,
        attrs_fn=lambda c: _solar_attrs(c, 0), enabled_fn=lambda c: bool(c.option(CONF_SOLAR))),
    JeroenSensorDescription(
        key="solar_forecast_tomorrow", device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=1,
        value_fn=lambda c: d.kwh if (d := _solar_day(c, 1)) else None,
        attrs_fn=lambda c: _solar_attrs(c, 1), enabled_fn=lambda c: bool(c.option(CONF_SOLAR))),
    *(JeroenSensorDescription(
        key=f"api_calls_{kind}", entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT, value_fn=_calls(kind))
      for kind in (KIND_POWER, KIND_GAS, KIND_SOLAR)),
)


async def async_setup_entry(hass: HomeAssistant, entry: JeroenConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(JeroenSensor(coordinator, d) for d in SENSORS if d.enabled_fn(coordinator))


class JeroenSensor(CoordinatorEntity[JeroenCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = BIG_LISTS
    entity_description: JeroenSensorDescription

    def __init__(self, coordinator: JeroenCoordinator, description: JeroenSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_translation_key = description.key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name=NAME,
                                            manufacturer="Jeroen.nl", entry_type=DeviceEntryType.SERVICE)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn else None
