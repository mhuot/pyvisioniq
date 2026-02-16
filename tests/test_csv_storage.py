"""Tests for CSVStorage."""

import csv
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.storage.csv_store import CSVStorage


@pytest.fixture
def storage(tmp_data_dir, monkeypatch):
    """Create a CSVStorage instance using a temp directory."""
    monkeypatch.setenv("WEATHER_SOURCE", "vehicle")
    with patch.object(CSVStorage, "_init_files"):
        s = CSVStorage(data_dir=tmp_data_dir)
    # Manually create data dir and init files
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s._init_files()
    return s


class TestInitFiles:
    def test_creates_csv_files(self, storage):
        assert storage.trips_file.exists()
        assert storage.battery_file.exists()
        assert storage.location_file.exists()
        assert storage.charging_sessions_file.exists()

    def test_csv_headers_present(self, storage):
        df = pd.read_csv(storage.trips_file)
        assert "timestamp" in df.columns
        assert "distance" in df.columns
        assert "total_consumed" in df.columns

    def test_idempotent(self, storage):
        """Calling _init_files again should not duplicate headers."""
        storage._init_files()
        with open(storage.trips_file) as f:
            lines = f.readlines()
        assert len(lines) == 1  # header only


class TestStoreVehicleData:
    def test_store_empty_data(self, storage):
        storage.store_vehicle_data({})
        # Should not crash, no data written
        df = storage.get_battery_df()
        assert df.empty

    def test_store_battery_data(self, storage):
        data = {
            "timestamp": "2024-01-15T10:00:00",
            "battery": {
                "level": 80,
                "is_charging": False,
                "charging_power": 0,
                "remaining_time": None,
                "range": 300,
            },
            "location": {"latitude": 44.88, "longitude": -93.13},
            "odometer": 5000,
        }
        with patch.object(storage.weather_service, "get_current_weather", return_value=None):
            storage.store_vehicle_data(data)

        df = storage.get_battery_df()
        assert len(df) == 1
        assert df.iloc[0]["battery_level"] == 80

    def test_store_location_data(self, storage):
        data = {
            "timestamp": "2024-01-15T10:00:00",
            "location": {
                "latitude": 44.88585,
                "longitude": -93.13727,
                "last_updated": "2024-01-15T10:00:00",
            },
        }
        storage.store_vehicle_data(data)

        df = storage.get_locations_df()
        assert len(df) == 1
        assert df.iloc[0]["latitude"] == pytest.approx(44.88585)

    def test_trip_deduplication(self, storage):
        trip = {
            "date": "2024-01-15",
            "distance": 25.5,
            "duration": 30,
            "average_speed": 51,
            "max_speed": 80,
            "idle_time": 5,
            "trips_count": 1,
            "total_consumed": 4.2,
            "regenerated_energy": 0.8,
            "accessories_consumed": 0.1,
            "climate_consumed": 0.5,
            "drivetrain_consumed": 3.0,
            "battery_care_consumed": 0.0,
            "odometer_start": 5000,
            "end_latitude": None,
            "end_longitude": None,
            "end_temperature": None,
        }
        data = {"timestamp": "2024-01-15T10:00:00", "trips": [trip]}

        storage.store_vehicle_data(data)
        storage.store_vehicle_data(data)  # Store same trip again

        df = storage.get_trips_df()
        assert len(df) == 1  # Should deduplicate


class TestGetDataFrames:
    def test_empty_trips(self, storage):
        df = storage.get_trips_df()
        assert df.empty

    def test_empty_battery(self, storage):
        df = storage.get_battery_df()
        assert df.empty

    def test_empty_locations(self, storage):
        df = storage.get_locations_df()
        assert df.empty

    def test_empty_charging_sessions(self, storage):
        df = storage.get_charging_sessions_df()
        assert df.empty

    def test_battery_history_all(self, storage):
        df = storage.get_battery_history(days=None)
        assert df.empty

    def test_latest_trips_empty(self, storage):
        df = storage.get_latest_trips(limit=5)
        assert df.empty
