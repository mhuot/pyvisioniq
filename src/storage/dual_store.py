"""
dual_store.py
Dual-write storage backend that writes to both CSV and Oracle simultaneously.
CSV is treated as the primary (reads come from CSV by default),
while Oracle receives all writes for Fabric Data Gateway consumption.
"""

import logging

import pandas as pd

from src.storage.base import StorageBackend
from src.storage.csv_store import CSVStorage

logger = logging.getLogger(__name__)


class DualWriteStorage(StorageBackend):
    """Writes to both CSV and Oracle; reads from CSV by default.

    Oracle write failures are logged but do not block the CSV write,
    ensuring local data integrity is always preserved.
    """

    def __init__(self, read_from="csv"):
        """Initialize both storage backends.

        Args:
            read_from: Which backend to read from ('csv' or 'oracle').
                       Defaults to 'csv' for proven reliability.
        """
        self.csv_storage = CSVStorage()

        # Lazy import to avoid requiring oracledb when not configured
        from src.storage.oracle_store import OracleStorage

        self.oracle_storage = OracleStorage()
        self.read_from = read_from
        logger.info("DualWriteStorage initialized (reads from: %s)", self.read_from)

    @property
    def _reader(self) -> StorageBackend:
        """Return the backend used for reads."""
        if self.read_from == "oracle":
            return self.oracle_storage
        return self.csv_storage

    # ------------------------------------------------------------------
    # Write methods — write to both backends
    # ------------------------------------------------------------------

    def store_vehicle_data(self, data):
        """Store data to CSV first (primary), then Oracle (secondary)."""
        # CSV is primary — always write here first
        self.csv_storage.store_vehicle_data(data)

        # Oracle is secondary — errors are logged but don't block
        try:
            self.oracle_storage.store_vehicle_data(data)
        except Exception as exc:
            logger.error("Oracle write failed (CSV write succeeded): %s", exc)

    # ------------------------------------------------------------------
    # Read methods — delegate to configured reader
    # ------------------------------------------------------------------

    def get_trips_df(self) -> pd.DataFrame:
        """Get trips data from the configured read backend."""
        return self._reader.get_trips_df()

    def get_battery_df(self) -> pd.DataFrame:
        """Get battery data from the configured read backend."""
        return self._reader.get_battery_df()

    def get_locations_df(self) -> pd.DataFrame:
        """Get locations data from the configured read backend."""
        return self._reader.get_locations_df()

    def get_charging_sessions_df(self) -> pd.DataFrame:
        """Get charging sessions data from the configured read backend."""
        return self._reader.get_charging_sessions_df()

    def get_latest_trips(self, limit=10) -> pd.DataFrame:
        """Get latest trips from the configured read backend."""
        return self._reader.get_latest_trips(limit=limit)

    def get_battery_history(self, days=7) -> pd.DataFrame:
        """Get battery history from the configured read backend."""
        return self._reader.get_battery_history(days=days)
