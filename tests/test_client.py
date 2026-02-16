"""Tests for CachedVehicleClient helper methods."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.api.client import CachedVehicleClient


@pytest.fixture
def client(tmp_cache_dir, monkeypatch):
    """Create a CachedVehicleClient with no real credentials."""
    monkeypatch.setenv("BLUELINKUSER", "")
    monkeypatch.setenv("BLUELINKPASS", "")
    monkeypatch.setenv("BLUELINKPIN", "")
    monkeypatch.setenv("BLUELINKVID", "test-vehicle-123")
    monkeypatch.setenv("API_DAILY_LIMIT", "30")
    monkeypatch.setenv("CACHE_DURATION_HOURS", "48")

    with patch.object(CachedVehicleClient, "_setup_api"):
        c = CachedVehicleClient(cache_dir=tmp_cache_dir)
    c.manager = None
    c.vehicle_id = "test-vehicle-123"
    return c


class TestCacheKey:
    def test_deterministic(self, client):
        key1 = client._get_cache_key("full_data")
        key2 = client._get_cache_key("full_data")
        assert key1 == key2

    def test_different_methods_different_keys(self, client):
        assert client._get_cache_key("full_data") != client._get_cache_key("other")

    def test_no_vehicle_id(self, client):
        client.vehicle_id = None
        assert client._get_cache_key("full_data") == "sample_data"


class TestCacheValidity:
    def test_missing_file_invalid(self, client):
        path = client._get_cache_path("nonexistent")
        assert not client._is_cache_valid(path)

    def test_fresh_file_valid(self, client, tmp_cache_dir):
        cache_path = Path(tmp_cache_dir) / "test.json"
        cache_path.write_text('{"test": true}')
        assert client._is_cache_valid(cache_path)

    def test_cache_age_format(self, client, tmp_cache_dir):
        # Write a cache file
        cache_key = client._get_cache_key("full_data")
        client._save_to_cache(cache_key, {"test": True})
        age = client._get_cache_age(cache_key)
        assert "h" in age and "m" in age

    def test_cache_age_missing(self, client):
        age = client._get_cache_age("nonexistent")
        assert age == "N/A"


class TestSaveAndLoadCache:
    def test_roundtrip(self, client):
        cache_key = client._get_cache_key("full_data")
        data = {"battery": {"level": 80}, "timestamp": "2024-01-15T10:00:00"}
        client._save_to_cache(cache_key, data)

        loaded = client._load_from_cache(cache_key)
        assert loaded is not None
        assert loaded["battery"]["level"] == 80
        assert loaded["is_cached"] is True

    def test_saves_history_file(self, client, tmp_cache_dir):
        cache_key = client._get_cache_key("full_data")
        client._save_to_cache(cache_key, {"test": True})

        history_files = list(Path(tmp_cache_dir).glob("history_*.json"))
        assert len(history_files) >= 1


class TestRemoteTimestampParsing:
    def test_iso_format(self, client):
        ts = client._parse_remote_timestamp("2024-01-15T10:30:00")
        assert ts is not None
        assert ts.year == 2024

    def test_z_suffix(self, client):
        ts = client._parse_remote_timestamp("2024-01-15T10:30:00Z")
        assert ts is not None

    def test_compact_format(self, client):
        ts = client._parse_remote_timestamp("20240115103000")
        assert ts is not None
        assert ts.minute == 30

    def test_none_returns_none(self, client):
        assert client._parse_remote_timestamp(None) is None

    def test_datetime_passthrough(self, client):
        dt = datetime(2024, 1, 15, 10, 30)
        assert client._parse_remote_timestamp(dt) is dt


class TestRemoteDataFreshness:
    def test_newer_timestamp_is_fresh(self, client):
        old = {"api_last_updated": "2024-01-15T10:00:00"}
        new = {"api_last_updated": "2024-01-15T11:00:00"}
        assert client._is_remote_data_fresh(new, old) is True

    def test_same_timestamp_same_payload_not_fresh(self, client):
        old = {"api_last_updated": "2024-01-15T10:00:00", "raw_data": {"key": "val"}}
        new = {"api_last_updated": "2024-01-15T10:00:00", "raw_data": {"key": "val"}}
        assert client._is_remote_data_fresh(new, old) is False

    def test_same_timestamp_different_payload_is_fresh(self, client):
        old = {"api_last_updated": "2024-01-15T10:00:00", "raw_data": {"key": "old"}}
        new = {"api_last_updated": "2024-01-15T10:00:00", "raw_data": {"key": "new"}}
        assert client._is_remote_data_fresh(new, old) is True

    def test_no_previous_is_fresh(self, client):
        new = {"api_last_updated": "2024-01-15T10:00:00"}
        assert client._is_remote_data_fresh(new, None) is True

    def test_invalid_data_not_fresh(self, client):
        assert client._is_remote_data_fresh("not a dict", None) is False


class TestClassifyError:
    def test_rate_limit(self, client):
        err = Exception("Rate limit exceeded")
        classified = client._classify_error(err)
        assert classified.error_type == "rate_limit"

    def test_auth_error(self, client):
        err = Exception("401 Unauthorized")
        classified = client._classify_error(err)
        assert classified.error_type == "auth"

    def test_network_error(self, client):
        err = Exception("Connection timeout")
        classified = client._classify_error(err)
        assert classified.error_type == "network"

    def test_service_unavailable(self, client):
        err = Exception("503 Service Unavailable")
        classified = client._classify_error(err)
        assert classified.error_type == "service_unavailable"

    def test_vehicle_offline(self, client):
        err = Exception("Cannot reach vehicle")
        classified = client._classify_error(err)
        assert classified.error_type == "vehicle_offline"

    def test_unknown_error(self, client):
        err = Exception("Something completely unexpected")
        classified = client._classify_error(err)
        assert classified.error_type == "unknown"
