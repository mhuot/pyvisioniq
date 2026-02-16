"""Shared fixtures for tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Ensure tests never touch real credentials or data directories."""
    monkeypatch.delenv("BLUELINKUSER", raising=False)
    monkeypatch.delenv("BLUELINKPASS", raising=False)
    monkeypatch.delenv("BLUELINKPIN", raising=False)
    monkeypatch.delenv("BLUELINKVID", raising=False)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary directory for CSV storage tests."""
    return str(tmp_path / "data")


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Provide a temporary directory for cache tests."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return str(cache_dir)
