"""Tests for WeatherService."""

from unittest.mock import patch, MagicMock

import pytest

from src.utils.weather import WeatherService


class TestWeatherDescription:
    def test_clear_sky(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service._get_weather_description(0) == "Clear sky"

    def test_thunderstorm(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service._get_weather_description(95) == "Thunderstorm"

    def test_unknown_code(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service._get_weather_description(999) == "Weather code 999"

    def test_none_code(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service._get_weather_description(None) == "Unknown"


class TestTemperatureConversion:
    def test_freezing_point(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service.get_temperature_in_celsius(32) == 0.0

    def test_boiling_point(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service.get_temperature_in_celsius(212) == 100.0

    def test_body_temp(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service.get_temperature_in_celsius(98.6) == pytest.approx(37.0, abs=0.1)

    def test_below_zero(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service.get_temperature_in_celsius(0) == pytest.approx(-17.8, abs=0.1)


class TestGetCurrentWeather:
    def test_missing_coordinates_returns_none(self, tmp_path):
        service = WeatherService(cache_dir=tmp_path)
        assert service.get_current_weather(None, None) is None
        assert service.get_current_weather(44.0, None) is None
        assert service.get_current_weather(None, -93.0) is None
    @patch("src.utils.weather.requests.get")
    def test_successful_fetch(self, mock_get, tmp_cache_dir):
        service = WeatherService(cache_dir=tmp_cache_dir)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "current": {
                "temperature_2m": 72.5,
                "apparent_temperature": 70.0,
                "relative_humidity_2m": 45,
                "wind_speed_10m": 8.0,
                "weather_code": 1,
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = service.get_current_weather(44.88, -93.13)

        assert result is not None
        assert result["temperature"] == 72.5
        assert result["source"] == "meteo"
        assert result["description"] == "Mainly clear"

    @patch("src.utils.weather.requests.get")
    def test_api_error_returns_none(self, mock_get, tmp_cache_dir):
        service = WeatherService(cache_dir=tmp_cache_dir)
        mock_get.side_effect = Exception("Connection error")

        result = service.get_current_weather(44.88, -93.13)
        assert result is None
