"""Pure logic for the Jeroen.nl integration: parsing, tariffs and the fetch schedule.

No Home Assistant or aiohttp imports, so everything here can be unit tested.
The API allows 10 calls per day per data type for all keys of an account together,
so the schedule fetches as little as possible and everything is cached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

NL_TZ = ZoneInfo("Europe/Amsterdam")
SLOT = timedelta(minutes=15)

# data types as the API counts them
KIND_POWER = "power"   # stroomprijzen: today, tomorrow and the price forecast
KIND_GAS = "gas"
KIND_SOLAR = "solar"   # opwekvoorspelling


class ApiError(Exception):
    """General API error."""


class AuthError(ApiError):
    """Key unknown or revoked (HTTP 403)."""


class RateLimitError(ApiError):
    """Daily limit reached (HTTP 429)."""


# ---------------------------------------------------------------- parsing

def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def _utc(raw: str) -> datetime:
    return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict) and "error" in data:
        raise ApiError(str(data.get("message") or data["error"]))
    raise ApiError(f"Unexpected response: {str(data)[:200]}")


@dataclass(frozen=True)
class PricePoint:
    """One quarter hour. Prices in EUR/kWh, market price excluding taxes."""

    start: datetime  # UTC
    price: float
    low: float | None = None    # forecast only: about 8 in 10 quarters fall in low..high
    high: float | None = None
    forecast: bool = False

    @property
    def end(self) -> datetime:
        return self.start + SLOT

    @property
    def local_date(self) -> date:
        return self.start.astimezone(NL_TZ).date()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"start": self.start.isoformat(), "price": self.price}
        if self.forecast:
            out.update(low=self.low, high=self.high, forecast=True)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PricePoint:
        return cls(datetime.fromisoformat(d["start"]), d["price"], d.get("low"),
                   d.get("high"), bool(d.get("forecast")))


def parse_prices(data: Any) -> tuple[list[PricePoint], datetime | None]:
    """Parse period=vandaag/morgen/voorspelling. Returns the points and, for a
    forecast, when it was made (Dutch time)."""
    points: list[PricePoint] = []
    made: datetime | None = None
    for row in _rows(data):
        price = _num(row.get("prijs_excl_belastingen"))
        if price is None or not row.get("datum_utc"):
            continue
        forecast = "band_laag" in row
        points.append(PricePoint(_utc(row["datum_utc"]), price, _num(row.get("band_laag")),
                                 _num(row.get("band_hoog")), forecast))
        if forecast and row.get("voorspeld_op"):
            made = datetime.strptime(row["voorspeld_op"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NL_TZ)
    points.sort(key=lambda p: p.start)
    return points, made


def parse_gas(data: Any) -> dict[date, float]:
    """Parse the gas history: price per day (EUR/m3, excluding taxes)."""
    out: dict[date, float] = {}
    for row in _rows(data):
        price = _num(row.get("prijs_excl_belastingen"))
        if price is not None and row.get("datum"):
            out[date.fromisoformat(str(row["datum"])[:10])] = price
    return out


@dataclass(frozen=True)
class SolarDay:
    day: date
    kwh: float
    p10: float | None
    p90: float | None


@dataclass(frozen=True)
class SolarHour:
    start: datetime  # aware, Dutch time with offset
    kwh: float


@dataclass(frozen=True)
class SolarForecast:
    updated: datetime | None
    total_kwp: float | None
    days: tuple[SolarDay, ...]
    hours: tuple[SolarHour, ...]

    def day(self, d: date) -> SolarDay | None:
        return next((x for x in self.days if x.day == d), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forecast_updated_utc": self.updated.isoformat() if self.updated else None,
            "total_kwp": self.total_kwp,
            "days": [{"date": d.day.isoformat(), "kwh": d.kwh, "kwh_p10": d.p10, "kwh_p90": d.p90}
                     for d in self.days],
            "hours": [{"start": h.start.isoformat(), "kwh": h.kwh} for h in self.hours],
        }


def parse_solar(data: Any) -> SolarForecast:
    if not isinstance(data, dict) or "days" not in data:
        if isinstance(data, dict) and "error" in data:
            raise ApiError(str(data.get("message") or data["error"]))
        raise ApiError(f"Unexpected solar response: {str(data)[:200]}")
    updated = data.get("forecast_updated_utc")
    return SolarForecast(
        updated=datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None,
        total_kwp=_num(data.get("total_kwp")),
        days=tuple(SolarDay(date.fromisoformat(d["date"]), _num(d.get("kwh")) or 0.0,
                            _num(d.get("kwh_p10")), _num(d.get("kwh_p90")))
                   for d in data.get("days", [])),
        hours=tuple(SolarHour(datetime.fromisoformat(h["start"]), _num(h.get("kwh")) or 0.0)
                    for h in data.get("hours", [])),
    )


# ---------------------------------------------------------------- tariffs

@dataclass(frozen=True)
class Tariff:
    """All-in price = (market + markup + energy tax) * (1 + VAT)."""

    markup: float
    energy_tax: float
    vat_pct: float

    def all_in(self, market: float | None) -> float | None:
        if market is None:
            return None
        return round((market + self.markup + self.energy_tax) * (1 + self.vat_pct / 100), 5)


# ---------------------------------------------------------------- cache and schedule

@dataclass
class Cache:
    """Everything fetched so far; survives restarts via HA storage."""

    prices: dict[date, list[PricePoint]] = field(default_factory=dict)  # final prices per local date
    forecast: list[PricePoint] = field(default_factory=list)
    forecast_made: datetime | None = None
    gas: dict[date, float] = field(default_factory=dict)
    solar: SolarForecast | None = None
    solar_fetched: datetime | None = None
    gas_fetched: datetime | None = None
    calls: dict[str, int] = field(default_factory=dict)       # per kind, for calls_day
    calls_day: date | None = None
    last_attempt: dict[str, datetime] = field(default_factory=dict)  # per request name

    def store_prices(self, points: list[PricePoint]) -> None:
        for day in {p.local_date for p in points}:
            day_points = [p for p in points if p.local_date == day]
            if len(day_points) >= len(self.prices.get(day, [])):
                self.prices[day] = day_points

    def prune(self, today: date) -> None:
        self.prices = {d: p for d, p in self.prices.items() if d >= today - timedelta(days=1)}
        self.gas = {d: p for d, p in self.gas.items() if d >= today - timedelta(days=7)}

    def count_call(self, kind: str, today: date) -> None:
        if self.calls_day != today:
            self.calls_day, self.calls = today, {}
        self.calls[kind] = self.calls.get(kind, 0) + 1

    def calls_today(self, kind: str, today: date) -> int:
        return self.calls.get(kind, 0) if self.calls_day == today else 0

    def combined_prices(self, now: datetime) -> list[PricePoint]:
        """Final prices from yesterday on, followed by forecast quarters after the last
        final quarter. This is the list Home EMS plans with."""
        final = sorted((p for pts in self.prices.values() for p in pts), key=lambda p: p.start)
        last = final[-1].start if final else now - timedelta(days=1)
        return final + [p for p in self.forecast if p.start > last]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prices": {d.isoformat(): [p.to_dict() for p in pts] for d, pts in self.prices.items()},
            "forecast": [p.to_dict() for p in self.forecast],
            "forecast_made": self.forecast_made.isoformat() if self.forecast_made else None,
            "gas": {d.isoformat(): v for d, v in self.gas.items()},
            "solar": self.solar.to_dict() if self.solar else None,
            "solar_fetched": self.solar_fetched.isoformat() if self.solar_fetched else None,
            "gas_fetched": self.gas_fetched.isoformat() if self.gas_fetched else None,
            "calls": self.calls,
            "calls_day": self.calls_day.isoformat() if self.calls_day else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Cache:
        def dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            prices={date.fromisoformat(k): [PricePoint.from_dict(p) for p in v]
                    for k, v in (d.get("prices") or {}).items()},
            forecast=[PricePoint.from_dict(p) for p in d.get("forecast") or []],
            forecast_made=dt(d.get("forecast_made")),
            gas={date.fromisoformat(k): v for k, v in (d.get("gas") or {}).items()},
            solar=parse_solar(d["solar"]) if d.get("solar") else None,
            solar_fetched=dt(d.get("solar_fetched")),
            gas_fetched=dt(d.get("gas_fetched")),
            calls=dict(d.get("calls") or {}),
            calls_day=date.fromisoformat(d["calls_day"]) if d.get("calls_day") else None,
        )


# request names and the data type they count against
REQ_TODAY = "today"
REQ_TOMORROW = "tomorrow"
REQ_FORECAST = "forecast"
REQ_GAS = "gas"
REQ_SOLAR = "solar"
REQUEST_KIND = {REQ_TODAY: KIND_POWER, REQ_TOMORROW: KIND_POWER, REQ_FORECAST: KIND_POWER,
                REQ_GAS: KIND_GAS, REQ_SOLAR: KIND_SOLAR}

TOMORROW_FROM = time(13, 15)
FORECAST_FROM = time(5, 0)     # a new forecast every morning (made ~04:00)
SOLAR_FROM = time(1, 15)       # new solar forecast ready for everyone around 01:00
GAS_FROM = time(6, 0)
RETRY = timedelta(minutes=30)


@dataclass(frozen=True)
class Limits:
    """Our own daily cap per data type. The API allows 10 per type for all keys of
    the account together, so leave room for other users of the same account."""

    power: int = 6
    gas: int = 2
    solar: int = 3

    def for_kind(self, kind: str) -> int:
        return {KIND_POWER: self.power, KIND_GAS: self.gas, KIND_SOLAR: self.solar}[kind]


def due_requests(cache: Cache, now: datetime, limits: Limits | None = None,
                 want_gas: bool = True, want_solar: bool = True) -> list[str]:
    """Which requests should be made now. `now` is aware; decisions use Dutch time."""
    limits = limits or Limits()
    local = now.astimezone(NL_TZ)
    today = local.date()
    tomorrow = today + timedelta(days=1)
    wanted: list[str] = []

    if not cache.prices.get(today):
        wanted.append(REQ_TODAY)
    if local.time() >= TOMORROW_FROM and not cache.prices.get(tomorrow):
        wanted.append(REQ_TOMORROW)
    forecast_today = cache.forecast_made is not None and cache.forecast_made.astimezone(NL_TZ).date() == today
    if local.time() >= FORECAST_FROM and not forecast_today:
        wanted.append(REQ_FORECAST)
    if want_gas and local.time() >= GAS_FROM and (
            cache.gas_fetched is None or cache.gas_fetched.astimezone(NL_TZ).date() < today):
        wanted.append(REQ_GAS)
    if want_solar:
        solar_cutoff = datetime.combine(today, SOLAR_FROM, NL_TZ)
        if local >= solar_cutoff and (cache.solar_fetched is None or cache.solar_fetched < solar_cutoff):
            wanted.append(REQ_SOLAR)

    due: list[str] = []
    for req in wanted:
        kind = REQUEST_KIND[req]
        last = cache.last_attempt.get(req)
        if last is not None and now - last < RETRY:
            continue
        if cache.calls_today(kind, today) >= limits.for_kind(kind):
            continue
        due.append(req)
    return due
