"""Weather data fetching module using Open-Meteo API"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)


def _at(values, index):
    """Index into an Open-Meteo series, tolerating a short or absent array."""
    if not values or index >= len(values):
        return None
    return values[index]


class WeatherService:
    """Service for fetching weather data from Open-Meteo API"""

    def __init__(self, cache_dir="cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

        # Get configuration from environment
        self.weather_source = os.getenv("WEATHER_SOURCE", "meteo").lower()

        # Open-Meteo API endpoint
        self.api_url = "https://api.open-meteo.com/v1/forecast"

        # Cache weather data for 30 minutes to avoid excessive calls
        self.cache_duration = timedelta(minutes=30)

        # Forecasts are recomputed upstream roughly hourly and are consumed
        # over a planning window of days, so refetching them on the
        # current-conditions cadence would be wasted calls.
        self.forecast_cache_duration = timedelta(hours=3)

    def get_current_weather(self, latitude: float, longitude: float) -> Optional[Dict]:
        """Get current weather data for the specified location"""

        # Require coordinates - no defaults
        if latitude is None or longitude is None:
            logger.error("Weather service requires latitude and longitude")
            return None

        lat = latitude
        lon = longitude

        # Check cache first
        cache_key = f"weather_{lat}_{lon}"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data

        try:
            # Fetch from Open-Meteo API
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "temperature_unit": "fahrenheit",  # Get in Fahrenheit
                "wind_speed_unit": "mph",
                "timezone": "America/Chicago",
            }

            response = requests.get(self.api_url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Extract current weather
            current = data.get("current", {})
            weather_data = {
                "temperature": current.get("temperature_2m"),
                "temperature_unit": "F",
                "feels_like": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "wind_speed": current.get("wind_speed_10m"),
                "weather_code": current.get("weather_code"),
                "description": self._get_weather_description(current.get("weather_code")),
                "timestamp": datetime.now().isoformat(),
                "source": "meteo",
                "location": {"lat": lat, "lon": lon},
            }

            # Save to cache
            self._save_to_cache(cache_key, weather_data)

            logger.info(
                f"Fetched weather data: {weather_data['temperature']}°F, {weather_data['description']}"
            )
            return weather_data

        except Exception as e:
            logger.error(f"Error fetching weather data: {e}")
            return None

    def get_forecast(self, latitude: float, longitude: float, days: int = 7) -> Optional[list]:
        """Daily forecast for trip planning: highs, lows and rain probability.

        Celsius throughout, unlike get_current_weather, because the measured
        efficiency bands the planner costs a trip against are in Celsius and
        converting twice is how unit bugs start.

        Open-Meteo's horizon is about 16 days; a level 1 planning window is
        three to five, so the default covers it comfortably.
        """
        if latitude is None or longitude is None:
            logger.error("Forecast requires latitude and longitude")
            return None

        cache_key = f"forecast_{latitude}_{longitude}_{days}"
        cached = self._get_from_cache(cache_key, self.forecast_cache_duration)
        if cached:
            return cached.get("days")

        try:
            response = requests.get(
                self.api_url,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "daily": (
                        "temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                    ),
                    "temperature_unit": "celsius",
                    "timezone": os.getenv("TZ", "America/Chicago"),
                    "forecast_days": days,
                },
                timeout=10,
            )
            response.raise_for_status()
            daily = response.json().get("daily", {})

            forecast = []
            for index, date in enumerate(daily.get("time", [])):
                high = _at(daily.get("temperature_2m_max"), index)
                low = _at(daily.get("temperature_2m_min"), index)
                forecast.append(
                    {
                        "date": date,
                        "temp_max_c": high,
                        "temp_min_c": low,
                        # The mean is what a day's driving actually happens at,
                        # more nearly than either extreme.
                        "temp_mean_c": (
                            round((high + low) / 2, 1)
                            if high is not None and low is not None
                            else None
                        ),
                        "precipitation_probability": _at(
                            daily.get("precipitation_probability_max"), index
                        ),
                    }
                )

            self._save_to_cache(
                cache_key, {"days": forecast, "fetched_at": datetime.now().isoformat()}
            )
            return forecast

        except (requests.RequestException, ValueError, KeyError) as error:
            logger.error("Error fetching forecast: %s", error)
            return None

    def forecast_for(self, latitude: float, longitude: float, when) -> Optional[Dict]:
        """The forecast entry covering a given date, or None if out of range."""
        target = when.date() if hasattr(when, "date") else when
        forecast = self.get_forecast(latitude, longitude)
        if not forecast:
            return None
        return next((day for day in forecast if day["date"] == target.isoformat()), None)

    def _get_weather_description(self, code: int) -> str:
        """Convert weather code to description"""
        if code is None:
            return "Unknown"

        # Based on WMO Weather interpretation codes
        weather_codes = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Foggy",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Slight snow",
            73: "Moderate snow",
            75: "Heavy snow",
            77: "Snow grains",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            85: "Slight snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail",
        }

        return weather_codes.get(code, f"Weather code {code}")

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get cache file path"""
        return self.cache_dir / f"{cache_key}.json"

    def _get_from_cache(self, cache_key: str, max_age=None) -> Optional[Dict]:
        """Get data from cache if still valid"""
        cache_path = self._get_cache_path(cache_key)

        if not cache_path.exists():
            return None

        try:
            # Check if cache is still valid
            modified_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
            if datetime.now() - modified_time > (max_age or self.cache_duration):
                return None

            with open(cache_path, "r") as f:
                return json.load(f)

        except Exception as e:
            logger.error(f"Error reading weather cache: {e}")
            return None

    def _save_to_cache(self, cache_key: str, data: Dict) -> None:
        """Save data to cache"""
        try:
            cache_path = self._get_cache_path(cache_key)
            with open(cache_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving weather cache: {e}")

    def get_temperature_in_celsius(self, fahrenheit: float) -> float:
        """Convert Fahrenheit to Celsius"""
        return round((fahrenheit - 32) * 5 / 9, 1)
