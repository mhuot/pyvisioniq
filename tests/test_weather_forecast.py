"""Tests for the Open-Meteo forecast used to cost a future trip."""

from datetime import date, datetime, timedelta

import pytest

from src.utils.weather import WeatherService

DAILY = {
    "daily": {
        "time": ["2026-09-01", "2026-09-02", "2026-09-03"],
        "temperature_2m_max": [31.8, 28.0, 24.1],
        "temperature_2m_min": [18.7, 16.0, 12.3],
        "precipitation_probability_max": [29, 5, 60],
    }
}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        """No-op: these fixtures are always successful responses."""

    def json(self):
        return self._payload


@pytest.fixture(name="service")
def _service(tmp_path):
    return WeatherService(cache_dir=str(tmp_path))


def test_forecast_is_parsed_into_daily_means(service, monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _Response(DAILY))
    days = service.get_forecast(44.9, -93.1, days=3)

    assert [d["date"] for d in days] == DAILY["daily"]["time"]
    # The mean is what a day's driving happens at, more nearly than an extreme.
    # (31.8 + 18.7) / 2 = 25.25, rounded half-to-even at one decimal.
    assert days[0]["temp_mean_c"] == pytest.approx(25.2, abs=0.01)
    assert days[2]["precipitation_probability"] == 60


def test_forecast_is_cached_rather_than_refetched(service, monkeypatch):
    calls = []

    def counting_get(*args, **kwargs):
        calls.append(1)
        return _Response(DAILY)

    monkeypatch.setattr("requests.get", counting_get)
    service.get_forecast(44.9, -93.1, days=3)
    service.get_forecast(44.9, -93.1, days=3)
    assert len(calls) == 1


def test_forecast_for_selects_the_departure_day(service, monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _Response(DAILY))
    day = service.forecast_for(44.9, -93.1, datetime(2026, 9, 2, 7, 0))
    assert day["date"] == "2026-09-02"
    assert service.forecast_for(44.9, -93.1, date(2030, 1, 1)) is None


def test_a_short_series_does_not_raise(service, monkeypatch):
    """Open-Meteo occasionally returns fewer values than dates."""
    ragged = {"daily": {"time": ["2026-09-01", "2026-09-02"], "temperature_2m_max": [31.8]}}
    monkeypatch.setattr("requests.get", lambda *a, **k: _Response(ragged))
    days = service.get_forecast(44.9, -93.1, days=2)
    assert days[1]["temp_max_c"] is None
    assert days[1]["temp_mean_c"] is None


def test_missing_coordinates_return_none(service):
    assert service.get_forecast(None, None) is None
