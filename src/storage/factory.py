"""
factory.py
Storage backend factory.
Reads the STORAGE_BACKEND environment variable and returns the appropriate
storage implementation. Uses lazy imports so that oracledb is only required
when Oracle or dual mode is explicitly enabled.
"""

import logging
import os

logger = logging.getLogger(__name__)


def create_storage():
    """Create and return the configured storage backend.

    Environment variable ``STORAGE_BACKEND`` controls which backend is used:
      - ``csv``   (default) — CSVStorage only, existing behaviour
      - ``oracle``          — OracleStorage only
      - ``dual``            — DualWriteStorage (CSV + Oracle)

    Returns:
        An instance of StorageBackend.
    """
    backend = os.getenv("STORAGE_BACKEND", "csv").lower().strip()

    if backend == "oracle":
        logger.info("Initializing Oracle-only storage backend")
        from src.storage.oracle_store import OracleStorage

        return OracleStorage()

    if backend == "dual":
        read_from = os.getenv("DUAL_READ_FROM", "csv").lower().strip()
        logger.info(
            "Initializing dual-write storage backend (reads from: %s)",
            read_from,
        )
        from src.storage.dual_store import DualWriteStorage

        return DualWriteStorage(read_from=read_from)

    # Default: CSV-only (zero changes from existing behaviour)
    if backend != "csv":
        logger.warning("Unknown STORAGE_BACKEND '%s', falling back to CSV", backend)

    from src.storage.csv_store import CSVStorage

    return CSVStorage()
