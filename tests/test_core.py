"""Tests for the pure Jeroen.nl logic (no Home Assistant needed).

Fixtures are trimmed real responses (prices, forecast, solar) or follow the format
in the API documentation (gas, rate limit).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "custom_components" / "jeroen_nl" / "core.py"
_SPEC = importlib.util.spec_from_file_location("jeroen_core", _PATH)
core = importlib.util.module_from_spec(_SPEC)
sys.modules["jeroen_core"] = core
_SPEC.loader.exec_module(core)

FIX = Path(__file__).parent / "fixtures"
NL = core.NL_TZ


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def nl(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NL)


# ---------------------------------------------------------------- parsing

def test_parse_prices_uses_utc_and_handles_dst_and_negative():
    points, made = core.parse_prices(load("prices.json"))
    assert made is None
    assert points[0].start == datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
    assert points[0].price == pytest.approx(0.0086)
    # 02:00 appears twice in Dutch time on the DST night; UTC keeps them apart
    assert len({p.start for p in points}) == 4
    assert any(p.price < 0 for p in points)
    assert all(p.local_date == date(2026, 10, 25) for p in points)


def test_parse_forecast_band_and_made():
    points, made = core.parse_prices(load("forecast.json"))
    assert made == nl(2026, 10, 27, 4)
    p = points[0]
    assert p.forecast and p.low == pytest.approx(0.141258) and p.high == pytest.approx(0.218661)
    assert p.low <= p.price <= p.high


def test_parse_solar():
    s = core.parse_solar(load("solar.json"))
    assert s.total_kwp == 7.8
    assert s.updated == datetime(2026, 10, 26, 19, 8, 27, tzinfo=timezone.utc)
    assert s.day(date(2026, 10, 28)).kwh == 14.51
    assert s.day(date(2026, 11, 1)).p10 is None
    assert s.hours[0].start.utcoffset() == timedelta(hours=1)


def test_parse_gas():
    gas = core.parse_gas(load("gas.json"))
    assert gas[date(2026, 10, 27)] == pytest.approx(0.3488)


def test_rate_limit_body_raises():
    with pytest.raises(core.ApiError, match="10 keer"):
        core.parse_prices(load("rate_limited.json"))


# ---------------------------------------------------------------- tariff

def test_tariff_all_in():
    t = core.Tariff(markup=0.02, energy_tax=0.088, vat_pct=21)
    assert t.all_in(0.10) == pytest.approx((0.10 + 0.02 + 0.088) * 1.21, abs=1e-5)
    assert t.all_in(None) is None


# ---------------------------------------------------------------- day statistics

def _points(prices, start=nl(2026, 10, 27, 10)):
    return [core.PricePoint((start + i * core.SLOT).astimezone(timezone.utc), p)
            for i, p in enumerate(prices)]


def test_day_stats_lowest_highest_average():
    stats = core.day_stats(_points([0.20, 0.05, 0.30, 0.05, 0.30]))
    assert stats.lowest.price == 0.05 and stats.lowest.start == _points([0, 0])[1].start  # earliest tie
    assert stats.highest.price == 0.30 and stats.highest.start == _points([0, 0, 0])[2].start
    assert stats.average == pytest.approx(0.18)
    assert core.day_stats([]) is None


def test_current_and_next_point_skip_forecast():
    pts = _points([0.1, 0.2, 0.3])
    fc = core.PricePoint(pts[-1].end, 0.9, 0.8, 1.0, True)
    now = pts[0].start + timedelta(minutes=5)
    assert core.current_point(pts + [fc], now).price == 0.1
    assert core.next_point(pts + [fc], now).price == 0.2
    assert core.next_point(pts + [fc], pts[2].start + timedelta(minutes=1)) is None


# ---------------------------------------------------------------- cache

def test_store_keeps_longest_day_and_combines_with_forecast():
    cache = core.Cache()
    today, _ = core.parse_prices(load("prices.json"))
    cache.store_prices(today)
    cache.store_prices(today[:1])  # a shorter answer does not overwrite
    assert len(cache.prices[date(2026, 10, 25)]) == 4
    forecast, made = core.parse_prices(load("forecast.json"))
    cache.forecast, cache.forecast_made = forecast, made
    combined = cache.combined_prices(nl(2026, 10, 25, 12))
    assert [p.forecast for p in combined] == [False] * 4 + [True] * 4


def test_forecast_quarters_already_final_are_dropped():
    cache = core.Cache()
    forecast, _ = core.parse_prices(load("forecast.json"))
    final = [core.PricePoint(forecast[0].start, 0.2), core.PricePoint(forecast[1].start, 0.2)]
    cache.store_prices(final)
    cache.forecast = forecast
    combined = cache.combined_prices(forecast[0].start)
    assert len(combined) == 4 and not combined[1].forecast and combined[2].forecast


def test_cache_roundtrip():
    cache = core.Cache()
    cache.store_prices(core.parse_prices(load("prices.json"))[0])
    cache.forecast, cache.forecast_made = core.parse_prices(load("forecast.json"))
    cache.solar = core.parse_solar(load("solar.json"))
    cache.gas = core.parse_gas(load("gas.json"))
    cache.count_call(core.KIND_POWER, date(2026, 10, 27))
    again = core.Cache.from_dict(json.loads(json.dumps(cache.to_dict())))
    assert again.prices == cache.prices
    assert again.forecast == cache.forecast and again.forecast_made == cache.forecast_made
    assert again.solar == cache.solar and again.gas == cache.gas
    assert again.calls_today(core.KIND_POWER, date(2026, 10, 27)) == 1


# ---------------------------------------------------------------- schedule

def full_cache(day: date) -> core.Cache:
    """A cache with everything for `day` already fetched."""
    c = core.Cache()
    start = datetime.combine(day, datetime.min.time(), NL)
    c.prices[day] = [core.PricePoint(start.astimezone(timezone.utc), 0.1)]
    c.forecast_made = datetime.combine(day, datetime.min.time(), NL).replace(hour=4)
    c.gas_fetched = start.replace(hour=7)
    c.solar_fetched = start.replace(hour=2)
    return c


def test_empty_cache_at_night_fetches_today_and_solar_only():
    due = core.due_requests(core.Cache(), nl(2026, 10, 27, 2))
    assert due == [core.REQ_TODAY, core.REQ_SOLAR]


def test_nothing_due_when_everything_cached():
    assert core.due_requests(full_cache(date(2026, 10, 27)), nl(2026, 10, 27, 12)) == []


def test_tomorrow_only_after_1315():
    c = full_cache(date(2026, 10, 27))
    assert core.REQ_TOMORROW not in core.due_requests(c, nl(2026, 10, 27, 13, 10))
    assert core.REQ_TOMORROW in core.due_requests(c, nl(2026, 10, 27, 13, 20))


def test_yesterdays_tomorrow_becomes_today_without_a_call():
    c = full_cache(date(2026, 10, 27))
    c.prices[date(2026, 10, 28)] = [core.PricePoint(nl(2026, 10, 28).astimezone(timezone.utc), 0.1)]
    assert core.REQ_TODAY not in core.due_requests(c, nl(2026, 10, 28, 0, 30))


def test_forecast_daily_after_five():
    c = full_cache(date(2026, 10, 27))
    assert core.REQ_FORECAST not in core.due_requests(c, nl(2026, 10, 28, 4, 30))
    assert core.REQ_FORECAST in core.due_requests(c, nl(2026, 10, 28, 5, 30))


def test_retry_spacing_and_daily_limit():
    c = core.Cache()
    now = nl(2026, 10, 27, 14)
    c.last_attempt[core.REQ_TODAY] = now - timedelta(minutes=10)
    assert core.REQ_TODAY not in core.due_requests(c, now)
    c.last_attempt[core.REQ_TODAY] = now - timedelta(minutes=40)
    assert core.REQ_TODAY in core.due_requests(c, now)
    for _ in range(6):
        c.count_call(core.KIND_POWER, date(2026, 10, 27))
    due = core.due_requests(c, now)
    assert not any(core.REQUEST_KIND[r] == core.KIND_POWER for r in due)


def test_solar_refetched_once_per_night():
    c = full_cache(date(2026, 10, 27))
    assert core.REQ_SOLAR not in core.due_requests(c, nl(2026, 10, 28, 1, 0))
    assert core.REQ_SOLAR in core.due_requests(c, nl(2026, 10, 28, 1, 30))
